"""Which findings the model sees, and therefore which ones the re-ranker must add.

The re-ranker weighs in the evidence the model did not see (absent findings,
and present findings that were not sent or that the model reported as
unknown); with the asymmetric weighting it also applies the exclusions of
every present finding, which the model does not know. Each strategy is then
just a choice of what to send to the model, selected with
``PREGUNTAS_RERANK_STRATEGY``:

``FIXED_PRIOR`` (default)
    Every round sends only the findings reported before the questions. The
    prior stays the first ranking and every answer, "sí" or "no", goes
    through the re-ranker, so a wrong "sí" weighs as one bounded factor and
    each "sí" counts once (``docs/EVALUACION.md``, sections 6.3 and 11.8).
``MODEL_SEES_PRESENT``
    Every round sends all present findings, original and answered "sí". The
    model re-ranks with everything positive it knows; only the "no" answers,
    negated findings, model-unknown terms and exclusions go through the
    re-ranker.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from diagnosticar_preguntas.domain import Evidence


class RerankStrategy(StrEnum):
    MODEL_SEES_PRESENT = "model_sees_present"
    FIXED_PRIOR = "fixed_prior"


def model_query(
    strategy: RerankStrategy, *, reported_present: Iterable[str], evidence: Evidence
) -> tuple[str, ...]:
    """HPO ids to send to ``ModelClient.rank`` this round, sorted for stable caching.

    ``reported_present`` are the present findings the family reported before
    any question (``present_hpo_ids`` of the request).
    """
    if strategy is RerankStrategy.FIXED_PRIOR:
        return tuple(sorted(set(reported_present)))
    return tuple(sorted(evidence.present))
