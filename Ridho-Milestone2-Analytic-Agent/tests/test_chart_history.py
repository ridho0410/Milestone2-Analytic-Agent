"""Cuplikan grafik bertahan saat dimuat ulang tanpa mengubah analitik atau kepemilikan."""

import ast
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from src import api
from src.conversations import MAX_CHART_BYTES, SCHEMA, ConversationStore, NotFoundError
from src.models import AgentResult
from tests.conftest import HEADERS_A, HEADERS_B


def chart_snapshot(title="Grafik Bandung", value=11):
    return {
        "kind": "line",
        "title": title,
        "x": "bucket",
        "y": ["volume"],
        "series": None,
        "data": [{"bucket": "2026-07-01", "volume": value}],
    }


def test_existing_database_migrates_without_losing_messages(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy_schema = SCHEMA.replace("    chart_json      TEXT,\n", "")
    with sqlite3.connect(path) as conn:
        conn.executescript(legacy_schema)
        conn.execute(
            "INSERT INTO conversations VALUES ('old-chat', 'user-a', 'Lama', 't', 't')"
        )
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES ('old-chat', 'assistant', 'Jawaban lama', 't')"
        )
    store = ConversationStore(path)
    before = store.get_conversation("old-chat", "user-a")["messages"]
    assert before == [{"role": "assistant", "content": "Jawaban lama", "created_at": "t"}]
    store.add_message(
        "old-chat", "user-a", "assistant", "Jawaban baru", chart=chart_snapshot()
    )
    reopened = ConversationStore(path)
    after = reopened.get_conversation("old-chat", "user-a")["messages"]
    assert after[0] == before[0]
    assert after[1]["chart"] == chart_snapshot()


def test_chart_lifecycle_keeps_ownership_and_model_history_separate(tmp_path):
    path = tmp_path / "charts.sqlite3"
    store = ConversationStore(path)
    conv_a = store.create_conversation("user-a")["id"]
    conv_b = store.create_conversation("user-b")["id"]
    chart_a = chart_snapshot("Grafik A", 11)
    chart_b = chart_snapshot("Grafik B", 15)
    store.add_message(conv_a, "user-a", "assistant", "Jawaban A", chart=chart_a)
    store.add_message(conv_a, "user-a", "assistant", "Tanpa grafik")
    store.add_message(conv_b, "user-b", "assistant", "Jawaban B", chart=chart_b)

    reopened = ConversationStore(path)
    messages = reopened.get_conversation(conv_a, "user-a")["messages"]
    assert messages[0]["chart"] == chart_a and "chart" not in messages[1]
    assert all(
        "chart" not in message and "chart_json" not in message
        for message in reopened.history(conv_a, "user-a", 10)
    )
    with pytest.raises(NotFoundError):
        reopened.get_conversation(conv_a, "user-b")
    with pytest.raises(NotFoundError):
        reopened.add_message(conv_a, "user-b", "assistant", "Salah", chart=chart_b)

    assert reopened.delete_conversation(conv_a, "user-a")
    assert reopened.count_messages(conv_a) == 0
    other = reopened.get_conversation(conv_b, "user-b")["messages"]
    assert other[0]["chart"] == chart_b


@pytest.mark.parametrize("invalid", ["oversize", "nonfinite"])
def test_chart_storage_limit_does_not_discard_the_answer(tmp_path, invalid):
    store = ConversationStore(tmp_path / "limited.sqlite3")
    conversation = store.create_conversation("user-a")["id"]
    chart = chart_snapshot()
    if invalid == "oversize":
        chart["title"] = "漢" * (MAX_CHART_BYTES // 3 + 1)
        assert len(json.dumps(chart, ensure_ascii=False)) < MAX_CHART_BYTES
    else:
        chart["data"][0]["volume"] = float("nan")
    store.add_message(
        conversation, "user-a", "assistant", "Jawaban tetap tersimpan", chart=chart
    )
    message = store.get_conversation(conversation, "user-a")["messages"][0]
    assert message["content"] == "Jawaban tetap tersimpan" and "chart" not in message


@pytest.mark.parametrize("payload", ["invalid-json", "[]", "null"])
def test_unreadable_chart_does_not_break_message_history(tmp_path, payload):
    path = tmp_path / "invalid.sqlite3"
    store = ConversationStore(path)
    conversation = store.create_conversation("user-a")["id"]
    store.add_message(conversation, "user-a", "assistant", "Masih terbaca")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE messages SET chart_json = ?", (payload,))
    message = store.get_conversation(conversation, "user-a")["messages"][0]
    assert message["content"] == "Masih terbaca" and "chart" not in message


def test_api_and_ui_restore_all_charts_across_conversations_and_sessions(
    client, clean_store, monkeypatch
):
    calls = []

    class FakeAgent:
        def handle(self, content, history, *, budget):
            calls.append(content)
            chart = (
                None
                if content == "tanpa grafik"
                else chart_snapshot(content, len(calls))
            )
            return AgentResult(
                answer=f"Jawaban {content}",
                outcome="answered",
                data_basis="CSV uji",
                chart=chart,
            )

    monkeypatch.setattr(api, "get_agent", lambda: FakeAgent())

    def create_conversation():
        response = client.post("/api/v1/conversations", headers=HEADERS_A, json={})
        assert response.status_code == 201
        return response.json()["id"]

    conv_a, conv_b = create_conversation(), create_conversation()

    def send(conversation, question):
        response = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            headers=HEADERS_A,
            json={"content": question},
        )
        assert response.status_code == 200
        return response.json()

    first = send(conv_a, "grafik pertama")
    second = send(conv_a, "grafik kedua")
    send(conv_a, "tanpa grafik")
    other = send(conv_b, "grafik percakapan lain")
    calls_before_reading = calls.copy()
    monkeypatch.setattr(api, "_store", ConversationStore(clean_store.path))
    restored = client.get(f"/api/v1/conversations/{conv_a}", headers=HEADERS_A).json()
    assistants = [
        message for message in restored["messages"] if message["role"] == "assistant"
    ]
    assert assistants[0]["chart"] == first["chart"]
    assert assistants[1]["chart"] == second["chart"]
    assert "chart" not in assistants[2]
    assert assistants[0]["data_basis"] == "CSV uji"
    unauthorized = client.get(f"/api/v1/conversations/{conv_a}", headers=HEADERS_B)
    assert unauthorized.status_code == 404
    restored_other = client.get(
        f"/api/v1/conversations/{conv_b}", headers=HEADERS_A
    ).json()
    assert restored_other["messages"][-1]["chart"] == other["chart"]

    def ui_request(method, url, **kwargs):
        kwargs.pop("timeout", None)
        return client.request(method, urlsplit(url).path, **kwargs)

    monkeypatch.setattr(httpx, "request", ui_request)
    app_path = str(Path(__file__).resolve().parents[1] / "app.py")

    def open_ui():
        ui = AppTest.from_file(app_path, default_timeout=15).run()
        ui.text_input[0].set_value("demo-token-aaa").run()
        assert not ui.exception
        return ui

    ui = open_ui()
    ui.selectbox[0].set_value(conv_a).run()
    assert len(ui.get("vega_lite_chart")) == 2
    ui.selectbox[0].set_value(conv_b).run()
    assert len(ui.get("vega_lite_chart")) == 1
    ui.selectbox[0].set_value(conv_a).run()
    assert len(ui.get("vega_lite_chart")) == 2
    new_chat = next(button for button in ui.button if button.label == "New conversation")
    new_chat.click().run()
    assert not ui.exception
    assert ui.session_state["conversation_id"] not in {conv_a, conv_b}
    assert len(ui.get("vega_lite_chart")) == 0
    ui.selectbox[0].set_value(conv_a).run()
    assert len(ui.get("vega_lite_chart")) == 2
    ui.text_input[0].set_value("demo-token-bbb").run()
    assert not ui.exception and not ui.chat_message
    assert len(ui.get("vega_lite_chart")) == 0
    fresh_ui = open_ui()
    fresh_ui.selectbox[0].set_value(conv_a).run()
    assert not fresh_ui.exception
    assert len(fresh_ui.get("vega_lite_chart")) == 2
    assert calls == calls_before_reading


@pytest.mark.parametrize("kind", ["line", "bar"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_ui_renders_sparse_series_but_rejects_duplicate_cells(kind, duplicate):
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    render = next(node for node in ast.parse(source).body
                  if isinstance(node, ast.FunctionDef) and node.name == "render_chart")
    chart = {
        "kind": kind, "title": "Sparse series", "x": "bucket",
        "y": ["volume"], "series": "city",
        "data": [
            {"bucket": "2026-08-01", "city": "Bandung", "volume": 11},
            {"bucket": "2026-08-02", "city": "Bandung", "volume": 15},
            {"bucket": "2026-08-02", "city": "Jakarta", "volume": 9},
        ],
    }
    if duplicate:
        chart["data"].append(dict(chart["data"][0], volume=3))
    code = "import streamlit as st\n" + ast.get_source_segment(source, render)
    ui = AppTest.from_string(code + f"\nrender_chart({chart!r})").run()
    assert not ui.exception
    assert len(ui.get("vega_lite_chart")) == (0 if duplicate else 1)


def test_welcome_example_prefills_without_sending_and_keeps_response(monkeypatch):
    requests = []
    messages = []
    question = "What is the total shipment volume per month from July to September 2026?"
    answer = "Pada **Juli 2026**, tercatat **243 kiriman**."

    def transport(method, url, **kwargs):
        path = urlsplit(url).path
        requests.append((method, path, kwargs.get("json")))
        if path == "/api/v1/conversations":
            payload = {"conversations": [{"id": "chat-a", "title": "Percakapan uji"}]}
        elif method == "POST":
            assert kwargs["json"] == {"content": question}
            messages.extend([{"role": "user", "content": question},
                             {"role": "assistant", "content": answer, "data_basis": "CSV uji"}])
            payload = {"answer": answer, "outcome": "answered"}
        else:
            payload = {"id": "chat-a", "title": "Percakapan uji", "messages": messages}
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", transport)
    ui = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    assert not ui.exception and not requests and not ui.chat_input
    assert ui.title[0].value.startswith("Shipment Analytics")
    assert ui.text_input[0].label == "Demo bearer token"
    ui.text_input[0].set_value("demo-token-aaa").run()
    assert not ui.exception and len(ui.chat_input) == 1
    next(button for button in ui.button if button.label == "Monthly volume").click().run()
    assert not ui.exception and not any(method == "POST" for method, _, _ in requests)
    assert ui.chat_input[0].proto.value == question
    ui.chat_input[0].set_value(question).run()
    assert not ui.exception
    assert sum(method == "POST" for method, _, _ in requests) == 1
    assert any(item.value == answer for item in ui.markdown)
    assert any(item.value == "Data basis: CSV uji" for item in ui.caption)
    details = next(item for item in ui.expander if item.label == "View data details")
    assert not details.proto.expanded
    assert any(item.value == "Data basis: CSV uji" for item in details.caption)
    assert any(item.value == "Percakapan uji" for item in ui.text)
    assert not any(button.label == "Monthly volume" for button in ui.button)


@pytest.mark.parametrize("change", ["identity", "removed_conversation"])
def test_identity_or_automatic_chat_change_clears_unsent_question(monkeypatch, change):
    ids = ["chat-a"]

    def transport(method, url, **kwargs):
        if urlsplit(url).path == "/api/v1/conversations":
            payload = {"conversations": [{"id": value, "title": "Uji"} for value in ids]}
        else:
            payload = {"id": "chat-a", "messages": []}
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", transport)
    ui = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    ui.text_input[0].set_value("demo-token-aaa").run()
    next(button for button in ui.button if button.label == "Monthly volume").click().run()
    assert ui.chat_input[0].proto.set_value
    assert "July to September" in ui.chat_input[0].proto.value
    if change == "identity":
        ui.text_input[0].set_value("demo-token-bbb").run()
    else:
        ids[:] = ["chat-b"]
        ui.run()
    assert not ui.exception
    assert ui.chat_input[0].proto.set_value
    assert ui.chat_input[0].proto.value == ""


@pytest.mark.parametrize("initial_title", ["New conversation", "Percakapan baru", "Manual title"])
def test_default_title_compatibility_keeps_existing_messages(tmp_path, initial_title):
    store = ConversationStore(tmp_path / "titles.sqlite3")
    created = (store.create_conversation("user-a") if initial_title == "New conversation"
               else store.create_conversation("user-a", initial_title))
    assert created["title"] == initial_title
    store.add_message(created["id"], "user-a", "assistant", "Saved answer", chart=chart_snapshot())
    before = store.get_conversation(created["id"], "user-a")["messages"]
    store.rename_if_default(created["id"], "user-a", "Shipment volume in August")
    after = ConversationStore(tmp_path / "titles.sqlite3").get_conversation(created["id"], "user-a")
    assert after["title"] == ("Manual title" if initial_title == "Manual title" else "Shipment volume in August")
    assert after["messages"] == before


def test_sidebar_actions_keep_the_selected_chat_and_render_title_as_text(monkeypatch):
    title = "![Remote image](https://example.com/image.png)"
    writes = []

    def transport(method, url, **kwargs):
        if method != "GET":
            writes.append((method, url))
        path = urlsplit(url).path
        payload = ({"conversations": [{"id": "chat-a", "title": title}]}
                   if path == "/api/v1/conversations" else {"id": "chat-a", "title": title, "messages": []})
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", transport)
    ui = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    ui.text_input[0].set_value("demo-token-aaa").run()
    assert not ui.exception and any(item.value == title for item in ui.text)
    assert not any(title in item.value for item in ui.markdown)
    next(button for button in ui.button if button.label == "Rename").click().run()
    assert not ui.exception and ui.session_state["conversation_id"] == "chat-a"
    assert next(item for item in ui.text_input if item.label == "New title").value == title
    assert any(button.label == "Save title" for button in ui.button)
    ui.run()
    next(button for button in ui.button if button.label == "Delete").click().run()
    assert not ui.exception and ui.session_state["conversation_id"] == "chat-a"
    assert any(item.value == "This action cannot be undone." for item in ui.caption)
    assert any(button.label == "Yes, delete" for button in ui.button)
    assert writes == []


def test_open_chat_survives_a_temporary_conversation_list_failure(monkeypatch):
    def transport(method, url, **kwargs):
        if urlsplit(url).path == "/api/v1/conversations":
            return httpx.Response(503, json={"detail": "Temporarily unavailable"},
                                  request=httpx.Request(method, url))
        return httpx.Response(200, json={"id": "chat-a", "title": "Shipment review",
            "messages": [{"role": "assistant", "content": "Saved verified answer"}]},
            request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", transport)
    ui = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    ui.session_state["active_token"] = "demo-token-aaa"
    ui.session_state["conversation_id"] = "chat-a"
    ui.text_input[0].set_value("demo-token-aaa").run()
    assert not ui.exception
    assert any(item.value == "Shipment review" for item in ui.text)
    assert any(item.value == "Saved verified answer" for item in ui.markdown)
    assert len(ui.chat_input) == 1
