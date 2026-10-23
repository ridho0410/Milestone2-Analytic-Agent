"""Antarmuka Streamlit untuk API analitik pengiriman dengan validasi."""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_URL = os.getenv("AGENT_API_URL", "http://localhost:8000").rstrip("/")
API_TIMEOUT_SECONDS = float(os.getenv("AGENT_API_TIMEOUT_SECONDS", "180"))


def api_request(method: str, path: str, token: str, **kwargs: object) -> dict | None:
    try:
        response = httpx.request(
            method,
            f"{API_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=API_TIMEOUT_SECONDS,
            **kwargs,
        )
        response.raise_for_status()
        # 204 No Content tidak punya isi; memanggil .json() akan melempar
        # ValueError dan keliru dilaporkan sebagai "respons tidak valid".
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", "Request failed")
        except ValueError:
            detail = "Request failed"
        st.error(f"API {exc.response.status_code}: {detail}")
    except (httpx.RequestError, ValueError):
        st.error("The backend is unavailable or returned an invalid response.")
    return None


def render_chart(chart: dict) -> None:
    """Gambar grafik dari resep yang dikirim API.

    Resep hanya memuat jenis grafik dari daftar tertutup beserta nama kolom dan
    datanya — tidak ada kode yang dijalankan. Karena itu UI cukup memetakan
    ``kind`` ke fungsi bawaan Streamlit dan tidak perlu mengevaluasi apa pun.

    Resep yang tidak dikenali diabaikan diam-diam: lebih baik tidak menampilkan
    grafik daripada menampilkan grafik yang salah.
    """
    jenis = str(chart.get("kind", "")).lower()
    data = chart.get("data") or []
    sumbu_x = chart.get("x")
    sumbu_y = chart.get("y") or []
    deret = chart.get("series")
    if not data or not sumbu_x or not sumbu_y:
        return

    judul = chart.get("title") or "Results chart"
    if jenis not in {"line", "bar", "scatter"}:
        return

    try:
        import pandas as pd

        kerangka = pd.DataFrame(data)
    except Exception:  # pragma: no cover - pandas selalu ada bersama Streamlit
        return

    kolom_y = [k for k in sumbu_y if k in kerangka.columns]
    if not kolom_y or sumbu_x not in kerangka.columns:
        return

    nilai = kolom_y[0]

    # Pivot menolak pasangan berulang tanpa menjumlahkan atau mengisi data kosong.
    if deret and deret in kerangka.columns:
        try:
            tabel = kerangka.pivot(index=sumbu_x, columns=deret, values=nilai)
        except Exception:  # pragma: no cover - bentuk data tak terduga
            return
    else:
        tabel = kerangka.set_index(sumbu_x)[[nilai]]

    st.caption(judul)
    if chart.get("truncated"):
        st.caption(
            f"The chart shows {len(data)} of {chart.get('total_rows')} result rows. "
            "Narrow the period or filters to view the complete chart."
        )
    if jenis == "scatter":
        st.scatter_chart(kerangka, x=sumbu_x, y=nilai)
    elif jenis == "line":
        st.line_chart(tabel)
    else:
        st.bar_chart(tabel)


st.set_page_config(
    page_title="🤖 Analytic Agent · Shipments",
    page_icon=":material/analytics:",
    layout="wide",
    initial_sidebar_state="auto",
)
# Hanya mengatur tata letak; tema bawaan menjaga warna kontrol dan grafik tetap selaras.
st.html("""
<style>
[data-testid="stMainBlockContainer"] {max-width: 66rem; padding-top: 2rem;}
[data-testid="stMainBlockContainer"] h1 {letter-spacing: -.035em;}
[data-testid="stChatMessage"] {
    border: 1px solid color-mix(in srgb, currentColor 10%, transparent);
    border-radius: .75rem; padding: 1.5rem; margin-block: .75rem;
}
[data-testid="stChatMessageContent"] {min-width: 0; overflow-wrap: anywhere;}
[data-testid="stChatMessageContent"] [data-testid="stMarkdownContainer"] p,
[data-testid="stChatMessageContent"] [data-testid="stMarkdownContainer"] li {line-height: 1.75;}
[data-testid="stChatMessageContent"] [data-testid="stExpander"] {margin-top: .75rem;}
.st-key-chat-header {margin-block: .75rem 1rem;}
.st-key-chat-header [data-testid="stText"] {
    font-size: 1.125rem; font-weight: 600; white-space: pre-wrap; overflow-wrap: anywhere;
}
.st-key-chat-header [data-testid="stCaptionContainer"] {letter-spacing: .08em;}
.st-key-welcome {padding-block: 2rem;}
.st-key-welcome h2, .st-key-welcome [data-testid="stCaptionContainer"] {text-align: center;}
.st-key-welcome button {min-height: 3.75rem; justify-content: flex-start; padding-inline: 1.25rem;}
.st-key-history-actions button {min-height: 2.75rem;}
[data-testid="stChatInput"] {border-radius: .75rem;}
@media (max-width: 640px) {
    [data-testid="stMainBlockContainer"] {padding-inline: 1rem; padding-top: 1.25rem;}
    [data-testid="stChatMessage"] {padding: 1rem;}
    .st-key-welcome {padding-block: 1rem;}
    .st-key-welcome button {padding-inline: .75rem;}
}
</style>
""")
st.sidebar.header("📦 Analytic Agent")
st.sidebar.caption("Shipment insights, one conversation away.")
st.sidebar.divider()
st.title("Shipment Analytics 📈")
st.caption(
    "Explore shipment volume, on-time performance, and refunds through your questions."
)

token = st.sidebar.text_input(
    "Demo bearer token",
    type="password",
    help="Use your assigned token. Conversations are linked to the identity verified by that token.",
)
if st.session_state.get("active_token") != token:
    # Identitas baru tidak boleh mewarisi percakapan yang dipilih pengguna sebelumnya.
    st.session_state["active_token"] = token
    st.session_state["conversation_id"] = None
    st.session_state["question_input"] = ""

if not token:
    with st.container(border=True):
        st.subheader("Welcome to Analytic Agent")
        st.write("Understand shipment performance without building reports manually.")
        st.info("Enter your demo token in the sidebar to access your conversations.")
        st.caption("Ask about shipment volume, weekly trends, carriers, and refunds.")
    st.stop()


# ---------------------------------------------------------------------------
# Dialog modal
#
# Memakai st.dialog, bukan st.popover: tombol popover selalu menampilkan tanda
# panah kecil di sampingnya, sehingga ikon tidak tampil bersih. Dialog juga
# memberi ruang lebih lega untuk kolom isian judul.
# ---------------------------------------------------------------------------


@st.dialog("Rename conversation", icon=":material/edit:")
def dialog_ganti_judul(conversation_id: str, judul_sekarang: str) -> None:
    judul_baru = st.text_input(
        "New title",
        value=judul_sekarang,
        max_chars=80,
        help="Up to 80 characters. Extra spaces are removed automatically.",
    )
    if st.button("Save title", type="primary", use_container_width=True):
        if not judul_baru.strip():
            st.error("The title cannot be empty.")
            return
        hasil = api_request(
            "PATCH",
            f"/api/v1/conversations/{conversation_id}",
            token,
            json={"title": judul_baru},
        )
        # api_request mengembalikan None bila gagal, dan sudah menampilkan
        # pesan errornya sendiri.
        if hasil is not None:
            st.rerun()


@st.dialog("Delete conversation", icon=":material/delete:")
def dialog_hapus(conversation_id: str) -> None:
    st.write("This conversation and all its messages will be permanently deleted.")
    st.caption("This action cannot be undone.")
    if st.button("Yes, delete", type="primary", use_container_width=True):
        api_request("DELETE", f"/api/v1/conversations/{conversation_id}", token)
        st.session_state["conversation_id"] = None
        st.rerun()


st.sidebar.divider()
if st.sidebar.button(
    "New conversation", icon=":material/add:", type="primary", width="stretch"
):
    created = api_request("POST", "/api/v1/conversations", token, json={})
    if created:
        st.session_state["conversation_id"] = created["id"]
        st.session_state["question_input"] = ""
        st.rerun()

listing = api_request("GET", "/api/v1/conversations", token)
conversations = listing.get("conversations", []) if listing else []
ids = [item["id"] for item in conversations]
if ids:
    current = st.session_state.get("conversation_id")
    if current not in ids:
        current = ids[0]
        st.session_state["conversation_id"] = current
        st.session_state["question_input"] = ""
    if st.session_state.get("conversation_selector") != current:
        st.session_state["conversation_selector"] = current

    def pilih_percakapan() -> None:
        st.session_state["conversation_id"] = st.session_state["conversation_selector"]
        st.session_state["question_input"] = ""

    def judul_dari(value: str) -> str:
        return next(
            (
                item.get("title") or value
                for item in conversations
                if item["id"] == value
            ),
            value,
        )

    # Pemilih memakai lebar penuh agar judul panjang tetap mudah dikenali.
    selected = st.sidebar.selectbox(
        "My conversations",
        ids,
        index=ids.index(current) if current in ids else 0,
        format_func=judul_dari,
        key="conversation_selector",
        on_change=pilih_percakapan,
    )
    st.session_state["conversation_id"] = selected

    with st.sidebar.container(key="history-actions"):
        col_ganti, col_hapus = st.columns(2)
        with col_ganti:
            if st.button(
                "Rename",
                icon=":material/edit:",
                help="Rename conversation",
                width="stretch",
                key="tombol_ganti_judul",
            ):
                dialog_ganti_judul(selected, judul_dari(selected))
        with col_hapus:
            if st.button(
                "Delete",
                icon=":material/delete:",
                help="Delete this conversation",
                width="stretch",
                key="tombol_hapus",
            ):
                dialog_hapus(selected)
else:
    st.info("Click New conversation in the sidebar to start your analysis.")

st.sidebar.divider()
st.sidebar.caption(
    "Your messages and charts remain available when you reopen a conversation."
)

conversation_id = st.session_state.get("conversation_id")
if conversation_id:
    detail = api_request("GET", f"/api/v1/conversations/{conversation_id}", token)
    if detail:
        with st.container(key="chat-header", border=True):
            st.caption("CURRENT CONVERSATION")
            # Judul pengguna ditampilkan sebagai teks, tanpa menafsirkan Markdown atau HTML.
            st.text(detail.get("title") or conversation_id)
        if not detail.get("messages"):
            with st.container(key="welcome"):
                st.header("What would you like to analyze?")
                st.caption("Start with an example below, or ask your own question.")
                examples = [
                    (
                        "Monthly volume",
                        ":material/bar_chart:",
                        "What is the total shipment volume per month from July to September 2026?",
                    ),
                    (
                        "City trends",
                        ":material/show_chart:",
                        "Show the weekly shipment trend for each destination city across all cities.",
                    ),
                    (
                        "Total shipments",
                        ":material/inventory_2:",
                        "How many shipments were there in August 2026?",
                    ),
                    (
                        "Express refunds",
                        ":material/payments:",
                        "What is the refund rate for express service in September 2026?",
                    ),
                ]
                columns = st.columns(2)
                for index, (label, icon, question) in enumerate(examples):
                    with columns[index % 2]:
                        if st.button(label, icon=icon, help=question, width="stretch"):
                            st.session_state["question_input"] = question
        for message in detail.get("messages", []):
            avatar = (
                ":material/person:"
                if message["role"] == "user"
                else ":material/analytics:"
            )
            with st.chat_message(message["role"], avatar=avatar):
                st.markdown(message["content"])
                if message["role"] == "assistant":
                    basis = message.get("data_basis")
                    if basis:
                        with st.expander(
                            "View data details",
                            expanded=False,
                            icon=":material/database:",
                        ):
                            st.caption(f"Data basis: {basis}")
                    grafik = message.get("chart")
                    if isinstance(grafik, dict):
                        render_chart(grafik)

    prompt = st.chat_input(
        "Ask about volume, on-time performance, refunds, carriers, or cities…",
        key="question_input",
    )
    if prompt:
        with st.spinner("Preparing your analysis…", show_time=True):
            result = api_request(
                "POST",
                f"/api/v1/conversations/{conversation_id}/messages",
                token,
                json={"content": prompt},
            )
        if result:
            st.rerun()
