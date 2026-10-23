"""Tool bawaan LangChain; setiap proposal memuat tepat satu panggilan tervalidasi."""

from typing import Any, Callable, Literal

from langchain_core.tools import tool
from pydantic import ValidationError

from .budget import RequestBudget
from .models import AgentResult, Evidence, SQLQuery, StrictModel


class QueryInput(StrictModel):
    query: SQLQuery


class ClarificationInput(StrictModel):
    reason: Literal["metric", "period", "filter", "comparison"]


class UnavailableInput(StrictModel):
    outcome: Literal["no_data", "refused"]


class GoldTools:
    def __init__(self, execute_query: Callable[[SQLQuery], Evidence | AgentResult], budget: RequestBudget) -> None:
        self.budget = budget

        @tool(args_schema=QueryInput, response_format="content_and_artifact")
        def execute_gold_query(query: SQLQuery) -> tuple[str, Evidence | AgentResult]:
            """Run one parameterized PostgreSQL SELECT from a qualified gold table.

            Put sql, params, start, end, and date_basis inside the single query argument.
            Independent SQL and intent checks run before database access.
            """
            evidence = execute_query(query)
            # Catatan tidak dikirim kembali ke model; UI mengutipnya sebagai data tidak tepercaya.
            return "Query completed; verified rows are rendered by the application.", evidence

        @tool(args_schema=ClarificationInput, response_format="content_and_artifact")
        def request_clarification(reason: str) -> tuple[str, AgentResult]:
            """Ask only for the missing metric, period, filter, or comparison reference."""
            messages = {
                "metric": "Which shipment metric would you like to analyze?",
                "period": "Which date range do you mean?",
                "filter": "Which entity or filter do you mean?",
                "comparison": "Which reference period would you like to compare against?",
            }
            result = AgentResult(answer=messages[reason], outcome="clarify")
            return result.answer, result

        @tool(args_schema=UnavailableInput, response_format="content_and_artifact")
        def report_unavailable(outcome: str) -> tuple[str, AgentResult]:
            """Report a request outside shipment analytics or unavailable in the Gold schema."""
            result = AgentResult(outcome=outcome, answer=(
                "This request is outside the scope of shipment analytics."
                if outcome == "refused" else "The requested information is not available in the Gold data."
            ))
            return result.answer, result

        self.tools = [execute_gold_query, request_clarification, report_unavailable]
        self.by_name = {item.name: item for item in self.tools}

    def execute(self, response: Any) -> Evidence | AgentResult:
        self.budget.consume("tool")
        calls = getattr(response, "tool_calls", [])
        if len(calls) != 1 or getattr(response, "invalid_tool_calls", []):
            raise ValueError("Panggil tepat satu tool dengan argumen valid.")
        call = calls[0]
        selected = self.by_name.get(call.get("name"))
        if selected is None or not isinstance(call.get("id"), str) or not call["id"]:
            raise ValueError("Nama atau ID tool tidak valid.")
        try:
            selected.args_schema.model_validate(call.get("args"))
        except ValidationError as exc:
            # Lokasi dan jenis kesalahan cukup untuk perbaikan; jangan sertakan nilai input.
            details = "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['type']}"
                for error in exc.errors(include_input=False, include_context=False, include_url=False)[:8]
            )
            raise ValueError(f"Invalid arguments for {selected.name}: {details}.") from exc
        message = selected.invoke({**call, "type": "tool_call"})
        self.budget.remaining()
        return message.artifact
