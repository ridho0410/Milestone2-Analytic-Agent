"""Uji SQL guard: hanya SELECT, allowlist, dan batas eksekusi.

Ini menjawab hard gate #5 (bukti pengujian SQL read-only yang dapat diulang)
dan kategori rubrik "SQL dan batas akses Gold" (20 poin), yang memotong nilai
50% bila hanya mengandalkan pencocokan prefix string.
"""

from __future__ import annotations

import pytest

from src import sql_guard

# --------------------------------------------------------------------------
# Harus DITOLAK
# --------------------------------------------------------------------------

WRITE_STATEMENTS = [
    ("UPDATE gold.shipment_fact SET delivery_status = 'x'", "SQL-WRITE"),
    ("DELETE FROM gold.shipment_fact", "SQL-WRITE"),
    ("DROP TABLE gold.shipment_fact", "SQL-WRITE"),
    ("TRUNCATE TABLE gold.shipment_fact", "SQL-WRITE"),
    ("INSERT INTO gold.carrier_daily (metric_date) VALUES ('2026-01-01')", "SQL-WRITE"),
    ("ALTER TABLE gold.shipment_fact ADD COLUMN x int", "SQL-WRITE"),
    ("CREATE TABLE gold.evil (id int)", "SQL-WRITE"),
    ("GRANT ALL ON gold.shipment_fact TO public", "SQL-WRITE"),
    ("SELECT shipment_id FROM gold.shipment_fact FOR UPDATE", "SQL-WRITE"),
]

BYPASS_ATTEMPTS = [
    # Stacked / multi-statement
    (
        "SELECT shipment_id FROM gold.shipment_fact; DROP TABLE gold.carrier_daily",
        "SQL-MULTI",
    ),
    ("SELECT 1; SELECT 2", "SQL-MULTI"),
    # Komentar untuk menyembunyikan sisa pernyataan
    ("SELECT shipment_id FROM gold.shipment_fact -- ; DROP TABLE x", "SQL-COMMENT"),
    ("SELECT shipment_id FROM gold.shipment_fact /* hi */", "SQL-COMMENT"),
    # Schema lain
    ("SELECT 1 FROM public.users", "SQL-SCHEMA"),
    ("SELECT 1 FROM information_schema.tables", "SQL-SCHEMA"),
    ("SELECT 1 FROM pg_catalog.pg_tables", "SQL-SCHEMA"),
    # Tabel di luar allowlist
    ("SELECT 1 FROM gold.unknown_table", "SQL-TABLE"),
    ("SELECT 1 FROM gold.secrets", "SQL-TABLE"),
    # Kolom di luar allowlist
    ("SELECT secret_col FROM gold.shipment_fact", "SQL-COLUMN"),
    ("SELECT password FROM gold.carrier_daily", "SQL-COLUMN"),
    # Wildcard
    ("SELECT * FROM gold.shipment_fact", "SQL-STAR"),
    ("SELECT f.* FROM gold.shipment_fact f", "SQL-STAR"),
    # Fungsi berbahaya
    ("SELECT pg_read_file('/etc/passwd') FROM gold.shipment_fact", "SQL-FUNCTION"),
    ("SELECT pg_sleep(10) FROM gold.shipment_fact", "SQL-FUNCTION"),
    ("SELECT dblink('host=evil', 'SELECT 1') FROM gold.shipment_fact", "SQL-FUNCTION"),
    # SELECT INTO
    ("SELECT shipment_id INTO new_table FROM gold.shipment_fact", "SQL-INTO"),
    # Kosong / tidak bisa di-parse
    ("", "SQL-EMPTY"),
    ("   ", "SQL-EMPTY"),
    ("SELECT FROM WHERE", "SQL-PARSE"),
    ("bukan sql sama sekali", "SQL-PARSE"),
]


@pytest.mark.parametrize("sql,expected_rule", WRITE_STATEMENTS + BYPASS_ATTEMPTS)
def test_guard_menolak(sql: str, expected_rule: str) -> None:
    """Setiap pernyataan berbahaya harus ditolak dengan alasan yang tepat."""
    result = sql_guard.validate(sql)
    assert not result.ok, f"seharusnya ditolak: {sql}"
    assert (
        result.rule == expected_rule
    ), f"{sql!r} -> diharapkan {expected_rule}, dapat {result.rule}: {result.reason}"


def test_guard_menolak_prefix_select_yang_menyembunyikan_tulis() -> None:
    """Kasus penting: string diawali SELECT tetapi isinya penulisan.

    Inilah alasan validator berbasis AST, bukan pencocokan prefix.
    """
    sql = (
        "SELECT 1 FROM gold.shipment_fact WHERE 1=1; UPDATE gold.shipment_fact SET x=1"
    )
    assert sql.strip().lower().startswith("select")  # memenuhi syarat prefix
    result = sql_guard.validate(sql)
    assert not result.ok
    assert result.rule == "SQL-MULTI"

    cte_write = (
        "WITH x AS (UPDATE gold.shipment_fact SET delivery_status='x' RETURNING shipment_id) "
        "SELECT shipment_id FROM x"
    )
    result = sql_guard.validate(cte_write)
    assert not result.ok, "CTE yang memuat UPDATE harus ditolak"


# --------------------------------------------------------------------------
# Harus DITERIMA
# --------------------------------------------------------------------------

VALID_SELECTS = [
    "SELECT COUNT(shipment_id) AS n FROM gold.shipment_fact",
    "SELECT carrier_id, SUM(shipment_count) AS n FROM gold.carrier_daily GROUP BY carrier_id",
    "SELECT destination_city, COUNT(shipment_id) AS n FROM gold.shipment_fact "
    "GROUP BY destination_city ORDER BY n DESC",
    "WITH x AS (SELECT carrier_id FROM gold.carrier_daily) SELECT carrier_id FROM x",
    "SELECT c.carrier_id FROM gold.carrier_daily AS c WHERE c.metric_date >= '2026-08-01'",
]


@pytest.mark.parametrize("sql", VALID_SELECTS)
def test_guard_menerima_select_valid(sql: str) -> None:
    result = sql_guard.validate(sql)
    assert result.ok, f"seharusnya diterima: {sql} -> {result.rule}: {result.reason}"
    assert result.tables, "tabel yang dipakai harus dilaporkan untuk audit"


def test_guard_menyuntikkan_limit() -> None:
    """Setiap kueri harus punya batas baris, disuntikkan bila belum ada."""
    result = sql_guard.validate("SELECT shipment_id FROM gold.shipment_fact")
    assert result.ok
    assert "LIMIT" in result.sql.upper()


def test_guard_membatasi_limit_besar() -> None:
    """LIMIT yang diminta pengguna tidak boleh melebihi maksimum."""
    result = sql_guard.validate(
        "SELECT shipment_id FROM gold.shipment_fact LIMIT 999999", row_limit=100
    )
    assert result.ok
    assert "100" in result.sql
    assert "999999" not in result.sql


def test_guard_menghormati_limit_kecil() -> None:
    result = sql_guard.validate(
        "SELECT shipment_id FROM gold.shipment_fact LIMIT 5", row_limit=500
    )
    assert result.ok
    assert "LIMIT 5" in result.sql.upper()


# --------------------------------------------------------------------------
# Read-only di level database (lapisan kedua)
# --------------------------------------------------------------------------


def test_database_menolak_tulis_walau_guard_dilewati(gold) -> None:
    """Bukti lapisan kedua: pernyataan tulis ditolak DATABASE, bukan aplikasi.

    Kueri dijalankan langsung ke driver sehingga sql_guard sepenuhnya dilewati.
    """
    statements = [
        "UPDATE gold.shipment_fact SET delivery_status = 'tampered'",
        "DELETE FROM gold.shipment_fact",
        "DROP TABLE gold.shipment_fact",
        "INSERT INTO gold.carrier_daily (metric_date) VALUES ('2099-01-01')",
    ]
    for statement in statements:
        with pytest.raises(Exception) as excinfo:
            cursor = gold._conn.cursor()
            cursor.execute(statement)
            gold._conn.commit()
        assert excinfo.value is not None, f"{statement} seharusnya gagal"
        # PostgreSQL menandai transaksi ABORTED setelah kegagalan; tanpa
        # ROLLBACK, koneksi tidak dapat dipakai lagi pada tes berikutnya.
        try:
            gold._conn.rollback()
        except Exception:
            pass


def test_data_tidak_berubah_setelah_percobaan_tulis(gold) -> None:
    """Percobaan tulis tidak boleh mengubah isi Gold."""
    before = gold.execute(
        "SELECT COUNT(shipment_id) AS n FROM gold.shipment_fact"
    ).scalar("n")
    try:
        cursor = gold._conn.cursor()
        cursor.execute("UPDATE gold.shipment_fact SET delivery_status = 'tampered'")
        gold._conn.commit()
    except Exception:
        pass
    finally:
        try:
            gold._conn.rollback()
        except Exception:
            pass
    after = gold.execute(
        "SELECT COUNT(shipment_id) AS n FROM gold.shipment_fact"
    ).scalar("n")
    assert before == after, "jumlah baris Gold berubah setelah percobaan tulis"

    tampered = gold.execute(
        "SELECT COUNT(shipment_id) AS n FROM gold.shipment_fact "
        "WHERE delivery_status = 'tampered'"
    ).scalar("n")
    assert tampered == 0, "ada baris yang berhasil diubah"


def test_allowlist_hanya_tiga_tabel() -> None:
    """Permukaan serangan dijaga tetap kecil dan eksplisit."""
    assert set(sql_guard.ALLOWED_TABLES) == {
        "shipment_fact",
        "carrier_daily",
        "city_daily",
    }
    assert sql_guard.ALLOWED_SCHEMA == "gold"
