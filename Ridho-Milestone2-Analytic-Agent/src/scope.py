"""Blokir permintaan berbahaya yang eksplisit; model memahami makna analitik."""

import re

from .config import settings

INJECTION = (
    r"abaikan\s+(semua\s+)?(aturan|instruksi|perintah)",
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"(prompt|instruksi)\s+(sistem|rahasia|internal)",
    r"system\s+prompt",
    r"(tampilkan|ungkap|bocorkan|reveal|show).*?(secret|token|kredensial|rahasia)",
    r"kamu\s+sekarang\s+adalah|you\s+are\s+now",
    r"(bypass|lewati|akali|jailbreak).*?(aturan|filter|keamanan)",
    r"<\s*/?\s*(system|assistant|im_start)\s*>",
    r"(cari|search).*?(web|internet|google)",
    r"(baca|buka)\s+(file|berkas)",
)
OUTSIDE = r"\b(cuaca|hujan|medis|dokter|obat|investasi|crypto|saham|berita|puisi|pantun|gaji|senjata|narkoba|meretas|prediksi|forecast)\b|\b(tulis|buat|kirim)\s+(email|surat)"
WRITE = r"\b(hapus|delete|drop|truncate|insert|update|alter|grant|copy)\b|\b(ubah|mengubah|rubah|ganti|mengganti|tandai|perbarui)\s+(status|data)"


def scan_untrusted_text(text: str) -> list[str]:
    return [pattern for pattern in INJECTION if re.search(pattern, text or "", re.I)]


def check_scope(question: str) -> bool:
    if (
        scan_untrusted_text(question)
        or re.search(OUTSIDE, question, re.I)
        or re.search(WRITE, question, re.I)
    ):
        return False
    return True


def sanitize_note(note: str) -> str:
    return " ".join("".join(char for char in note if char.isprintable()).split())[
        : settings.max_note_chars
    ]
