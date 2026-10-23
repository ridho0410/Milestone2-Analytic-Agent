"""Uji jawaban faktual terverifikasi, referensi narasi, dan penanganan kegagalan yang aman."""

import json
from dataclasses import replace
import pytest
from src.agent import AnalyticsAgent
from src.answers import render_answer
from src.config import settings
from src.llm_client import LangChainClient
from src.models import SQLQuery
from src.query_engine import QueryEngine
from tests.test_analysis import query
from tests.test_limits_and_langchain import proposal, reply, run_agent


def narration_evidence(gold):
    return QueryEngine(gold).execute(SQLQuery(
        sql="SELECT COUNT(*) AS volume FROM gold.shipment_fact WHERE destination_city=:city",
        params={"city": "Bandung"}, start="2026-07-01", end="2026-09-20"))


def test_friendly_answer_keeps_verified_number_without_sql_jargon(gold):
    answer = render_answer(narration_evidence(gold))
    assert "164 shipments" in answer and "Bandung" in answer
    assert "1 July" in answer and "20 September 2026" in answer
    assert not any(word in answer for word in ("Gold", "shipment_fact", "order_date", "destination_city", "parameter filter", "Hasil analisis"))


def test_narrative_layout_cannot_change_entities_metrics_or_values(gold):
    from src.answers import answer_facts
    from src.models import NarrativeDraft
    evidence = narration_evidence(gold)
    facts = answer_facts(evidence)
    draft = NarrativeDraft(paragraphs=[[key] for key in facts])
    answer = render_answer(evidence, draft)
    assert "164 shipments" in answer and "Bandung" in answer
    assert all(fact in answer for fact in facts.values())


@pytest.mark.parametrize("paragraphs", [[], [[]], [["unknown"]], [["f0", "f0"]], [["999 kiriman"]]])
def test_invalid_narrative_references_are_rejected(gold, paragraphs):
    from src.models import NarrativeDraft
    with pytest.raises(ValueError):
        render_answer(narration_evidence(gold), NarrativeDraft(paragraphs=paragraphs))


def test_editorial_narrative_cannot_inject_free_prose_or_claims():
    from src.models import NarrativeDraft
    with pytest.raises(ValueError):
        NarrativeDraft(paragraphs=[["f0"]], conclusion="Penurunan disebabkan cuaca")


def test_optional_narration_failure_keeps_verified_query_answer(gold):
    requests = []
    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        requests.append(payload)
        if "facts" in payload:
            import httpx
            return httpx.Response(500, json={"error": {"message": "provider unavailable", "type": "server_error"}})
        if "available_dimensions" in payload:
            return reply("execute_gold_query", {"query": proposal()})
        return reply("QueryReview", {"approved": True, "feedback": ""})
    result = run_agent(gold, handler)
    assert result.outcome == "answered" and result.table
    assert result.audit["narration"] == "fallback" and result.audit["query_calls"] == 1
    assert result.audit["model_calls"] == 3 and result.audit["repair_attempts"] == 0
    assert "C01" in result.answer and "C02" in result.answer


def test_model_budget_for_narration_does_not_hide_completed_analysis(gold):
    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        return reply("execute_gold_query", {"query": proposal()}) if "available_dimensions" in payload else reply("QueryReview", {"approved": True, "feedback": ""})
    result = run_agent(gold, handler, limits=replace(settings, max_model_calls=2))
    assert result.outcome == "answered" and result.audit["query_calls"] == 1
    assert result.audit["model_calls"] == 2 and result.audit["narration"] == "fallback"


@pytest.mark.parametrize("predicate", ["destination_city=:city", "(destination_city=:city)", ":city=destination_city"])
def test_narration_preserves_equivalent_entity_filter_forms(gold, predicate):
    evidence = QueryEngine(gold).execute(SQLQuery(sql="SELECT COUNT(*) AS volume FROM gold.shipment_fact WHERE " + predicate,
        params={"city": "Bandung"}, start="2026-07-01", end="2026-09-20"))
    assert "164 shipments" in render_answer(evidence) and "Bandung" in render_answer(evidence)


def test_complex_scope_is_disclosed_without_inventing_a_conjunction(gold):
    evidence = QueryEngine(gold).execute(SQLQuery(sql="SELECT COUNT(*) AS volume FROM gold.shipment_fact WHERE destination_city=:city OR carrier_id=:carrier",
        params={"city": "Bandung", "carrier": "C01"}, start="2026-07-01", end="2026-09-20"))
    answer = render_answer(evidence)
    assert "filters in your question" in answer
    assert "to **Bandung** via carrier **C01**" not in answer


def test_temporal_narration_cannot_reorder_periods(gold):
    from src.answers import answer_facts
    from src.models import NarrativeDraft
    evidence = QueryEngine(gold).execute(query("SELECT CAST(DATE_TRUNC('month',order_date) AS DATE) AS bucket,COUNT(*) AS volume FROM gold.shipment_fact GROUP BY 1 ORDER BY 1"))
    with pytest.raises(ValueError, match="Urutan periode"):
        render_answer(evidence, NarrativeDraft(paragraphs=[list(reversed(answer_facts(evidence)))]))


def test_operational_notes_bypass_narration_model(gold):
    from scripts.evaluate import RecordedModel, recorded_query
    model = RecordedModel({"catatan": recorded_query("SELECT shipment_id,exception_note FROM gold.shipment_fact WHERE exception_note IS NOT NULL AND exception_note <> '' ORDER BY shipment_id")})
    agent = AnalyticsAgent(gold, LangChainClient(model));agent.warmup()
    result = agent.handle("catatan")
    assert result.outcome == "answered" and "untrusted data" in result.answer
    assert result.audit["narration"] == "skipped" and result.audit["model_calls"] == 2
    assert all(mode != "narration" for mode, payload in model.requests)


def test_large_result_uses_friendly_renderer_without_editorial_model(gold):
    from scripts.evaluate import RecordedModel, recorded_query
    model = RecordedModel({"harian": recorded_query("SELECT order_date,COUNT(*) AS volume FROM gold.shipment_fact GROUP BY order_date ORDER BY order_date", "2026-07-01", "2026-09-20")})
    agent = AnalyticsAgent(gold, LangChainClient(model));agent.warmup()
    result = agent.handle("harian")
    assert result.outcome == "answered" and len(result.table) > 20
    assert result.audit["narration"] == "skipped" and result.audit["model_calls"] == 2
    assert "order_date" not in result.answer


def test_non_date_text_alias_cannot_crash_friendly_renderer(gold):
    evidence = narration_evidence(gold)
    evidence.rows = [{"day": "in_transit", "volume": 1}]
    assert r"in\_transit" in render_answer(evidence)


@pytest.mark.parametrize("count, expected", [(0, "0 shipments"), (1, "1 shipment"), (2, "2 shipments")])
def test_english_count_units_preserve_the_value(gold, count, expected):
    evidence = narration_evidence(gold)
    evidence.rows = [{"volume": count}]
    answer = render_answer(evidence)
    assert f"**{expected}**" in answer and "Bandung" in answer
    assert "the recorded total was" in answer
