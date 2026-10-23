"""Validasi SQL model sebelum kueri hanya baca, tanpa kompilasi templat pertanyaan."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal, DecimalException

import sqlglot
from sqlglot import exp

from .config import Settings, settings
from .gold import GoldClient, GoldError
from .metrics import RATIOS, expressions, percentage
from .models import Evidence, SQLQuery
from .sql_guard import validate_or_raise


def _signature(expression: exp.Expression) -> str:
    expression = expression.copy()
    for column in expression.find_all(exp.Column):
        column.set("table", None)
    for parenthesis in list(expression.find_all(exp.Paren)):
        parenthesis.replace(parenthesis.this)
    for literal in expression.find_all(exp.Literal):
        if literal.is_number:
            try:
                value = Decimal(literal.this)
                if not value.is_finite() or len(literal.this) > 64 or abs(value.adjusted()) > 20:
                    raise ValueError("Literal numerik SQL melebihi batas analitik.")
                literal.set("this", str(value.normalize()))
            except DecimalException as exc:
                raise ValueError("Literal numerik SQL tidak valid.") from exc
    return expression.sql(dialect="postgres", normalize=True)


class QueryEngine:
    def __init__(self, gold: GoldClient, limits: Settings = settings) -> None:
        self.gold, self.limits = gold, limits

    def prepare(self, query: SQLQuery) -> tuple[str, dict, list[str], str]:
        query = SQLQuery.model_validate(query.model_dump())
        validate_or_raise(query.sql, row_limit=self.limits.gold_row_limit)
        tree = sqlglot.parse_one(query.sql, read="postgres")
        _signature(tree)
        # Satu sumber mencegah duplikasi dan penjumlahan antar-grain tanpa kompiler SQL tambahan.
        if (not isinstance(tree, exp.Select) or len(list(tree.find_all(exp.Select))) != 1
                or len(list(tree.find_all(exp.Table))) != 1 or tree.args.get("joins")
                or tree.args.get("with_") or tree.find(exp.Window)):
            raise ValueError("Gunakan satu SELECT dan satu tabel Gold, tanpa join, CTE, subquery, atau window.")
        table = next(tree.find_all(exp.Table))
        if table.db != "gold":
            raise ValueError("Gunakan nama tabel berkualifikasi gold.")
        placeholders = {node.name for node in tree.find_all(exp.Placeholder)}
        if placeholders != set(query.params):
            raise ValueError("Parameter harus tepat sama dengan placeholder :nama pada SQL.")
        date_column = "order_date" if table.name == "shipment_fact" else "metric_date"
        if query.date_basis == "delivery_date":
            if table.name != "shipment_fact":
                raise ValueError("Basis delivery hanya tersedia pada shipment_fact.")
            date_column = "delivered_at_utc"
        where = tree.args.get("where")
        if where and any(c.name in {"order_date", "metric_date", "delivered_at_utc"}
                         for c in where.find_all(exp.Column)):
            raise ValueError("Tanggal cohort diatur oleh start/end/date_basis; jangan filter tanggal lagi di WHERE.")
        names, trusted, bucket_unit = [], [], ""
        formulas = expressions(table.name)
        for projection in tree.expressions:
            name = projection.alias_or_name
            if not name or len(name) > 100 or name in names:
                raise ValueError("Setiap kolom hasil harus memiliki nama unik, maksimum 100 karakter.")
            names.append(name)
            expression = projection.this if isinstance(projection, exp.Alias) else projection
            if any(parameter.find_ancestor(exp.Predicate) is None for parameter in expression.find_all(exp.Placeholder)):
                raise ValueError("Parameter proyeksi hanya boleh menjadi nilai pembanding dalam kondisi, bukan angka hasil.")
            for fallback in expression.find_all(exp.Coalesce):
                if isinstance(fallback.this, (exp.Literal, exp.Boolean)):
                    raise ValueError("COALESCE tidak boleh menutupi sumber data dengan konstanta non-NULL.")
            if not expression.find(exp.Column) and not expression.find(exp.Count):
                raise ValueError("Kolom fakta harus berasal dari data Gold, bukan konstanta.")
            for operation in expression.find_all(exp.Mul, exp.Sub, exp.Div):
                if (isinstance(operation, exp.Mul) and any(
                        isinstance(part, exp.Literal) and part.is_number and Decimal(part.this) == 0
                        for part in (operation.this, operation.expression))
                        or isinstance(operation, (exp.Sub, exp.Div))
                        and _signature(operation.this) == _signature(operation.expression)):
                    raise ValueError("Ekspresi meniadakan sumber data dan menghasilkan konstanta.")
            if name in formulas:
                candidate = expression
                if isinstance(candidate, exp.Round) and str(candidate.args.get("decimals")) == "2":
                    candidate = candidate.this
                valid = {_signature(sqlglot.parse_one(formulas[name], read="postgres"))}
                if name == "volume" and table.name == "shipment_fact":
                    valid.add(_signature(sqlglot.parse_one("COUNT(*)")))
                if _signature(candidate) not in valid:
                    raise ValueError(f"Rumus alias {name} harus mengikuti definisi kanonis: {formulas[name]}.")
                trusted.append(name)
            if name == "bucket":
                bucket = expression
                if isinstance(bucket, exp.Cast) and bucket.args["to"].is_type(exp.DataType.Type.DATE):
                    bucket = bucket.this
                if isinstance(bucket, (exp.DateTrunc, exp.TimestampTrunc)):
                    bucket_unit = str(bucket.args["unit"].this).lower()
                    bucket = bucket.this
                if not isinstance(bucket, exp.Column) or bucket.name != date_column:
                    raise ValueError("bucket harus memakai tanggal sesuai date_basis, langsung atau DATE_TRUNC tanpa modifikasi.")
            if expression.find(exp.Column) and any(c.name == "exception_note" for c in expression.find_all(exp.Column)):
                if not isinstance(expression, exp.Column) or expression.name != "exception_note" or name != "exception_note":
                    raise ValueError("Catatan hanya boleh dipilih langsung sebagai exception_note.")
        for metric, (numerator, denominator) in RATIOS.items():
            if metric in trusted and not {numerator, denominator} <= set(trusted):
                raise ValueError(f"Sertakan {numerator} dan {denominator} untuk memverifikasi {metric}.")
        # Aplikasi menentukan dan mengikat sendiri rentang tanggal yang ditampilkan.
        column = exp.column(date_column, table=table.alias_or_name)
        if query.date_basis == "delivery_date":
            column = exp.Cast(this=column, to=exp.DataType.build("DATE"))
        params = dict(query.params, _scope_start=query.start.isoformat(), _scope_end=query.end.isoformat())
        predicate = exp.and_(exp.GTE(this=column.copy(), expression=exp.Placeholder(this="_scope_start")),
                             exp.LTE(this=column, expression=exp.Placeholder(this="_scope_end")))
        tree = tree.where(predicate)
        sql = tree.sql(dialect="postgres")
        validate_or_raise(sql, row_limit=self.limits.gold_row_limit)
        return sql, params, trusted, bucket_unit

    def execute(self, query: SQLQuery) -> Evidence:
        sql, params, trusted, bucket_unit = self.prepare(query)
        result = self.gold.execute(sql, params=params, row_limit=self.limits.gold_row_limit)
        if result.truncated:
            raise GoldError("Hasil terpotong; persempit filter atau kelompok.")
        if len(json.dumps(result.rows, default=str, ensure_ascii=False).encode("utf-8")) > self.limits.gold_result_byte_limit:
            raise GoldError("Hasil melampaui batas ukuran.")
        rows = []
        for source in result.rows:
            row = dict(source)
            if any(isinstance(value, (float, Decimal)) and not math.isfinite(value) for value in row.values()):
                raise ValueError("Hasil query memuat angka non-finite.")
            for metric, (numerator, denominator) in RATIOS.items():
                if metric in trusted:
                    expected = percentage(row[numerator], row[denominator])
                    actual = row[metric]
                    if (expected is None) != (actual is None) or (expected is not None and abs(Decimal(str(actual)) - expected) > Decimal("0.011")):
                        raise ValueError("Persentase tidak cocok dengan pembilang dan penyebut hasil query.")
                    row[metric] = expected
            rows.append({key: float(value) if isinstance(value, Decimal)
                         else value.isoformat() if isinstance(value, (date, datetime))
                         else value for key, value in row.items()})
        selection = sqlglot.parse_one(query.sql, read="postgres")
        return Evidence(query=query, rows=rows, tables=result.tables, trusted_metrics=trusted,
                        selection_limited=bool(selection.args.get("limit") or selection.args.get("offset")),
                        bucket_unit=bucket_unit, partial=query.date_basis == "delivery_date"
                        or query.start.isoformat() < self.limits.dataset_start
                        or query.end.isoformat() > self.limits.dataset_end)
