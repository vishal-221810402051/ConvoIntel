# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import re
from typing import Any


SUPPORT_DIRECT = "DIRECTLY_SUPPORTED"
SUPPORT_ACCEPTABLE = "ACCEPTABLE_INFERENCE"
SUPPORT_WEAK = "WEAK_INFERENCE"

SUPPORT_LEVELS = {SUPPORT_DIRECT, SUPPORT_ACCEPTABLE, SUPPORT_WEAK}
CLAIM_STRENGTH_MAP = {
    SUPPORT_DIRECT: "direct",
    SUPPORT_ACCEPTABLE: "inferred",
    SUPPORT_WEAK: "weak",
}

_UNCERTAINTY_LEXICON = {
    "maybe",
    "perhaps",
    "depends",
    "can",
    "could",
    "might",
    "explore",
    "likely",
    "potentially",
    "i think",
    "i was thinking",
    "sounds reasonable",
    "need to define",
    "need to check",
}

_DECISION_ANCHORS = {
    "decide",
    "decision",
    "goal",
    "objective",
    "setup",
    "agree",
    "agreed",
    "confirm",
    "confirmed",
    "commit",
    "commitment",
    "will",
    "start",
    "proceed",
    "move forward",
    "pilot",
}
_OWNER_ANCHORS = {
    "owner",
    "responsible",
    "responsibility",
    "lead",
    "representative",
    "accountable",
    "authority",
    "report",
}
_FUNDING_ANCHORS = {
    "funding",
    "fund",
    "budget",
    "revenue",
    "margin",
    "tuition",
    "finance",
    "financial",
    "compensation",
}
_TIMELINE_ANCHORS = {
    "today",
    "tomorrow",
    "week",
    "month",
    "year",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "deadline",
    "start",
    "end",
    "after",
}
_GOVERNANCE_ANCHORS = {
    "authority",
    "approve",
    "approval",
    "governance",
    "structure",
    "decision power",
    "responsible",
    "reporting",
    "ownership",
}
_WARNING_ANCHORS = {
    "risk",
    "warning",
    "unclear",
    "undefined",
    "blocked",
    "open",
    "dependency",
    "authority",
    "governance",
    "funding",
}


def _split_sentences(text: str) -> list[str]:
    chunks = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    if chunks:
        return chunks
    text = text.strip()
    return [text] if text else []


def _tokenize(text: str) -> set[str]:
    return {
        tok
        for tok in re.findall(r"[A-Za-z0-9]+", text.lower())
        if len(tok) >= 3 and tok not in {"that", "this", "with", "from", "will", "have", "were"}
    }


def _contains_any(text: str, anchors: set[str]) -> bool:
    lowered = text.lower()
    return any(anchor in lowered for anchor in anchors)


def _time_expression_present(text: str) -> bool:
    lowered = text.lower()
    if _contains_any(lowered, _TIMELINE_ANCHORS):
        return True
    if re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)?\b", lowered):
        return True
    if re.search(r"\b(first|second|third|end)\s+(week|month|quarter)\b", lowered):
        return True
    return False


def _heuristic_claim_type(claim: str, claim_type: str) -> str:
    normalized = str(claim_type or "").strip().lower()
    if normalized:
        return normalized
    lowered = claim.lower()
    if _contains_any(lowered, _OWNER_ANCHORS):
        return "owner"
    if _contains_any(lowered, _FUNDING_ANCHORS):
        return "dependency:funding_dependency"
    if _contains_any(lowered, _GOVERNANCE_ANCHORS):
        return "dependency:authority_dependency"
    if _time_expression_present(lowered):
        return "timeline"
    if _contains_any(lowered, _DECISION_ANCHORS):
        return "decision"
    return "generic"


def semantic_support_score(claim: str, evidence_span: str, claim_type: str = "generic") -> float:
    claim_text = str(claim or "").strip().lower()
    evidence_text = str(evidence_span or "").strip().lower()
    if not claim_text or not evidence_text:
        return 0.0
    if claim_text == evidence_text:
        return 1.0

    normalized_type = _heuristic_claim_type(claim_text, claim_type)
    claim_tokens = _tokenize(claim_text)
    evidence_tokens = _tokenize(evidence_text)
    overlap = len(claim_tokens & evidence_tokens)
    base_overlap = overlap / max(1, len(claim_tokens))

    anchor_score = 0.0
    if normalized_type == "decision":
        anchor_score = 1.0 if _contains_any(evidence_text, _DECISION_ANCHORS) else 0.0
    elif normalized_type == "owner":
        anchor_score = 1.0 if _contains_any(evidence_text, _OWNER_ANCHORS) else 0.0
    elif normalized_type.startswith("dependency:funding"):
        anchor_score = 1.0 if _contains_any(evidence_text, _FUNDING_ANCHORS) else 0.0
    elif normalized_type.startswith("dependency:timeline") or normalized_type == "timeline":
        anchor_score = 1.0 if _time_expression_present(evidence_text) else 0.0
    elif normalized_type.startswith("dependency:authority") or normalized_type.startswith("dependency:governance"):
        anchor_score = 1.0 if _contains_any(evidence_text, _GOVERNANCE_ANCHORS) else 0.0
    elif normalized_type.startswith("dependency:partner"):
        anchor_score = 1.0 if ("partner" in evidence_text or "company" in evidence_text or "institution" in evidence_text) else 0.0
    elif normalized_type == "warning":
        anchor_score = 1.0 if _contains_any(evidence_text, _WARNING_ANCHORS) else 0.0
    else:
        anchor_score = 0.6 if overlap >= 2 else 0.3 if overlap == 1 else 0.0

    uncertainty_penalty = 0.15 if _contains_any(evidence_text, _UNCERTAINTY_LEXICON) else 0.0
    score = (0.6 * base_overlap) + (0.4 * anchor_score) - uncertainty_penalty
    return max(0.0, min(1.0, round(score, 3)))


def is_semantically_supportive(claim: str, evidence_span: str, claim_type: str = "generic") -> bool:
    return semantic_support_score(claim, evidence_span, claim_type=claim_type) >= 0.5


def extract_verbatim_spans(text: str, transcript: str) -> list[str]:
    """Return exact transcript substrings that best support the claim text."""
    claim = str(text or "").strip()
    body = str(transcript or "")
    if not claim or not body:
        return []

    spans: list[str] = []
    seen: set[str] = set()

    def push(span: str) -> None:
        value = span.strip()
        if value and value in body and value not in seen:
            seen.add(value)
            spans.append(value)

    if claim in body:
        push(claim)
        return spans

    for sentence in _split_sentences(claim):
        push(sentence)

    claim_tokens = _tokenize(claim)
    if claim_tokens:
        best_sentence = ""
        best_score = 0
        for sentence in _split_sentences(body):
            sent_tokens = _tokenize(sentence)
            if not sent_tokens:
                continue
            overlap = len(claim_tokens & sent_tokens)
            if overlap > best_score:
                best_score = overlap
                best_sentence = sentence
        if best_sentence and best_score >= 2:
            push(best_sentence)

    spans.sort(key=len, reverse=True)
    return spans


def validate_evidence_span(span: str, transcript: str) -> bool:
    value = str(span or "").strip()
    body = str(transcript or "")
    return bool(value and body and value in body)


def compute_evidence_confidence(span: str, claim: str, claim_type: str = "generic") -> float:
    ev = str(span or "").strip()
    cl = str(claim or "").strip()
    if not ev or not cl:
        return 0.0
    if ev == cl:
        sem = semantic_support_score(cl, ev, claim_type=claim_type)
        return round(max(sem, 1.0 if sem >= 0.5 else 0.49), 3)

    ev_low = ev.lower()
    cl_low = cl.lower()
    if ev_low == cl_low:
        sem = semantic_support_score(cl_low, ev_low, claim_type=claim_type)
        return round(max(sem, 1.0 if sem >= 0.5 else 0.49), 3)
    if cl_low in ev_low or ev_low in cl_low:
        base = 0.85

    else:
        ev_tokens = _tokenize(ev_low)
        cl_tokens = _tokenize(cl_low)
        if not ev_tokens or not cl_tokens:
            base = 0.4
        else:
            overlap = len(ev_tokens & cl_tokens)
            if overlap == 0:
                base = 0.2
            else:
                precision = overlap / len(cl_tokens)
                recall = overlap / len(ev_tokens)
                score = (precision + recall) / 2.0
                if score >= 0.8:
                    base = 0.8
                elif score >= 0.6:
                    base = 0.7
                elif score >= 0.4:
                    base = 0.6
                else:
                    base = 0.45

    semantic = semantic_support_score(cl, ev, claim_type=claim_type)
    if semantic < 0.5:
        return round(min(base, 0.49), 3)
    final = max(base, semantic)
    return round(min(1.0, final), 3)


def classify_support_level(confidence: float) -> str:
    if confidence >= 0.95:
        return SUPPORT_DIRECT
    if confidence >= 0.6:
        return SUPPORT_ACCEPTABLE
    return SUPPORT_WEAK


def build_evidence_binding(
    claim: str,
    transcript: str,
    preferred_spans: list[str] | None = None,
    claim_type: str = "generic",
) -> dict[str, Any]:
    candidates: list[str] = []
    seen: set[str] = set()

    for item in preferred_spans or []:
        value = str(item or "").strip()
        if value and value not in seen and validate_evidence_span(value, transcript):
            seen.add(value)
            candidates.append(value)

    for item in extract_verbatim_spans(claim, transcript):
        if item not in seen:
            seen.add(item)
            candidates.append(item)

    best_span = ""
    best_conf = 0.0
    best_semantic = 0.0
    for span in candidates:
        conf = compute_evidence_confidence(span, claim, claim_type=claim_type)
        sem = semantic_support_score(claim, span, claim_type=claim_type)
        if conf > best_conf:
            best_span = span
            best_conf = conf
            best_semantic = sem

    if not best_span:
        return {
            "support_level": SUPPORT_WEAK,
            "claim_strength": CLAIM_STRENGTH_MAP[SUPPORT_WEAK],
            "evidence_span": "",
            "evidence_start_index": -1,
            "evidence_end_index": -1,
            "evidence_confidence": 0.0,
            "semantic_support": False,
            "semantic_score": 0.0,
        }

    start = transcript.find(best_span)
    end = start + len(best_span) if start >= 0 else -1
    support_level = classify_support_level(best_conf)
    return {
        "support_level": support_level,
        "claim_strength": CLAIM_STRENGTH_MAP[support_level],
        "evidence_span": best_span,
        "evidence_start_index": start,
        "evidence_end_index": end,
        "evidence_confidence": round(float(best_conf), 3),
        "semantic_support": bool(best_semantic >= 0.5),
        "semantic_score": round(float(best_semantic), 3),
    }
