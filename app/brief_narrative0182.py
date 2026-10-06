"""Grounded prose generation with exact evidence IDs, retries and safe fallback."""
import json
import re

from llm_core013 import MODEL, Invalid, content_stems
from brief_core017 import KEEP_ALIVE, _structured_call


NARRATIVE_VERSION = "narrative-0.18.4.5"
NARRATIVE_SYSTEM = """Ты редактор управленческих брифов на русском языке. Напиши связное, спокойное и ясное резюме совещания только из переданных подтверждённых фактов.

Требования:
- 3–5 логически связанных абзацев; для короткой встречи допустимо 1–2;
- ориентир 2500–4000 знаков, но не раздувай короткую встречу;
- начни с предмета разговора, затем объедини факты по темам и явно подтверждённым связям;
- не пересказывай реплики по очереди и не повторяй постоянно «обсудили»;
- сохрани значимые имена, организации, даты и числа без изменения;
- не добавляй причины, оценки, решения или выводы, которых нет в доказательствах;
- conclusion — один короткий итог: принятое решение, следующий шаг либо оставшийся открытым вопрос;
- поручения не выдумывай: они выводятся приложением отдельно;
- копируй evidence_ids только из переданного списка и не изменяй ни одного символа;
- каждый абзац и итог снабди evidence_ids только тех фактов, на которых он основан.

Верни только JSON по схеме."""


def _schema(evidence_ids, max_paragraphs):
    ids = {"type": "string", "enum": list(evidence_ids)}
    cited = {
        "type": "array",
        "minItems": 1,
        "maxItems": 8,
        "uniqueItems": True,
        "items": ids,
    }
    return {
        "type": "object",
        "properties": {
            "paragraphs": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_paragraphs,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "minLength": 40, "maxLength": 1600},
                        "evidence_ids": cited,
                    },
                    "required": ["text", "evidence_ids"],
                    "additionalProperties": False,
                },
            },
            "conclusion": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "minLength": 20, "maxLength": 900},
                    "evidence_ids": cited,
                },
                "required": ["text", "evidence_ids"],
                "additionalProperties": False,
            },
        },
        "required": ["paragraphs", "conclusion"],
        "additionalProperties": False,
    }


NARRATIVE_SCHEMA = _schema(["0000000000000000"], 6)


def infer_narrative(evidence, attempt_sink=None):
    reduced = [{"id": item["id"], "category": item["category"], "statement": item["statement"]} for item in evidence]
    ids = [item["id"] for item in evidence]
    payloads = []
    for maximum, temperature, predict in ((6, 0.10, 2800), (4, 0, 2400)):
        schema = _schema(ids, maximum)
        payloads.append(
            {
                "model": MODEL,
                "stream": False,
                "think": False,
                "keep_alive": KEEP_ALIVE,
                "format": schema,
                "options": {
                    "temperature": temperature,
                    "seed": 23,
                    "num_ctx": 8192,
                    "num_predict": predict,
                    "num_thread": 4,
                },
                "messages": [
                    {"role": "system", "content": NARRATIVE_SYSTEM + "\nJSON schema: " + json.dumps(schema, ensure_ascii=False)},
                    {"role": "user", "content": json.dumps({"evidence": reduced}, ensure_ascii=False)},
                ],
            }
        )
    return _structured_call("NARRATIVE", payloads, "NARRATIVE_OUTPUT_INCOMPLETE", attempt_sink)


def _validated_item(item, by_id, minimum, maximum):
    if not isinstance(item, dict) or set(item) != {"text", "evidence_ids"}:
        raise Invalid("NARRATIVE_ITEM_SCHEMA")
    text = item["text"].strip()
    ids = item["evidence_ids"]
    if not minimum <= len(text) <= maximum:
        raise Invalid("NARRATIVE_ITEM_LENGTH")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 8 or len(ids) != len(set(ids)):
        raise Invalid("NARRATIVE_EVIDENCE")
    try:
        sources = [by_id[item_id] for item_id in ids]
    except KeyError as exc:
        raise Invalid("NARRATIVE_UNKNOWN_EVIDENCE") from exc
    support = " ".join(source["statement"] for source in sources)
    if not (content_stems(text) & content_stems(support)):
        raise Invalid("NARRATIVE_NOT_GROUNDED")
    numbers = set(re.findall(r"\d+(?:[.,]\d+)?", text))
    supported_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", support))
    if not numbers <= supported_numbers:
        raise Invalid("NARRATIVE_NUMBER")
    return {"text": text, "evidence_ids": ids}


def validate_narrative(response, evidence):
    if not isinstance(response, dict) or set(response) != {"paragraphs", "conclusion"}:
        raise Invalid("NARRATIVE_SCHEMA")
    paragraphs = response["paragraphs"]
    if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 6:
        raise Invalid("NARRATIVE_SCHEMA")
    by_id = {item["id"]: item for item in evidence}
    result = {
        "paragraphs": [_validated_item(item, by_id, 40, 1600) for item in paragraphs],
        "conclusion": _validated_item(response["conclusion"], by_id, 20, 900),
        "version": NARRATIVE_VERSION,
        "fallback": False,
    }
    if sum(len(item["text"]) for item in result["paragraphs"]) < 80:
        raise Invalid("NARRATIVE_TOO_SHORT")
    return result


def _sentence(value):
    text = value.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def fallback_narrative(evidence, reason):
    chosen = list(evidence[:12])
    paragraphs = []
    for offset in range(0, len(chosen), 3):
        group = chosen[offset : offset + 3]
        text = " ".join(_sentence(item["statement"]) for item in group)
        if len(text) >= 40:
            paragraphs.append({"text": text[:1600], "evidence_ids": [item["id"] for item in group]})
        if len(paragraphs) == 4:
            break
    preferred = next((item for item in chosen if item["category"] == "DECISION"), None)
    preferred = preferred or next((item for item in chosen if item["category"] == "OPEN_QUESTION"), None)
    preferred = preferred or (chosen[-1] if chosen else None)
    conclusion = None
    if preferred:
        conclusion = {"text": _sentence(preferred["statement"]), "evidence_ids": [preferred["id"]]}
    return {
        "paragraphs": paragraphs,
        "conclusion": conclusion,
        "version": NARRATIVE_VERSION,
        "fallback": True,
        "fallback_reason": reason,
    }


def attach_narrative(content, narrative, evidence):
    by_id = {item["id"]: item for item in evidence}
    rows = list(narrative.get("paragraphs") or [])
    if narrative.get("conclusion"):
        rows.append(narrative["conclusion"])
    used = {item_id for item in rows for item_id in item["evidence_ids"]}
    sources = {item["id"]: item for item in content.get("sources", [])}
    for item_id in used:
        sources[item_id] = by_id[item_id]
    content["sources"] = list(sources.values())
    content["narrative"] = narrative
    if narrative.get("fallback"):
        content.setdefault("warnings", []).append("NARRATIVE_FALLBACK_USED")
    return content


def empty_narrative():
    return {"paragraphs": [], "conclusion": None, "version": NARRATIVE_VERSION, "fallback": False}
