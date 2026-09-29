"""Shared plumbing of the evaluation: paths, seeds, the cached model client and configs."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from diagnosticar_preguntas.agent.guardrails import PatientVocabulary
from diagnosticar_preguntas.agent.round import RoundService
from diagnosticar_preguntas.config import LLMProviderName, Settings
from diagnosticar_preguntas.domain import ModelClient, ModelRanking, QuestionSelector
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.llm.provider import LLMProvider
from diagnosticar_preguntas.ranking.likelihood import LikelihoodConfig, LikelihoodModel
from diagnosticar_preguntas.ranking.model_client import HttpModelClient, NoKnownHpoIdsError
from diagnosticar_preguntas.ranking.reranker import AnswerWeighting, BayesianReranker, RerankConfig
from diagnosticar_preguntas.ranking.strategy import RerankStrategy
from diagnosticar_preguntas.selection.selector import InformationGainSelector, SelectionConfig
from diagnosticar_preguntas.wiring import patient_vocabulary

REPO_ROOT = Path(__file__).resolve().parents[1]
KB_PATH = REPO_ROOT / "data" / "kb.sqlite"
DATA_DIR = REPO_ROOT / "eval" / "data"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
CACHE_DIR = RESULTS_DIR / "cache"
CASES_PATH = DATA_DIR / "cases.json"
MODEL_URL = os.environ.get("EVAL_MODEL_URL", "http://127.0.0.1:18021")
MODEL_CONCURRENCY = 4
TOP_K = 50


def stable_seed(*parts: object) -> int:
    """A 64-bit seed that depends only on ``parts``, stable across processes and runs."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


class CachedModelClient:
    """``ModelClient`` that stores every answer on disk, keyed by sorted ids and ``top_k``.

    ``gate`` bounds concurrent calls to the real service, which serializes
    predictions; pass a ``multiprocessing`` semaphore to share it between
    worker processes.
    """

    def __init__(
        self,
        inner: ModelClient,
        cache_dir: Path = CACHE_DIR / "model",
        gate: Any = None,
    ) -> None:
        self._inner = inner
        self._dir = cache_dir
        self._gate = gate if gate is not None else threading.BoundedSemaphore(MODEL_CONCURRENCY)
        self.hits = 0
        self.misses = 0

    def _path(self, hpo_ids: Sequence[str], top_k: int) -> Path:
        key = hashlib.sha256(json.dumps([sorted(hpo_ids), top_k]).encode()).hexdigest()
        return self._dir / key[:2] / f"{key}.json"

    def rank(self, hpo_ids: Sequence[str], top_k: int) -> ModelRanking:
        query = tuple(hpo_ids)
        path = self._path(query, top_k)
        if path.is_file():
            self.hits += 1
            stored = json.loads(path.read_text())
        else:
            self.misses += 1
            with self._gate:
                try:
                    ranking = self._inner.rank(sorted(query), top_k)
                    stored = {
                        "scores": dict(ranking.scores),
                        "confidence": ranking.confidence,
                        "unknown_hpo_ids": list(ranking.unknown_hpo_ids),
                    }
                except NoKnownHpoIdsError:
                    stored = {"error": "no-known-hpo-ids"}
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(stored))
            tmp.replace(path)
        if stored.get("error") == "no-known-hpo-ids":
            raise NoKnownHpoIdsError(query)
        return ModelRanking(
            scores=stored["scores"],
            confidence=stored["confidence"],
            unknown_hpo_ids=tuple(stored["unknown_hpo_ids"]),
            query_hpo_ids=query,
        )


def real_model(gate: Any = None) -> CachedModelClient:
    return CachedModelClient(HttpModelClient(MODEL_URL, timeout=120.0), gate=gate)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Every knob the evaluation varies. Defaults are read from the code defaults."""

    strategy: RerankStrategy = Settings.model_fields["rerank_strategy"].default
    temperature: float = RerankConfig().temperature
    background: float = LikelihoodConfig().background
    missing_frequency: float = LikelihoodConfig().missing_frequency
    epsilon: float = LikelihoodConfig().epsilon
    p_unknown: float = SelectionConfig().p_unknown
    confident_threshold: float = Settings.model_fields["confident_threshold"].default
    min_information_gain: float = Settings.model_fields["min_information_gain"].default
    answer_weighting: AnswerWeighting = RerankConfig().answer_weighting
    answer_weight: float = RerankConfig().answer_weight
    exclusion_factor: float = RerankConfig().exclusion_factor

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["strategy"] = self.strategy.value
        data["answer_weighting"] = self.answer_weighting.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentConfig:
        """A config saved before ``answer_weighting`` existed used the calibrated weighting."""
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        if "strategy" in values:
            values["strategy"] = RerankStrategy(values["strategy"])
        values["answer_weighting"] = AnswerWeighting(
            values.get("answer_weighting", AnswerWeighting.CALIBRATED)
        )
        return cls(**values)

    def label(self) -> str:
        weighting = self.answer_weighting.value
        if self.answer_weighting is AnswerWeighting.ASYMMETRIC:
            weighting += f" k={self.answer_weight:g} x={self.exclusion_factor:g}"
        return (
            f"{self.strategy.value} T={self.temperature:g} bg={self.background:g} "
            f"mf={self.missing_frequency:g} pu={self.p_unknown:g} {weighting}"
        )


CALIBRATED_DEFAULTS = AgentConfig(
    strategy=RerankStrategy.FIXED_PRIOR, answer_weighting=AnswerWeighting.CALIBRATED
)
"""The code defaults until 2026-09-28, measured in docs/EVALUACION.md sections 5 to 11.7."""


@dataclass(frozen=True, slots=True)
class Pipeline:
    """The ranking and selection components for one ``AgentConfig``, shared between cases."""

    config: AgentConfig
    kb: KnowledgeBase
    likelihood: LikelihoodModel
    reranker: BayesianReranker
    selector: InformationGainSelector
    vocabulary: PatientVocabulary


def build_pipeline(
    kb: KnowledgeBase, config: AgentConfig, selector: InformationGainSelector | None = None
) -> Pipeline:
    """``selector`` may be reused across configs that share the likelihood and ``p_unknown``."""
    likelihood = LikelihoodModel(
        kb,
        LikelihoodConfig(
            background=config.background,
            missing_frequency=config.missing_frequency,
            epsilon=config.epsilon,
        ),
    )
    reranker = BayesianReranker(
        kb,
        likelihood,
        RerankConfig(
            temperature=config.temperature,
            answer_weighting=config.answer_weighting,
            answer_weight=config.answer_weight,
            exclusion_factor=config.exclusion_factor,
        ),
    )
    selector = selector or InformationGainSelector(
        kb, likelihood, SelectionConfig(p_unknown=config.p_unknown)
    )
    return Pipeline(config, kb, likelihood, reranker, selector, patient_vocabulary(kb))


def settings_for(
    config: AgentConfig,
    *,
    early_stop: bool,
    llm_provider: LLMProviderName = LLMProviderName.TEMPLATE,
) -> Settings:
    """Settings with every value the evaluation depends on given explicitly.

    With ``early_stop`` off, the service always asks while it has candidates;
    the stop rules are then applied afterwards from the recorded trajectory,
    which is exact because a stop only freezes the ranking of that round.
    """
    return Settings(
        model_service_url=MODEL_URL,
        kb_path=KB_PATH,
        llm_provider=llm_provider,
        shortlist_size=8,
        questions_per_round=3,
        max_rounds=3,
        confident_threshold=config.confident_threshold if early_stop else 1.0,
        min_information_gain=config.min_information_gain if early_stop else 0.0,
        rerank_strategy=config.strategy,
        answer_weighting=config.answer_weighting,
        answer_weight=config.answer_weight,
    )


def round_service(
    pipeline: Pipeline,
    *,
    model: ModelClient,
    selector: QuestionSelector,
    llm: LLMProvider,
    settings: Settings,
) -> RoundService:
    """The production ``RoundService`` over ``pipeline``'s ranking components."""
    return RoundService(
        model=model,
        reranker=pipeline.reranker,
        selector=selector,
        labels=pipeline.kb,
        llm=llm,
        settings=settings,
        kb_version=pipeline.kb.version,
        patient_vocabulary=pipeline.vocabulary,
    )


def open_kb() -> KnowledgeBase:
    return KnowledgeBase.open(KB_PATH)
