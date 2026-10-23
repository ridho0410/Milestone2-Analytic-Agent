"""Pemilihan grafik yang deterministik dari hasil kueri.

Prinsipnya sama dengan pembentukan SQL di proyek ini: **model tidak menghasilkan
kode yang dapat dieksekusi.** Untuk grafik, model bahkan tidak dilibatkan sama
sekali — pemilihan dilakukan kode berdasarkan bentuk kolom hasil.

Jenjang pemilihannya sengaja kecil dan tertutup:

| Bentuk hasil | Grafik |
|---|---|
| ada kolom waktu | ``line`` — tren |
| ada kolom kategori | ``bar`` — perbandingan |
| ada dua kolom angka | ``scatter`` — hubungan |

Bila tidak ada yang cocok, hasilnya ``None`` dan UI hanya menampilkan tabel.
Menampilkan tidak ada grafik jauh lebih baik daripada memaksa grafik yang
menyesatkan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .profiler import profile_result

# Jenis grafik yang diizinkan. UI hanya mengenali nilai pada daftar ini.
JENIS_DIIZINKAN: frozenset[str] = frozenset({"line", "bar", "scatter"})

# Batas panjang judul grafik.
_MAKS_JUDUL = 90

# Batas baris yang dikirim untuk digambar. Cukup untuk tren mingguan, daftar
# kota, maupun daftar carrier; jauh di bawah GOLD_ROW_LIMIT sehingga tidak
# memperbesar respons secara berlebihan.
_MAKS_BARIS_GRAFIK = 200

# Kolom yang tidak perlu dikirim ke UI karena tidak dipakai grafik.
_KOLOM_DIABAIKAN = re.compile(r"(pipeline_run_id|exception_note)", re.IGNORECASE)


@dataclass(frozen=True)
class ChartSpec:
    """Resep grafik: jenis, judul, sumbu, deret, dan datanya.

    Data disertakan agar resep ini **mandiri** — UI dapat langsung menggambar
    tanpa meminta barisnya sekali lagi ke API.

    ``series`` adalah kolom dimensi KEDUA. Bila diisi, grafiknya harus
    menggambar satu batang per nilai ``series`` secara **berdampingan** pada
    setiap nilai ``x`` — bukan menjumlahkannya.
    """

    kind: str
    title: str
    x: str
    y: list[str] = field(default_factory=list)
    series: str | None = None
    data: list[dict[str, Any]] = field(default_factory=list)
    total_rows: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "kind": self.kind,
            "title": self.title,
            "x": self.x,
            "y": list(self.y),
            "series": self.series,
            "data": self.data,
        }
        if self.total_rows is not None and self.total_rows > len(self.data):
            result.update(
                truncated=True,
                total_rows=self.total_rows,
                displayed_rows=len(self.data),
            )
        return result


def _rapikan_baris(
    rows: list[dict[str, Any]], dipakai: list[str]
) -> list[dict[str, Any]]:
    """Ambil hanya kolom yang dipakai grafik, dengan nilai JSON-aman.

    Nilai ``Decimal`` dan ``datetime`` diubah menjadi bentuk yang dapat
    diserialisasi. Kolom catatan tidak pernah ikut: isinya teks tidak tepercaya
    dan tidak diperlukan grafik.
    """
    kolom = [k for k in dipakai if not _KOLOM_DIABAIKAN.search(k)]
    keluaran: list[dict[str, Any]] = []
    for baris in rows[:_MAKS_BARIS_GRAFIK]:
        isi: dict[str, Any] = {}
        for nama in kolom:
            nilai = baris.get(nama)
            if isinstance(nilai, Decimal):
                nilai = float(nilai)
            elif isinstance(nilai, (date, datetime)):
                nilai = nilai.isoformat()
            isi[nama] = nilai
        keluaran.append(isi)
    return keluaran


def _judul(question: str, akhiran: str) -> str:
    """Judul ringkas dari pertanyaan pengguna."""
    teks = " ".join(question.split())
    if len(teks) > _MAKS_JUDUL:
        teks = teks[: _MAKS_JUDUL - 1].rstrip() + "…"
    return f"{teks} ({akhiran})"


def _kombinasi_unik(rows: list[dict[str, Any]], x: str, series: str) -> bool:
    """Apakah setiap pasangan (x, series) muncul paling banyak sekali?

    Ini penjaga terpenting pada modul ini. Bila pasangannya berulang, grafik
    akan **menjumlahkan** baris-baris itu pada satu batang, dan angkanya menjadi
    salah tanpa terlihat salah.

    Kasus nyata: "berapa kiriman per carrier di kota Jakarta dan Bandung"
    menghasilkan 6 baris — tiga carrier dikali dua kota. Ketika hanya
    ``carrier_id`` yang dijadikan sumbu X, Streamlit menjumlahkan Bandung dan
    Jakarta, sehingga C01 tampil sebagai 36 (20 + 16) padahal jawaban sebenarnya
    adalah 20 dan 16. Karena itu, bila pasangannya tidak unik, lebih baik
    **tidak menampilkan grafik sama sekali**.
    """
    terlihat = {(str(b.get(x)), str(b.get(series))) for b in rows}
    return len(terlihat) == len(rows)


def plan_chart(
    question: str,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> ChartSpec | None:
    """Pilih grafik yang sesuai untuk hasil kueri, atau ``None``.

    Args:
        question: Pertanyaan pengguna, dipakai hanya untuk judul.
        columns: Nama kolom hasil.
        rows: Baris hasil.
    """
    bentuk = profile_result(columns, rows)
    if not bentuk.has_chartable_shape:
        return None

    angka = bentuk.numeric_columns[0]

    if bentuk.date_columns:
        x = bentuk.date_columns[0]
        # Kolom kategori kedua menjadi deret: satu garis per kota/carrier.
        series = bentuk.categorical_columns[0] if bentuk.categorical_columns else None
        if series and not _kombinasi_unik(rows, x, series):
            return None
        dipakai = [x, angka] + ([series] if series else [])
        return ChartSpec(
            kind="line",
            title=_judul(question, "trend"),
            x=x,
            y=[angka],
            series=series,
            data=_rapikan_baris(rows, dipakai),
            total_rows=len(rows),
        )

    if bentuk.categorical_columns:
        x = bentuk.categorical_columns[0]
        # Kolom kategori kedua menjadi deret: batang berdampingan, BUKAN
        # dijumlahkan. Tanpa ini, perbandingan antar-kota pada tiap carrier
        # runtuh menjadi satu batang berisi penjumlahan keduanya.
        series = (
            bentuk.categorical_columns[1]
            if len(bentuk.categorical_columns) >= 2
            else None
        )
        if series and not _kombinasi_unik(rows, x, series):
            return None
        dipakai = [x, angka] + ([series] if series else [])
        return ChartSpec(
            kind="bar",
            title=_judul(question, "comparison"),
            x=x,
            y=[angka],
            series=series,
            data=_rapikan_baris(rows, dipakai),
            total_rows=len(rows),
        )

    if len(bentuk.numeric_columns) >= 2:
        x, kedua = bentuk.numeric_columns[0], bentuk.numeric_columns[1]
        return ChartSpec(
            kind="scatter",
            title=_judul(question, "relationship"),
            x=x,
            y=[kedua],
            data=_rapikan_baris(rows, [x, kedua]),
            total_rows=len(rows),
        )

    return None


def build_chart(
    question: str, columns: list[str], rows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Rakit spesifikasi grafik siap kirim, atau ``None``.

    Mengembalikan ``dict`` (bukan :class:`ChartSpec`) karena nilai ini diteruskan
    sebagai JSON ke UI, dan hanya berisi nilai yang dapat diserialisasi.
    """
    spec = plan_chart(question, columns, rows)
    return spec.to_dict() if spec else None
