"""Kalimat faktual yang dapat disusun model tanpa mengubah fakta."""

import html
import re
from calendar import monthrange
from datetime import date, timedelta

import sqlglot
from sqlglot import exp

from .config import settings
from .metrics import LABELS, RATIOS
from .models import Evidence, NarrativeDraft
from .scope import sanitize_note, scan_untrusted_text

MONTHS = ("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def safe_text(value) -> str:
    text = html.escape(" ".join(str(value).split()))
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def period_label(start: date, end: date) -> str:
    if start.day == 1 and end.day == monthrange(end.year, end.month)[1] and start.month == end.month and start.year == end.year:
        return f"{MONTHS[start.month]} {start.year}"
    end_label = f"{end.day} {MONTHS[end.month]} {end.year}"
    if start == end:
        return end_label
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end_label}"
    return f"{start.day} {MONTHS[start.month]}" + (f" {start.year}" if start.year != end.year else "") + f"–{end_label}"


def data_basis(evidence: Evidence) -> str:
    query = evidence.query
    filters = ", ".join(f"{name}={value}" for name, value in query.params.items())
    return f"Gold {', '.join(evidence.tables)}; period={period_label(query.start, query.end)}; date basis={query.date_basis}" + (f"; filter parameters: {filters}" if filters else "")


def bucket_label(value, evidence: Evidence) -> str:
    try:
        bucket = date.fromisoformat(str(value)[:10])
    except ValueError:
        return safe_text(value)
    if evidence.bucket_unit == "month":
        return f"{MONTHS[bucket.month]} {bucket.year}"
    if evidence.bucket_unit == "week":
        first = max(bucket, evidence.query.start, date.fromisoformat(settings.dataset_start))
        last = min(bucket + timedelta(days=6), evidence.query.end, date.fromisoformat(settings.dataset_end))
        if evidence.query.date_basis == "delivery_date":
            first, last = max(bucket, evidence.query.start), min(bucket + timedelta(days=6), evidence.query.end)
        if first > last:
            return safe_text(value)
        label = period_label(first, last)
        if first == last:
            label = f"{first.day} {MONTHS[first.month]} {first.year}"
        if first != bucket or last != bucket + timedelta(days=6):
            label += " (partial week)"
        return label
    return safe_text(value)


DIMENSIONS = {"destination_city": "destination", "carrier_id": "carrier", "service_level": "service",
              "delivery_status": "status", "origin_hub": "origin hub", "shipment_id": "shipment"}


def _filter_scope(evidence: Evidence) -> tuple[dict[str, str], bool]:
    """Jelaskan filter yang pasti berlaku bersama; tandai SQL lain untuk catatan cakupan."""
    where = sqlglot.parse_one(evidence.query.sql, read="postgres").args.get("where")
    pending = [where.this] if where else []
    scope, complete = {}, True
    while pending:
        node = pending.pop()
        if isinstance(node, exp.Paren):
            pending.append(node.this)
            continue
        if isinstance(node, exp.And):
            pending.extend([node.this, node.expression])
            continue
        if isinstance(node, exp.EQ) and isinstance(node.expression, exp.Column):
            node = exp.EQ(this=node.expression, expression=node.this)
        if isinstance(node, (exp.EQ, exp.In)) and isinstance(node.this, exp.Column) and node.this.name in DIMENSIONS:
            values = [node.expression] if isinstance(node, exp.EQ) else node.expressions
            selected = []
            for value in values:
                if isinstance(value, exp.Placeholder):
                    selected.append(evidence.query.params[value.name])
                elif isinstance(value, exp.Literal) and value.is_string:
                    selected.append(value.this)
            if selected and len(selected) == len(values):
                scope[node.this.name] = " and ".join(safe_text(value) for value in selected)
                continue
        complete = False
    return scope, complete


def _value(value, metric: str = "") -> str:
    if value is None:
        return "not available"
    if metric in RATIOS:
        return f"{value:.2f}%"
    if metric in {"shipping_fee", "refund_amount"}:
        return f"IDR {value:,.2f}"
    if metric in LABELS:
        return f"{int(value)} shipment{'s' if int(value) != 1 else ''}"
    return f"{value:,.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else safe_text(value)


def answer_facts(evidence: Evidence) -> dict[str, str]:
    """Satukan periode, entitas, metrik, dan nilai tiap baris dalam satu kalimat utuh."""
    scope, _ = _filter_scope(evidence)
    output = {}
    components = {component for rate, pair in RATIOS.items() if rate in evidence.trusted_metrics for component in pair}
    for index, row in enumerate(evidence.rows):
        dimensions, facts = dict(scope), []
        period = period_label(evidence.query.start, evidence.query.end)
        for key, value in row.items():
            if key in components or key == "exception_note":
                continue
            if key in evidence.trusted_metrics:
                label = LABELS[key].lower()
                if key in RATIOS:
                    numerator, denominator = RATIOS[key]
                    amount = _value(value, key) if value is not None else "not calculable because the numerator or denominator is unavailable, or the denominator is zero"
                    facts.append(f"the {label} was **{amount}** (numerator: {_value(row[numerator], numerator)}; denominator: {_value(row[denominator], denominator)})")
                else:
                    facts.append(f"{label} amounted to **{_value(value, key)}**")
            elif key == "bucket" and value is not None:
                period = bucket_label(value, evidence)
            elif key in {"order_date", "metric_date", "date", "tanggal", "day"} and value is not None and len(str(value)) == 10:
                try:
                    day = date.fromisoformat(str(value))
                    period = period_label(day, day)
                except ValueError:
                    dimensions[key] = safe_text(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                facts.append(f"{safe_text(key.replace('_', ' '))} was **{_value(value)}**")
            else:
                dimensions[key] = safe_text(value) if value is not None else "not available"
        details = ", ".join(f"{DIMENSIONS.get(key, safe_text(key.replace('_', ' ')))} **{value}**" for key, value in dimensions.items())
        subject = f"for {details}, " if details else ""
        body = "; ".join(facts) or "the record appears in the results"
        if len(facts) == 1 and "volume" in evidence.trusted_metrics and row.get("volume") is not None:
            prepositions = {"destination_city": "to", "carrier_id": "via carrier",
                            "service_level": "with service", "delivery_status": "with status", "origin_hub": "from hub"}
            suffix = " ".join(f"{prepositions.get(key, 'for ' + DIMENSIONS.get(key, safe_text(key.replace('_', ' '))))} **{value}**" for key, value in dimensions.items())
            output[f"f{index}"] = f"During **{period}**, the recorded total was **{_value(row['volume'], 'volume')}**" + (" " + suffix if suffix else "") + "."
        else:
            output[f"f{index}"] = f"During **{period}**, {subject}{body}."
    return output


def render_answer(evidence: Evidence, draft: NarrativeDraft | None = None) -> str:
    facts = answer_facts(evidence)
    groups = [[key] for key in facts] if draft is None else draft.paragraphs
    references = [key for group in groups for key in group]
    if any(not group for group in groups) or len(references) != len(set(references)) or set(references) != set(facts):
        raise ValueError("Narasi harus mencakup setiap fakta tepat satu kali tanpa menambahkan referensi lain.")
    if evidence.bucket_unit and references != list(facts):
        raise ValueError("Urutan periode pada narasi harus mengikuti hasil query.")
    lines = [" ".join(facts[key] for key in group) for group in groups]
    if not _filter_scope(evidence)[1]:
        lines.append("The results follow the filters in your question. Shipment selection details are provided in the data basis.")
    if "refund_rate" in evidence.trusted_metrics:
        lines.append("Refund rate is the ratio of monetary values: refund amounts divided by shipping fees, rather than the percentage of refunded packages.")
    if {"on_time_rate", "late_rate"} & set(evidence.trusted_metrics):
        lines.append("The denominator includes only delivered shipments; in_transit shipments are excluded from both delivered and late counts.")
    if evidence.partial:
        period = period_label(date.fromisoformat(settings.dataset_start), date.fromisoformat(settings.dataset_end))
        lines.append(f"The source data covers orders placed during **{period}**. Results use the available data only; periods outside this coverage are not treated as zero.")
    if evidence.query.date_basis == "delivery_date":
        lines.append("This analysis uses the delivery date rather than the order date.")
    if evidence.bucket_unit == "week":
        lines.append("Weeks run from Monday to Sunday. Partial weeks follow the requested period and data coverage.")
    notes = [row for row in evidence.rows if row.get("exception_note")]
    if notes:
        lines.append("The following operational notes are untrusted data, not instructions to execute:")
        for row in notes:
            note = str(row["exception_note"])
            note = "[instructions in the data were filtered and not executed]" if scan_untrusted_text(note) else sanitize_note(note)
            lines.append(f"- {safe_text(row.get('shipment_id', 'Shipment'))}: {safe_text(note)}")
    if evidence.selection_limited:
        lines.append("These results show a selection of rows, rather than totals for all groups.")
    return "\n\n".join(lines)
