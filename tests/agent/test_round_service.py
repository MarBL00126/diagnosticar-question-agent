from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from round_fakes import (
    GENES,
    KB_VERSION,
    LABELS,
    PRESENT,
    SHORTLIST,
    FakeClock,
    FakeModel,
    FakeReranker,
    FakeSelector,
    ScriptedLLM,
    candidate,
    notes,
    question,
    request,
    round_draft,
    service,
    summary,
)

from diagnosticar_preguntas import __version__
from diagnosticar_preguntas.agent.round import (
    MIN_LLM_CALL_S,
    WARNING_NOTES,
    WARNING_QUESTIONS,
    WARNING_SUMMARY,
)
from diagnosticar_preguntas.domain import AnswerValue, GeneScore, Mode, ModelUnavailableError
from diagnosticar_preguntas.llm.claude_cli import ClaudeCliProvider
from diagnosticar_preguntas.llm.provider import (
    LLMInvalidOutputError,
    LLMQuotaError,
    LLMTimeoutError,
)
from diagnosticar_preguntas.llm.tasks import NotesTask, QuestionTask, SummaryTask
from diagnosticar_preguntas.llm.template import TemplateProvider
from diagnosticar_preguntas.ranking.strategy import RerankStrategy

FIRST_THREE = ("HP:0001738", "HP:0001696", "HP:0002099")
# After yes_no_answers() the first two shortlist terms are known, leaving these.
ROUND_TWO = ("HP:0002099", "HP:0000765")
NOTE = "creo que no, no estoy segura"


class RoundTwoSelector(FakeSelector):
    """Like the real selector, never offers a term that is already known."""

    def shortlist(self, posterior: Any, evidence: Any, k: int) -> list[Any]:
        self.calls.append(k)
        return [c for c in self.candidates if c.hpo_id not in evidence.asked_or_known][:k]


def yes_no_answers() -> list[dict[str, Any]]:
    return [
        {"hpo_id": "HP:0001738", "answer": "si"},
        {"hpo_id": "HP:0001696", "answer": "no"},
        {"hpo_id": "HP:0012236", "answer": "no_se"},
    ]


def answers_with_note() -> list[dict[str, Any]]:
    answers = yes_no_answers()
    answers[0]["note"] = NOTE
    return answers


def round_two(llm: Any = None, **kwargs: Any) -> Any:
    return service(llm, selector=kwargs.pop("selector", RoundTwoSelector()), **kwargs)


class TestFirstRound:
    def test_returns_three_questions_with_round_scoped_ids(self) -> None:
        llm = ScriptedLLM(questions=[round_draft(*FIRST_THREE)])

        response = service(llm).run(request())

        assert response.status == "questions"
        assert response.finish_reason is None
        assert [q.id for q in response.questions] == [f"r1-{h}" for h in FIRST_THREE]
        assert response.warnings == []
        assert response.summary is None

    @pytest.mark.parametrize("mode", list(Mode))
    def test_the_response_says_which_mode_its_texts_are_for(self, mode: Mode) -> None:
        assert service().run(request(mode=mode)).mode is mode

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
        assert response.versions.knowledge_base == KB_VERSION
        assert response.versions.llm_model == "claude-opus-5"

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


class TestStrategy:
    def test_by_default_the_model_only_ever_sees_the_reported_findings(self) -> None:
        model = FakeModel()

        round_two(model=model).run(request(round=2, answers=yes_no_answers()))

        assert model.calls == [(sorted(PRESENT), 50)]

    def test_every_answer_reaches_the_reranker_as_evidence_the_model_did_not_see(self) -> None:
        reranker = FakeReranker()

        round_two(reranker=reranker).run(request(round=2, answers=yes_no_answers()))

        evidence = reranker.evidence[0]
        assert evidence.present == {"HP:0002205", "HP:0004322", "HP:0001738"}
        assert evidence.absent == {"HP:0001250", "HP:0001696"}
        assert evidence.unknown == {"HP:0012236"}
        assert reranker.rankings[0].query_hpo_ids == tuple(sorted(PRESENT))

    def test_model_sees_present_sends_yes_answers_to_the_model(self) -> None:
        model = FakeModel()

        round_two(model=model, rerank_strategy=RerankStrategy.MODEL_SEES_PRESENT).run(
            request(round=2, answers=yes_no_answers())
        )

        assert model.calls == [(sorted([*PRESENT, "HP:0001738"]), 50)]

    def test_specialties_and_confidence_come_from_the_model_call(self) -> None:
        response = round_two().run(request(round=2, answers=yes_no_answers()))

        assert response.specialties == {"top_specialties": ["Pulmonology", "Gastroenterology"]}
        assert response.confidence == 0.41


class TestAnswers:
    def test_interpreted_answers_echo_every_answer_in_order(self) -> None:
        response = round_two().run(request(round=2, answers=yes_no_answers()))

        assert [(a.hpo_id, a.answer, a.effective_answer) for a in response.interpreted_answers] == [
            ("HP:0001738", AnswerValue.YES, AnswerValue.YES),
            ("HP:0001696", AnswerValue.NO, AnswerValue.NO),
            ("HP:0012236", AnswerValue.UNKNOWN, AnswerValue.UNKNOWN),
        ]

    def test_a_contradicting_note_downgrades_the_answer_to_no_se(self) -> None:
        llm = ScriptedLLM(
            notes=[notes(("HP:0001738", False, "Duda si lo tiene."))],
            questions=[round_draft(*ROUND_TWO)],
        )
        reranker = FakeReranker()

        response = round_two(llm, reranker=reranker).run(
            request(round=2, answers=answers_with_note())
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

        round_two(llm).run(request(round=2, answers=answers_with_note()))

        assert [i.hpo_id for i in llm.tasks(NotesTask)[0].items] == ["HP:0001738"]

    def test_readings_for_answers_without_notes_are_ignored(self) -> None:
        llm = ScriptedLLM(
            notes=[notes(("HP:0001738", True, None), ("HP:0001696", False, None))],
            questions=[round_draft(*ROUND_TWO)],
        )

        response = round_two(llm).run(request(round=2, answers=answers_with_note()))

        assert response.interpreted_answers[1].effective_answer is AnswerValue.NO

    def test_a_note_with_an_echoed_reading_is_not_read_again(self) -> None:
        answers = answers_with_note()
        answers[0]["reading"] = {"effective_answer": "no_se", "note_summary": "Duda."}
        llm = ScriptedLLM(questions=[round_draft(*ROUND_TWO)])
        reranker = FakeReranker()

        response = round_two(llm, reranker=reranker).run(request(round=3, answers=answers))

        assert llm.tasks(NotesTask) == []
        first = response.interpreted_answers[0]
        assert (first.effective_answer, first.note_summary) == (AnswerValue.UNKNOWN, "Duda.")
        assert "HP:0001738" in reranker.evidence[0].unknown

    def test_only_unread_notes_are_sent_when_some_carry_a_reading(self) -> None:
        answers = answers_with_note()
        answers[0]["reading"] = {"effective_answer": "si", "note_summary": None}
        answers[1]["note"] = "no, pero la mamá sí"
        llm = ScriptedLLM(
            notes=[notes(("HP:0001696", True, "La madre sí."))],
            questions=[round_draft(*ROUND_TWO)],
        )

        response = round_two(llm).run(request(round=3, answers=answers))

        assert [i.hpo_id for i in llm.tasks(NotesTask)[0].items] == ["HP:0001696"]
        assert [a.note_summary for a in response.interpreted_answers] == [
            None,
            "La madre sí.",
            None,
        ]

    def test_note_failure_keeps_the_buttons_and_warns(self) -> None:
        llm = ScriptedLLM(notes=[LLMTimeoutError("slow")], questions=[round_draft(*ROUND_TWO)])

        response = round_two(llm).run(request(round=2, answers=answers_with_note()))

        assert response.interpreted_answers[0].effective_answer is AnswerValue.YES
        assert WARNING_NOTES in response.warnings

    def test_no_llm_call_when_no_answer_has_a_note(self) -> None:
        llm = ScriptedLLM(questions=[round_draft(*ROUND_TWO)])

        round_two(llm).run(request(round=2, answers=yes_no_answers()))

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

        response = service(reranker=reranker).run(request())

        assert response.finish_reason == "confident"

    def test_a_gene_below_the_default_threshold_keeps_asking(self) -> None:
        reranker = FakeReranker(genes=(GeneScore("CFTR", 0.85, 0.4, 1),))

        response = service(reranker=reranker).run(request())

        assert response.status == "questions"

    def test_an_empty_shortlist_closes_with_no_useful_questions(self) -> None:
        response = service(selector=FakeSelector([])).run(request())

        assert response.finish_reason == "no_useful_questions"

    def test_low_information_gain_closes_with_no_useful_questions(self) -> None:
        selector = FakeSelector([candidate("HP:0001738", 0.04)])

        response = service(selector=selector).run(request())

        assert response.finish_reason == "no_useful_questions"

    def test_finishing_skips_the_selector(self) -> None:
        selector = FakeSelector()

        service(selector=selector).run(request(finish=True))

        assert selector.calls == []


class TestModes:
    def test_patient_mode_hides_the_genes_and_the_rationale(self) -> None:
        llm = ScriptedLLM(questions=[round_draft(*FIRST_THREE)])

        response = service(llm).run(request(mode=Mode.PATIENT))

        assert response.questions
        for q in response.questions:
            assert (q.genes_if_yes, q.genes_if_no, q.rationale) == (None, None, None)

    def test_clinician_mode_shows_at_most_five_genes_per_answer_and_the_rationale(self) -> None:
        wide = candidate(
            "HP:0001738",
            genes_yes=("G1", "G2", "G3", "G4", "G5", "G6"),
            genes_no=("G7", "G8"),
        )
        llm = ScriptedLLM(questions=[round_draft("HP:0001738")])

        response = service(llm, selector=FakeSelector([wide])).run(request())

        only = response.questions[0]
        assert only.genes_if_yes == ["G1", "G2", "G3", "G4", "G5"]
        assert only.genes_if_no == ["G7", "G8"]
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
        leaky = question("HP:0001696", text="¿Hay algo que apunte a CFTR?")
        bad = round_draft("HP:0001738")
        bad = bad.model_copy(update={"questions": [*bad.questions, leaky]})
        llm = ScriptedLLM(questions=[bad, bad])

        response = service(llm).run(request(mode=Mode.PATIENT))

        assert len(response.questions) == 3
        assert response.questions[0].text == bad.questions[0].text
        assert all("CFTR" not in q.text for q in response.questions)
        assert WARNING_QUESTIONS in response.warnings
        assert response.versions.llm_model == "claude-opus-5"

    def test_a_patient_question_naming_a_top_disease_is_rejected(self) -> None:
        naming = question("HP:0001696", text="¿Te dijeron que tiene fibrosis quística?")
        draft = round_draft("HP:0001738", "HP:0002099")
        draft = draft.model_copy(update={"questions": [*draft.questions, naming]})
        llm = ScriptedLLM(questions=[draft, draft])

        response = service(llm).run(request(mode=Mode.PATIENT))

        retry = llm.tasks(QuestionTask)[1]
        assert any("Cystic fibrosis" in c for c in retry.corrections)
        assert all("quística" not in q.text for q in response.questions)

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


class TestLlmModelVersion:
    def test_is_null_when_every_text_came_from_templates(self) -> None:
        llm = ScriptedLLM(questions=[LLMQuotaError("spent")])

        response = service(llm).run(request())

        assert response.versions.llm_model is None

    def test_is_null_when_the_summary_fell_back_and_nothing_else_used_the_llm(self) -> None:
        llm = ScriptedLLM(summaries=[LLMTimeoutError("slow")])

        response = service(llm).run(request(finish=True))

        assert response.versions.llm_model is None

    def test_is_set_when_only_the_note_reading_came_from_the_llm(self) -> None:
        llm = ScriptedLLM(
            notes=[notes(("HP:0001738", False, "Duda."))], questions=[LLMQuotaError("spent")]
        )

        response = round_two(llm).run(request(round=2, answers=answers_with_note()))

        assert response.versions.llm_model == "claude-opus-5"


class TestSummary:
    def test_the_summary_gets_answers_and_genes_but_no_specialties(self) -> None:
        llm = ScriptedLLM(summaries=[summary()])

        response = service(llm).run(request(round=2, finish=True, answers=yes_no_answers()))

        task = llm.tasks(SummaryTask)[0]
        assert task.finish_reason == "user_finished"
        assert task.answers[0].label == "Insuficiencia pancreática exocrina"
        assert task.top_genes
        assert not hasattr(task, "specialties")
        assert response.summary == summary().summary
        assert response.versions.llm_model == "claude-opus-5"

    def test_a_patient_summary_naming_a_gene_is_retried_then_templated(self) -> None:
        leaky = summary("Todo apunta a CFTR.")
        llm = ScriptedLLM(summaries=[leaky, leaky])

        response = service(llm).run(request(mode=Mode.PATIENT, finish=True))

        assert response.summary
        assert "CFTR" not in response.summary
        assert WARNING_SUMMARY in response.warnings
        assert llm.tasks(SummaryTask)[1].corrections

    def test_a_patient_summary_may_repeat_the_label_of_an_answered_term(self) -> None:
        answers = [{"hpo_id": "HP:0001738", "answer": "si"}]
        text = "Se informó insuficiencia pancreática exocrina. Conversalo con el médico."
        llm = ScriptedLLM(summaries=[summary(text)])

        response = service(llm).run(
            request(mode=Mode.PATIENT, round=2, finish=True, answers=answers)
        )

        assert response.summary == text

    def test_a_clinician_summary_may_name_genes(self) -> None:
        llm = ScriptedLLM(summaries=[summary("CFTR subió del puesto 3 al 1 con la esteatorrea.")])

        response = service(llm).run(request(finish=True))

        assert response.summary == "CFTR subió del puesto 3 al 1 con la esteatorrea."
        assert response.warnings == []

    def test_a_clinician_summary_recommending_studies_is_retried_then_templated(self) -> None:
        advice = summary("Conviene solicitar test del sudor antes de secuenciar CFTR.")
        llm = ScriptedLLM(summaries=[advice, advice])

        response = service(llm).run(request(finish=True))

        assert response.summary
        assert "sudor" not in response.summary
        assert WARNING_SUMMARY in response.warnings
        assert any("estudios o pasos a seguir" in c for c in llm.tasks(SummaryTask)[1].corrections)

    def test_a_summary_failure_degrades_to_the_template(self) -> None:
        llm = ScriptedLLM(summaries=[LLMTimeoutError("slow")])

        response = service(llm).run(request(finish=True))

        assert response.summary
        assert WARNING_SUMMARY in response.warnings


class TestTimeBudget:
    """Default budget 75 s, LLM timeout 60 s, no call with less than 10 s left."""

    def test_each_call_gets_the_llm_timeout_while_the_budget_allows_it(self) -> None:
        llm = ScriptedLLM(questions=[round_draft(*FIRST_THREE)])

        service(llm).run(request())

        assert llm.timeouts == [60.0]

    def test_a_call_never_gets_more_than_what_is_left(self) -> None:
        bad = round_draft("HP:0009999")
        llm = ScriptedLLM(
            questions=[bad, round_draft(*FIRST_THREE)], clock=FakeClock(), seconds_per_call=30
        )

        service(llm, round_budget_s=50).run(request())

        assert llm.timeouts == [50.0, 20.0]

    def test_the_guardrail_retry_is_skipped_when_too_little_is_left(self) -> None:
        bad = round_draft("HP:0009999")
        llm = ScriptedLLM(questions=[bad], clock=FakeClock(), seconds_per_call=66)

        response = service(llm).run(request())

        assert len(llm.tasks(QuestionTask)) == 1
        assert [q.hpo_id for q in response.questions] == list(FIRST_THREE)
        assert WARNING_QUESTIONS in response.warnings

    def test_notes_leave_room_for_the_question_call(self) -> None:
        llm = ScriptedLLM(
            notes=[notes(("HP:0001738", True, None))],
            questions=[round_draft(*ROUND_TWO)],
            clock=FakeClock(),
        )

        round_two(llm, round_budget_s=40).run(request(round=2, answers=answers_with_note()))

        assert llm.timeouts == [40 - MIN_LLM_CALL_S, 40.0]

    def test_notes_are_skipped_when_the_budget_is_short(self) -> None:
        llm = ScriptedLLM(questions=[round_draft(*ROUND_TWO)], clock=FakeClock())

        response = round_two(llm, round_budget_s=15).run(
            request(round=2, answers=answers_with_note())
        )

        assert llm.tasks(NotesTask) == []
        assert response.interpreted_answers[0].effective_answer is AnswerValue.YES
        assert WARNING_NOTES in response.warnings
        assert llm.timeouts == [15.0]

    def test_a_slow_note_reading_leaves_the_questions_to_templates(self) -> None:
        llm = ScriptedLLM(
            notes=[notes(("HP:0001738", True, None))], clock=FakeClock(), seconds_per_call=70
        )

        response = round_two(llm).run(request(round=2, answers=answers_with_note()))

        assert llm.tasks(QuestionTask) == []
        assert response.questions
        assert WARNING_QUESTIONS in response.warnings

    def test_the_summary_falls_back_when_the_budget_is_spent(self) -> None:
        llm = ScriptedLLM(
            notes=[notes(("HP:0001738", True, None))], clock=FakeClock(), seconds_per_call=70
        )

        response = service(llm).run(request(round=2, finish=True, answers=answers_with_note()))

        assert llm.tasks(SummaryTask) == []
        assert response.summary
        assert WARNING_SUMMARY in response.warnings


class TestLogPrivacy:
    def test_an_invalid_note_reading_logs_no_note_and_no_hpo_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("DEBUG")
        # The CLI answers with a reading that breaks the schema and quotes the note.
        bad = {"readings": [{"hpo_id": "HP:0001738", "consistent": "tal vez", "summary": NOTE}]}
        envelope = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(bad),
            "structured_output": bad,
        }

        def runner(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(envelope), "")

        llm = ClaudeCliProvider(
            model="claude-opus-5", effort="high", timeout_s=30, binary="claude", runner=runner
        )

        response = round_two(llm).run(request(round=2, answers=answers_with_note()))

        assert WARNING_NOTES in response.warnings
        assert "LLMInvalidOutputError" in caplog.text
        for value in (NOTE, "tal vez", "HP:0001738", *PRESENT):
            assert value not in caplog.text
        assert all(r.exc_info is None for r in caplog.records)


def test_model_failure_propagates_as_model_unavailable() -> None:
    with pytest.raises(ModelUnavailableError):
        service(model=FakeModel(fail=True)).run(request())
