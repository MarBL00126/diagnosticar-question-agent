from __future__ import annotations

from typing import Any

import pytest
from round_fakes import (
    GENES,
    LABELS,
    SHORTLIST,
    FakeLabels,
    FakeModel,
    FakeReranker,
    FakeSelector,
    ScriptedLLM,
    candidate,
    notes,
    question,
    request,
    round_draft,
    settings,
    summary,
)

from diagnosticar_preguntas import __version__
from diagnosticar_preguntas.agent.round import (
    WARNING_NOTES,
    WARNING_QUESTIONS,
    WARNING_SUMMARY,
    RoundService,
)
from diagnosticar_preguntas.domain import (
    AnswerValue,
    GeneScore,
    Mode,
    ModelUnavailableError,
)
from diagnosticar_preguntas.llm.provider import (
    LLMInvalidOutputError,
    LLMQuotaError,
    LLMTimeoutError,
)
from diagnosticar_preguntas.llm.tasks import QuestionTask, SummaryTask
from diagnosticar_preguntas.llm.template import TemplateProvider

FIRST_THREE = ("HP:0001738", "HP:0001696", "HP:0002099")
# After yes_no_answers() the first two shortlist terms are known, leaving these.
ROUND_TWO = ("HP:0002099", "HP:0000765")


def service(
    llm: Any = None,
    *,
    model: FakeModel | None = None,
    reranker: FakeReranker | None = None,
    selector: FakeSelector | None = None,
    **config: Any,
) -> RoundService:
    return RoundService(
        model=model or FakeModel(),
        reranker=reranker or FakeReranker(),
        selector=selector or FakeSelector(),
        labels=FakeLabels(),
        llm=llm or TemplateProvider(),
        settings=settings(**config),
    )


def yes_no_answers() -> list[dict[str, Any]]:
    return [
        {"hpo_id": "HP:0001738", "answer": "si"},
        {"hpo_id": "HP:0001696", "answer": "no"},
        {"hpo_id": "HP:0012236", "answer": "no_se"},
    ]


class TestFirstRound:
    def test_returns_three_questions_with_round_scoped_ids(self) -> None:
        llm = ScriptedLLM(questions=[round_draft(*FIRST_THREE)])

        response = service(llm).run(request())

        assert response.status == "questions"
        assert response.finish_reason is None
        assert [q.id for q in response.questions] == [f"r1-{h}" for h in FIRST_THREE]
        assert response.warnings == []
        assert response.summary is None

    def test_each_question_carries_the_spanish_label_of_its_term(self) -> None:
        llm = ScriptedLLM(questions=[round_draft(*FIRST_THREE)])

        response = service(llm).run(request())

        assert [(q.hpo_id, q.label) for q in response.questions] == [
            (h, LABELS[h]) for h in FIRST_THREE
        ]

    def test_template_questions_carry_the_label_too(self) -> None:
        response = service(TemplateProvider()).run(request(mode=Mode.PATIENT))

        assert all(q.label == LABELS[q.hpo_id] for q in response.questions)

    def test_ranking_uses_gene_symbol_as_gene_id(self) -> None:
        response = service().run(request())

        assert [(g.gene_id, g.gene_symbol) for g in response.ranking] == [
            (g.gene_symbol, g.gene_symbol) for g in GENES
        ]
        assert response.ranking[0].model_rank == 1

    def test_reports_agent_kb_and_llm_versions(self) -> None:
        response = service(ScriptedLLM(questions=[round_draft(*FIRST_THREE)])).run(request())

        assert response.versions.agent == __version__
        assert response.versions.knowledge_base == "kb-test-1"
        assert response.versions.llm_model == "claude-opus-5"

    def test_passes_specialties_and_confidence_from_the_model(self) -> None:
        response = service().run(request())

        assert response.specialties == {"top_specialties": ["Pulmonology", "Gastroenterology"]}
        assert response.confidence == 0.41

    def test_asks_the_selector_for_the_configured_shortlist_size(self) -> None:
        selector = FakeSelector()

        service(selector=selector, shortlist_size=5).run(request())

        assert selector.calls == [5]

    def test_asks_fewer_questions_when_fewer_candidates_exist(self) -> None:
        selector = FakeSelector([candidate("HP:0001738")])
        llm = ScriptedLLM(questions=[round_draft("HP:0001738")])

        response = service(llm, selector=selector).run(request())

        assert [q.hpo_id for q in response.questions] == ["HP:0001738"]
        assert llm.tasks(QuestionTask)[0].n_questions == 1


class TestEvidence:
    def test_model_sees_extracted_findings_plus_yes_answers(self) -> None:
        model = FakeModel()

        service(model=model).run(request(round=2, answers=yes_no_answers()))

        assert model.calls == [(sorted(["HP:0002205", "HP:0004322", "HP:0001738"]), 50)]

    def test_reranker_gets_no_answers_as_absent_and_no_se_as_unknown(self) -> None:
        reranker = FakeReranker()

        service(reranker=reranker).run(request(round=2, answers=yes_no_answers()))

        evidence = reranker.evidence[0]
        assert evidence.present == {"HP:0002205", "HP:0004322", "HP:0001738"}
        assert evidence.absent == {"HP:0001250", "HP:0001696"}
        assert evidence.unknown == {"HP:0012236"}

    def test_interpreted_answers_echo_every_answer_in_order(self) -> None:
        response = service().run(request(round=2, answers=yes_no_answers()))

        assert [(a.hpo_id, a.answer, a.effective_answer) for a in response.interpreted_answers] == [
            ("HP:0001738", AnswerValue.YES, AnswerValue.YES),
            ("HP:0001696", AnswerValue.NO, AnswerValue.NO),
            ("HP:0012236", AnswerValue.UNKNOWN, AnswerValue.UNKNOWN),
        ]


class TestNotes:
    def answers_with_note(self) -> list[dict[str, Any]]:
        answers = yes_no_answers()
        answers[0]["note"] = "creo que no, no estoy segura"
        return answers

    def test_a_contradicting_note_downgrades_the_answer_to_no_se(self) -> None:
        llm = ScriptedLLM(
            notes=[notes(("HP:0001738", False, "Duda si lo tiene."))],
            questions=[round_draft(*ROUND_TWO)],
        )
        reranker = FakeReranker()

        response = service(llm, reranker=reranker).run(
            request(round=2, answers=self.answers_with_note())
        )

        first = response.interpreted_answers[0]
        assert (first.answer, first.effective_answer) == (AnswerValue.YES, AnswerValue.UNKNOWN)
        assert first.note_summary == "Duda si lo tiene."
        assert "HP:0001738" in reranker.evidence[0].unknown
        assert "HP:0001738" not in reranker.evidence[0].present

    def test_only_answers_with_notes_are_sent_to_the_llm(self) -> None:
        llm = ScriptedLLM(
            notes=[notes(("HP:0001738", True, None))], questions=[round_draft(*ROUND_TWO)]
        )

        service(llm).run(request(round=2, answers=self.answers_with_note()))

        notes_task = llm.requests[0].task
        assert [i.hpo_id for i in notes_task.items] == ["HP:0001738"]

    def test_readings_for_answers_without_notes_are_ignored(self) -> None:
        llm = ScriptedLLM(
            notes=[notes(("HP:0001738", True, None), ("HP:0001696", False, None))],
            questions=[round_draft(*ROUND_TWO)],
        )

        response = service(llm).run(request(round=2, answers=self.answers_with_note()))

        assert response.interpreted_answers[1].effective_answer is AnswerValue.NO

    def test_note_failure_keeps_the_buttons_and_warns(self) -> None:
        llm = ScriptedLLM(notes=[LLMTimeoutError("slow")], questions=[round_draft(*ROUND_TWO)])

        response = service(llm).run(request(round=2, answers=self.answers_with_note()))

        assert response.interpreted_answers[0].effective_answer is AnswerValue.YES
        assert WARNING_NOTES in response.warnings

    def test_no_llm_call_when_no_answer_has_a_note(self) -> None:
        llm = ScriptedLLM(questions=[round_draft(*ROUND_TWO)])

        service(llm).run(request(round=2, answers=yes_no_answers()))

        assert all(isinstance(r.task, QuestionTask) for r in llm.requests)


class TestStopReasons:
    def test_finish_flag_closes_with_user_finished(self) -> None:
        response = service().run(request(round=2, finish=True, answers=yes_no_answers()))

        assert response.status == "finished"
        assert response.finish_reason == "user_finished"
        assert response.questions == []
        assert response.summary

    def test_round_after_the_last_closes_with_max_rounds(self) -> None:
        response = service().run(request(round=4))

        assert response.finish_reason == "max_rounds"

    def test_max_rounds_comes_from_settings(self) -> None:
        response = service(max_rounds=1).run(request(round=2))

        assert response.finish_reason == "max_rounds"

    def test_a_dominant_gene_closes_with_confident(self) -> None:
        reranker = FakeReranker(genes=(GeneScore("CFTR", 0.9, 0.4, 1),))

        response = service(reranker=reranker, confident_threshold=0.85).run(request())

        assert response.finish_reason == "confident"

    def test_an_empty_shortlist_closes_with_no_useful_questions(self) -> None:
        response = service(selector=FakeSelector([])).run(request())

        assert response.finish_reason == "no_useful_questions"

    def test_low_information_gain_closes_with_no_useful_questions(self) -> None:
        selector = FakeSelector([candidate("HP:0001738", 0.001)])

        response = service(selector=selector, min_information_gain=0.01).run(request())

        assert response.finish_reason == "no_useful_questions"

    def test_candidates_already_known_are_never_asked(self) -> None:
        selector = FakeSelector([candidate("HP:0002205"), candidate("HP:0001250")])

        response = service(selector=selector).run(request())

        assert response.finish_reason == "no_useful_questions"

    def test_finishing_skips_the_selector(self) -> None:
        selector = FakeSelector()

        service(selector=selector).run(request(finish=True))

        assert selector.calls == []


class TestModes:
    def test_patient_mode_hides_separates_and_rationale(self) -> None:
        llm = ScriptedLLM(
            questions=[round_draft(*FIRST_THREE)],
        )

        response = service(llm).run(request(mode=Mode.PATIENT))

        assert response.questions
        assert all(q.separates is None and q.rationale is None for q in response.questions)

    def test_clinician_mode_shows_rationale_and_at_most_five_separated_genes(self) -> None:
        wide = candidate("HP:0001738", genes_yes=("G1", "G2", "G3"), genes_no=("G4", "G5", "G6"))
        llm = ScriptedLLM(questions=[round_draft("HP:0001738")])

        response = service(llm, selector=FakeSelector([wide])).run(request())

        only = response.questions[0]
        assert only.separates == ["G1", "G2", "G3", "G4", "G5"]
        assert only.rationale == "Separa CFTR de DNAH5."

    def test_patient_mode_drops_findings_that_need_a_study(self) -> None:
        shortlist = [*SHORTLIST[:3], candidate("HP:0012236", 0.9, requires_testing=True)]
        llm = ScriptedLLM(questions=[round_draft(*FIRST_THREE)])

        service(llm, selector=FakeSelector(shortlist)).run(request(mode=Mode.PATIENT))

        offered = [c.hpo_id for c in llm.tasks(QuestionTask)[0].candidates]
        assert "HP:0012236" not in offered

    def test_patient_mode_keeps_study_findings_when_too_few_remain(self) -> None:
        shortlist = [
            candidate("HP:0001738"),
            candidate("HP:0012236", requires_testing=True),
            candidate("HP:0000765", requires_testing=True),
        ]
        llm = ScriptedLLM(questions=[round_draft("HP:0001738", "HP:0012236", "HP:0000765")])

        service(llm, selector=FakeSelector(shortlist)).run(request(mode=Mode.PATIENT))

        offered = [c.hpo_id for c in llm.tasks(QuestionTask)[0].candidates]
        assert offered == ["HP:0001738", "HP:0012236", "HP:0000765"]

    def test_clinician_mode_keeps_findings_that_need_a_study(self) -> None:
        shortlist = [*SHORTLIST[:3], candidate("HP:0012236", requires_testing=True)]
        llm = ScriptedLLM(questions=[round_draft(*FIRST_THREE)])

        service(llm, selector=FakeSelector(shortlist)).run(request())

        assert len(llm.tasks(QuestionTask)[0].candidates) == 4


class TestQuestionGuardrails:
    def test_a_rejected_draft_is_retried_with_the_violations(self) -> None:
        bad = round_draft("HP:0001738", "HP:0001738", "HP:0009999")
        llm = ScriptedLLM(questions=[bad, round_draft(*FIRST_THREE)])

        response = service(llm).run(request())

        retry = llm.tasks(QuestionTask)[1]
        assert any("HP:0009999" in c for c in retry.corrections)
        assert [q.hpo_id for q in response.questions] == list(FIRST_THREE)
        assert response.warnings == []

    def test_two_rejected_drafts_fall_back_to_templates_for_the_missing_questions(self) -> None:
        leaky = question("HP:0001696", text="¿Tiene algo que apunte a CFTR?")
        bad = round_draft("HP:0001738")
        bad = bad.model_copy(update={"questions": [*bad.questions, leaky]})
        llm = ScriptedLLM(questions=[bad, bad])

        response = service(llm).run(request(mode=Mode.PATIENT))

        assert len(response.questions) == 3
        assert response.questions[0].text == bad.questions[0].text
        assert all("CFTR" not in q.text for q in response.questions)
        assert WARNING_QUESTIONS in response.warnings

    def test_template_questions_never_repeat_an_accepted_one(self) -> None:
        llm = ScriptedLLM(questions=[round_draft("HP:0001696"), round_draft("HP:0001696")])

        response = service(llm).run(request())

        ids = [q.hpo_id for q in response.questions]
        assert ids[0] == "HP:0001696"
        assert len(set(ids)) == 3

    def test_an_llm_failure_degrades_to_templates_without_raising(self) -> None:
        llm = ScriptedLLM(questions=[LLMQuotaError("spent")])

        response = service(llm).run(request())

        assert [q.hpo_id for q in response.questions] == list(FIRST_THREE)
        assert WARNING_QUESTIONS in response.warnings
        assert len(llm.tasks(QuestionTask)) == 1

    def test_invalid_json_is_retried_once(self) -> None:
        llm = ScriptedLLM(questions=[LLMInvalidOutputError("not json"), round_draft(*FIRST_THREE)])

        response = service(llm).run(request())

        assert response.warnings == []
        assert len(llm.tasks(QuestionTask)) == 2

    def test_template_provider_never_warns(self) -> None:
        response = service(TemplateProvider()).run(request(mode=Mode.PATIENT))

        assert len(response.questions) == 3
        assert response.warnings == []
        assert response.versions.llm_model is None


class TestSummary:
    def test_the_summary_gets_answers_genes_and_specialties(self) -> None:
        llm = ScriptedLLM(summaries=[summary()])

        response = service(llm).run(request(round=2, finish=True, answers=yes_no_answers()))

        task = llm.tasks(SummaryTask)[0]
        assert task.finish_reason == "user_finished"
        assert task.answers[0].label == "Insuficiencia pancreática exocrina"
        assert task.specialties == ("Pulmonology", "Gastroenterology")
        assert response.summary == summary().summary

    def test_a_patient_summary_naming_a_gene_is_retried_then_templated(self) -> None:
        leaky = summary("Todo apunta a CFTR.")
        llm = ScriptedLLM(summaries=[leaky, leaky])

        response = service(llm).run(request(mode=Mode.PATIENT, finish=True))

        assert response.summary
        assert "CFTR" not in response.summary
        assert WARNING_SUMMARY in response.warnings
        assert llm.tasks(SummaryTask)[1].corrections

    def test_a_clinician_summary_may_name_genes(self) -> None:
        llm = ScriptedLLM(summaries=[summary("CFTR subió del puesto 3 al 1 con la esteatorrea.")])

        response = service(llm).run(request(finish=True))

        assert response.summary == "CFTR subió del puesto 3 al 1 con la esteatorrea."
        assert response.warnings == []

    def test_a_summary_failure_degrades_to_the_template(self) -> None:
        llm = ScriptedLLM(summaries=[LLMTimeoutError("slow")])

        response = service(llm).run(request(finish=True))

        assert response.summary
        assert WARNING_SUMMARY in response.warnings


def test_model_failure_propagates_as_model_unavailable() -> None:
    with pytest.raises(ModelUnavailableError):
        service(model=FakeModel(fail=True)).run(request())


def test_kb_version_is_unknown_when_labels_do_not_carry_one() -> None:
    class BareLabels(FakeLabels):
        version = None  # type: ignore[assignment]

    svc = RoundService(
        model=FakeModel(),
        reranker=FakeReranker(),
        selector=FakeSelector(),
        labels=BareLabels(),
        llm=TemplateProvider(),
        settings=settings(),
    )

    assert svc.run(request()).versions.knowledge_base == "unknown"
