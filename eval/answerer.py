"""Simulated family answering the agent's questions about a known true disease.

The patient is drawn once per case (a latent set of findings), then every
question is answered from it with noise:

1. Latent findings: each positive annotation of the true disease is present
   with its frequency (``missing_frequency`` when unstated); the terms the
   family already reported are always present. With ``annotation_keep`` < 1
   each annotation first survives only with that probability, so the patient
   lacks documented features the likelihood model expects.
2. The truthful answer to term ``t`` is "sí" when some latent finding is ``t``
   or a descendant of ``t``. Otherwise it is "sí" with probability
   ``spurious_yes`` (findings unrelated to the disease, or noticed by
   mistake), except for terms the disease explicitly excludes (``NOT``).
3. "No sé" with probability ``p_unknown`` (``p_unknown_testing`` for findings
   only a study reveals, in patient mode); otherwise the truthful answer is
   flipped with probability ``flip``.

Marginally, step 2 gives ``P(sí | t)`` equal to the noisy-OR of the annotated
frequencies at or below ``t``, the same form as the agent's likelihood model.
That is the circularity of the evaluation: the simulated patients are built
from the annotations the agent reasons with. The answerer's parameters differ
from the agent's (``missing_frequency`` 0.6 vs 0.5, spurious rate 0.02 vs a
background of 0.05, a real "No sé" and flip noise), and the dropout variant
breaks the assumption that every documented feature can appear.

Every random draw is seeded by (case, term), so a given case answers the same
question the same way whichever policy asks it and in whatever order.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from eval.cases import Case, positive_annotations
from eval.harness import stable_seed

from diagnosticar_preguntas.domain import AnswerValue, Mode
from diagnosticar_preguntas.kb.store import KnowledgeBase


@dataclass(frozen=True, slots=True)
class AnswererConfig:
    name: str = "default"
    missing_frequency: float = 0.6
    spurious_yes: float = 0.02
    flip: float = 0.1
    p_unknown: float = 0.2
    p_unknown_testing: float = 0.5
    annotation_keep: float = 1.0


DEFAULT = AnswererConfig()
DROPOUT = AnswererConfig(name="dropout", annotation_keep=0.7, spurious_yes=0.05)
NOISELESS = AnswererConfig(name="noiseless", flip=0.0, p_unknown=0.0, p_unknown_testing=0.0)
ANSWERERS = {a.name: a for a in (DEFAULT, DROPOUT, NOISELESS)}


class SimulatedPatient:
    def __init__(
        self, kb: KnowledgeBase, case: Case, config: AnswererConfig, mode: Mode = Mode.PATIENT
    ) -> None:
        self._kb = kb
        self._case = case
        self._config = config
        self._mode = mode
        rng = random.Random(stable_seed("latent", config.name, case.case_id))
        latent = set(case.present)
        for annotation in positive_annotations(kb, case.disease_id):
            keep = rng.random() < config.annotation_keep
            frequency = (
                config.missing_frequency if annotation.frequency is None else annotation.frequency
            )
            if keep and rng.random() < frequency:
                latent.add(annotation.hpo_id)
        self.latent = frozenset(latent)
        self._implied = frozenset(
            term for finding in latent for term in (finding, *kb.ancestors(finding))
        )
        self._excluded = frozenset(a.hpo_id for a in kb.annotations(case.disease_id) if a.negated)

    def has(self, hpo_id: str) -> bool:
        return hpo_id in self._implied

    def excluded(self, hpo_id: str) -> bool:
        return hpo_id in self._excluded or not self._excluded.isdisjoint(self._kb.ancestors(hpo_id))

    def answer(self, hpo_id: str) -> AnswerValue:
        config = self._config
        rng = random.Random(stable_seed("answer", self._case.case_id, hpo_id))
        unknown_draw, flip_draw, spurious_draw = rng.random(), rng.random(), rng.random()
        p_unknown = (
            config.p_unknown_testing
            if self._mode is Mode.PATIENT and self._kb.requires_testing(hpo_id)
            else config.p_unknown
        )
        if unknown_draw < p_unknown:
            return AnswerValue.UNKNOWN
        truth = self.has(hpo_id) or (
            not self.excluded(hpo_id) and spurious_draw < config.spurious_yes
        )
        if flip_draw < config.flip:
            truth = not truth
        return AnswerValue.YES if truth else AnswerValue.NO
