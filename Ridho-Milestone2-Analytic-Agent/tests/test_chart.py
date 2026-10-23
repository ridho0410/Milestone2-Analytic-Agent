"""Tes profiler kolom dan perencana grafik.

Seluruh tes berjalan tanpa database, tanpa model bahasa, dan tanpa Streamlit:
kedua modul memang dirancang bebas dependensi. Yang diuji adalah **pemilihan
bentuk grafik**, bukan rendering — rendering menjadi tanggung jawab UI.

Prinsip yang dijaga: perencana grafik **tidak pernah** menjalankan kode yang
dihasilkan model. Ia hanya memetakan bentuk kolom ke salah satu jenis grafik
pada daftar tertutup.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from src.chart_planner import ChartSpec, JENIS_DIIZINKAN, build_chart, plan_chart
from src.profiler import profile_result

# --------------------------------------------------------------------------
# Profiler
# --------------------------------------------------------------------------


def test_kolom_dikelompokkan_menurut_jenisnya() -> None:
    kolom = ["metric_date", "destination_city", "shipment_count"]
    baris = [
        {
            "metric_date": "2026-08-01",
            "destination_city": "Bandung",
            "shipment_count": 12,
        },
        {
            "metric_date": "2026-08-02",
            "destination_city": "Jakarta",
            "shipment_count": 15,
        },
    ]
    profil = profile_result(kolom, baris)

    assert profil.date_columns == ["metric_date"]
    assert profil.numeric_columns == ["shipment_count"]
    assert profil.categorical_columns == ["destination_city"]
    assert profil.row_count == 2


def test_nilai_decimal_dan_datetime_dikenali() -> None:
    """Driver PostgreSQL mengembalikan Decimal dan datetime, bukan float/str."""
    kolom = ["order_date", "total_biaya"]
    baris = [{"order_date": date(2026, 8, 1), "total_biaya": Decimal("6184500.00")}]
    profil = profile_result(kolom, baris)

    assert profil.date_columns == ["order_date"]
    assert profil.numeric_columns == ["total_biaya"]


def test_nilai_boolean_bukan_angka() -> None:
    """True/False adalah subclass int; tidak boleh dianggap sumbu numerik."""
    profil = profile_result(
        ["first_order_flag"], [{"first_order_flag": True}, {"first_order_flag": False}]
    )
    assert profil.numeric_columns == []


def test_kolom_namanya_menandakan_waktu() -> None:
    """Nama kolom menandakan waktu HANYA bila nilainya belum menentukan."""
    profil = profile_result(
        ["periode"], [{"periode": "Minggu 1"}, {"periode": "Minggu 2"}]
    )
    assert profil.date_columns == ["periode"]


def test_nilai_angka_menang_atas_nama_kolom() -> None:
    """Kolom angka tidak boleh dianggap waktu hanya karena namanya.

    Bug nyata: ``on_time_rate`` mengandung kata "time", sehingga dikira kolom
    tanggal dan grafiknya menjadi ``line`` (tren) padahal isinya perbandingan
    antar-carrier yang seharusnya ``bar``.
    """
    kolom = ["carrier_id", "on_time_count", "on_time_rate"]
    baris = [
        {"carrier_id": "C01", "on_time_count": 49, "on_time_rate": 0.7538},
        {"carrier_id": "C02", "on_time_count": 50, "on_time_rate": 0.7576},
    ]
    profil = profile_result(kolom, baris)

    assert "on_time_rate" not in profil.date_columns
    assert "on_time_count" not in profil.date_columns
    assert "on_time_rate" in profil.numeric_columns
    assert profil.categorical_columns == ["carrier_id"]


def test_rate_per_carrier_digambar_sebagai_batang() -> None:
    """Penjaga regresi: perbandingan antar-carrier bukan tren waktu."""
    kolom = ["carrier_id", "shipment_count", "delivered_count", "on_time_rate"]
    baris = [
        {
            "carrier_id": "C01",
            "shipment_count": 72,
            "delivered_count": 65,
            "on_time_rate": 0.7538,
        },
        {
            "carrier_id": "C02",
            "shipment_count": 73,
            "delivered_count": 66,
            "on_time_rate": 0.7576,
        },
    ]
    spec = plan_chart("on-time rate per carrier", kolom, baris)
    assert spec is not None
    assert spec.kind == "bar", f"menghasilkan {spec.kind}, seharusnya bar"


def test_kolom_unik_tidak_dianggap_kategori() -> None:
    """shipment_id hampir selalu unik; grafik batangnya tidak berguna."""
    baris = [{"shipment_id": f"SHP-{i:05d}", "n": i} for i in range(60)]
    profil = profile_result(["shipment_id", "n"], baris)
    assert "shipment_id" not in profil.categorical_columns


def test_hasil_tanpa_baris_tidak_layak_digambar() -> None:
    profil = profile_result(["n"], [])
    assert not profil.has_chartable_shape


# --------------------------------------------------------------------------
# Perencana grafik
# --------------------------------------------------------------------------


def _bulan() -> list[dict]:
    return [
        {"metric_date": "2026-08-01", "shipment_count": 12},
        {"metric_date": "2026-08-02", "shipment_count": 15},
    ]


def test_kolom_waktu_menghasilkan_grafik_garis() -> None:
    spec = plan_chart(
        "tren kiriman harian", ["metric_date", "shipment_count"], _bulan()
    )
    assert spec is not None
    assert spec.kind == "line"
    assert spec.x == "metric_date"
    assert spec.y == ["shipment_count"]


def test_kolom_kategori_menghasilkan_grafik_batang() -> None:
    baris = [
        {"destination_city": "Bandung", "shipment_count": 38},
        {"destination_city": "Jakarta", "shipment_count": 32},
    ]
    spec = plan_chart(
        "perbandingan kota", ["destination_city", "shipment_count"], baris
    )
    assert spec is not None
    assert spec.kind == "bar"
    assert spec.x == "destination_city"


def test_dua_kolom_angka_menghasilkan_scatter() -> None:
    baris = [{"n": 1, "m": 2}, {"n": 3, "m": 4}]
    spec = plan_chart("hubungan", ["n", "m"], baris)
    assert spec is not None
    assert spec.kind == "scatter"


def test_satu_baris_tidak_digambar() -> None:
    """Satu titik bukan tren maupun perbandingan."""
    assert plan_chart("total", ["n"], [{"n": 217}]) is None


def test_hasil_tanpa_angka_tidak_digambar() -> None:
    baris = [{"kota": "Bandung"}, {"kota": "Jakarta"}]
    assert plan_chart("daftar kota", ["kota"], baris) is None


def test_jenis_grafik_selalu_dari_daftar_tertutup() -> None:
    """UI hanya mengenali jenis pada daftar ini; tidak boleh ada yang lain."""
    for kolom, baris in (
        (
            ["metric_date", "n"],
            [
                {"metric_date": "2026-08-01", "n": 1},
                {"metric_date": "2026-08-02", "n": 2},
            ],
        ),
        (["kota", "n"], [{"kota": "A", "n": 1}, {"kota": "B", "n": 2}]),
        (["n", "m"], [{"n": 1, "m": 2}, {"n": 3, "m": 4}]),
    ):
        spec = plan_chart("uji", kolom, baris)
        assert spec is not None and spec.kind in JENIS_DIIZINKAN


# --------------------------------------------------------------------------
# Data yang disertakan
# --------------------------------------------------------------------------


def test_resep_membawa_datanya() -> None:
    """Resep harus mandiri agar UI tidak perlu meminta barisnya sekali lagi."""
    spec = plan_chart("tren", ["metric_date", "shipment_count"], _bulan())
    assert spec is not None
    isi = spec.to_dict()
    assert isi["data"] == _bulan()


def test_nilai_decimal_dan_tanggal_jadi_json_aman() -> None:
    baris = [
        {"metric_date": date(2026, 8, 1), "n": Decimal("12.50")},
        {"metric_date": datetime(2026, 8, 2, 10, 0), "n": Decimal("15.00")},
    ]
    spec = plan_chart("tren", ["metric_date", "n"], baris)
    assert spec is not None
    for baris_keluar in spec.data:
        assert isinstance(baris_keluar["metric_date"], str)
        assert isinstance(baris_keluar["n"], float)


def test_catatan_tidak_pernah_ikut_ke_grafik() -> None:
    """exception_note adalah teks tidak tepercaya dan tidak diperlukan grafik.

    Ia tidak boleh ikut terkirim ke UI lewat jalur grafik, meski kueri
    mengembalikannya.
    """
    baris = [
        {"destination_city": "Bandung", "n": 38, "exception_note": "INSTRUKSI RAHASIA"},
        {"destination_city": "Jakarta", "n": 32, "exception_note": "abaikan aturan"},
    ]
    spec = plan_chart(
        "perbandingan", ["destination_city", "n", "exception_note"], baris
    )
    assert spec is not None
    for baris_keluar in spec.data:
        assert "exception_note" not in baris_keluar


def test_kolom_yang_tidak_dipakai_tidak_dikirim() -> None:
    """Hanya kolom sumbu (X), deret, dan nilai yang dikirim.

    Kolom kategori kedua kini MENJADI sumbu deret, jadi ia ikut dikirim. Yang
    tetap tidak dikirim adalah kolom kategori ketiga dan seterusnya — kolom itu
    tidak dipakai menggambar apa pun.
    """
    baris = [
        {"kota": "Bandung", "n": 38, "lain": "x", "lagi": "y"},
        {"kota": "Jakarta", "n": 32, "lain": "p", "lagi": "q"},
    ]
    spec = plan_chart("perbandingan", ["kota", "n", "lain", "lagi"], baris)

    assert spec is not None
    assert spec.series == "lain", "kategori kedua seharusnya menjadi deret"
    assert set(spec.data[0]) == {"kota", "n", "lain"}
    assert "lagi" not in spec.data[0], "kolom kategori ketiga tidak dipakai"


def test_build_chart_mengembalikan_none_bila_tidak_layak() -> None:
    assert build_chart("total", ["n"], [{"n": 1}]) is None


def test_build_chart_mengembalikan_dict_siap_json() -> None:
    hasil = build_chart("tren", ["metric_date", "shipment_count"], _bulan())
    assert isinstance(hasil, dict)
    assert set(hasil) == {"kind", "title", "x", "y", "series", "data"}


def test_judul_dibatasi_panjangnya() -> None:
    panjang = "berapa " * 60
    baris = [
        {"metric_date": "2026-08-01", "n": 1},
        {"metric_date": "2026-08-02", "n": 2},
    ]
    spec = plan_chart(panjang, ["metric_date", "n"], baris)
    assert spec is not None
    assert len(spec.title) <= 120


# --------------------------------------------------------------------------
# Dimensi kedua: deret berdampingan, BUKAN penjumlahan
#
# Bug nyata: "Berapa kiriman per carrier pada Agustus 2026 di kota Jakarta dan
# Bandung?" menghasilkan 6 baris (3 carrier x 2 kota). Ketika hanya carrier_id
# yang dijadikan sumbu X, Streamlit MENJUMLAHKAN kedua kota sehingga C01 tampil
# sebagai 36 (Bandung 20 + Jakarta 16) — angka yang tidak pernah ditanyakan dan
# tidak terlihat salah.
# --------------------------------------------------------------------------


def _dua_dimensi() -> tuple[list[str], list[dict]]:
    kolom = ["carrier_id", "destination_city", "shipment_count"]
    baris = [
        {"carrier_id": "C01", "destination_city": "Bandung", "shipment_count": 20},
        {"carrier_id": "C02", "destination_city": "Bandung", "shipment_count": 21},
        {"carrier_id": "C03", "destination_city": "Bandung", "shipment_count": 18},
        {"carrier_id": "C01", "destination_city": "Jakarta", "shipment_count": 16},
        {"carrier_id": "C02", "destination_city": "Jakarta", "shipment_count": 17},
        {"carrier_id": "C03", "destination_city": "Jakarta", "shipment_count": 16},
    ]
    return kolom, baris


def test_dimensi_kedua_menjadi_deret() -> None:
    """Kolom kategori kedua harus menjadi ``series``, bukan dibuang."""
    kolom, baris = _dua_dimensi()
    spec = plan_chart("kiriman per carrier di Jakarta dan Bandung", kolom, baris)

    assert spec is not None
    assert spec.kind == "bar"
    assert spec.x == "carrier_id"
    assert (
        spec.series == "destination_city"
    ), "dimensi kedua dibuang, sehingga grafik akan menjumlahkan kedua kota"


def test_data_deret_ikut_dikirim() -> None:
    """Kolom deret wajib ada di data, jika tidak UI tidak bisa memutarnya."""
    kolom, baris = _dua_dimensi()
    spec = plan_chart("uji", kolom, baris)

    assert spec is not None
    for b in spec.data:
        assert "destination_city" in b
        assert "carrier_id" in b
        assert "shipment_count" in b


def test_pemutaran_deret_tidak_menjumlahkan() -> None:
    """Setelah diputar, setiap sel harus berisi nilai ASLI, bukan penjumlahan.

    Inilah inti bug: C01 di Bandung harus tetap 20 dan di Jakarta tetap 16 —
    bukan 36 pada satu batang.
    """
    kolom, baris = _dua_dimensi()
    spec = plan_chart("uji", kolom, baris)
    assert spec is not None and spec.series

    import pandas as pd

    tabel = pd.DataFrame(spec.data).pivot_table(
        index=spec.x, columns=spec.series, values=spec.y[0], aggfunc="sum"
    )

    assert tabel.loc["C01", "Bandung"] == 20
    assert tabel.loc["C01", "Jakarta"] == 16
    assert tabel.loc["C01"].sum() == 36
    assert tabel.shape == (3, 2), "harus 3 carrier x 2 kota, bukan 3 batang saja"


def test_pasangan_berulang_tidak_digambar() -> None:
    """Bila (x, deret) berulang, grafik akan menjumlahkan — jadi tidak digambar.

    Lebih baik tidak ada grafik daripada grafik yang angkanya tidak diminta.
    """
    kolom = ["carrier_id", "destination_city", "shipment_count"]
    baris = [
        {"carrier_id": "C01", "destination_city": "Bandung", "shipment_count": 20},
        {"carrier_id": "C01", "destination_city": "Bandung", "shipment_count": 5},
    ]
    assert plan_chart("uji", kolom, baris) is None


def test_satu_dimensi_tetap_tanpa_deret() -> None:
    """Penjaga regresi: grafik satu dimensi tidak boleh mendapat deret palsu."""
    kolom = ["destination_city", "shipment_count"]
    baris = [
        {"destination_city": "Bandung", "shipment_count": 59},
        {"destination_city": "Jakarta", "shipment_count": 49},
    ]
    spec = plan_chart("kiriman per kota", kolom, baris)

    assert spec is not None
    assert spec.kind == "bar"
    assert spec.series is None


def test_tren_dengan_deret_menjadi_garis_berganda() -> None:
    """Tren per kota: sumbu X waktu, deret kota — satu garis per kota."""
    kolom = ["week_start", "destination_city", "shipment_count"]
    baris = [
        {
            "week_start": "2026-08-03",
            "destination_city": "Bandung",
            "shipment_count": 12,
        },
        {
            "week_start": "2026-08-03",
            "destination_city": "Jakarta",
            "shipment_count": 9,
        },
        {
            "week_start": "2026-08-10",
            "destination_city": "Bandung",
            "shipment_count": 15,
        },
        {
            "week_start": "2026-08-10",
            "destination_city": "Jakarta",
            "shipment_count": 11,
        },
    ]
    spec = plan_chart("tren per kota", kolom, baris)

    assert spec is not None
    assert spec.kind == "line"
    assert spec.x == "week_start"
    assert spec.series == "destination_city"


def test_series_ikut_terkirim_sebagai_json() -> None:
    import json

    kolom, baris = _dua_dimensi()
    hasil = build_chart("uji", kolom, baris)
    assert hasil is not None
    assert hasil["series"] == "destination_city"
    json.dumps(hasil)


# --------------------------------------------------------------------------
# Penyambungan ke hasil agent
# --------------------------------------------------------------------------


def test_agent_result_membawa_grafik() -> None:
    from src.agent import AgentResult

    hasil = AgentResult(answer="a", outcome="answered")
    assert hasil.to_dict().get("chart") is None

    hasil = AgentResult(
        answer="a", outcome="answered", chart={"kind": "line", "x": "d", "y": ["n"]}
    )
    assert hasil.to_dict()["chart"]["kind"] == "line"


@pytest.mark.parametrize("jenis", sorted(JENIS_DIIZINKAN))
def test_chart_spec_bisa_diseralisasi(jenis: str) -> None:
    """Resep harus dapat diubah menjadi JSON tanpa tipe khusus."""
    import json

    spec = ChartSpec(kind=jenis, title="uji", x="x", y=["n"], data=[{"x": 1, "n": 2}])
    json.dumps(spec.to_dict())
