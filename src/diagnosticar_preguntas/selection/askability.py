"""Which HPO terms make sensible yes/no questions at all.

A term is askable when it sits strictly below ``Phenotypic abnormality``
(inheritance modes, onset and frequency terms are not symptoms), is current,
and is specific enough that the answer means something:

* ``min_depth`` (default 3): organ-system terms sit at depth 2
  ("Abnormality of the nervous system"), and asking them tells nothing.
* ``max_descendants`` (default 400): a term covering more than a few hundred
  findings is a category, not a symptom. "Seizure" (346 descendants in HPO
  2026-02-16) stays askable; "Abnormality of mental function" (581) does not.
* ``grouping_label_patterns``: HPO names its grouping classes "Abnormality
  of X", "Abnormal X ..." and "Aplasia/Hypoplasia of X". A term with such a
  label *and* at least one descendant is a navigation node ("Abnormal
  reflex", "Abnormal homeostasis") whose children are what a clinician
  would actually ask; information gain alone would favour these because
  they split the candidates in half. Leaves keep their question ("Abnormal
  rituals" has nothing more specific below it).
* ``require_spanish_label`` (default on): questions are shown in Spanish and
  must start from the official translation; a term without one is skipped
  rather than shown with an ad-hoc translation. HPO 2026-02-16 translates
  all but a handful of terms, so the loss is negligible. With the rule off,
  ``CandidateTerm.label_es`` falls back to the English label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from diagnosticar_preguntas.kb.store import KnowledgeBase

DEFAULT_GROUPING_PATTERNS = (
    r"^Abnormal(ity)? ",
    r"^Aplasia/Hypoplasia ",
)


@dataclass(frozen=True, slots=True)
class AskabilityRules:
    min_depth: int = 3
    max_descendants: int = 400
    grouping_label_patterns: tuple[str, ...] = DEFAULT_GROUPING_PATTERNS
    require_spanish_label: bool = True


def askable_terms(kb: KnowledgeBase, rules: AskabilityRules) -> frozenset[str]:
    grouping = [re.compile(pattern) for pattern in rules.grouping_label_patterns]
    askable: set[str] = set()
    for hpo_id in kb.term_ids():
        term = kb.term(hpo_id)
        if (
            term is None
            or term.obsolete
            or term.depth is None
            or term.depth < rules.min_depth
            or not kb.is_phenotypic_abnormality(hpo_id)
            or len(kb.descendants(hpo_id)) > rules.max_descendants
            or (rules.require_spanish_label and not term.label_es)
            or (kb.descendants(hpo_id) and any(p.search(term.label_en) for p in grouping))
        ):
            continue
        askable.add(hpo_id)
    return frozenset(askable)
