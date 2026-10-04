"""Which findings the model sees, and therefore which ones the re-ranker must add.

The re-ranker weighs in the evidence the model did not see (absent findings,
and present findings that were not sent or that the model reported as
unknown); with the asymmetric weighting it also applies the exclusions of
every present finding, which the model does not know. Each strategy is then
just a choice of what to send to the model, selected with
``PREGUNTAS_RERANK_STRATEGY``:

``MODEL_SEES_PRESENT`` (default)
    Every round sends all present findings, original and answered "sí". The
    model re-ranks with everything positive it knows; only the "no" answers,
    negated findings, model-unknown terms and exclusions go through the
    re-ranker, and with the default ``AnswerWeighting.NONE`` not even those:
    the ranking is the model's (``docs/DECISIONES.md``, D-18). The effective
    "no" answers can also go to the model as ``excluded_hpo_ids``
    (``PREGUNTAS_RERANK_NEGATIONS``, ``docs/NEGACIONES.md``).
``FIXED_PRIOR``
    Every round sends only the findings reported before the questions. The
    prior stays the first ranking and every answer, "sí" or "no", goes
    through the re-ranker, so a wrong "sí" weighs as one bounded factor and
    each "sí" counts once (``docs/EVALUACION.md``, sections 6.3 and 11.8).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import NamedTuple

from diagnosticar_preguntas.domain import Evidence


class RerankStrategy(StrEnum):
    MODEL_SEES_PRESENT = "model_sees_present"
    FIXED_PRIOR = "fixed_prior"


class ModelQuery(NamedTuple):
    """What one ``ModelClient.rank`` call carries, both sorted for stable caching."""

    hpo_ids: tuple[str, ...]
    excluded_hpo_ids: tuple[str, ...] = ()


def model_query(
    strategy: RerankStrategy,
    *,
    reported_present: Iterable[str],
    evidence: Evidence,
    answered_no: Iterable[str] = (),
) -> ModelQuery:
    """The terms to send to ``ModelClient.rank`` this round.

    ``reported_present`` are the present findings the family reported before
    any question (``present_hpo_ids`` of the request). ``answered_no`` are the
    effective "no" answers, and nothing else: not "no sé", and not the
    negated findings of the free text, which are not validated. They go to
    the model only under ``MODEL_SEES_PRESENT``, where the re-ranker does not
    apply them again, and never a term that is also present.
    """
    if strategy is RerankStrategy.FIXED_PRIOR:
        return ModelQuery(tuple(sorted(set(reported_present))))
    present = tuple(sorted(evidence.present))
    return ModelQuery(present, tuple(sorted(set(answered_no) - evidence.present)))
