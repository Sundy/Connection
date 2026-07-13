import math
import re


ALLOWED_ANNOTATION_KINDS = {"correct_tick", "error_circle", "error_cross", "comment"}
CONCLUSION_ANNOTATION_KINDS = {"correct_tick", "error_circle", "error_cross"}


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: object) -> float:
    return max(0.0, min(1.0, _number(value)))


def normalize_question_no(value: object) -> str:
    text = str(value or "").strip()
    match = re.match(r"^(?:第\s*)?([0-9一二三四五六七八九十百]+)", text)
    return match.group(1) if match else text


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


def group_questions(raw_questions: object, threshold: float) -> list[dict]:
    grouped: dict[tuple[int, str], dict] = {}
    for raw in raw_questions if isinstance(raw_questions, list) else []:
        if not isinstance(raw, dict):
            continue
        image_index = max(1, int(_number(raw.get("source_image_index"), 1)))
        question_no = normalize_question_no(raw.get("question_no"))
        key = (image_index, question_no)
        row = grouped.setdefault(key, {
            "source_image_index": image_index,
            "question_no": question_no,
            "question_type": raw.get("question_type") or "unknown",
            "recognized_answer": raw.get("recognized_answer"),
            "expected_answer": raw.get("expected_answer"),
            "is_correct": True,
            "score": raw.get("score"),
            "explanation_parts": [],
            "confidence_score": raw.get("confidence_score"),
            "annotations": [],
        })
        if raw.get("is_correct") is False:
            row["is_correct"] = False
        elif raw.get("is_correct") is None and row["is_correct"] is True:
            row["is_correct"] = None
        explanation = str(raw.get("explanation") or "").strip()
        if explanation and explanation not in row["explanation_parts"]:
            row["explanation_parts"].append(explanation)
        annotations = normalize_annotations(raw.get("annotations"), threshold)
        if raw.get("is_correct") is None:
            annotations = remove_conclusion_annotations(annotations)
        row["annotations"].extend(annotations)
    result = []
    for row in grouped.values():
        row["explanation"] = "；".join(row.pop("explanation_parts")) or None
        result.append(row)
    return result
