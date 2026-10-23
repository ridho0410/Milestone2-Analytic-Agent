"""Hasil kueri diperiksa terhadap perhitungan CSV yang independen."""

import csv
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytest

from src.answers import render_answer
from src.config import settings
from src.models import SQLQuery
from src.query_engine import QueryEngine
from src.metrics import expressions


def query(sql="SELECT COUNT(shipment_id) AS volume FROM gold.shipment_fact", **updates):
    return SQLQuery.model_validate(dict(sql=sql, start="2026-07-01", end="2026-09-30", **updates))


def source_rows():
    with (settings.data_dir / "shipment_fact.csv").open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def test_monthly_volume_uses_sql_without_a_new_grain_enum(gold):
    evidence = QueryEngine(gold).execute(query(
        "SELECT CAST(DATE_TRUNC('month', order_date) AS DATE) AS bucket, COUNT(shipment_id) AS volume "
        "FROM gold.shipment_fact GROUP BY 1 ORDER BY 1"))
    assert {row["bucket"][:7]: row["volume"] for row in evidence.rows} == {"2026-07": 243, "2026-08": 217, "2026-09": 140}
    assert sum(row["volume"] for row in evidence.rows) == len(source_rows())
    assert "September 2026" in render_answer(evidence) and evidence.partial


def test_express_september_refund_is_a_money_ratio(gold):
    formulas = expressions()
    evidence = QueryEngine(gold).execute(SQLQuery(
        sql="SELECT " + ", ".join(f"{formulas[name]} AS {name}" for name in ("refund_rate", "refund_amount", "shipping_fee"))
        + " FROM gold.shipment_fact WHERE service_level = :service", params={"service": "express"},
        start="2026-09-01", end="2026-09-30"))
    row = evidence.rows[0]
    assert row == {"refund_rate": 3.57, "refund_amount": 28500.0, "shipping_fee": 798000.0}
    answer = render_answer(evidence)
    assert all(word in answer for word in ("3.57%", "ratio of monetary values", "numerator", "denominator", "20 September 2026"))


@pytest.mark.parametrize("month", (7, 8, 9))
@pytest.mark.parametrize("city", (None, "Bandung", "Jakarta"))
@pytest.mark.parametrize("service", (None, "express"))
def test_metric_grid_matches_independent_csv(gold, month, city, service):
    formulas = expressions()
    names = list(formulas)
    filters, params = [], {}
    for name, value in (("destination_city", city), ("service_level", service)):
        if value:
            filters.append(f"{name} = :{name}")
            params[name] = value
    sql = "SELECT " + ", ".join(f"{formulas[name]} AS {name}" for name in names) + " FROM gold.shipment_fact"
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    first = date(2026, month, 1)
    last = date(2026, month + 1, 1) - timedelta(days=1)
    actual = QueryEngine(gold).execute(SQLQuery(sql=sql, params=params, start=first, end=last)).rows[0]
    rows = [row for row in source_rows() if first.isoformat() <= row["order_date"] <= last.isoformat()
            and (not city or row["destination_city"] == city) and (not service or row["service_level"] == service)]
    delivered = [row for row in rows if row["delivery_status"] == "delivered"]
    on_time = sum(row["delivered_at_utc"] <= row["promised_delivery_at_utc"] for row in delivered)
    late = len(delivered) - on_time
    fee = sum(Decimal(row["shipping_fee_idr"]) for row in rows)
    refund = sum(Decimal(row["refund_amount_idr"]) for row in rows)
    expected = {"volume": len(rows), "delivered": len(delivered), "on_time": on_time, "late": late,
                "in_transit": len(rows) - len(delivered), "shipping_fee": float(fee), "refund_amount": float(refund)}
    for name, numerator, denominator in (("on_time_rate", on_time, len(delivered)), ("late_rate", late, len(delivered)), ("refund_rate", refund, fee)):
        expected[name] = float((Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if denominator else None
    assert actual == expected


@pytest.mark.parametrize("unit", ("day", "week", "month"))
def test_time_buckets_match_independent_csv(gold, unit):
    evidence = QueryEngine(gold).execute(query(
        f"SELECT CAST(DATE_TRUNC('{unit}', order_date) AS DATE) AS bucket, COUNT(*) AS volume "
        "FROM gold.shipment_fact WHERE destination_city = :city GROUP BY 1 ORDER BY 1", params={"city": "Bandung"}))
    expected = {}
    for row in source_rows():
        if row["destination_city"] != "Bandung":
            continue
        day = date.fromisoformat(row["order_date"])
        bucket = day - timedelta(days=day.weekday()) if unit == "week" else day.replace(day=1) if unit == "month" else day
        expected[bucket.isoformat()] = expected.get(bucket.isoformat(), 0) + 1
    assert {row["bucket"][:10]: row["volume"] for row in evidence.rows} == expected
    if unit == "week":
        answer = render_answer(evidence)
        assert "1–5 July 2026 (partial week)" in answer and "29 June" not in answer


def test_new_computable_metric_needs_no_application_branch(gold):
    sql = "SELECT service_level, AVG(shipping_fee_idr) AS average_fee_idr FROM gold.shipment_fact GROUP BY service_level ORDER BY service_level"
    actual = QueryEngine(gold).execute(query(sql)).rows
    for row in actual:
        fees = [float(item["shipping_fee_idr"]) for item in source_rows() if item["service_level"] == row["service_level"]]
        assert row["average_fee_idr"] == pytest.approx(sum(fees) / len(fees))


@pytest.mark.parametrize("table", ("carrier_daily", "city_daily"))
def test_one_aggregate_table_has_same_canonical_totals(gold, table):
    formulas = expressions(table)
    sql = "SELECT " + ", ".join(f"{value} AS {name}" for name, value in formulas.items()) + " FROM gold." + table
    evidence = QueryEngine(gold).execute(query(sql))
    assert evidence.rows[0]["volume"] == 600


def test_runtime_date_scope_cannot_be_omitted_by_model(gold):
    evidence = QueryEngine(gold).execute(SQLQuery(sql="SELECT COUNT(*) AS volume FROM gold.shipment_fact", start="2026-08-01", end="2026-08-01"))
    assert evidence.rows == [{"volume": sum(row["order_date"] == "2026-08-01" for row in source_rows())}]


def test_carrier_ranking_and_delivered_threshold_use_gold_sql(gold):
    formulas = expressions()
    sql = "SELECT carrier_id, " + ", ".join(f"{formulas[name]} AS {name}" for name in ("on_time_rate", "on_time", "delivered"))
    sql += " FROM gold.shipment_fact GROUP BY carrier_id HAVING " + formulas["delivered"] + " >= :minimum ORDER BY " + formulas["on_time_rate"] + " DESC LIMIT 1"
    evidence = QueryEngine(gold).execute(SQLQuery(sql=sql, params={"minimum": 20}, start="2026-08-01", end="2026-08-31"))
    assert evidence.rows[0]["carrier_id"] == "C02" and evidence.rows[0]["on_time_rate"] == 75.76
    assert evidence.selection_limited
