"""Templates are the last resort, so they must pass the guardrails for any term.

Checked over every askable term of the real knowledge base, against every gene
symbol and the name of every disease linked to a gene (a superset of any
round's top hypotheses).
"""

from __future__ import annotations

import pytest

from diagnosticar_preguntas.agent.guardrails import QuestionRules, check_round, check_summary
from diagnosticar_preguntas.domain import AnswerValue, CandidateTerm, GeneScore, Mode
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.llm.schemas import RoundDraft, SummaryDraft
from diagnosticar_preguntas.llm.tasks import AnsweredTerm, SummaryTask
from diagnosticar_preguntas.llm.template import phrase_question, summarize
from diagnosticar_preguntas.selection.askability import AskabilityRules, askable_terms
from diagnosticar_preguntas.wiring import patient_vocabulary
from fixtures.real_kb import real_kb_path

pytestmark = pytest.mark.skipif(not real_kb_path().is_file(), reason="data/kb.sqlite not built")


def test_template_questions_pass_the_patient_guardrails_for_every_askable_term() -> None:
    kb = KnowledgeBase.open(real_kb_path())
    diseases = {d for gene in kb.genes() for d in kb.diseases_for_gene(gene)}
    patient_text = patient_vocabulary(kb).for_diseases(diseases)
    failures = []
    terms = sorted(askable_terms(kb, AskabilityRules()))
    for hpo_id in terms:
        term = kb.term(hpo_id)
        assert term is not None
        candidate = CandidateTerm(
            hpo_id=hpo_id,
            label_es=term.label_es or term.label_en,
            label_en=term.label_en,
            information_gain=1.0,
            p_yes=0.5,
            requires_testing=term.requires_testing,
        )
        rules = QuestionRules(
            mode=Mode.PATIENT,
            shortlist=frozenset({hpo_id}),
            already_known=frozenset(),
            expected=1,
            patient_text=patient_text,
            labels={hpo_id: (candidate.label_es, candidate.label_en)},
        )
        draft = RoundDraft(questions=[phrase_question(candidate, Mode.PATIENT)])
        if violations := check_round(draft, rules).violations:
            failures.append((hpo_id, draft.questions[0].text, violations))

    assert len(terms) > 1000
    assert failures == []


def test_the_clinician_template_summary_passes_the_guardrails_for_every_askable_term() -> None:
    """Only the clinician summary quotes the answered labels; the patient one never does."""
    mode = Mode.CLINICIAN
    kb = KnowledgeBase.open(real_kb_path())
    patient_text = patient_vocabulary(kb).for_diseases(())
    top_genes = (GeneScore("CFTR", 0.7, 0.3, 2), GeneScore("DNAH5", 0.3, 0.4, 1))
    failures = []
    for hpo_id in sorted(askable_terms(kb, AskabilityRules())):
        term = kb.term(hpo_id)
        assert term is not None
        label = term.label_es or term.label_en or hpo_id
        answers = (AnsweredTerm(hpo_id, label, AnswerValue.YES),)
        summary = summarize(SummaryTask(mode, "user_finished", answers, top_genes))
        if problems := check_summary(SummaryDraft(summary=summary), mode, patient_text, [label]):
            failures.append((hpo_id, summary, problems))

    assert failures == []
