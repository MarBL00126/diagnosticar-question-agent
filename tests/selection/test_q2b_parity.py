"""The agent's Q2b picks what the EXP-Q-001 harness's ``Q2b.next_batch`` picked.

Two checks over the same hand-built scenarios (``fixtures/q2b_scenarios.py``):

* ``test_matches_frozen_harness_picks`` compares with ``expected_picks.json``, the
  output of the harness recorded by running this file with the training repo's
  interpreter (see ``__main__``). It always runs.
* ``test_matches_live_harness`` imports the harness read-only from the training repo and
  compares again. It is skipped where that checkout or its dependencies (scipy) are missing.

The harness gets the quantity the agent computes, ``P(term | gene)`` as the mean of
``P(term | disease)`` over the gene's annotated diseases, through a stand-in for its
``GeneKnowledge``; what is compared is the selection logic (top-10 renormalisation,
gain, blocking, diversity, tie-break), not the two knowledge bases.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from diagnosticar_preguntas.selection.q2b import Q2bConfig, Q2bSelector
from fixtures.q2b_scenarios import Scenario, World, build_scenarios, build_world

TRAINING_SRC = Path("/home/mate/FIUBA/trabajo-profesional/diagnostic.ar-training/src")
FROZEN = Path(__file__).with_name("expected_picks.json")
LAMBDA = 0.6
QUESTIONS = 3

WORLD = build_world()
SCENARIOS = build_scenarios(WORLD)


def agent_picks(world: World, scenario: Scenario) -> list[str]:
    selector = Q2bSelector(
        world.kb,
        world.likelihood,
        Q2bConfig(
            askability=_open_askability(),
            diversity_lambda=LAMBDA,
            n_questions=QUESTIONS,
        ),
    )
    shortlist = selector.shortlist(scenario.posterior(world), scenario.evidence(), k=8)
    return [c.hpo_id for c in shortlist]


def _open_askability() -> Any:
    from diagnosticar_preguntas.selection.askability import AskabilityRules

    return AskabilityRules(
        min_depth=0, max_descendants=10_000, grouping_label_patterns=(), require_spanish_label=False
    )


def harness_picks(world: World, scenario: Scenario) -> list[str]:
    sys.path.insert(0, str(TRAINING_SRC))
    try:
        from diagnostic_ar_training.eval.interactive import PublicCase
        from diagnostic_ar_training.eval.questioning import Q2b
        from diagnostic_ar_training.ontology import Ontology
    finally:
        sys.path.remove(str(TRAINING_SRC))

    kb, likelihood = world.kb, world.likelihood
    ontology = Ontology(
        parents=dict(world.parents),
        labels={},
        alt_ids={},
        replaced_by={},
        obsolete=frozenset(),
    )

    class Knowledge:
        def __init__(self) -> None:
            self.kb = {
                "diseases": {
                    d: {"phenotypes": {t: {} for t in likelihood.profile(d).annotated}}
                    for gene in world.genes
                    for d in kb.annotated_diseases_for_gene(gene)
                }
            }

        def diseases(self, gene: str) -> dict[str, float]:
            diseases = kb.annotated_diseases_for_gene(gene)
            return {d: 1 / len(diseases) for d in diseases}

        def term_probability(self, gene: str, term: str) -> float:
            profiles = [likelihood.profile(d) for d in kb.annotated_diseases_for_gene(gene)]
            return sum(p.probability(term) for p in profiles) / len(profiles)

    posterior = scenario.posterior(world)
    scores = {g.gene_symbol: g.score for g in posterior.genes}
    probs = np.array([scores[g] for g in world.genes])
    ctx = SimpleNamespace(
        e0=SimpleNamespace(genes=world.genes),
        knowledge=Knowledge(),
        ontology=ontology,
        posterior=lambda case, rerank: probs,
    )
    public = PublicCase(case_id=scenario.name, present=scenario.reported)
    return Q2b(lam=LAMBDA).next_batch(ctx, public, dict(scenario.answers), QUESTIONS)


def _harness_available() -> bool:
    try:
        import scipy  # noqa: F401
    except ModuleNotFoundError:
        return False
    return (TRAINING_SRC / "diagnostic_ar_training").is_dir()


def test_scenarios_are_not_trivial() -> None:
    picks = [agent_picks(WORLD, s) for s in SCENARIOS]

    assert sum(len(p) == QUESTIONS for p in picks) >= 0.8 * len(picks)
    assert any(s.answers for s in SCENARIOS)
    assert any(s.reported for s in SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_matches_frozen_harness_picks(scenario: Scenario) -> None:
    frozen = json.loads(FROZEN.read_text())

    assert agent_picks(WORLD, scenario) == frozen[scenario.name]


@pytest.mark.skipif(not _harness_available(), reason="training harness or scipy not available")
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_matches_live_harness(scenario: Scenario) -> None:
    assert agent_picks(WORLD, scenario) == harness_picks(WORLD, scenario)


if __name__ == "__main__":
    # Refreshes expected_picks.json; needs scipy, so run it with the training repo's interpreter.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    frozen = {s.name: harness_picks(WORLD, s) for s in SCENARIOS}
    FROZEN.write_text(json.dumps(frozen, indent=1) + "\n")
