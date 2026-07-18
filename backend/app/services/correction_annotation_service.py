import math
import re


ALLOWED_ANNOTATION_KINDS = {"correct_tick", "error_circle", "error_cross", "comment"}
CONCLUSION_ANNOTATION_KINDS = {"correct_tick", "error_circle", "error_cross"}
COMBINED_QUESTION_PATTERNS = (
    re.compile(
        r"^\s*([一二三四五六七八九十百]+)\s*[、,.，]\s*(\d+)"
        r"\s*(?:[（(]\s*([^）)]+)\s*[）)])?\s*$"
    ),
    re.compile(
        r"^\s*(?:第\s*)?(\d+)\s*(?:题)?"
        r"\s*(?:[（(]\s*([^）)]+)\s*[）)])?\s*$"
    ),
)


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: object) -> float:
    return max(0.0, min(1.0, _number(value)))


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def parse_question_identity(raw: dict) -> tuple[str | None, str, str | None]:
    combined = _optional_text(raw.get("question_no")) or ""
    parsed_section = None
    parsed_main = combined
    parsed_subquestion = None
    section_match = COMBINED_QUESTION_PATTERNS[0].match(combined)
    number_match = COMBINED_QUESTION_PATTERNS[1].match(combined)
    if section_match:
        parsed_section, parsed_main, parsed_subquestion = section_match.groups()
    elif number_match:
        parsed_main, parsed_subquestion = number_match.groups()
    return (
        _optional_text(raw.get("section_no")) or parsed_section,
        parsed_main.strip(),
        _optional_text(raw.get("subquestion_no")) or parsed_subquestion,
    )


def normalize_question_no(value: object) -> str:
    return parse_question_identity({"question_no": value})[1]


def normalize_annotations(raw_annotations: object, threshold: float) -> list[dict]:
    normalized = []
    for raw in raw_annotations if isinstance(raw_annotations, list) else []:
        if not isinstance(raw, dict) or raw.get("kind") not in ALLOWED_ANNOTATION_KINDS:
            continue
        confidence = _clamp(raw.get("confidence"))
        if confidence < threshold:
            continue
        x = _clamp(raw.get("x"))
        y = _clamp(raw.get("y"))
        width = min(_clamp(raw.get("width")), 1.0 - x)
        height = min(_clamp(raw.get("height")), 1.0 - y)
        if width <= 0 or height <= 0:
            continue
        normalized.append({
            "kind": raw["kind"],
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "text": str(raw.get("text") or "").strip() or None,
            "confidence": confidence,
        })
    return normalized


def remove_conclusion_annotations(annotations: list[dict]) -> list[dict]:
    return [
        annotation
        for annotation in annotations
        if isinstance(annotation, dict) and annotation.get("kind") not in CONCLUSION_ANNOTATION_KINDS
    ]


def _merge_status(current: bool | None, incoming: bool | None) -> bool | None:
    if current is False or incoming is False:
        return False
    if current is None or incoming is None:
        return None
    return True


def _normalize_correctness(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def normalize_question_leaves(raw_questions: object, threshold: float) -> list[dict]:
    grouped: dict[tuple[int, str | None, str, str | None], dict] = {}
    for raw in raw_questions if isinstance(raw_questions, list) else []:
        if not isinstance(raw, dict):
            continue
        image_index = max(1, int(_number(raw.get("source_image_index"), 1)))
        section_no, question_no, subquestion_no = parse_question_identity(raw)
        if not question_no:
            continue
        key = (image_index, section_no, question_no, subquestion_no)
        is_correct = _normalize_correctness(raw.get("is_correct"))
        annotations = normalize_annotations(raw.get("annotations"), threshold)
        if is_correct is None:
            annotations = remove_conclusion_annotations(annotations)
        if key not in grouped:
            grouped[key] = {
                "source_image_index": image_index,
                "section_no": section_no,
                "question_no": question_no,
                "subquestion_no": subquestion_no,
                "question_type": raw.get("question_type") or "unknown",
                "recognized_answer": raw.get("recognized_answer"),
                "expected_answer": raw.get("expected_answer"),
                "is_correct": is_correct,
                "score": raw.get("score"),
                "explanation": _optional_text(raw.get("explanation")),
                "confidence_score": raw.get("confidence_score"),
                "annotations": annotations,
            }
            continue
        row = grouped[key]
        row["is_correct"] = _merge_status(
            row["is_correct"],
            is_correct,
        )
        row["annotations"].extend(annotations)
        if row["is_correct"] is None:
            row["annotations"] = remove_conclusion_annotations(
                row["annotations"]
            )
        explanation = _optional_text(raw.get("explanation"))
        explanation_parts = str(row.get("explanation") or "").split("；")
        if explanation and explanation not in explanation_parts:
            row["explanation"] = "；".join(
                value
                for value in (row.get("explanation"), explanation)
                if value
            )
    return list(grouped.values())


def group_questions(raw_questions: object, threshold: float) -> list[dict]:
    return normalize_question_leaves(raw_questions, threshold)


def missing_global_question_nos(questions: list[dict]) -> list[int]:
    mains = list(dict.fromkeys(
        (
            question.get("section_no"),
            str(question.get("question_no") or ""),
        )
        for question in questions
    ))
    if not mains or any(
        not number.isdigit() or int(number) < 1
        for _, number in mains
    ):
        return []
    numbers = [int(number) for _, number in mains]
    if numbers[0] != 1 or max(numbers) != 14:
        return []
    if any(
        current <= previous
        for previous, current in zip(numbers, numbers[1:])
    ):
        return []
    sections_by_number: dict[int, set[str | None]] = {}
    for (section, _), number in zip(mains, numbers):
        sections_by_number.setdefault(number, set()).add(section)
    if any(len(sections) > 1 for sections in sections_by_number.values()):
        return []
    observed = set(numbers)
    return [
        number
        for number in range(1, max(numbers) + 1)
        if number not in observed
    ]
