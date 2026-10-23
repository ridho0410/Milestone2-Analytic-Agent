"""Penyimpanan percakapan di SQLite dengan pemeriksaan kepemilikan.

Aturan yang ditegakkan:
- Setiap percakapan punya tepat satu pemilik (`user_id`).
- Operasi list, read, dan send SELALU memfilter `user_id`.
- Percakapan milik pengguna lain menghasilkan `NotFoundError` -> HTTP 404,
  bukan 403, agar keberadaannya tidak terungkap (anti-enumerasi ID).
- Tidak ada endpoint yang menerima `user_id` dari klien.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Iterator

logger = logging.getLogger(__name__)
MAX_CHART_BYTES = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT 'New conversation',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    outcome         TEXT,
    data_basis      TEXT,
    context_json    TEXT,
    chart_json      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id, id);
"""


class NotFoundError(Exception):
    """Percakapan tidak ada ATAU bukan milik pengguna ini.

    Sengaja tidak dibedakan: pemanggil selalu memetakannya ke 404.
    """


def utc_now() -> str:
    """Waktu ISO 8601 UTC dengan sufiks Z, sesuai kontrak API."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def serialize_chart(chart: dict[str, Any] | None) -> str | None:
    """Simpan cuplikan grafik ringkas tanpa menghalangi penyimpanan jawaban."""
    if not isinstance(chart, dict) or not chart:
        return None
    try:
        payload = json.dumps(
            chart, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError):
        logger.warning("Chart snapshot omitted: invalid JSON data")
        return None
    if len(payload.encode("utf-8")) > MAX_CHART_BYTES:
        logger.warning("Chart snapshot omitted: storage size limit exceeded")
        return None
    return payload


def deserialize_chart(payload: str | None) -> dict[str, Any] | None:
    """Pesan lama dan cuplikan yang tidak valid tetap dapat dibaca sebagai teks."""
    if not payload:
        return None
    try:
        chart = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return chart if isinstance(chart, dict) else None


@dataclass
class ConversationStore:
    """Akses SQLite. Satu instance per proses; koneksi per thread."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------- internal
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        # Tanpa ini, SQLite hanya menandai halaman sebagai "bebas" saat baris
        # dihapus — isinya tetap ada di file dan masih bisa dibaca dengan
        # editor hex. `secure_delete` menimpa halaman yang dibebaskan dengan
        # nol, sehingga percakapan yang dihapus benar-benar hilang.
        conn.execute("PRAGMA secure_delete = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # Migrasi ringan: tambahkan kolom bila database lama sudah ada.
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "context_json" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN context_json TEXT")
            if "chart_json" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN chart_json TEXT")

    # ------------------------------------------------------------ penyimpanan
    def create_conversation(
        self, user_id: str, title: str = "New conversation"
    ) -> dict[str, Any]:
        conversation_id = str(uuid.uuid4())
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, user_id, title, now, now),
            )
        return {
            "id": conversation_id,
            "title": title,
            "updated_at": now,
        }

    def list_conversations(self, user_id: str) -> list[dict[str, Any]]:
        """Hanya percakapan milik user_id. Filter ada di SQL, bukan di Python."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, updated_at FROM conversations "
                "WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any]:
        """Ambil percakapan beserta pesannya, hanya bila milik user_id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, user_id, title, created_at, updated_at FROM conversations "
                "WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(conversation_id)

            messages = conn.execute(
                "SELECT role, content, created_at, data_basis, chart_json FROM messages "
                "WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()

        items = [dict(message) for message in messages]
        for message in items:
            chart = deserialize_chart(message.pop("chart_json"))
            if chart is not None:
                message["chart"] = chart
            if not message["data_basis"]:
                message.pop("data_basis")

        return {
            "id": row["id"],
            "title": row["title"],
            "messages": items,
        }

    def assert_owned(self, conversation_id: str, user_id: str) -> None:
        """Pastikan kepemilikan; lempar NotFoundError bila bukan miliknya."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(conversation_id)

    def add_message(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        outcome: str | None = None,
        data_basis: str | None = None,
        context_json: str | None = None,
        chart: dict[str, Any] | None = None,
    ) -> None:
        """Tambah pesan. Kepemilikan diperiksa dalam transaksi yang sama."""
        now = utc_now()
        with self._connect() as conn:
            owned = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()
            if owned is None:
                raise NotFoundError(conversation_id)

            chart_json = serialize_chart(chart) if role == "assistant" else None
            conn.execute(
                "INSERT INTO messages "
                "(conversation_id, role, content, outcome, data_basis, context_json, chart_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    role,
                    content,
                    outcome,
                    data_basis,
                    context_json,
                    chart_json,
                    now,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

    def history(
        self, conversation_id: str, user_id: str, limit: int
    ) -> list[dict[str, Any]]:
        """Riwayat TERBATAS untuk konteks follow-up.

        Dibatasi agar biaya, latency, dan risiko instruksi lama tetap terkendali.
        Diambil dari ekor percakapan, lalu dikembalikan dalam urutan kronologis.
        Menyertakan `context_json` agar follow-up dapat dilanjutkan setelah
        halaman dimuat ulang, bukan hanya selama sesi browser hidup.
        """
        self.assert_owned(conversation_id, user_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, outcome, data_basis, context_json "
                "FROM messages WHERE conversation_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (conversation_id, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def rename_if_default(self, conversation_id: str, user_id: str, title: str) -> None:
        """Beri judul percakapan dari pertanyaan pertama, tanpa menimpa judul manual."""
        clean = " ".join(title.split())[:70].strip()
        if not clean:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET title = ? "
                "WHERE id = ? AND user_id = ? AND title IN ('New conversation', 'Percakapan baru')",
                (clean, conversation_id, user_id),
            )

    def rename_conversation(
        self, conversation_id: str, user_id: str, title: str
    ) -> bool:
        """Ganti judul percakapan milik `user_id` dengan judul pilihan pengguna.

        Berbeda dari `rename_if_default`, method ini menimpa judul apa pun —
        termasuk judul yang sudah diubah sebelumnya. Itu memang yang diinginkan
        pada fitur ganti nama manual.

        Judul sudah dinormalkan pemanggil (spasi dirapikan, panjang dibatasi),
        dan filter kepemilikan berada DI DALAM pernyataan SQL sehingga
        percakapan milik pengguna lain tidak mungkin tersentuh.

        Mengembalikan True bila ada percakapan yang benar-benar diperbarui.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (title, utc_now(), conversation_id, user_id),
            )
            return cursor.rowcount > 0

    def purge_user(self, user_id: str) -> int:
        """Hapus seluruh percakapan milik pengguna (dipakai pengujian)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM conversations WHERE user_id = ?", (user_id,)
            )
            return cursor.rowcount

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        """Hapus satu percakapan milik `user_id` beserta seluruh pesannya.

        Filter kepemilikan berada DI DALAM pernyataan SQL (`AND user_id = ?`),
        bukan diperiksa terpisah lalu dihapus. Dengan begitu percakapan milik
        pengguna lain tidak mungkin terhapus walaupun ID-nya diketahui — tidak
        ada celah antara pemeriksaan dan penghapusan (TOCTOU).

        Pesan ikut terhapus lewat `ON DELETE CASCADE` (foreign_keys diaktifkan
        pada setiap koneksi), sehingga tidak ada baris yatim yang tertinggal.

        Mengembalikan True bila ada percakapan yang benar-benar terhapus.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            )
            deleted = cursor.rowcount > 0

        # Setelah penghapusan berhasil, ruang yang dibebaskan dipadatkan.
        # `secure_delete` sudah menimpa isi barisnya, tetapi halaman kosong itu
        # masih tercatat di dalam file; VACUUM membangun ulang file sehingga
        # ruangnya benar-benar dilepaskan dan sisa teks tidak tertinggal.
        if deleted:
            self.compact()

        return deleted

    def compact(self) -> None:
        """Padatkan berkas database: kosongkan WAL lalu buang halaman bebas.

        Dijalankan setelah penghapusan agar isi percakapan tidak dapat
        dipulihkan dari berkas mentah dan ukuran berkas ikut menyusut.

        Operasi ini bersifat *best effort*: kegagalannya TIDAK boleh menggagalkan
        penghapusan yang sudah berhasil. VACUUM memerlukan koneksi tanpa
        transaksi aktif, karena itu dibuat koneksi tersendiri dengan autocommit.

        Catatan produksi: pada basis data besar, VACUUM setiap kali hapus bisa
        mahal. Di sini skalanya kecil (percakapan chat), dan jaminan "benar-benar
        terhapus" lebih penting daripada beberapa milidetik tambahan.
        """
        try:
            conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None)
            try:
                # Kuras WAL ke berkas utama lalu kosongkan; tanpa ini salinan
                # lama masih tertinggal di berkas -wal.
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("VACUUM")
            finally:
                conn.close()
        except sqlite3.Error:
            # Pemadatan tidak boleh menggagalkan permintaan pengguna.
            pass

    def count_messages(self, conversation_id: str) -> int:
        """Hitung pesan TANPA filter pemilik.

        HANYA untuk pengujian: membuktikan CASCADE benar-benar membersihkan
        pesan setelah percakapan dihapus. Jangan dipakai pada jalur permintaan,
        karena melewati pemeriksaan kepemilikan.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return int(row["n"]) if row else 0
