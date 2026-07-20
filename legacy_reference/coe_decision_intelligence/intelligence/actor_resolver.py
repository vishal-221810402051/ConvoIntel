# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Any


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z\-']+", str(text or ""))


def _normalize_token(token: str) -> str:
    return token.lower().strip()


def _transcript_token_set(transcript: str) -> set[str]:
    return {_normalize_token(tok) for tok in _word_tokens(transcript)}


def actor_present_in_transcript(actor: str, transcript: str, alias_map: dict[str, str] | None = None) -> bool:
    name = str(actor or "").strip()
    body = str(transcript or "")
    if not name or not body:
        return False
    if re.search(rf"\b{re.escape(name)}\b", body, flags=re.IGNORECASE):
        return True
    if alias_map:
        for source, canonical in alias_map.items():
            if str(canonical).strip().lower() != name.lower():
                continue
            if re.search(rf"\b{re.escape(str(source))}\b", body, flags=re.IGNORECASE):
                return True

    transcript_tokens = _transcript_token_set(body)
    parts = [_normalize_token(tok) for tok in _word_tokens(name) if len(tok) >= 4]
    if not parts:
        return False
    matched = 0
    for token in parts:
        if token in transcript_tokens:
            matched += 1
            continue
        close = get_close_matches(token, list(transcript_tokens), n=1, cutoff=0.84)
        if close:
            matched += 1
    return matched >= 1


def infer_speaker_clusters(transcript: str) -> dict[str, int]:
    lines = [line.strip() for line in str(transcript or "").splitlines() if line.strip()]
    clusters: dict[str, int] = {}
    for line in lines:
        match = re.match(r"^([A-Z][A-Za-z\-'. ]{1,40}):", line)
        if not match:
            continue
        speaker = match.group(1).strip()
        clusters[speaker] = clusters.get(speaker, 0) + 1
    return clusters


def resolve_actor_from_text(
    text: str,
    transcript: str,
    alias_map: dict[str, str],
    preferred_actor: str = "",
    fallback_actor: str = "",
) -> tuple[str, float]:
    sentence = str(text or "")
    for actor in [preferred_actor, fallback_actor]:
        normalized = str(actor or "").strip()
        if not normalized:
            continue
        if actor_present_in_transcript(normalized, transcript, alias_map):
            return normalized, 0.85

    for source, canonical in alias_map.items():
        source_str = str(source or "").strip()
        canon_str = str(canonical or "").strip()
        if not source_str or not canon_str:
            continue
        if re.search(rf"\b{re.escape(source_str)}\b", sentence, flags=re.IGNORECASE) and actor_present_in_transcript(
            canon_str, transcript, alias_map
        ):
            return canon_str, 0.9

    if re.search(r"\byou\b", sentence, flags=re.IGNORECASE):
        for actor in [preferred_actor, fallback_actor]:
            normalized = str(actor or "").strip()
            if normalized and actor_present_in_transcript(normalized, transcript, alias_map):
                return normalized, 0.75
        return "unknown", 0.4

    return "unknown", 0.35


def actor_support_payload(
    actor: str,
    transcript: str,
    alias_map: dict[str, str],
    preferred_actor: str = "",
    fallback_actor: str = "",
) -> dict[str, Any]:
    resolved, confidence = resolve_actor_from_text(
        text=actor,
        transcript=transcript,
        alias_map=alias_map,
        preferred_actor=preferred_actor,
        fallback_actor=fallback_actor,
    )
    if resolved == "unknown":
        return {"actor": "unknown", "actor_confidence": round(confidence, 3)}
    return {"actor": resolved, "actor_confidence": round(confidence, 3)}
