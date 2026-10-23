"""Profil kolom hasil kueri, dipakai perencana grafik.

Modul ini menjawab satu pertanyaan: **kolom apa yang sedang kita hadapi?**
Tanggal, angka, atau kategori. Jawabannya dipakai :mod:`src.chart_planner` untuk
memilih jenis grafik yang masuk akal.

Sengaja tanpa dependensi: klasifikasi hanya memakai tipe Python dan nama kolom,
sehingga dapat diuji tanpa database maupun model bahasa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# Tanggal ISO sederhana, misalnya 2026-08-01 atau 2026-08-01T10:00:00.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Nama kolom yang menandakan waktu meski nilainya belum tentu bertipe tanggal.
_NAMA_WAKTU = re.compile(
    r"(date|tanggal|time|waktu|periode|bulan|minggu|hari)", re.IGNORECASE
)

# Batas jumlah nilai unik agar sebuah kolom masih dianggap kategorikal.
# Kolom dengan nilai hampir unik (misalnya shipment_id) bukan kategori yang
# berguna untuk grafik batang.
_MAKS_KATEGORI = 50


@dataclass(frozen=True)
class ResultProfile:
    """Bentuk kolom pada satu hasil kueri."""

    row_count: int
    date_columns: list[str] = field(default_factory=list)
    numeric_columns: list[str] = field(default_factory=list)
    categorical_columns: list[str] = field(default_factory=list)

    @property
    def has_chartable_shape(self) -> bool:
        """True bila hasilnya punya cukup isi untuk sebuah grafik."""
        return self.row_count >= 2 and bool(self.numeric_columns)


def _adalah_angka(nilai: Any) -> bool:
    return isinstance(nilai, (int, float, Decimal)) and not isinstance(nilai, bool)


def _adalah_waktu(nilai: Any) -> bool:
    if isinstance(nilai, (date, datetime)):
        return True
    return isinstance(nilai, str) and bool(_ISO_DATE.match(nilai.strip()))


def _klasifikasi_kolom(nama: str, nilai: list[Any]) -> str:
    """Tentukan satu kolom termasuk waktu, angka, atau kategori.

    Urutannya penting: **nilai diperiksa lebih dulu, baru nama kolom.**
    Memeriksa nama lebih dulu menimbulkan bug nyata — kolom ``on_time_rate``
    mengandung kata "time" sehingga dikira kolom tanggal, dan grafiknya menjadi
    ``line`` (tren) padahal isinya perbandingan antar-carrier yang seharusnya
    ``bar``. Nama kolom hanya dipakai sebagai petunjuk ketika nilainya belum
    menentukan, misalnya kolom waktu yang berisi teks seperti "Minggu 1".
    """
    if not nilai:
        # Kolom tanpa nilai tidak dapat dipakai grafik apa pun; dianggap
        # kategorikal agar tidak masuk daftar sumbu numerik.
        return "kategori"

    # 1. Seluruh nilainya angka -> pasti sumbu numerik, apa pun namanya.
    if all(_adalah_angka(v) for v in nilai):
        return "angka"

    # 2. Nilainya berbentuk tanggal.
    if _adalah_waktu(nilai[0]):
        return "waktu"

    # 3. Nilai belum menentukan; nama kolom dipakai sebagai petunjuk terakhir.
    if _NAMA_WAKTU.search(nama):
        return "waktu"

    return "kategori"


def profile_result(columns: list[str], rows: list[dict[str, Any]]) -> ResultProfile:
    """Klasifikasikan ``columns`` berdasarkan nilainya pada ``rows``."""
    waktu: list[str] = []
    angka: list[str] = []
    kategori: list[str] = []

    for kolom in columns:
        nilai = [baris.get(kolom) for baris in rows if baris.get(kolom) is not None]
        jenis = _klasifikasi_kolom(kolom, nilai)
        if jenis == "waktu":
            waktu.append(kolom)
        elif jenis == "angka":
            angka.append(kolom)
        else:
            # Kolom dengan nilai nyaris unik bukan kategori yang berguna.
            unik = len({str(v) for v in nilai})
            if unik <= _MAKS_KATEGORI:
                kategori.append(kolom)

    return ResultProfile(
        row_count=len(rows),
        date_columns=waktu,
        numeric_columns=angka,
        categorical_columns=kategori,
    )
