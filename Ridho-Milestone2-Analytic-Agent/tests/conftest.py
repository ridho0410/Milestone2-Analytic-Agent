"""Fixture lokal mengisolasi rahasia, panggilan provider, dan penyimpanan percakapan."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = Path(tempfile.mkdtemp(prefix="ridho-agent-tests-"))
os.environ.update(
    {
        "GOLD_BACKEND": "sqlite",
        "GOLD_DATA_DIR": str(ROOT / "data"),
        "SQLITE_PATH": str(_TMP / "conversations.sqlite3"),
        "OPENAI_API_KEY": "",
        "LLM_ENABLED": "false",
        "EXPOSE_SQL": "false",
        "DEMO_TOKEN_A": "demo-token-aaa",
        "DEMO_TOKEN_B": "demo-token-bbb",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }
)

import pytest
from fastapi.testclient import TestClient
from src.api import app, get_store
from src.gold import GoldClient

HEADERS_A = {"Authorization": "Bearer demo-token-aaa"}
HEADERS_B = {"Authorization": "Bearer demo-token-bbb"}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def gold():
    gold = GoldClient("sqlite")
    gold.connect()
    yield gold
    gold.close()


@pytest.fixture
def store():
    return get_store()


@pytest.fixture
def clean_store(store):
    for user in ("user-a", "user-b"):
        store.purge_user(user)
    yield store
    for user in ("user-a", "user-b"):
        store.purge_user(user)
