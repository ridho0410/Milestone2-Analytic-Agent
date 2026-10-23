"""Proposal SQL dan pemeriksaan semantik ChatOpenAI yang dibatasi per permintaan."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .budget import BudgetExceeded, RequestBudget
from .config import Settings, settings
from .models import NarrativeDraft, QueryReview, SQLQuery, TurnContext
from .prompts import NARRATIVE_PROMPT, REVIEW_PROMPT, query_prompt


class LLMError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class LangChainClient:
    def __init__(self, chat_model: Any, limits: Settings = settings) -> None:
        self.chat_model = chat_model
        self.limits = limits

    def _invoke(
        self, runnable: Any, messages: Sequence[Any], budget: RequestBudget
    ) -> Any:
        budget.consume("model")
        try:
            result = runnable.invoke(
                messages, timeout=budget.timeout(self.limits.llm_timeout_seconds)
            )
        except BudgetExceeded:
            raise
        except Exception as exc:
            # Jangan sertakan isi respons provider, kredensial, atau prompt permintaan.
            diagnostics = {"type": type(exc).__name__}
            status = getattr(exc, "status_code", None)
            if isinstance(status, int):
                diagnostics["http_status"] = status
            for name in ("code", "param"):
                value = getattr(exc, name, None)
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z_.-]{1,64}", value):
                    diagnostics[name] = value
            raise LLMError(
                f"Pemanggilan model gagal ({type(exc).__name__}).", diagnostics
            ) from exc
        budget.remaining()
        return result

    def select_tool(
        self, question: str, context: TurnContext, dimensions: dict,
        tools: list[Any], budget: RequestBudget, feedback: str = "",
        history: Sequence[dict[str, Any]] = (),
    ) -> Any:
        payload = self._payload(question, context, history)
        payload.update(available_dimensions=dimensions, feedback=feedback)
        runnable = self.chat_model.bind_tools(
            tools, tool_choice="required", parallel_tool_calls=False, strict=False
        )
        return self._invoke(runnable, [SystemMessage(content=query_prompt()),
                            HumanMessage(content=json.dumps(payload, ensure_ascii=False))], budget)

    def _payload(self, question: str, context: TurnContext, history: Sequence[dict]) -> dict:
        comparison_weeks = None
        if context.query:
            # Acuan berasal dari jawaban sebelumnya, bukan rentang proposal yang sedang diperiksa.
            end = context.query.end - timedelta(days=(context.query.end.weekday() + 1) % 7)
            comparison_weeks = {
                "previous": {"start": (end - timedelta(days=13)).isoformat(),
                             "end": (end - timedelta(days=7)).isoformat()},
                "latest": {"start": (end - timedelta(days=6)).isoformat(), "end": end.isoformat()},
            }
        return {
            "question": question, "context": context.model_dump(mode="json"),
            "comparison_weeks": comparison_weeks,
            "current_date": datetime.now(timezone(timedelta(hours=7))).date().isoformat(),
            "dataset_period": {"start": self.limits.dataset_start, "end": self.limits.dataset_end},
            "recent_messages": [{"role": item["role"],
                "content": str(item.get("content", ""))[:self.limits.max_message_chars]}
                for item in history[-self.limits.history_limit:]
                if item.get("role") in {"user", "assistant"}],
        }

    def review(self, question: str, query: SQLQuery, sql: str, params: dict,
               context: TurnContext, history: Sequence[dict], budget: RequestBudget) -> QueryReview:
        payload = self._payload(question, context, history)
        # Hanya SQL final yang dinilai; SQL usulan belum memuat filter tanggal dari aplikasi.
        payload.update(query=query.model_dump(mode="json", exclude={"sql"}), guarded_sql=sql, bound_params=params)
        runnable = self.chat_model.with_structured_output(QueryReview, method="function_calling", strict=False)
        result = self._invoke(runnable, [SystemMessage(content=REVIEW_PROMPT),
                              HumanMessage(content=json.dumps(payload, ensure_ascii=False))], budget)
        return QueryReview.model_validate(result)

    def narrate(self, question: str, facts: dict[str, str], budget: RequestBudget) -> NarrativeDraft:
        runnable = self.chat_model.with_structured_output(
            NarrativeDraft, method="function_calling", strict=False
        )
        result = self._invoke(runnable, [SystemMessage(content=NARRATIVE_PROMPT),
            HumanMessage(content=json.dumps({"question": question, "facts": facts}, ensure_ascii=False))], budget)
        return NarrativeDraft.model_validate(result)

    def close(self) -> None:
        root = getattr(self.chat_model, "root_client", None)
        if root is not None:
            root.close()


def build_llm_client(limits: Settings = settings) -> LangChainClient | None:
    if not limits.llm_enabled:
        return None
    if not limits.openai_api_key.strip():
        raise ValueError("OPENAI_API_KEY wajib diisi ketika LLM_ENABLED=true.")
    effort = limits.llm_reasoning_effort or (
        "low" if limits.llm_model.startswith("gpt-5.6") else None
    )
    if limits.llm_api_mode not in {"auto", "chat_completions", "responses"}:
        raise ValueError("LLM_API_MODE harus auto, chat_completions, atau responses.")
    use_responses = limits.llm_api_mode == "responses" or (
        limits.llm_api_mode == "auto" and limits.llm_model.startswith("gpt-5.6")
    )
    model = ChatOpenAI(
        model=limits.llm_model,
        api_key=limits.openai_api_key,
        base_url=limits.openai_base_url,
        timeout=limits.llm_timeout_seconds,
        # Percobaan ulang tersembunyi dari SDK dapat melewati penghitung model per permintaan.
        max_retries=0,
        max_completion_tokens=limits.llm_max_output_tokens,
        reasoning_effort=effort,
        use_responses_api=use_responses,
    )
    return LangChainClient(model, limits)
