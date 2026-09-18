from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from genphenai_repreguntas.config import load_settings
from genphenai_repreguntas.engine import RepreguntasEngine
from genphenai_repreguntas.session import DiagnosticSession


class CandidateDisease(BaseModel):
    name: str | None = None
    orpha_code: str | None = None
    score: float | None = None


class StartSessionRequest(BaseModel):
    case_text: str = Field(min_length=3, max_length=12000)
    top_candidates: list[CandidateDisease] = Field(default_factory=list)


class AnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=6000)


app = FastAPI(title="GenPhenAI Repreguntas API", version="1.0.0")
sessions: dict[str, DiagnosticSession] = {}


@lru_cache(maxsize=1)
def get_engine() -> RepreguntasEngine:
    return RepreguntasEngine(load_settings())


@app.get("/health")
def health() -> dict[str, Any]:
    settings = load_settings()
    try:
        engine = get_engine()
        count = engine.vector_store._collection.count()
        return {"status": "ok", "documents": count, "model": settings.openai_model}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@app.post("/sessions")
def start_session(request: StartSessionRequest) -> dict[str, Any]:
    candidate_codes = [
        candidate.orpha_code
        for candidate in request.top_candidates
        if candidate.orpha_code
    ]
    candidate_names = [
        candidate.name
        for candidate in request.top_candidates
        if candidate.name
    ]
    session_id = str(uuid.uuid4())
    session = DiagnosticSession(
        case_text=request.case_text,
        candidate_orpha_codes=candidate_codes,
        candidate_names=candidate_names,
    )
    sessions[session_id] = session
    evaluation = get_engine().evaluate(session)
    return get_engine().response_from(session_id, session, evaluation)


@app.post("/sessions/{session_id}/answers")
def answer_session(session_id: str, request: AnswerRequest) -> dict[str, Any]:
    session = sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.add_answer(request.answer)
    evaluation = get_engine().evaluate(session)
    return get_engine().response_from(session_id, session, evaluation)


def main() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run("genphenai_repreguntas.api:app", host=settings.api_host, port=settings.api_port, reload=False)


if __name__ == "__main__":
    main()
