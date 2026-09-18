from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


app = FastAPI(title="Diagnostic.ar Demo HPO Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExtractRequest(BaseModel):
    text_es: str
    patient_locale: str | None = None


HPO_TERMS = [
    {
        "hpo_id": "HP:0000478",
        "label_es": "Anomalía ocular",
        "label_en": "Abnormality of the eye",
        "keywords": ["visual", "vision", "visión", "ojo", "ojos", "ocular"],
    },
    {
        "hpo_id": "HP:0000505",
        "label_es": "Disminución de agudeza visual",
        "label_en": "Visual impairment",
        "keywords": ["problemas visuales", "perdida visual", "pérdida visual", "baja vision", "baja visión"],
    },
    {
        "hpo_id": "HP:0000365",
        "label_es": "Pérdida auditiva",
        "label_en": "Hearing impairment",
        "keywords": ["perdida auditiva", "pérdida auditiva", "hipoacusia", "audicion", "audición"],
    },
    {
        "hpo_id": "HP:0001250",
        "label_es": "Convulsiones",
        "label_en": "Seizure",
        "keywords": ["convulsion", "convulsión", "convulsiones", "epilepsia"],
    },
    {
        "hpo_id": "HP:0001251",
        "label_es": "Ataxia",
        "label_en": "Ataxia",
        "keywords": ["ataxia", "equilibrio", "marcha inestable", "inestabilidad"],
    },
    {
        "hpo_id": "HP:0001263",
        "label_es": "Retraso global del desarrollo",
        "label_en": "Global developmental delay",
        "keywords": ["retraso del desarrollo", "desarrollo", "retraso madurativo"],
    },
    {
        "hpo_id": "HP:0001324",
        "label_es": "Debilidad muscular",
        "label_en": "Muscle weakness",
        "keywords": ["debilidad", "debilidad muscular", "fuerza"],
    },
    {
        "hpo_id": "HP:0002354",
        "label_es": "Memoria disminuida",
        "label_en": "Memory impairment",
        "keywords": ["memoria", "problemas de memoria", "olvidos"],
    },
    {
        "hpo_id": "HP:0002540",
        "label_es": "Movilidad reducida",
        "label_en": "Impaired mobility",
        "keywords": ["movilidad reducida", "movilidad", "caminar", "marcha"],
    },
    {
        "hpo_id": "HP:0000790",
        "label_es": "Hematuria",
        "label_en": "Hematuria",
        "keywords": ["hematuria", "sangre en orina", "sangre en la orina"],
    },
    {
        "hpo_id": "HP:0000093",
        "label_es": "Proteinuria",
        "label_en": "Proteinuria",
        "keywords": ["proteinuria", "proteina en orina", "proteína en orina", "espuma al orinar"],
    },
    {
        "hpo_id": "HP:0000119",
        "label_es": "Anomalía del sistema urinario",
        "label_en": "Abnormality of the genitourinary system",
        "keywords": ["orinar", "miccion", "micción", "urinario", "orina"],
    },
    {
        "hpo_id": "HP:0000819",
        "label_es": "Diabetes mellitus",
        "label_en": "Diabetes mellitus",
        "keywords": ["diabetes", "glucemia", "hiperglucemia"],
    },
    {
        "hpo_id": "HP:0001959",
        "label_es": "Polidipsia",
        "label_en": "Polydipsia",
        "keywords": ["sed", "mucha sed", "polidipsia"],
    },
    {
        "hpo_id": "HP:0000103",
        "label_es": "Poliuria",
        "label_en": "Polyuria",
        "keywords": ["poliuria", "orina mucho", "orinar mucho", "miccion frecuente", "micción frecuente"],
    },
    {
        "hpo_id": "HP:0003477",
        "label_es": "Neuropatía periférica",
        "label_en": "Peripheral neuropathy",
        "keywords": ["dolor quemante", "manos y pies", "neuropatia", "neuropatía", "acroparestesia"],
    },
]


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-záéíóúüñ0-9: ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def score_term(term: dict[str, Any], query: str) -> int:
    haystack = normalize(" ".join([term["hpo_id"], term["label_es"], term["label_en"], *term["keywords"]]))
    query = normalize(query)
    if not query:
        return 0
    if query in haystack:
        return 3
    return sum(1 for token in query.split() if token in haystack)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/hpo/es/search")
def search_hpo(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=50)) -> dict[str, Any]:
    ranked = [
        (score_term(term, q), term)
        for term in HPO_TERMS
    ]
    results = [
        {key: term[key] for key in ("hpo_id", "label_es", "label_en")}
        for score, term in sorted(ranked, key=lambda item: item[0], reverse=True)
        if score > 0
    ][:limit]
    return {"results": results}


@app.post("/extract-hpo")
def extract_hpo(payload: ExtractRequest) -> dict[str, Any]:
    text = payload.text_es or ""
    normalized = normalize(text)
    phenotypes = []
    for term in HPO_TERMS:
        matched_keyword = next((keyword for keyword in term["keywords"] if normalize(keyword) in normalized), None)
        if not matched_keyword:
            continue
        start = normalized.find(normalize(matched_keyword))
        phenotypes.append({
            "hpo_id": term["hpo_id"],
            "label": term["label_en"],
            "label_es": term["label_es"],
            "span_es": matched_keyword,
            "span_en": matched_keyword,
            "start_es": max(start, 0),
            "end_es": max(start, 0) + len(matched_keyword),
            "start_en": max(start, 0),
            "end_en": max(start, 0) + len(matched_keyword),
            "negated": False,
            "confidence": 0.85,
            "matched_by": "demo_keyword",
        })
    return {"phenotypes": phenotypes}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("demo_hpo_service:app", host="127.0.0.1", port=8000, reload=False)
