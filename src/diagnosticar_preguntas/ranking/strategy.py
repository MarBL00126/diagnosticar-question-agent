"""Which findings the model sees, and therefore which ones the re-ranker must add.

The re-ranker follows a single rule: it multiplies in the likelihood of every
piece of evidence the model did not see (absent findings, and present
findings that were not sent or that the model reported as unknown). Each
strategy is then just a choice of what to send to the model:

``MODEL_SEES_PRESENT`` (default)
    Every round sends all present findings, original and answered "sí". The
    model re-ranks with everything positive it knows; only the "no" answers,
    negated findings and model-unknown terms go through the likelihood.
``FIXED_PRIOR``
    Every round sends only the findings reported before the questions. The
    prior stays the first ranking and every answer, "sí" or "no", goes
    through the likelihood. Useful as an evaluation baseline because the
    effect of the answers is isolated from the model's.
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
