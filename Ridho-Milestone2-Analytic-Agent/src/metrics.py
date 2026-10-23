"""Definisi bisnis yang terpisah dari susunan pertanyaan dan pengelompokan SQL."""

from decimal import Decimal, ROUND_HALF_UP

MEASURES = {
    "volume": "COUNT(shipment_id)",
    "delivered": "SUM(CASE WHEN delivery_status = 'delivered' THEN 1 ELSE 0 END)",
    "in_transit": "SUM(CASE WHEN delivery_status = 'in_transit' THEN 1 ELSE 0 END)",
    "on_time": "SUM(CASE WHEN delivery_status = 'delivered' AND delivered_at_utc <= promised_delivery_at_utc THEN 1 ELSE 0 END)",
    "late": "SUM(CASE WHEN delivery_status = 'delivered' AND delivered_at_utc > promised_delivery_at_utc THEN 1 ELSE 0 END)",
    "shipping_fee": "SUM(shipping_fee_idr)",
    "refund_amount": "SUM(refund_amount_idr)",
}
RATIOS = {
    "on_time_rate": ("on_time", "delivered"),
    "late_rate": ("late", "delivered"),
    "refund_rate": ("refund_amount", "shipping_fee"),
}
LABELS = {
    "volume": "Total shipments", "delivered": "Delivered shipments",
    "in_transit": "In-transit shipments", "on_time": "On-time shipments",
    "late": "Late shipments", "shipping_fee": "Total shipping fees",
    "refund_amount": "Total refunds", "on_time_rate": "On-time rate",
    "late_rate": "Late rate", "refund_rate": "Refund rate",
}


def expressions(table: str = "shipment_fact") -> dict[str, str]:
    measures = dict(MEASURES)
    if table != "shipment_fact":
        measures.update({
            "volume": "SUM(shipment_count)", "delivered": "SUM(delivered_count)",
            "in_transit": "SUM(in_transit_count)",
            "on_time": "SUM(on_time_delivered_count)", "late": "SUM(late_delivered_count)",
        })
    return {**measures, **{
        name: f"100.0 * {measures[numerator]} / NULLIF({measures[denominator]}, 0)"
        for name, (numerator, denominator) in RATIOS.items()
    }}


def percentage(numerator, denominator) -> Decimal | None:
    if numerator is None or not denominator:
        return None
    return (Decimal(str(numerator)) * 100 / Decimal(str(denominator))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
