"""Pertanyaan -> tool SQL -> validasi AST dan makna -> Gold hanya baca -> hasil."""

from __future__ import annotations

import logging
from typing import Any, Sequence

from .answers import answer_facts, data_basis, render_answer
from .budget import BudgetExceeded, RequestBudget, budget_scope
from .chart_planner import build_chart
from .config import Settings, settings
from .gold import GoldClient, GoldError
from .llm_client import LangChainClient, LLMError
from .metrics import RATIOS
from .models import AgentResult, SQLQuery, TurnContext
from .query_engine import QueryEngine
from .scope import check_scope
from .sql_guard import SQLGuardError
from .tools import GoldTools

logger = logging.getLogger(__name__)


class AnalyticsAgent:
    def __init__(self, client: GoldClient, llm: LangChainClient | None = None,
                 limits: Settings = settings) -> None:
        self.client, self.llm, self.limits = client, llm, limits
        self.engine = QueryEngine(client, limits)
        self.dimensions: dict[str, list[str]] = {}

    @property
    def llm_enabled(self) -> bool:
        return self.llm is not None

    def warmup(self) -> None:
        columns = ("destination_city", "origin_hub", "carrier_id", "service_level", "delivery_status")
        result = self.client.execute("SELECT " + ", ".join(columns) +
                                     " FROM gold.shipment_fact GROUP BY " + ", ".join(columns))
        if result.truncated:
            raise GoldError("Katalog dimensi terlalu besar.")
        self.dimensions = {column: sorted({str(row[column]) for row in result.rows if row[column]})
                           for column in columns}

    def close(self) -> None:
        if self.llm:
            self.llm.close()
        self.client.close()

    @staticmethod
    def _context(history: Sequence[dict[str, Any]]) -> TurnContext:
        for message in reversed(history):
            if message.get("role") == "assistant" and message.get("context_json"):
                context = TurnContext.from_json(message["context_json"])
                if context.query or context.legacy:
                    return context
        return TurnContext()

    def handle(self, question: str, history: Sequence[dict[str, Any]] = (),
               budget: RequestBudget | None = None) -> AgentResult:
        budget = budget or RequestBudget(self.limits)
        if not check_scope(question):
            return AgentResult(answer="This request is outside the scope of shipment analytics. I can only read Gold data and cannot modify data or disclose internal configuration.",
                               outcome="refused", audit=budget.audit())
        if not self.llm:
            return AgentResult(answer="The AI service is disabled (LLM_ENABLED=false). Enable the model to interpret questions and construct queries; no queries or figures are generated automatically.",
                               outcome="no_data", audit={**budget.audit(), "stop_reason": "llm_disabled"})
        context = self._context(history)
        stage = "catalog"
        with budget_scope(budget):
            try:
                if not self.dimensions:
                    self.warmup()

                def execute_query(query: SQLQuery):
                    nonlocal stage
                    stage = "validation"
                    sql, params, _, _ = self.engine.prepare(query)
                    if query.date_basis == "order_date" and (query.end.isoformat() < self.limits.dataset_start
                            or query.start.isoformat() > self.limits.dataset_end):
                        return AgentResult(answer=f"The requested period is outside the available order data: {self.limits.dataset_start} to {self.limits.dataset_end}.",
                                           outcome="no_data")
                    review = self.llm.review(question, query, sql, params, context, history, budget)
                    if not review.approved:
                        raise ValueError("Pemeriksaan semantik: " + review.feedback)
                    stage = "gold"
                    return self.engine.execute(query)

                tools = GoldTools(execute_query, budget)
                feedback = ""
                for attempt in range(2):
                    try:
                        stage = "sql_proposal"
                        proposal = self.llm.select_tool(question, context, self.dimensions,
                                                       tools.tools, budget, feedback=feedback, history=history)
                        evidence = tools.execute(proposal)
                        break
                    except (ValueError, SQLGuardError) as exc:
                        if attempt:
                            raise
                        budget.consume("repair")
                        feedback = str(exc)[:1500]
                if isinstance(evidence, AgentResult):
                    evidence.audit = budget.audit()
                    return evidence
                if not evidence.rows:
                    return AgentResult(answer="No data matches the requested period and filters.",
                                       outcome="no_data", data_basis=data_basis(evidence), audit=budget.audit())
                budget.remaining()
                answer = render_answer(evidence)
                facts = answer_facts(evidence)
                narration = "skipped"
                # Batasi penyusunan narasi; hasil besar dan catatan operasional memakai renderer faktual yang sama.
                if len(facts) <= 20 and sum(map(len, facts.values())) <= 12000 and not any("exception_note" in row for row in evidence.rows):
                    try:
                        draft = self.llm.narrate(question, facts, budget)
                        answer = render_answer(evidence, draft)
                        narration = "verified"
                    except (LLMError, ValueError, BudgetExceeded):
                        logger.info("Narrative unavailable or invalid; keeping verified factual answer")
                        narration = "fallback"
                hidden = {"exception_note"} | {component for rate, pair in RATIOS.items()
                          if rate in evidence.trusted_metrics for component in pair}
                chart_rows = [{k: v for k, v in row.items() if k not in hidden} for row in evidence.rows]
                chart = build_chart(question, list(chart_rows[0]), chart_rows)
                return AgentResult(answer=answer, outcome="answered",
                                   data_basis=data_basis(evidence), context=TurnContext(query=evidence.query),
                                   table=[{k: v for k, v in row.items() if k != "exception_note"} for row in evidence.rows], chart=chart,
                                   audit={**budget.audit(), "grounded": True, "native_tools": True, "narration": narration})
            except BudgetExceeded as exc:
                return AgentResult(answer="The analysis reached its call or processing time limit. The results have not been verified. Try narrowing the period or filters.",
                                   outcome="clarify", audit={**budget.audit(), "stop_reason": exc.reason, "stage": stage})
            except LLMError as exc:
                logger.info("Model request failed stage=%s diagnostics=%s", stage, exc.diagnostics)
                return AgentResult(answer="The AI service could not process this request. Please try again.", outcome="no_data",
                                   audit={**budget.audit(), "stop_reason": "model", "stage": stage, "model_failure": exc.diagnostics})
            except (ValueError, SQLGuardError):
                logger.info("SQL or semantic validation failed stage=%s", stage)
                return AgentResult(answer="The query did not pass the safety or question alignment checks after one repair attempt. No results are displayed.",
                                   outcome="no_data", audit={**budget.audit(), "stop_reason": "validation", "stage": stage})
            except GoldError:
                return AgentResult(answer="Gold data could not be retrieved, or the result is too large. Try again or narrow the period and filters.",
                                   outcome="no_data", audit={**budget.audit(), "stop_reason": "gold", "stage": stage})
