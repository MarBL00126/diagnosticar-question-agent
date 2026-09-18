from __future__ import annotations

import json
import re
from typing import Any


def parse_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(match.group(0))


def normalize_certainty(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = payload.get("enfermedades_candidatas", [])
    high_candidates = [
        item for item in candidates
        if isinstance(item, dict) and str(item.get("probabilidad", "")).lower() == "alta"
    ]
    evidence_mixed = bool(payload.get("evidencia_mixta")) or len(high_candidates) >= 2
    unexplained = payload.get("fenotipos_no_explicados", [])
    for key in ("certeza", "certeza_final"):
        if isinstance(payload.get(key), (int, float)):
            if evidence_mixed or unexplained:
                payload[key] = min(payload[key], 70)
            if not candidates:
                payload[key] = min(payload[key], 40)
    if evidence_mixed:
        payload["evidencia_mixta"] = True
        payload["diagnostico_principal"] = "Diagnostico diferencial no concluyente"
    return payload
