import re
from typing import Any

import httpx

from backend.app.core.config import settings
from backend.app.services.llm_service import LLMResponseError, analyze_import_file_with_llm


TEMP_NAME_PATTERN = re.compile(
    r"(?:tmp[_-])?[0-9a-f]{16,}|\.(?:pdf|docx?|xlsx?|png|jpe?g)$",
    re.IGNORECASE,
)
TRAILING_EXTENSION_PATTERN = re.compile(r"(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)+$")
UNSAFE_TITLE_PATTERN = re.compile(
    r"tmp"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|[0-9a-f]{16,}"
    r"|(?<!\d)(?:\d{14}|\d{13}|\d{10})(?!\d)"
    r"|(?:19|20)\d{2}[-/._年](?:0?[1-9]|1[0-2])[-/._月](?:0?[1-9]|[12]\d|3[01])日?"
    r"|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
    r"|(?<!\d)(?:[01]?\d|2[0-3])[:：][0-5]\d(?::[0-5]\d)?(?!\d)",
    re.IGNORECASE,
)

ANALYSIS_KEYS = (
    "subject",
    "grade_hint",
    "material",
    "chapter",
    "exercise_type",
    "question_start",
    "question_end",
    "question_count",
    "keywords",
    "is_answer",
    "recommended_title",
    "confidence_score",
    "content_summary",
)

SUBJECT_MARKERS = (
    ("数学", ("数学", "算术", "口算")),
    ("语文", ("语文", "阅读理解", "作文", "古诗")),
    ("英语", ("英语", "英文", "English")),
    ("物理", ("物理",)),
    ("化学", ("化学",)),
    ("生物", ("生物",)),
    ("历史", ("历史",)),
    ("地理", ("地理",)),
    ("道德与法治", ("道德与法治", "政治")),
)

CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
NUMBER_PATTERN = r"(?:\d+|[零〇一二两三四五六七八九十百]+)"
ANSWER_PATTERN = re.compile(r"参考答案|答案|解析|解答|答题说明")


def _safe_semantic_title(value: object) -> str | None:
    raw = TRAILING_EXTENSION_PATTERN.sub("", str(value or "").strip()).strip(" _-.")
    if not raw or not re.search(r"[\u4e00-\u9fff]", raw):
        return None
    if UNSAFE_TITLE_PATTERN.search(raw):
        return None
    return raw[:40]


def normalize_content_title(candidate: object, signature: dict) -> str | None:
    candidate_title = _safe_semantic_title(candidate)
    if candidate_title:
        return candidate_title
    parts = [
        signature.get("subject"),
        signature.get("grade_hint"),
        signature.get("chapter"),
        signature.get("exercise_type"),
    ]
    fallback = "".join(str(part).strip() for part in parts if part)
    return _safe_semantic_title(fallback)


def _chinese_number_to_int(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if not value or any(char not in CHINESE_DIGITS and char not in "十百" for char in value):
        return None

    total = 0
    current = 0
    for char in value:
        if char == "百":
            total += (current or 1) * 100
            current = 0
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        else:
            current = CHINESE_DIGITS[char]
    return total + current


def _int_to_chinese_digit(value: int) -> str:
    digits = "零一二三四五六七八九"
    if 0 <= value < 10:
        return digits[value]
    return str(value)


def _extract_subject(text: str) -> str | None:
    lowered = text.lower()
    for subject, markers in SUBJECT_MARKERS:
        if any(marker.lower() in lowered for marker in markers):
            return subject
    return None


def _extract_grade(text: str) -> str | None:
    match = re.search(
        rf"(?P<grade>[一二三四五六七八九1-9])年级\s*(?P<term>[上下])?\s*(?:学期|册)?",
        text,
    )
    if not match:
        return None
    grade_value = _chinese_number_to_int(match.group("grade"))
    if grade_value is None:
        return None
    term = match.group("term")
    return f"{_int_to_chinese_digit(grade_value)}年级{term + '册' if term else ''}"


def _extract_material(text: str) -> str | None:
    match = re.search(r"(?:人教|北师大|苏教|沪教|外研|译林|冀教|浙教)版", text)
    return match.group(0) if match else None


def _extract_chapter(text: str) -> str | None:
    match = re.search(rf"第\s*(?P<number>{NUMBER_PATTERN})\s*(?P<kind>单元|章|课)", text)
    if not match:
        return None
    number = _chinese_number_to_int(match.group("number"))
    if number is None:
        return None
    return f"第{number}{match.group('kind')}"


def _extract_question_range(text: str) -> tuple[int | None, int | None]:
    match = re.search(
        rf"第?\s*(?P<start>{NUMBER_PATTERN})\s*(?:至|到|[-—~～])\s*"
        rf"第?\s*(?P<end>{NUMBER_PATTERN})\s*题",
        text,
    )
    if match:
        start = _chinese_number_to_int(match.group("start"))
        end = _chinese_number_to_int(match.group("end"))
        if start is not None and end is not None and end >= start:
            return start, end

    single = re.search(rf"第\s*(?P<number>{NUMBER_PATTERN})\s*题", text)
    if single:
        number = _chinese_number_to_int(single.group("number"))
        return number, number
    return None, None


def _extract_exercise_type(
    text: str,
    subject: str | None,
    grade_hint: str | None,
    chapter: str | None,
) -> str | None:
    cleaned = text
    cleaned = re.sub(
        rf"第?\s*{NUMBER_PATTERN}\s*(?:至|到|[-—~～])\s*第?\s*{NUMBER_PATTERN}\s*题",
        " ",
        cleaned,
    )
    cleaned = re.sub(rf"第\s*{NUMBER_PATTERN}\s*题", " ", cleaned)
    cleaned = re.sub(rf"第\s*{NUMBER_PATTERN}\s*(?:单元|章|课)", " ", cleaned)
    cleaned = re.sub(r"[一二三四五六七八九1-9]年级\s*[上下]?\s*(?:学期|册)?", " ", cleaned)
    for value in (subject, grade_hint, chapter):
        if value:
            cleaned = cleaned.replace(value, " ")
    cleaned = re.sub(r"(?:人教|北师大|苏教|沪教|外研|译林|冀教|浙教)版", " ", cleaned)
    cleaned = re.sub(r"[\s，,。；;：:、_]+", "", cleaned).strip("-—~～")
    match = re.search(r"([\u4e00-\u9fffA-Za-z0-9]{0,16}(?:练习|作业|试卷|测试|习题|口算|默写|背诵|阅读))", cleaned)
    return match.group(1)[-20:] if match else None


def _extract_keywords(text: str, signature: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    for key in ("subject", "material", "chapter", "exercise_type"):
        value = signature.get(key)
        if value and value not in keywords:
            keywords.append(str(value))

    for token in re.findall(r"[\u4e00-\u9fff]{2,10}|[A-Za-z]{3,}", text):
        token = token.strip()
        if token not in keywords and not re.fullmatch(r"(?:参考)?答案|解析|第.*题", token):
            keywords.append(token)
        if len(keywords) == 8:
            break
    return keywords[:8]


def _normalize_text(value: object) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_integer(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    normalized = str(value).strip()
    parsed = _chinese_number_to_int(normalized)
    return parsed if parsed is not None and parsed >= 0 else None


def _normalize_confidence(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(normalized, 0.0), 1.0)


def _normalize_boolean(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "是"}:
            return True
        if normalized in {"false", "0", "no", "否"}:
            return False
    return None


def _normalize_keywords(value: object) -> list[str] | None:
    if isinstance(value, str):
        raw_keywords = re.split(r"[,，、;；\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_keywords = list(value)
    else:
        return None

    keywords: list[str] = []
    for raw_keyword in raw_keywords:
        keyword = _normalize_text(raw_keyword)
        if keyword and keyword not in keywords:
            keywords.append(keyword[:20])
        if len(keywords) == 8:
            break
    return keywords or None


def _normalize_llm_analysis(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, Any] = {}
    for key in (
        "subject",
        "grade_hint",
        "material",
        "chapter",
        "exercise_type",
        "recommended_title",
        "content_summary",
    ):
        normalized[key] = _normalize_text(payload.get(key))
    for key in ("question_start", "question_end", "question_count"):
        normalized[key] = _normalize_integer(payload.get(key))
    normalized["keywords"] = _normalize_keywords(payload.get("keywords"))
    normalized["is_answer"] = _normalize_boolean(payload.get("is_answer"))
    normalized["confidence_score"] = _normalize_confidence(payload.get("confidence_score"))
    return normalized


def _local_analysis(text: str, document_role: str) -> dict[str, Any]:
    normalized_text = re.sub(r"\s+", " ", str(text or "")).strip()
    signature: dict[str, Any] = {key: None for key in ANALYSIS_KEYS}
    signature["keywords"] = []
    signature["is_answer"] = bool(ANSWER_PATTERN.search(normalized_text))
    signature["confidence_score"] = 0.0
    signature["content_summary"] = normalized_text[:120] or None
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", normalized_text):
        return signature

    signature["subject"] = _extract_subject(normalized_text)
    signature["grade_hint"] = _extract_grade(normalized_text)
    signature["material"] = _extract_material(normalized_text)
    signature["chapter"] = _extract_chapter(normalized_text)
    question_start, question_end = _extract_question_range(normalized_text)
    signature["question_start"] = question_start
    signature["question_end"] = question_end
    if question_start is not None and question_end is not None:
        signature["question_count"] = question_end - question_start + 1
    signature["exercise_type"] = _extract_exercise_type(
        normalized_text,
        signature["subject"],
        signature["grade_hint"],
        signature["chapter"],
    )
    signature["keywords"] = _extract_keywords(normalized_text, signature)

    score = 0.0
    score += 0.30 if signature["subject"] else 0.0
    score += 0.15 if signature["grade_hint"] else 0.0
    score += 0.15 if signature["chapter"] else 0.0
    score += 0.25 if signature["exercise_type"] else 0.0
    score += 0.15 if signature["question_count"] else 0.0
    signature["confidence_score"] = min(score, 1.0)
    signature["recommended_title"] = normalize_content_title(None, signature)
    return signature


def _has_usable_answer_signature(signature: dict[str, Any]) -> bool:
    for key in (
        "material",
        "chapter",
        "exercise_type",
        "question_start",
        "question_end",
        "question_count",
    ):
        if signature.get(key) not in (None, "", []):
            return True

    subject = str(signature.get("subject") or "")
    for keyword in signature.get("keywords") or []:
        semantic_keyword = ANSWER_PATTERN.sub("", str(keyword))
        if subject:
            semantic_keyword = semantic_keyword.replace(subject, "")
        semantic_keyword = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", semantic_keyword)
        if len(semantic_keyword) >= 2:
            return True
    return False


def analyze_import_content(text: str, document_role: str) -> dict:
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", str(text or "")):
        signature = _local_analysis(text, document_role)
        return {
            "recognized_title": None,
            "recognition_status": "failed",
            "signature": signature,
        }

    try:
        llm_analysis = _normalize_llm_analysis(
            analyze_import_file_with_llm(text, document_role)
        )
    except (httpx.HTTPError, LLMResponseError):
        llm_analysis = {}

    signature = _local_analysis(text, document_role)
    for key in ANALYSIS_KEYS:
        llm_value = llm_analysis.get(key)
        if llm_value is not None:
            signature[key] = llm_value

    question_start = signature.get("question_start")
    question_end = signature.get("question_end")
    if (
        signature.get("question_count") is None
        and question_start is not None
        and question_end is not None
        and question_end >= question_start
    ):
        signature["question_count"] = question_end - question_start + 1

    title = normalize_content_title(signature.get("recommended_title"), signature)
    signature["recommended_title"] = title
    confidence = signature.get("confidence_score") or 0.0

    if document_role == "answer":
        usable_signature = _has_usable_answer_signature(signature)
        succeeded = bool(signature.get("is_answer") and usable_signature)
    else:
        succeeded = bool(title and confidence >= settings.import_title_confidence_threshold)

    return {
        "recognized_title": title if succeeded and document_role != "answer" else None,
        "recognition_status": "success" if succeeded else "failed",
        "signature": signature,
    }
