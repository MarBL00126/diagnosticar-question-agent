from __future__ import annotations

from collections.abc import Iterable
from itertools import pairwise

import pytest

from diagnosticar_preguntas.config import SelectorName, Settings
from diagnosticar_preguntas.domain import CandidateTerm, Evidence, Posterior
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import LikelihoodModel
from diagnosticar_preguntas.selection.askability import AskabilityRules
from diagnosticar_preguntas.selection.q2b import Q2bConfig, Q2bSelector
from diagnosticar_preguntas.selection.selector import InformationGainSelector
from diagnosticar_preguntas.wiring import build_selector
from fixtures.q2b_scenarios import World, build_scenarios, build_world

WORLD = build_world()
SCENARIOS = build_scenarios(WORLD)
OPEN = AskabilityRules(
    min_depth=0, max_descendants=10_000, grouping_label_patterns=(), require_spanish_label=False
)
EVERYTHING = 10_000
NO_EVIDENCE = Evidence(present=frozenset())


def selector(
    world: World = WORLD, *, n_questions: int = 3, diversity_lambda: float = 0.6
) -> Q2bSelector:
    return Q2bSelector(
        world.kb,
        world.likelihood,
        Q2bConfig(askability=OPEN, n_questions=n_questions, diversity_lambda=diversity_lambda),
    )


def ids(candidates: Iterable[CandidateTerm]) -> list[str]:
    return [c.hpo_id for c in candidates]


@pytest.fixture(scope="module")
def posterior() -> Posterior:
    return SCENARIOS[0].posterior(WORLD)


@pytest.fixture(scope="module")
def everything(posterior: Posterior) -> set[str]:
    """Every term Q2b would score with nothing blocked."""
    return set(ids(selector(n_questions=EVERYTHING).shortlist(posterior, NO_EVIDENCE, EVERYTHING)))


def related(term: str, kb: KnowledgeBase) -> set[str]:
    return {term} | set(kb.ancestors(term)) | set(kb.descendants(term))


def candidates_with(posterior: Posterior, evidence: Evidence) -> set[str]:
    return set(ids(selector(n_questions=EVERYTHING).shortlist(posterior, evidence, EVERYTHING)))


@pytest.fixture(scope="module")
def family() -> tuple[Posterior, set[str], str, str]:
    """A posterior, its scored terms, and a scored term with a scored descendant."""
    kb = WORLD.kb
    for scenario in SCENARIOS:
        posterior = scenario.posterior(WORLD)
        scored = candidates_with(posterior, NO_EVIDENCE)
        for parent in sorted(scored):
            for child in sorted(kb.descendants(parent) & scored):
                return posterior, scored, parent, child
    raise AssertionError("no scenario scores a term together with its descendant")


class TestBlocking:
    def test_reported_term_blocks_itself_ancestors_and_descendants(self, family) -> None:
        posterior, scored, parent, child = family
        for term in (parent, child):
            evidence = Evidence(present=frozenset({term}))

            assert candidates_with(posterior, evidence) == scored - related(term, WORLD.kb)

    def test_yes_answer_blocks_like_a_reported_term(self, family) -> None:
        posterior, scored, parent, _ = family
        evidence = Evidence(present=frozenset({parent}), asked=frozenset({parent}))

        assert candidates_with(posterior, evidence) == scored - related(parent, WORLD.kb)

    def test_no_answer_blocks_the_term_and_its_descendants_but_not_its_ancestors(
        self, family
    ) -> None:
        posterior, scored, parent, child = family
        evidence = Evidence(
            present=frozenset(), absent=frozenset({child}), asked=frozenset({child})
        )

        result = candidates_with(posterior, evidence)

        assert result == scored - {child} - set(WORLD.kb.descendants(child))
        assert parent in result

    def test_no_answer_on_a_parent_blocks_its_children(self, family) -> None:
        posterior, scored, parent, child = family
        evidence = Evidence(
            present=frozenset(), absent=frozenset({parent}), asked=frozenset({parent})
        )

        result = candidates_with(posterior, evidence)

        assert result == scored - {parent} - set(WORLD.kb.descendants(parent))
        assert child not in result

    def test_unknown_answer_blocks_the_term_and_its_descendants_but_not_its_ancestors(
        self, family
    ) -> None:
        posterior, scored, parent, child = family
        evidence = Evidence(
            present=frozenset(), unknown=frozenset({child}), asked=frozenset({child})
        )

        result = candidates_with(posterior, evidence)

        assert result == scored - {child} - set(WORLD.kb.descendants(child))
        assert parent in result

    def test_negated_finding_from_the_text_blocks_like_a_no(self, family) -> None:
        posterior, scored, parent, _ = family
        evidence = Evidence(present=frozenset(), absent=frozenset({parent}))

        assert candidates_with(posterior, evidence) == scored - (
            {parent} | set(WORLD.kb.descendants(parent))
        )

    def test_anything_already_asked_is_blocked(self, family) -> None:
        posterior, _, parent, _ = family
        evidence = Evidence(present=frozenset(), asked=frozenset({parent}))

        assert parent not in candidates_with(posterior, evidence)

    def test_picks_never_include_blocked_terms_in_any_scenario(self) -> None:
        kb = WORLD.kb
        for scenario in SCENARIOS:
            evidence = scenario.evidence()
            blocked = set(evidence.asked_or_known)
            for term in evidence.present:
                blocked |= related(term, kb)
            for term in evidence.absent | evidence.unknown:
                blocked |= {term} | set(kb.descendants(term))

            picks = ids(selector().shortlist(scenario.posterior(WORLD), evidence, k=8))

            assert not set(picks) & blocked, scenario.name


def lineage(term: str) -> set[str]:
    kb = WORLD.kb
    organ_level = {"HP:0000001", "HP:0000118"} | {
        t for t in kb.term_ids() if "HP:0000118" in kb.parents(t)
    }
    return (set(kb.ancestors(term)) | {term}) - organ_level


class TestDiversity:
    def test_a_term_sharing_a_lineage_with_the_first_pick_is_dropped_for_an_unrelated_one(
        self,
    ) -> None:
        checked = 0
        for scenario in SCENARIOS:
            posterior, evidence = scenario.posterior(WORLD), scenario.evidence()
            picks = ids(selector(diversity_lambda=1.0).shortlist(posterior, evidence, k=8))
            if len(picks) < 2:
                continue
            others = candidates_with(posterior, evidence) - {picks[0]}
            if any(not lineage(t) & lineage(picks[0]) for t in others):
                assert not lineage(picks[1]) & lineage(picks[0]), scenario.name
                checked += 1
        assert checked > 10

    def test_an_earlier_question_penalises_its_lineage_like_an_earlier_pick(self) -> None:
        checked = 0
        for scenario in SCENARIOS:
            posterior, base = scenario.posterior(WORLD), scenario.evidence()
            first = ids(selector().shortlist(posterior, base, k=8))
            if not first:
                continue
            evidence = Evidence(
                present=base.present,
                absent=base.absent,
                unknown=base.unknown | {first[0]},
                asked=base.asked | {first[0]},
            )
            picks = ids(selector(diversity_lambda=1.0).shortlist(posterior, evidence, k=8))
            others = candidates_with(posterior, evidence)
            if picks and any(not lineage(t) & lineage(first[0]) for t in others):
                assert not lineage(picks[0]) & lineage(first[0]), scenario.name
                checked += 1
        assert checked > 10

    def test_reported_findings_do_not_count_as_earlier_questions(self) -> None:
        for scenario in SCENARIOS:
            posterior = scenario.posterior(WORLD)
            evidence = scenario.evidence()
            reported_only = Evidence(
                present=evidence.present, absent=evidence.absent, unknown=evidence.unknown
            )
            if evidence.asked:
                continue
            assert ids(selector().shortlist(posterior, evidence, k=8)) == ids(
                selector().shortlist(posterior, reported_only, k=8)
            )

    def test_sharing_only_the_organ_system_is_not_penalised(self) -> None:
        organ_only = 0
        for scenario in SCENARIOS:
            picks = ids(
                selector(diversity_lambda=1.0).shortlist(
                    scenario.posterior(WORLD), scenario.evidence(), k=8
                )
            )
            for a, b in pairwise(picks):
                organ_only += not lineage(a) & lineage(b) and bool(
                    set(WORLD.kb.ancestors(a))
                    & set(WORLD.kb.ancestors(b)) - {"HP:0000001", "HP:0000118"}
                )
        assert organ_only > 0

    def test_penalty_changes_the_picks(self) -> None:
        differing = 0
        for scenario in SCENARIOS:
            posterior, evidence = scenario.posterior(WORLD), scenario.evidence()
            with_penalty = ids(selector(diversity_lambda=0.6).shortlist(posterior, evidence, 8))
            without = ids(selector(diversity_lambda=0.0).shortlist(posterior, evidence, 8))
            differing += with_penalty != without
        assert differing > 0

    def test_a_shared_lineage_costs_exactly_lambda_of_the_gain(self) -> None:
        penalised = 0
        for scenario in SCENARIOS:
            posterior, evidence = scenario.posterior(WORLD), NO_EVIDENCE
            ranked = selector(n_questions=EVERYTHING, diversity_lambda=0.0).shortlist(
                posterior, evidence, EVERYTHING
            )
            if len(ranked) < 2:
                continue
            gain = {c.hpo_id: c.information_gain for c in ranked}
            first = ranked[0].hpo_id

            def adjusted(
                term: str, first: str = first, gain: dict[str, float] = gain
            ) -> tuple[float, int]:
                shared = bool(lineage(term) & lineage(first))
                return (gain[term] * 0.4 if shared else gain[term], -int(term[3:]))

            expected = max((t for t in gain if t != first), key=adjusted)
            picks = ids(selector(diversity_lambda=0.6).shortlist(posterior, evidence, 2))

            assert picks[:2] == [first, expected], scenario.name
            penalised += expected != ranked[1].hpo_id
        assert penalised > 0

    def test_ties_go_to_the_smaller_hpo_number(self) -> None:
        tied_pairs = 0
        for scenario in SCENARIOS:
            ranked = selector(n_questions=EVERYTHING, diversity_lambda=0.0).shortlist(
                scenario.posterior(WORLD), scenario.evidence(), EVERYTHING
            )
            for a, b in pairwise(ranked):
                if a.information_gain == b.information_gain:
                    tied_pairs += 1
                    assert int(a.hpo_id[3:]) < int(b.hpo_id[3:])
        assert tied_pairs > 0


class TestCandidatePosterior:
    def test_only_the_top_ten_genes_with_a_disease_are_considered(self) -> None:
        scenario = SCENARIOS[0]
        posterior = scenario.posterior(WORLD)
        small = Q2bSelector(
            WORLD.kb,
            WORLD.likelihood,
            Q2bConfig(askability=OPEN, n_candidates=2, n_questions=EVERYTHING),
        )

        picks = small.shortlist(posterior, NO_EVIDENCE, EVERYTHING)

        top = [
            g.gene_symbol
            for g in posterior.genes
            if WORLD.kb.annotated_diseases_for_gene(g.gene_symbol)
        ][:2]
        assert picks
        for candidate in picks:
            assert set(candidate.genes_yes) | set(candidate.genes_no) <= set(top)

    def test_fewer_than_two_genes_means_no_questions(self) -> None:
        posterior = SCENARIOS[0].posterior(WORLD)
        only_one = Posterior(genes=posterior.genes[:1])

        assert selector().shortlist(only_one, NO_EVIDENCE, k=8) == []

    def test_returns_at_most_the_round_size_even_when_asked_for_more(
        self, posterior: Posterior
    ) -> None:
        assert len(selector(n_questions=3).shortlist(posterior, NO_EVIDENCE, k=8)) == 3
        assert len(selector(n_questions=3).shortlist(posterior, NO_EVIDENCE, k=2)) == 2
        assert selector().shortlist(posterior, NO_EVIDENCE, k=0) == []

    def test_gain_is_reported_in_bits_and_picks_carry_gene_shifts(
        self, posterior: Posterior
    ) -> None:
        picks = selector().shortlist(posterior, NO_EVIDENCE, k=8)

        assert all(0 < c.information_gain <= 1.0 for c in picks)
        assert all(0 < c.p_yes < 1 for c in picks)
        assert any(c.genes_yes or c.genes_no for c in picks)

    def test_only_askable_terms_are_asked(self) -> None:
        strict = AskabilityRules(min_depth=5)
        default = Q2bSelector(WORLD.kb, WORLD.likelihood, Q2bConfig(askability=strict))

        picks = default.shortlist(SCENARIOS[0].posterior(WORLD), NO_EVIDENCE, k=8)

        assert picks == []


class TestSwitch:
    def test_q2b_is_the_default(self) -> None:
        assert Settings(_env_file=None).selector is SelectorName.Q2B  # type: ignore[call-arg]

    @pytest.mark.parametrize("value", ["legacy", "q2b"])
    def test_env_variable_selects_it(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("PREGUNTAS_SELECTOR", value)

        assert Settings(_env_file=None).selector.value == value  # type: ignore[call-arg]

    def test_unknown_value_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PREGUNTAS_SELECTOR", "q3")

        with pytest.raises(ValueError):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_wiring_builds_each_selector(self) -> None:
        kb, likelihood = WORLD.kb, WORLD.likelihood
        q2b = build_selector(Settings(_env_file=None), kb, likelihood)  # type: ignore[call-arg]
        legacy = build_selector(
            Settings(_env_file=None, selector=SelectorName.LEGACY),  # type: ignore[call-arg]
            kb,
            likelihood,
        )

        assert isinstance(q2b, Q2bSelector)
        assert q2b.config.n_questions == 3
        assert q2b.config.diversity_lambda == 0.6
        assert q2b.config.n_candidates == 10
        assert type(legacy) is InformationGainSelector

    def test_questions_per_round_sets_the_q2b_round_size(self) -> None:
        settings = Settings(_env_file=None, questions_per_round=2)  # type: ignore[call-arg]

        q2b = build_selector(settings, WORLD.kb, WORLD.likelihood)

        assert isinstance(q2b, Q2bSelector)
        assert q2b.config.n_questions == 2


class TestLegacyIsUnchanged:
    def test_legacy_answers_exactly_like_a_default_information_gain_selector(self) -> None:
        legacy = build_selector(
            Settings(_env_file=None, selector=SelectorName.LEGACY),  # type: ignore[call-arg]
            WORLD.kb,
            WORLD.likelihood,
        )
        reference = InformationGainSelector(WORLD.kb, LikelihoodModel(WORLD.kb))
        compared = 0
        for scenario in SCENARIOS:
            posterior, evidence = scenario.posterior(WORLD), scenario.evidence()
            got = legacy.shortlist(posterior, evidence, k=8)

            assert repr(got) == repr(reference.shortlist(posterior, evidence, k=8))
            compared += bool(got)
        assert compared > 10

    def test_legacy_ignores_which_terms_were_asked(self) -> None:
        legacy = InformationGainSelector(WORLD.kb, WORLD.likelihood)
        for scenario in SCENARIOS:
            posterior, evidence = scenario.posterior(WORLD), scenario.evidence()
            bare = Evidence(evidence.present, evidence.absent, evidence.unknown)

            assert repr(legacy.shortlist(posterior, evidence, k=8)) == repr(
                legacy.shortlist(posterior, bare, k=8)
            )
