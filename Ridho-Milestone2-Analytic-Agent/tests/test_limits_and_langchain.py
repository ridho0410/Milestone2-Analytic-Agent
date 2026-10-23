"""Panggilan tool ChatOpenAI melalui HTTP tiruan tanpa provider atau jaringan eksternal."""

import json
from dataclasses import replace

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from src.agent import AnalyticsAgent
from src.budget import BudgetExceeded, RequestBudget, budget_scope
from src.config import settings
from src.llm_client import LangChainClient, build_llm_client
from src.models import SQLQuery, TurnContext
from src.query_engine import QueryEngine
from src.tools import GoldTools


def proposal(**updates):
    return {"sql": "SELECT carrier_id, COUNT(shipment_id) AS volume FROM gold.shipment_fact WHERE carrier_id IN (:a,:b) GROUP BY carrier_id ORDER BY carrier_id",
            "params": {"a": "C01", "b": "C02"}, "start": "2026-08-01", "end": "2026-08-31", **updates}


def reply(name, arguments):
    return httpx.Response(200, json={"id": "chat_mock", "object": "chat.completion", "model": "gpt-4o-mini",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "call_test", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]})


def run_agent(gold, handler, *, limits=settings, history=()):
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        model = ChatOpenAI(model="gpt-4o-mini", api_key="test", http_client=http, max_retries=0)
        agent = AnalyticsAgent(gold, LangChainClient(model, limits), limits)
        agent.warmup()
        return agent.handle("Bandingkan volume C01 dan C02 pada Agustus 2026", history)


@pytest.mark.parametrize("operation,field,counter", [
    ("tool", "max_tool_calls", "tool_calls"), ("model", "max_model_calls", "model_calls"),
    ("query", "max_query_calls", "query_calls"), ("repair", "max_repair_attempts", "repair_attempts")])
def test_limits_reject_the_next_attempt(operation, field, counter):
    budget = RequestBudget(replace(settings, **{field: 1}))
    budget.consume(operation)
    with pytest.raises(BudgetExceeded):
        budget.consume(operation)
    assert getattr(budget, counter) == 1


def test_deadline_shrinks_operation_timeout():
    now = [100.0]
    budget = RequestBudget(replace(settings, agent_timeout_seconds=10), clock=lambda: now[0])
    assert budget.timeout(25) == 10
    now[0] += 7
    assert budget.timeout(25) == 3
    now[0] += 3
    with pytest.raises(BudgetExceeded):
        budget.consume("tool")


@pytest.mark.parametrize("end", ["2026-08-30", "2026-08-31", "2026-09-01"])
def test_week_comparison_anchors_use_previous_context_not_proposed_dates(end):
    client = LangChainClient(object())
    context = TurnContext(query=SQLQuery(sql="SELECT COUNT(*) AS volume FROM gold.shipment_fact",
        start="2026-08-01", end=end))
    payload = client._payload("Bagaimana dibanding minggu sebelumnya?", context, [])
    assert payload["comparison_weeks"] == {
        "previous": {"start": "2026-08-17", "end": "2026-08-23"},
        "latest": {"start": "2026-08-24", "end": "2026-08-30"},
    }
    assert client._payload("New question", TurnContext(), [])["comparison_weeks"] is None


def test_chatopenai_native_sql_and_structured_review(gold):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert 0 < request.extensions["timeout"]["read"] <= settings.llm_timeout_seconds
        payload = json.loads(body["messages"][-1]["content"])
        if "available_dimensions" in payload:
            assert body["parallel_tool_calls"] is False
            assert body["tool_choice"] == "required"
            assert {tool["function"]["name"] for tool in body["tools"]} == {"execute_gold_query", "request_clarification", "report_unavailable"}
            assert payload["available_dimensions"]["service_level"] == ["express", "standard"]
            return reply("execute_gold_query", {"query": proposal()})
        if "facts" in payload:
            assert "exception_note" not in json.dumps(payload)
            return reply("NarrativeDraft", {"paragraphs": [[key] for key in payload["facts"]]})
        assert "_scope_start" in payload["bound_params"]
        review_instructions = body["messages"][0]["content"]
        assert "CANONICAL SQL BY TABLE" in review_instructions
        assert "SUM(on_time_delivered_count)" in review_instructions
        assert "metric_date" in review_instructions and "carrier_daily" in review_instructions
        assert "sql" not in payload["query"]
        assert "_scope_start" in payload["guarded_sql"] and "_scope_end" in payload["guarded_sql"]
        assert payload["query"]["start"] == "2026-08-01"
        return reply("QueryReview", {"approved": True, "feedback": ""})
    result = run_agent(gold, handler)
    assert result.outcome == "answered"
    assert result.table == [{"carrier_id": "C01", "volume": 72}, {"carrier_id": "C02", "volume": 73}]
    assert len(requests) == result.audit["model_calls"] == 3
    assert result.audit["narration"] == "verified"
    assert result.audit["tool_calls"] == result.audit["query_calls"] == 1


def test_semantic_rejection_repairs_before_any_query(gold):
    proposals, reviews = [], []
    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        if "available_dimensions" in payload:
            proposals.append(payload)
            return reply("execute_gold_query", {"query": proposal()})
        reviews.append(payload)
        return reply("QueryReview", {"approved": len(reviews) == 2, "feedback": "missing requested filter" if len(reviews) == 1 else ""})
    result = run_agent(gold, handler)
    assert result.outcome == "answered"
    assert "missing requested filter" in proposals[1]["feedback"]
    assert result.audit["repair_attempts"] == 1 and result.audit["query_calls"] == 1


def test_repeated_semantic_rejection_publishes_no_numbers(gold):
    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        return reply("execute_gold_query", {"query": proposal()}) if "available_dimensions" in payload else reply("QueryReview", {"approved": False, "feedback": "wrong city"})
    result = run_agent(gold, handler)
    assert result.outcome == "no_data" and result.audit["query_calls"] == 0
    assert result.audit["repair_attempts"] == 1 and not result.table and not result.chart


def test_model_limit_stops_before_database(gold):
    result = run_agent(gold, lambda request: reply("execute_gold_query", {"query": proposal()}), limits=replace(settings, max_model_calls=1))
    assert result.audit["stop_reason"] == "model_calls" and result.audit["query_calls"] == 0


def test_provider_failure_has_no_hidden_retry_or_body_leak(gold):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(500, json={"error": {"message": "private database password", "type": "server_error"}})
    result = run_agent(gold, handler)
    assert len(requests) == 1 and result.audit["model_calls"] == 1
    assert result.audit["stop_reason"] == "model" and "private" not in result.answer


def test_direct_tools_cannot_bypass_query_budget(gold):
    budget = RequestBudget(replace(settings, max_query_calls=1))
    tools = GoldTools(QueryEngine(gold).execute, budget)
    call = AIMessage(content="", tool_calls=[{"name": "execute_gold_query", "args": {"query": proposal()}, "id": "test"}])
    with budget_scope(budget):
        assert tools.execute(call).rows
        with pytest.raises(BudgetExceeded):
            tools.execute(call)
    assert budget.query_calls == 1 and budget.tool_calls == 2


@pytest.mark.parametrize("invalid", [[], [{"name": "unknown", "args": {}, "id": "bad"}],
    [{"name": "execute_gold_query", "args": {"query": {}}, "id": "bad"}],
    [{"name": "report_unavailable", "args": {"outcome": "no_data", "extra": 1}, "id": "bad"}]])
def test_invalid_native_calls_fail_before_database(gold, invalid):
    budget = RequestBudget()
    tools = GoldTools(QueryEngine(gold).execute, budget)
    with pytest.raises(ValueError):
        tools.execute(AIMessage(content="", tool_calls=invalid))
    assert budget.tool_calls == 1 and budget.query_calls == 0


def test_tool_schema_feedback_identifies_invalid_fields_without_exposing_values(gold):
    tools = GoldTools(QueryEngine(gold).execute, RequestBudget())
    response = AIMessage(content="", tool_calls=[{
        "name": "execute_gold_query", "args": {"query": proposal(), "start": "private-value"},
        "id": "invalid-envelope",
    }])
    with pytest.raises(ValueError) as failure:
        tools.execute(response)
    feedback = str(failure.value)
    assert "start" in feedback and "extra_forbidden" in feedback
    assert "private-value" not in feedback
    assert tools.budget.query_calls == 0


def test_fresh_context_can_repair_misnested_tool_arguments_before_querying(gold):
    attempts = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        if "available_dimensions" in payload:
            attempts.append(payload)
            arguments = {"query": proposal()}
            if "start" not in payload["feedback"] or "extra_forbidden" not in payload["feedback"]:
                arguments["start"] = "2026-08-01"
            return reply("execute_gold_query", arguments)
        if "facts" in payload:
            return reply("NarrativeDraft", {"paragraphs": [[key] for key in payload["facts"]]})
        return reply("QueryReview", {"approved": True, "feedback": ""})

    result = run_agent(gold, handler)
    assert attempts[0]["context"]["query"] is None and attempts[0]["recent_messages"] == []
    assert result.outcome == "answered"
    assert result.audit["repair_attempts"] == 1 and result.audit["query_calls"] == 1
    assert result.table == [{"carrier_id": "C01", "volume": 72}, {"carrier_id": "C02", "volume": 73}]


def test_disabled_model_is_explicit_and_performs_no_query(gold):
    result = AnalyticsAgent(gold).handle("Volume per bulan")
    assert result.outcome == "no_data" and "LLM_ENABLED=false" in result.answer
    assert result.audit["query_calls"] == result.audit["model_calls"] == 0


def test_enabled_model_requires_key_and_disabled_does_not_build_client():
    with pytest.raises(ValueError):
        build_llm_client(replace(settings, llm_enabled=True, openai_api_key=""))
    assert build_llm_client(replace(settings, llm_enabled=False)) is None


def test_gpt56_responses_and_explicit_reasoning_configuration():
    limits = replace(settings, llm_enabled=True, openai_api_key="test", llm_model="gpt-5.6-luna", llm_reasoning_effort="high")
    client = build_llm_client(limits)
    try:
        assert client.chat_model.reasoning_effort == "high"
        assert client.chat_model.use_responses_api is True and client.chat_model.max_retries == 0
    finally:
        client.close()


def test_response_after_deadline_does_not_reach_tool_or_database(gold):
    now = [0.0]
    limits = replace(settings, agent_timeout_seconds=10)
    budget = RequestBudget(limits, clock=lambda: now[0])
    def handler(request):
        now[0] = 11.0
        return reply("execute_gold_query", {"query": proposal()})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        agent = AnalyticsAgent(gold, LangChainClient(ChatOpenAI(model="gpt-4o-mini", api_key="test", http_client=http, max_retries=0), limits), limits)
        agent.warmup()
        result = agent.handle("Volume C01 dan C02 Agustus", budget=budget)
    assert result.audit["stop_reason"] == "deadline" and result.audit["tool_calls"] == result.audit["query_calls"] == 0


def test_native_responses_api_executes_same_guarded_query(gold):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path.endswith("/responses")
        tool_names = {tool["name"] for tool in body["tools"]}
        if "NarrativeDraft" in tool_names:
            name, args = "NarrativeDraft", {"paragraphs": [["f0"], ["f1"]]}
        else:
            name, args = ("QueryReview", {"approved": True, "feedback": ""}) if "QueryReview" in tool_names else ("execute_gold_query", {"query": proposal()})
        return httpx.Response(200, json={"id": "resp_mock", "object": "response", "created_at": 0, "status": "completed",
            "model": "gpt-5.6-luna", "output": [{"type": "function_call", "id": "fc_mock", "call_id": "call_mock", "name": name,
            "arguments": json.dumps(args), "status": "completed"}], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        model = ChatOpenAI(model="gpt-5.6-luna", api_key="test", http_client=http, max_retries=0, use_responses_api=True)
        agent = AnalyticsAgent(gold, LangChainClient(model))
        agent.warmup()
        result = agent.handle("Bandingkan volume C01 dan C02 pada Agustus 2026")
    assert result.outcome == "answered" and len(requests) == 3 and result.audit["query_calls"] == 1
    assert result.audit["narration"] == "verified"
