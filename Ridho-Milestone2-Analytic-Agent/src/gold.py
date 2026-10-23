"""Koneksi Gold: Neon (produksi) atau SQLite lokal (dev/uji).

Dua backend, satu antarmuka, sehingga SQL guard dan agent dapat diuji penuh
tanpa kredensial Neon — sementara jalur produksi memakai role read-only asli.

PENTING soal read-only berlapis:
- Backend Neon memakai `GOLD_DATABASE_URL` milik role hanya baca. Validasi
  aplikasi (sql_guard) adalah lapisan TAMBAHAN, bukan pengganti izin database.
- Backend SQLite membuka database dari CSV dan menolak tulis di level koneksi,
  meniru perilaku role read-only sehingga uji guard tetap realistis.
"""

from __future__ import annotations

import csv
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from . import sql_guard
from .config import settings
from .budget import BudgetExceeded, current_budget

GOLD_TABLES = ("shipment_fact", "carrier_daily", "city_daily")

_SQLITE_DDL = {
    "shipment_fact": (
        "CREATE TABLE gold.shipment_fact ("
        "shipment_id TEXT PRIMARY KEY, order_date TEXT NOT NULL, "
        "carrier_id TEXT NOT NULL, origin_hub TEXT, destination_city TEXT NOT NULL, "
        "service_level TEXT NOT NULL, promised_delivery_at_utc TEXT, "
        "delivered_at_utc TEXT, delivery_status TEXT NOT NULL, "
        "shipping_fee_idr REAL, refund_amount_idr REAL, exception_note TEXT)"
    ),
    "carrier_daily": (
        "CREATE TABLE gold.carrier_daily ("
        "metric_date TEXT NOT NULL, carrier_id TEXT NOT NULL, shipment_count INTEGER, "
        "delivered_count INTEGER, on_time_delivered_count INTEGER, "
        "late_delivered_count INTEGER, in_transit_count INTEGER, "
        "shipping_fee_idr REAL, refund_amount_idr REAL)"
    ),
    "city_daily": (
        "CREATE TABLE gold.city_daily ("
        "metric_date TEXT NOT NULL, destination_city TEXT NOT NULL, "
        "shipment_count INTEGER, delivered_count INTEGER, "
        "on_time_delivered_count INTEGER, late_delivered_count INTEGER, "
        "in_transit_count INTEGER, shipping_fee_idr REAL, refund_amount_idr REAL)"
    ),
}

_NUMERIC_COLUMNS = frozenset(
    {
        "shipping_fee_idr",
        "refund_amount_idr",
        "shipment_count",
        "delivered_count",
        "on_time_delivered_count",
        "late_delivered_count",
        "in_transit_count",
    }
)


class GoldError(Exception):
    """Kegagalan akses Gold. Pesannya aman ditampilkan (tanpa kredensial)."""


@dataclass
class QueryResult:
    """Hasil kueri: baris + metadata eksekusi untuk audit dan data_basis."""

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: int = 0
    tables: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.row_count == 0

    def scalar(self, key: str | None = None) -> Any:
        if not self.rows:
            return None
        row = self.rows[0]
        if key is None:
            return next(iter(row.values()), None)
        return row.get(key)


def _coerce(column: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if column in _NUMERIC_COLUMNS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def build_sqlite_from_csv(data_dir: Path) -> sqlite3.Connection:
    """Bangun database SQLite in-memory dari CSV Gold.

    `check_same_thread=False` diperlukan karena FastAPI menjalankan endpoint
    sinkron pada threadpool: koneksi dibuat saat startup (thread utama) lalu
    dipakai dari worker thread. Tanpa flag ini sqlite3 melempar
    `ProgrammingError: SQLite objects created in a thread can only be used in
    that same thread`. Keamanan akses bersamaan dijaga oleh kunci di GoldClient.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS gold")

    for table in GOLD_TABLES:
        conn.execute(_SQLITE_DDL[table])

    for table in GOLD_TABLES:
        csv_path = data_dir / f"{table}.csv"
        if not csv_path.exists():
            raise GoldError(
                f"Berkas data '{table}.csv' tidak ditemukan. "
                "Sediakan data Gold atau atur GOLD_BACKEND=neon dengan GOLD_DATABASE_URL."
            )
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            if not columns:
                continue
            column_list = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            batch = [
                tuple(_coerce(col, row.get(col)) for col in columns) for row in reader
            ]
            conn.executemany(
                f"INSERT INTO gold.{table} ({column_list}) VALUES ({placeholders})",
                batch,
            )
    conn.commit()

    # Setelah data dimuat, kunci koneksi menjadi hanya-baca.
    # `PRAGMA query_only = ON` membuat setiap INSERT/UPDATE/DELETE/DROP gagal di
    # level driver — meniru perilaku role Neon yang hanya punya SELECT, sehingga
    # uji "lapisan kedua" (izin database) tetap bermakna pada backend dev ini.
    conn.execute("PRAGMA query_only = ON")
    conn.commit()
    return conn


def _build_pg_connection() -> Any:  # pragma: no cover - butuh jaringan
    """Buka koneksi PostgreSQL read-only ke Neon."""
    try:
        import psycopg  # type: ignore
    except ImportError:
        try:
            import psycopg2 as psycopg  # type: ignore
        except ImportError as exc:
            raise GoldError(
                "Driver PostgreSQL tidak terpasang. "
                "Tambahkan 'psycopg[binary]' pada requirements."
            ) from exc

    url = settings.gold_database_url
    if not url:
        raise GoldError("GOLD_DATABASE_URL belum diatur.")

    try:
        budget = current_budget()
        connect_timeout = settings.gold_connect_timeout_seconds
        if budget:
            import math

            connect_timeout = max(1, math.ceil(budget.timeout(connect_timeout)))
        conn = psycopg.connect(url, connect_timeout=connect_timeout)
    except Exception as exc:
        raise GoldError(f"Tidak dapat terhubung ke Gold: {type(exc).__name__}") from exc

    # Pertahanan berlapis: paksa transaksi read-only di level sesi.
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cur.execute(f"SET search_path TO {sql_guard.ALLOWED_SCHEMA}")
            cur.execute("SET TIME ZONE 'UTC'")
        conn.commit()
    except Exception as exc:
        conn.close()
        raise GoldError("Tidak dapat mengaktifkan transaksi Gold hanya baca.") from exc
    return conn


class GoldClient:
    """Klien Gold. Semua kueri melewati sql_guard lalu dibatasi waktu dan baris."""

    def __init__(self, backend: str | None = None) -> None:
        self.backend = (backend or settings.gold_backend).lower().strip()
        self._conn: Any = None
        self._backend_name = ""
        # Melindungi koneksi SQLite in-memory dari akses bersamaan antar-thread.
        self._lock = threading.RLock()

    def connect(self) -> None:
        with self._lock:
            self._connect()

    def _connect(self) -> None:
        if self._conn is not None:
            return
        if self.backend == "sqlite":
            self._conn = build_sqlite_from_csv(settings.data_dir)
            self._backend_name = "sqlite-csv"
        elif self.backend == "neon":
            self._conn = _build_pg_connection()
            self._backend_name = "neon-readonly"
        else:
            raise GoldError(
                f"GOLD_BACKEND tidak dikenal: {self.backend!r}. Gunakan 'neon' atau 'sqlite'."
            )

    def close(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @property
    def backend_name(self) -> str:
        return self._backend_name or self.backend

    @property
    def dialect(self) -> str:
        """Dialek SQL driver tujuan, dipakai sql_guard saat merender placeholder."""
        return "sqlite" if self.backend == "sqlite" else "postgres"

    def execute(
        self,
        sql: str,
        row_limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> QueryResult:
        """Urutkan akses koneksi, termasuk percobaan ulang dan pembersihan transaksi."""
        budget = current_budget()
        if budget is None:
            with self._lock:
                return self._execute(sql, row_limit, params)
        if not self._lock.acquire(timeout=budget.remaining()):
            raise BudgetExceeded("deadline")
        try:
            result = self._execute(sql, row_limit, params)
            budget.remaining()
            return result
        finally:
            self._lock.release()

    def _execute(
        self,
        sql: str,
        row_limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> QueryResult:
        """Validasi lalu jalankan satu kueri baca.

        `params` diteruskan TERPISAH ke driver sebagai parameter terikat. Nilai
        filter tidak pernah dirangkai menjadi literal SQL, sehingga injeksi
        lewat nilai tidak mungkin terjadi — pertahanan ini berdiri sendiri,
        terlepas dari validator.
        """
        limit = min(
            sql_guard.MAX_ROW_LIMIT,
            max(1, row_limit or settings.gold_row_limit),
            settings.gold_row_limit,
        )
        checked = sql_guard.validate_or_raise(sql, limit, dialect=self.dialect)
        # Ambil satu baris tambahan untuk membedakan hasil lengkap dari hasil terpotong.
        # Tambah hanya batas aplikasi; pertahankan LIMIT eksplisit SQL yang lebih kecil.
        tree = sqlglot.parse_one(checked.sql, read=self.dialect)
        query = tree
        while isinstance(query, (exp.Subquery, exp.Paren)):
            query = query.this
        effective_limit = int(query.args["limit"].expression.name)
        if effective_limit == limit:
            query.set("limit", exp.Limit(expression=exp.Literal.number(limit + 1)))
        execution_sql = tree.sql(dialect=self.dialect)
        started = time.perf_counter()
        bound_params = params or {}

        # Koneksi diperiksa SEBELUM dipakai, dan bila gagal maka disambung ulang
        # sekali lalu dicoba lagi.
        #
        # Ini wajib untuk Neon: compute ditidurkan setelah beberapa menit tanpa
        # aktivitas, sehingga koneksi lama menjadi basi. Tanpa penyambungan
        # ulang, `_conn` tetap tidak None, `connect()` langsung return, dan
        # SETIAP permintaan berikutnya gagal sampai container di-restart.
        last_error: Exception | None = None
        for attempt in (1, 2):
            budget = current_budget()
            if budget:
                budget.consume("query")
            try:
                if attempt == 2 or not self._connection_alive():
                    self.close()
                    self.connect()
                if self.backend == "sqlite":
                    result = self._execute_sqlite(
                        execution_sql, checked.tables, bound_params, limit
                    )
                else:
                    result = self._execute_pg(
                        execution_sql, checked.tables, bound_params, limit
                    )
                result.elapsed_ms = int((time.perf_counter() - started) * 1000)
                return result
            except (sql_guard.SQLGuardError, BudgetExceeded):
                raise
            except GoldError:
                raise
            except Exception as exc:
                last_error = exc
                # Buang koneksi yang rusak agar percobaan kedua memakai yang baru.
                self.close()
                if attempt == 2:
                    break

        raise GoldError(
            f"Kueri gagal dijalankan ({type(last_error).__name__})."
        ) from last_error

    def _connection_alive(self) -> bool:
        """Apakah koneksi masih ada dan tidak rusak?

        psycopg menandai koneksi mati lewat `.closed` dan `.broken`; sqlite3
        tidak punya keduanya, sehingga dianggap hidup selama objeknya ada.
        """
        conn = self._conn
        if conn is None:
            return False
        if getattr(conn, "closed", False):
            return False
        if getattr(conn, "broken", False):
            return False
        return True

    def _execute_sqlite(
        self, sql: str, tables: list[str], params: dict[str, Any], limit: int
    ) -> QueryResult:
        with self._lock:
            cursor = self._conn.cursor()
            budget = current_budget()
            timeout = (
                budget.timeout(settings.gold_timeout_seconds)
                if budget
                else settings.gold_timeout_seconds
            )
            deadline = time.monotonic() + timeout
            self._conn.set_progress_handler(
                lambda: int(time.monotonic() >= deadline), 1000
            )
            try:
                cursor.execute(sql, params)
                raw_rows = cursor.fetchmany(limit + 1)
                columns = [d[0] for d in (cursor.description or [])]
            finally:
                self._conn.set_progress_handler(None, 0)
                cursor.close()
        rows = [dict(zip(columns, tuple(row))) for row in raw_rows]
        truncated = len(rows) > limit
        return QueryResult(
            columns=columns,
            rows=rows[:limit],
            row_count=min(len(rows), limit),
            truncated=truncated,
            tables=tables,
        )

    def _execute_pg(  # pragma: no cover - butuh database sungguhan
        self, sql: str, tables: list[str], params: dict[str, Any], limit: int
    ) -> QueryResult:
        cursor = self._conn.cursor()
        try:
            budget = current_budget()
            timeout = (
                budget.timeout(settings.gold_timeout_seconds)
                if budget
                else settings.gold_timeout_seconds
            )
            cursor.execute(
                f"SET LOCAL statement_timeout = {max(1, int(timeout * 1000))}"
            )
            # psycopg memakai placeholder bernama bergaya `%(nama)s`;
            # sql_guard merender dalam dialek postgres sehingga bentuknya cocok.
            cursor.execute(sql, params)
            columns = [d[0] for d in (cursor.description or [])]
            raw_rows = cursor.fetchmany(limit + 1)
        finally:
            try:
                self._conn.rollback()
            except Exception:
                pass
            cursor.close()

        rows = [dict(zip(columns, tuple(row))) for row in raw_rows]
        truncated = len(rows) > limit
        return QueryResult(
            columns=columns,
            rows=rows[:limit],
            row_count=min(len(rows), limit),
            truncated=truncated,
            tables=tables,
        )

    def ping(self) -> bool:
        try:
            self.execute(
                "SELECT COUNT(shipment_id) AS n FROM gold.shipment_fact", row_limit=1
            )
            return True
        except Exception:
            return False

    def dataset_period(self) -> tuple[str | None, str | None]:
        """Rentang tanggal order yang benar-benar ada di data.

        Dipakai untuk menjawab "di luar cakupan" secara faktual, bukan asumsi.
        """
        try:
            result = self.execute(
                "SELECT MIN(order_date) AS min_date, MAX(order_date) AS max_date "
                "FROM gold.shipment_fact",
                row_limit=1,
            )
        except Exception:
            return None, None
        if result.is_empty:
            return None, None
        return (
            str(result.rows[0].get("min_date") or "") or None,
            str(result.rows[0].get("max_date") or "") or None,
        )
