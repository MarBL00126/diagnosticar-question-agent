"""Types shared by every layer of the agent.

The agent is stateless: each round receives the full evidence gathered so far
and returns a refined ranking plus the next questions. These types are the
seams between the knowledge base, the re-ranker, the question selector, the
LLM layer and the HTTP API, so each can be built and tested on its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class Mode(StrEnum):
    """Who is answering. Changes wording, never the evidence."""

    PATIENT = "paciente"
    CLINICIAN = "medico"


class AnswerValue(StrEnum):
    YES = "si"
    NO = "no"
    UNKNOWN = "no_se"


@dataclass(frozen=True, slots=True)
class Answer:
    """One answered question. ``value`` comes from the button the user pressed."""

    hpo_id: str
    value: AnswerValue
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    """Everything known about the patient at the start of a round.

    ``present`` and ``absent`` merge the extraction step (non-negated and
    negated findings) with the effective answers of earlier rounds. ``unknown``
    holds terms answered "No sé": they are never asked again and never used as
    evidence. ``asked`` holds every term the patient answered in earlier rounds,
    whatever the effective answer: a subset of the three sets above, kept apart
    because ``present`` and ``absent`` also hold what was reported before any
    question.
    """

    present: frozenset[str]
    absent: frozenset[str] = frozenset()
    unknown: frozenset[str] = frozenset()
    asked: frozenset[str] = frozenset()

    @property
    def asked_or_known(self) -> frozenset[str]:
        return self.present | self.absent | self.unknown


@dataclass(frozen=True, slots=True)
class ModelRanking:
    """Output of one call to the diagnostic.ar model (``/rank-genes``).

    ``scores`` maps gene symbol to the model's softmax probability for the
    genes returned (the top-k, not the whole vocabulary).

    ``query_hpo_ids`` are the terms that were sent to the model. The re-ranker
    applies as likelihoods exactly the present terms the model did not see
    (not sent, or reported unknown), so no finding is counted twice. ``None``
    means "not recorded" and is read as "the model saw every present term".

    ``rerank`` is the tag of the re-ranker the service applied to the excluded
    terms of this call (for example ``rr-v1[neg=0.5]``), and ``None`` when it
    applied none, including every service that does not know the field.
    """

    scores: Mapping[str, float]
    specialties: Mapping[str, Any] | None = None
    confidence: float | None = None
    unknown_hpo_ids: tuple[str, ...] = ()
    query_hpo_ids: tuple[str, ...] | None = None
    rerank: str | None = None


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """A gene paired with one of its annotated diseases.

    Genes are scored through their diseases rather than a merged profile:
    merging a pleiotropic gene's diseases into one symptom list measurably
    hurts ranking.
    """

    gene_symbol: str
    disease_id: str
    weight: float


@dataclass(frozen=True, slots=True)
class GeneScore:
    gene_symbol: str
    score: float
    model_score: float
    model_rank: int


@dataclass(frozen=True, slots=True)
class Posterior:
    """Refined ranking after applying the evidence to the model's prior.

    ``genes`` is sorted by descending ``score``; scores sum to 1 over the
    genes considered.
    """

    genes: tuple[GeneScore, ...]
    hypotheses: tuple[Hypothesis, ...] = ()

    def top(self, n: int) -> tuple[GeneScore, ...]:
        return self.genes[:n]


@dataclass(frozen=True, slots=True)
class CandidateTerm:
    """A symptom worth asking about, as scored by the question selector.

    ``genes_yes`` are the genes whose probability rises on a "sí";
    ``genes_no`` those that rise on a "no", strongest first, both under the
    selector's likelihood. They feed the clinician view (``genes_if_yes`` /
    ``genes_if_no`` of the API), which drops ``genes_no`` when "no" answers do
    not reach the ranking.
    ``requires_testing`` marks findings only a lab or imaging study reveals;
    patient mode avoids them unless nothing else is left to ask.
    """

    hpo_id: str
    label_es: str
    label_en: str
    information_gain: float
    p_yes: float
    genes_yes: tuple[str, ...] = field(default=())
    genes_no: tuple[str, ...] = field(default=())
    requires_testing: bool = False


class ModelUnavailableError(RuntimeError):
    """The diagnostic.ar model service could not produce a ranking.

    Raised by ``ModelClient`` implementations on connection errors, timeouts,
    5xx and unusable responses. Without a ranking a round cannot run, so the
    API layer maps it to 503 instead of degrading.
    """


class ModelRequestError(RuntimeError):
    """The model service rejected the request itself (a 4xx other than ``no-known-hpo-ids``).

    Sending the same request again cannot succeed, so the API answers 422
    instead of asking the caller to retry.
    """


class ModelClient(Protocol):
    def rank(
        self, hpo_ids: Sequence[str], top_k: int, excluded_hpo_ids: Sequence[str] = ()
    ) -> ModelRanking:
        """Rank genes for ``hpo_ids``, penalising those that annotate ``excluded_hpo_ids``.

        Raises ``ModelUnavailableError`` or ``ModelRequestError``. Messages
        never carry the terms sent or the service's response body.
        """
        ...


class Reranker(Protocol):
    def rerank(self, ranking: ModelRanking, evidence: Evidence) -> Posterior: ...


class QuestionSelector(Protocol):
    def shortlist(self, posterior: Posterior, evidence: Evidence, k: int) -> list[CandidateTerm]:
        """Best terms to ask next.

        Implementations guarantee that no term of ``evidence.asked_or_known``
        is returned; the round service relies on it.
        """
        ...


class TermLabels(Protocol):
    def label_es(self, hpo_id: str) -> str | None: ...

    def label_en(self, hpo_id: str) -> str | None: ...
