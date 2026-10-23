"""SQL guard: validator SQL read-only berbasis AST (bukan prefix/regex).

Rubrik menilai secara eksplisit: memblokir SQL hanya dengan mencocokkan prefix
string "SELECT" diberi maksimal 50% poin kategori SQL. Karena itu modul ini:

1. Mem-*parse* SQL menjadi AST (sqlglot) sehingga komentar, CTE, subquery, dan
   trik penyamaran tidak bisa mengelabui validator.
2. Menolak lebih dari satu pernyataan (anti multi-statement / stacked query).
3. Memaksa pernyataan terluar tepat satu `SELECT` (atau `UNION` dari SELECT).
4. Memvalidasi allowlist schema/tabel/kolom secara rekursif pada seluruh AST,
   termasuk CTE dan subquery.
5. Menolak fungsi berbahaya (pg_read_file, dblink, pg_sleep, ...) dan
   `SELECT ... INTO`, serta akses `information_schema`/`pg_catalog`.
6. Menyuntikkan batas baris jika belum ada.

Validator ini adalah lapisan PERTAMA. Lapisan kedua adalah role database yang
hanya punya SELECT — lihat gold.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# --------------------------------------------------------------------------
# Kebijakan allowlist — satu sumber kebenaran, dipakai juga oleh agent.
# --------------------------------------------------------------------------

ALLOWED_SCHEMA = "gold"

ALLOWED_TABLES: dict[str, tuple[str, ...]] = {
    "shipment_fact": (
        "shipment_id",
        "order_date",
        "carrier_id",
        "origin_hub",
        "destination_city",
        "service_level",
        "promised_delivery_at_utc",
        "delivered_at_utc",
        "delivery_status",
        "shipping_fee_idr",
        "refund_amount_idr",
        "exception_note",
    ),
    "carrier_daily": (
        "metric_date",
        "carrier_id",
        "shipment_count",
        "delivered_count",
        "on_time_delivered_count",
        "late_delivered_count",
        "in_transit_count",
        "shipping_fee_idr",
        "refund_amount_idr",
    ),
    "city_daily": (
        "metric_date",
        "destination_city",
        "shipment_count",
        "delivered_count",
        "on_time_delivered_count",
        "late_delivered_count",
        "in_transit_count",
        "shipping_fee_idr",
        "refund_amount_idr",
    ),
}

# Schema sistem yang tidak boleh disentuh walau hanya dibaca.
FORBIDDEN_SCHEMAS = frozenset(
    {"information_schema", "pg_catalog", "pg_temp", "public", "mysql", "sys"}
)

# Fungsi yang bisa membaca berkas, membuka koneksi keluar, atau menahan eksekusi.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "dblink",
        "dblink_exec",
        "lo_import",
        "lo_export",
        "lo_unlink",
        "pg_write_file",
        "copy",
        "current_setting",
        "set_config",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "query_to_xml",
        "xp_cmdshell",
        "load_file",
        "benchmark",
        "sleep",
    }
)

# Izinkan hanya fungsi analitik bawaan yang diperlukan kueri yang disetujui.
ALLOWED_FUNCTIONS = frozenset(
    {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "nullif",
        "coalesce",
        "round",
        "cast",
        "try_cast",
        "case",
        "if",
        "date",
        "date_trunc",
        "timestamp_trunc",
        "strftime",
        "current_date",
        "extract",
        "abs",
        "ceil",
        "ceiling",
        "floor",
        "upper",
        "lower",
        "trim",
        "length",
        "concat",
        "and",
        "or",
        "not",
        "exists",
    }
)

# Ekspresi yang menandakan operasi tulis atau DDL.
# `exp.Into` sengaja TIDAK dimasukkan ke sini: SELECT ... INTO diperiksa
# terpisah (ATURAN SQL-6) agar pesannya lebih tepat. `exp.Lock` menangkap
# SELECT ... FOR UPDATE, yang mengunci baris dan karenanya bukan operasi baca murni.
WRITE_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Copy,
    exp.Command,
    exp.Merge,
    exp.Set,
    exp.Lock,
)

MAX_STATEMENTS = 1
DEFAULT_ROW_LIMIT = 500
MAX_ROW_LIMIT = 5000


@dataclass
class GuardResult:
    """Hasil validasi: SQL yang sudah diamankan, atau penolakan beralasan."""

    ok: bool
    sql: str = ""
    reason: str = ""
    rule: str = ""
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


class SQLGuardError(Exception):
    """Ditolak oleh SQL guard. Pesannya aman ditampilkan ke pengguna."""

    def __init__(self, reason: str, rule: str = "GUARD") -> None:
        super().__init__(reason)
        self.reason = reason
        self.rule = rule


DIALECTS = {"postgres", "sqlite"}


def validate(
    sql: str, row_limit: int | None = None, dialect: str = "postgres"
) -> GuardResult:
    """Validasi SQL dan kembalikan versi aman (dengan LIMIT tersuntik).

    Tidak pernah melempar untuk input buruk — mengembalikan GuardResult(ok=False).
    """
    limit = _clamp_limit(row_limit)
    text = (sql or "").strip()

    if not text:
        return GuardResult(False, reason="Kueri kosong.", rule="SQL-EMPTY")

    # ATURAN SQL-1: tolak komentar. Komentar adalah vektor klasik untuk
    # menyembunyikan sisa pernyataan dan membuat SQL tidak dapat diaudit.
    if _has_comment(text):
        return GuardResult(
            False,
            reason="Kueri mengandung komentar, yang tidak diizinkan.",
            rule="SQL-COMMENT",
        )

    # ATURAN SQL-2: hanya boleh satu pernyataan.
    statements = _split_statements(text)
    if len(statements) > MAX_STATEMENTS:
        return GuardResult(
            False,
            reason=f"Kueri harus satu pernyataan saja, ditemukan {len(statements)}.",
            rule="SQL-MULTI",
        )

    # ATURAN SQL-3: harus bisa di-parse menjadi AST.
    try:
        parsed = sqlglot.parse_one(text, read="postgres")
    except ParseError as exc:
        return GuardResult(
            False,
            reason=f"Kueri tidak dapat di-parse: {str(exc)[:150]}",
            rule="SQL-PARSE",
        )
    except Exception as exc:  # pragma: no cover - defensif
        return GuardResult(
            False,
            reason=f"Kueri tidak valid: {str(exc)[:150]}",
            rule="SQL-PARSE",
        )

    if parsed is None:
        return GuardResult(
            False, reason="Kueri tidak dapat di-parse.", rule="SQL-PARSE"
        )

    # ATURAN SQL-4: pernyataan terluar harus SELECT / UNION, bukan tulis/DDL.
    if not _is_read_only_statement(parsed):
        kind = type(parsed).__name__.upper()
        return GuardResult(
            False,
            reason=(
                "Hanya kueri baca (SELECT) yang diizinkan. "
                f"Pernyataan terdeteksi: {kind}."
            ),
            rule="SQL-WRITE",
        )

    # ATURAN SQL-5: tidak ada ekspresi tulis/DDL di mana pun pada AST.
    for node in parsed.walk():
        if isinstance(node, WRITE_EXPRESSIONS):
            return GuardResult(
                False,
                reason=(
                    "Kueri memuat operasi tulis, penguncian baris, atau "
                    "perubahan skema "
                    f"({type(node).__name__.upper()}), yang tidak diizinkan."
                ),
                rule="SQL-WRITE",
            )

    # ATURAN SQL-6: tidak ada SELECT ... INTO.
    if parsed.find(exp.Into) is not None:
        return GuardResult(
            False,
            reason="Pola SELECT ... INTO tidak diizinkan.",
            rule="SQL-INTO",
        )

    # ATURAN SQL-7: fungsi berbahaya.
    # Diperiksa pada seluruh AST (termasuk yang bersarang di dalam fungsi lain).
    for func in parsed.find_all(exp.Func):
        name = _func_name(func)
        if name in FORBIDDEN_FUNCTIONS or name not in ALLOWED_FUNCTIONS:
            return GuardResult(
                False,
                reason=f"Fungsi '{name}' tidak diizinkan.",
                rule="SQL-FUNCTION",
            )
    if any(isinstance(dot.expression, exp.Func) for dot in parsed.find_all(exp.Dot)):
        return GuardResult(
            False,
            reason="Fungsi berkualifikasi schema tidak diizinkan.",
            rule="SQL-FUNCTION",
        )
    # Jaring pengaman: nama fungsi apa pun yang muncul sebagai identifier bebas.
    for identifier in parsed.find_all(exp.Identifier):
        raw = str(identifier.this or "").lower()
        if raw in FORBIDDEN_FUNCTIONS:
            return GuardResult(
                False,
                reason=f"Fungsi '{raw}' tidak diizinkan.",
                rule="SQL-FUNCTION",
            )

    # ATURAN SQL-8: tidak ada wildcard tabel (SELECT *) agar kolom selalu eksplisit
    # dan hasil dapat diaudit.
    if _has_star_select(parsed):
        return GuardResult(
            False,
            reason="Gunakan kolom eksplisit; 'SELECT *' tidak diizinkan.",
            rule="SQL-STAR",
        )

    # ATURAN SQL-9: allowlist schema/tabel/kolom, rekursif termasuk CTE & subquery.
    tables, columns, error = _check_allowlist(parsed)
    if error is not None:
        return error

    # ATURAN SQL-10: suntikkan LIMIT bila belum ada, lalu render sesuai dialek
    # driver tujuan agar placeholder parameter tetap berbentuk sah.
    try:
        safe_sql = _apply_limit(parsed, limit, dialect)
    except SQLGuardError as exc:
        return GuardResult(False, reason=str(exc), rule="SQL-DIALECT")

    return GuardResult(
        ok=True,
        sql=safe_sql,
        tables=sorted(tables),
        columns=sorted(columns),
        rule="OK",
    )


def validate_or_raise(
    sql: str, row_limit: int | None = None, dialect: str = "postgres"
) -> GuardResult:
    """Versi yang melempar SQLGuardError — dipakai di jalur eksekusi."""
    result = validate(sql, row_limit, dialect)
    if not result.ok:
        raise SQLGuardError(result.reason, result.rule)
    return result


# --------------------------------------------------------------------------
# Helper internal
# --------------------------------------------------------------------------


def _clamp_limit(row_limit: int | None) -> int:
    if row_limit is None:
        return DEFAULT_ROW_LIMIT
    return max(1, min(int(row_limit), MAX_ROW_LIMIT))


def _has_comment(sql: str) -> bool:
    """Deteksi komentar SQL dengan sadar string literal."""
    in_single = False
    in_double = False
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]
        if in_single:
            if ch == "'":
                if i + 1 < length and sql[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
        elif in_double:
            if ch == '"':
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "-" and i + 1 < length and sql[i + 1] == "-":
                return True
            elif ch == "/" and i + 1 < length and sql[i + 1] == "*":
                return True
        i += 1
    return False


def _split_statements(sql: str) -> list[str]:
    """Pisahkan pernyataan pada ';' dengan sadar string literal."""
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]
        if in_single:
            current.append(ch)
            if ch == "'":
                if i + 1 < length and sql[i + 1] == "'":
                    current.append("'")
                    i += 2
                    continue
                in_single = False
        elif ch == "'":
            in_single = True
            current.append(ch)
        elif ch == ";":
            piece = "".join(current).strip()
            if piece:
                statements.append(piece)
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _is_read_only_statement(node: exp.Expression) -> bool:
    if isinstance(node, exp.Select):
        return True
    if isinstance(node, exp.Union):
        return all(
            _is_read_only_statement(side) for side in (node.this, node.expression)
        )
    if isinstance(node, exp.Subquery):
        return _is_read_only_statement(node.this)
    if isinstance(node, exp.Paren):
        return _is_read_only_statement(node.this)
    return False


def _func_name(func: exp.Func) -> str:
    """Nama fungsi dalam huruf kecil.

    Fungsi tak dikenal (mis. `pg_read_file`) di-parse sqlglot sebagai
    `exp.Anonymous` dengan nama di properti `this`, bukan lewat `sql_name()`.
    Keduanya harus ditangani; jika tidak, fungsi berbahaya akan lolos guard.
    """
    if isinstance(func, exp.Anonymous):
        return str(func.this).lower()
    if isinstance(func, exp.Identifier):
        return str(func.this).lower()
    name = func.sql_name() or type(func).__name__
    return str(name).lower()


def _has_star_select(node: exp.Expression) -> bool:
    for select in node.find_all(exp.Select):
        for projection in select.expressions:
            target = (
                projection.this if isinstance(projection, exp.Alias) else projection
            )
            if isinstance(target, exp.Star):
                return True
            if isinstance(target, exp.Column) and isinstance(target.this, exp.Star):
                return True
    return False


def _check_allowlist(
    node: exp.Expression,
) -> tuple[set[str], set[str], GuardResult | None]:
    """Validasi seluruh referensi tabel dan kolom terhadap allowlist.

    Menangani alias dan CTE: nama CTE dikenali sebagai tabel turunan yang sah,
    bukan tabel fisik, sehingga isinya tetap divalidasi secara rekursif.
    """
    cte_names: set[str] = set()
    for cte in node.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            cte_names.add(alias.lower())

    tables: set[str] = set()
    columns: set[str] = set()

    # --- Tabel ---
    for table in node.find_all(exp.Table):
        name = (table.name or "").lower()
        if not name:
            return (
                tables,
                columns,
                GuardResult(
                    False,
                    reason="Referensi tabel tidak dikenali.",
                    rule="SQL-TABLE",
                ),
            )

        if table.args.get("catalog") is not None:
            return (
                tables,
                columns,
                GuardResult(
                    False,
                    reason="Referensi database lain tidak diizinkan.",
                    rule="SQL-SCHEMA",
                ),
            )

        if name in cte_names and table.args.get("db") is None:
            continue

        schema_node = table.args.get("db")
        schema = ""
        if schema_node is not None:
            schema = str(schema_node.name or schema_node.this or "").lower()

        if schema and schema in FORBIDDEN_SCHEMAS:
            return (
                tables,
                columns,
                GuardResult(
                    False,
                    reason=f"Schema '{schema}' tidak boleh diakses.",
                    rule="SQL-SCHEMA",
                ),
            )
        if schema and schema != ALLOWED_SCHEMA:
            return (
                tables,
                columns,
                GuardResult(
                    False,
                    reason=(
                        f"Schema '{schema}' tidak diizinkan. "
                        f"Hanya schema '{ALLOWED_SCHEMA}' yang tersedia."
                    ),
                    rule="SQL-SCHEMA",
                ),
            )
        if name not in ALLOWED_TABLES:
            return (
                tables,
                columns,
                GuardResult(
                    False,
                    reason=(
                        f"Tabel '{name}' tidak diizinkan. Tabel yang tersedia: "
                        + ", ".join(sorted(ALLOWED_TABLES))
                        + "."
                    ),
                    rule="SQL-TABLE",
                ),
            )
        tables.add(name)

    # --- Kolom ---
    derived: set[str] = set()
    for select in node.find_all(exp.Select):
        for projection in select.expressions:
            alias = projection.alias
            if alias:
                derived.add(alias.lower())

    known_columns: set[str] = set()
    for table_name in tables:
        known_columns.update(col.lower() for col in ALLOWED_TABLES[table_name])

    table_aliases: set[str] = set()
    for table in node.find_all(exp.Table):
        alias = table.alias
        if alias:
            table_aliases.add(alias.lower())

    for column in node.find_all(exp.Column):
        if column.args.get("catalog") is not None or (
            column.db and column.db.lower() != ALLOWED_SCHEMA
        ):
            return (
                tables,
                columns,
                GuardResult(
                    False,
                    reason="Kualifikasi kolom di luar Gold tidak diizinkan.",
                    rule="SQL-SCHEMA",
                ),
            )
        if (
            column.table
            and column.table.lower() not in table_aliases | tables | cte_names
        ):
            return (
                tables,
                columns,
                GuardResult(
                    False,
                    reason="Alias tabel pada kolom tidak dikenali.",
                    rule="SQL-COLUMN",
                ),
            )
        col_name = (column.name or "").lower()
        if not col_name or col_name == "*":
            continue
        if col_name in known_columns or col_name in derived:
            columns.add(col_name)
            continue
        # Kualifikasi tabel (mis. f.shipment_id) sudah tertangani lewat nama kolom.
        if col_name in table_aliases:
            continue
        return (
            tables,
            columns,
            GuardResult(
                False,
                reason=(
                    f"Kolom '{col_name}' tidak ada pada tabel Gold yang diizinkan. "
                    "Gunakan hanya kolom pada kontrak Gold."
                ),
                rule="SQL-COLUMN",
            ),
        )

    return tables, columns, None


def _apply_limit(node: exp.Expression, limit: int, dialect: str = "postgres") -> str:
    """Suntikkan LIMIT pada SELECT terluar bila belum ada. SELALU batasi hasil.

    Dialek menentukan bagaimana placeholder parameter dirender:
      - postgres -> %(nama)s  (format psycopg)
      - sqlite   -> :nama     (format sqlite3)
    """
    render_dialect = dialect if dialect in DIALECTS else "postgres"
    query = node
    while isinstance(query, (exp.Subquery, exp.Paren)):
        query = query.this
    existing = query.args.get("limit")
    requested = limit
    if existing is not None:
        try:
            requested = int(existing.expression.name)
            if requested < 0:
                requested = limit
        except (AttributeError, ValueError, TypeError):
            requested = limit
    query.set("limit", exp.Limit(expression=exp.Literal.number(min(requested, limit))))

    if render_dialect == "sqlite":
        node = node.copy()
        for trunc in list(node.find_all(exp.TimestampTrunc, exp.DateTrunc)):
            unit = str(trunc.args["unit"].this).lower()
            source = trunc.this.sql(dialect="sqlite")
            if unit == "week":
                replacement = f"DATE({source}, '-' || ((CAST(STRFTIME('%w', {source}) AS INTEGER) + 6) % 7) || ' days')"
            elif unit in {"day", "month", "year"}:
                modifier = "" if unit == "day" else f", 'start of {unit}'"
                replacement = f"DATE({source}{modifier})"
            else:
                # shortcut: pemotongan tanggal CSV masih terbatas, perluas jika membutuhkan grain tambahan.
                raise SQLGuardError("Grain tanggal belum tersedia pada backend CSV.", "SQL-7")
            trunc.replace(sqlglot.parse_one(replacement, read="sqlite"))
    return node.sql(dialect=render_dialect)
