"""API terautentikasi; kepemilikan diperiksa sebelum analisis dijalankan."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .agent import AnalyticsAgent
from .budget import BudgetExceeded, RequestBudget
from .auth import Identity, require_identity
from .config import settings
from .conversations import ConversationStore, NotFoundError, utc_now
from .gold import GoldClient, GoldError
from .llm_client import build_llm_client

logger = logging.getLogger(__name__)
_store: ConversationStore | None = None
_client: GoldClient | None = None
_agent: AnalyticsAgent | None = None
_initialization_lock = threading.RLock()
# Kelompok kunci membatasi memori sekaligus mengurutkan baca, proses, dan tulis tiap percakapan.
_message_locks = tuple(threading.Lock() for _ in range(64))


def get_store() -> ConversationStore:
    global _store
    with _initialization_lock:
        if _store is None:
            _store = ConversationStore(settings.sqlite_path)
        return _store


def get_client() -> GoldClient:
    global _client
    with _initialization_lock:
        if _client is None:
            _client = GoldClient()
        return _client


def get_agent() -> AnalyticsAgent:
    global _agent
    with _initialization_lock:
        if _agent is None:
            _agent = AnalyticsAgent(get_client(), build_llm_client())
        return _agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent, _client
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    get_store()
    try:
        get_client().connect()
        get_agent().warmup()
    except GoldError:
        logger.warning("Gold is not yet available")
    yield
    if _agent:
        _agent.close()
    elif _client:
        _client.close()
    _agent = _client = None


app = FastAPI(
    title="Ridho Milestone 2 Analytics Agent", version=__version__, lifespan=lifespan
)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=settings.max_message_chars)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=settings.max_title_chars)


@app.exception_handler(NotFoundError)
async def not_found(request, exc):
    return JSONResponse(
        status_code=404, content={"detail": "Conversation not found."}
    )


@app.exception_handler(Exception)
async def unhandled(request, exc):
    logger.error("Unhandled request failure (%s)", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/v1/health/detail")
def health_detail(identity: Identity = Depends(require_identity)) -> dict:
    return {
        "version": __version__,
        "gold_reachable": get_client().ping(),
        "llm_enabled": get_agent().llm_enabled,
        "limits": {
            name: getattr(settings, name)
            for name in (
                "max_tool_calls",
                "max_model_calls",
                "max_query_calls",
                "max_repair_attempts",
                "agent_timeout_seconds",
            )
        },
    }


@app.get("/api/v1/conversations")
def list_conversations(identity: Identity = Depends(require_identity)) -> dict:
    return {"conversations": get_store().list_conversations(identity.user_id)}


@app.post("/api/v1/conversations", status_code=201)
def create_conversation(identity: Identity = Depends(require_identity)) -> dict:
    return get_store().create_conversation(identity.user_id)


@app.get("/api/v1/conversations/{conversation_id}")
def read_conversation(
    conversation_id: str, identity: Identity = Depends(require_identity)
) -> dict:
    return get_store().get_conversation(conversation_id, identity.user_id)


@app.post("/api/v1/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: str,
    payload: MessageRequest,
    identity: Identity = Depends(require_identity),
) -> dict:
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "Message content cannot be empty.")
    store = get_store()
    budget = RequestBudget()
    store.assert_owned(conversation_id, identity.user_id)
    lock = _message_locks[hash(conversation_id) % len(_message_locks)]
    try:
        wait_seconds = budget.remaining()
    except BudgetExceeded:
        raise HTTPException(
            429, "The request timed out. Please try again."
        ) from None
    if not lock.acquire(timeout=wait_seconds):
        raise HTTPException(429, "This conversation is being processed. Please try again.")
    try:
        store.assert_owned(conversation_id, identity.user_id)
        history = store.history(
            conversation_id, identity.user_id, settings.history_limit
        )
        store.add_message(conversation_id, identity.user_id, "user", content)
        result = get_agent().handle(content, history, budget=budget)
        store.add_message(
            conversation_id,
            identity.user_id,
            "assistant",
            result.answer,
            outcome=result.outcome,
            data_basis=result.data_basis,
            context_json=(
                result.context.to_json() if result.outcome == "answered" else ""
            ),
            chart=result.chart,
        )
        store.rename_if_default(conversation_id, identity.user_id, content)
        logger.info("Analysis outcome=%s budget=%s", result.outcome, result.audit)
        response = {"conversation_id": conversation_id, **result.to_dict()}
        if settings.expose_sql:
            response["audit"] = result.audit
        return response
    finally:
        lock.release()


@app.delete("/api/v1/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str, identity: Identity = Depends(require_identity)
) -> Response:
    if not get_store().delete_conversation(conversation_id, identity.user_id):
        raise NotFoundError(conversation_id)
    return Response(status_code=204)


@app.patch("/api/v1/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: str,
    payload: RenameRequest,
    identity: Identity = Depends(require_identity),
) -> dict:
    title = " ".join(payload.title.split()).strip()
    if not title:
        raise HTTPException(422, "The conversation title cannot be empty.")
    if not get_store().rename_conversation(conversation_id, identity.user_id, title):
        raise NotFoundError(conversation_id)
    return {"id": conversation_id, "title": title, "updated_at": utc_now()}
