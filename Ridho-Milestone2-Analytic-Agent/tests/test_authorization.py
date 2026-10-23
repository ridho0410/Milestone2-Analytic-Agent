"""Uji otorisasi: token -> identitas, kepemilikan percakapan, anti-IDOR.

Menjawab hard gate #1 (pengguna tidak boleh membaca/menambah pesan percakapan
pengguna lain) dan hard gate #5 (bukti pengujian yang dapat diulang).
"""

from __future__ import annotations

import pytest

from tests.conftest import HEADERS_A, HEADERS_B

# --------------------------------------------------------------------------
# Autentikasi
# --------------------------------------------------------------------------


def test_healthz_tanpa_token(client) -> None:
    """Health check adalah satu-satunya endpoint tanpa autentikasi."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_endpoint_terlindungi_menolak_tanpa_token(client) -> None:
    assert client.get("/api/v1/conversations").status_code == 401
    assert client.post("/api/v1/conversations", json={}).status_code == 401


def test_token_salah_ditolak(client) -> None:
    response = client.get(
        "/api/v1/conversations",
        headers={"Authorization": "Bearer token-palsu"},
    )
    assert response.status_code == 401
    assert "detail" in response.json()


def test_skema_authorization_bukan_bearer_ditolak(client) -> None:
    response = client.get(
        "/api/v1/conversations", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert response.status_code == 401


def test_header_authorization_malformed_ditolak(client) -> None:
    for value in ("", "Bearer", "Bearer ", "token-tanpa-skema"):
        response = client.get("/api/v1/conversations", headers={"Authorization": value})
        assert response.status_code == 401, f"nilai {value!r} seharusnya 401"


def test_error_tidak_membocorkan_detail_internal(client) -> None:
    """Pesan error tidak boleh memuat stack trace, SQL, atau kredensial."""
    response = client.get(
        "/api/v1/conversations", headers={"Authorization": "Bearer salah"}
    )
    body = response.text.lower()
    for leak in ("traceback", "sqlite", "postgres", "neon", "select ", 'file "'):
        assert leak not in body, f"error membocorkan '{leak}': {response.text[:200]}"


# --------------------------------------------------------------------------
# IDOR — inti hard gate
# --------------------------------------------------------------------------


def test_pengguna_B_tidak_dapat_membaca_percakapan_A(client, clean_store) -> None:
    """O1: token B membaca ID percakapan A harus 404 tanpa kebocoran isi."""
    created = client.post("/api/v1/conversations", json={}, headers=HEADERS_A)
    assert created.status_code == 201
    conv_a = created.json()["id"]

    # A menulis pesan berisi penanda rahasia.
    sent = client.post(
        f"/api/v1/conversations/{conv_a}/messages",
        json={"content": "Berapa jumlah kiriman pada Agustus 2026?"},
        headers=HEADERS_A,
    )
    assert sent.status_code == 200

    # B mencoba membaca.
    response = client.get(f"/api/v1/conversations/{conv_a}", headers=HEADERS_B)
    assert response.status_code == 404, "B seharusnya mendapat 404"
    assert conv_a not in response.text, "respons membocorkan ID percakapan A"


def test_pengguna_B_tidak_dapat_mengirim_ke_percakapan_A(client, clean_store) -> None:
    """O2: token B mengirim pesan ke percakapan A harus 404 dan tidak mengubah A."""
    created = client.post("/api/v1/conversations", json={}, headers=HEADERS_A)
    conv_a = created.json()["id"]
    client.post(
        f"/api/v1/conversations/{conv_a}/messages",
        json={"content": "Berapa jumlah kiriman pada Agustus 2026?"},
        headers=HEADERS_A,
    )

    before = client.get(f"/api/v1/conversations/{conv_a}", headers=HEADERS_A).json()
    count_before = len(before["messages"])

    response = client.post(
        f"/api/v1/conversations/{conv_a}/messages",
        json={"content": "Kirim pesan sebagai B"},
        headers=HEADERS_B,
    )
    assert response.status_code == 404

    after = client.get(f"/api/v1/conversations/{conv_a}", headers=HEADERS_A).json()
    assert (
        len(after["messages"]) == count_before
    ), "jumlah pesan percakapan A berubah setelah percobaan B"
    assert not any("Kirim pesan sebagai B" in m["content"] for m in after["messages"])


def test_daftar_percakapan_terpisah_per_pengguna(client, clean_store) -> None:
    """B tidak boleh melihat percakapan A pada list."""
    conv_a = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]
    conv_b = client.post("/api/v1/conversations", json={}, headers=HEADERS_B).json()[
        "id"
    ]

    ids_a = {
        item["id"]
        for item in client.get("/api/v1/conversations", headers=HEADERS_A).json()[
            "conversations"
        ]
    }
    ids_b = {
        item["id"]
        for item in client.get("/api/v1/conversations", headers=HEADERS_B).json()[
            "conversations"
        ]
    }

    assert conv_a in ids_a and conv_a not in ids_b
    assert conv_b in ids_b and conv_b not in ids_a


def test_id_percakapan_tidak_ada_menghasilkan_404(client, clean_store) -> None:
    response = client.get(
        "/api/v1/conversations/00000000-0000-0000-0000-000000000000", headers=HEADERS_A
    )
    assert response.status_code == 404


def test_id_palsu_tidak_membocorkan_perbedaan(client, clean_store) -> None:
    """404 untuk 'tidak ada' dan 'milik orang lain' harus identik isinya."""
    conv_a = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]
    milik_orang_lain = client.get(f"/api/v1/conversations/{conv_a}", headers=HEADERS_B)
    tidak_ada = client.get(
        "/api/v1/conversations/00000000-0000-0000-0000-000000000000", headers=HEADERS_A
    )
    assert milik_orang_lain.status_code == tidak_ada.status_code == 404
    assert (
        milik_orang_lain.json() == tidak_ada.json()
    ), "respons berbeda dapat dipakai menebak keberadaan percakapan"


def test_user_id_dari_klien_diabaikan(client, clean_store) -> None:
    """Rubrik: mempercayai user_id dari request = 0 poin otorisasi.

    Pengguna B mengirim X-User-Id: user-a; identitas tetap harus dari token.
    """
    conv_a = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]

    spoofed = {**HEADERS_B, "X-User-Id": "user-a"}
    assert (
        client.get(f"/api/v1/conversations/{conv_a}", headers=spoofed).status_code
        == 404
    )

    # Bahkan bila B mengaku sebagai A saat membuat percakapan, percakapan itu
    # tetap tidak muncul di daftar A.
    created = client.post("/api/v1/conversations", json={}, headers=spoofed)
    assert created.status_code == 201
    conv_spoofed = created.json()["id"]
    ids_a = {
        item["id"]
        for item in client.get("/api/v1/conversations", headers=HEADERS_A).json()[
            "conversations"
        ]
    }
    assert conv_spoofed not in ids_a


def test_pergantian_token_tidak_mewarisi_percakapan(client, clean_store) -> None:
    """Pergantian identitas pada UI tidak boleh membawa percakapan sebelumnya."""
    conv_a = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]
    assert (
        client.get(f"/api/v1/conversations/{conv_a}", headers=HEADERS_B).status_code
        == 404
    )
    # Token A tetap dapat mengaksesnya kembali.
    assert (
        client.get(f"/api/v1/conversations/{conv_a}", headers=HEADERS_A).status_code
        == 200
    )


# --------------------------------------------------------------------------
# Hapus percakapan
#
# Operasi destruktif adalah tempat kesalahan otorisasi paling mahal, sehingga
# pemeriksaannya diuji seketat read/send.
# --------------------------------------------------------------------------


def test_hapus_percakapan_sendiri_berhasil(client, clean_store) -> None:
    """Pemilik dapat menghapus percakapannya; pesannya ikut terhapus."""
    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]
    client.post(
        f"/api/v1/conversations/{conv}/messages",
        json={"content": "Berapa jumlah kiriman pada Agustus 2026?"},
        headers=HEADERS_A,
    )
    assert clean_store.count_messages(conv) == 2

    response = client.delete(f"/api/v1/conversations/{conv}", headers=HEADERS_A)
    assert response.status_code == 204, response.text
    assert response.content == b"", "204 tidak boleh punya isi"

    # Hilang dari read ...
    assert (
        client.get(f"/api/v1/conversations/{conv}", headers=HEADERS_A).status_code
        == 404
    )
    # ... dan dari list.
    ids = {
        item["id"]
        for item in client.get("/api/v1/conversations", headers=HEADERS_A).json()[
            "conversations"
        ]
    }
    assert conv not in ids
    # ... dan pesannya benar-benar terhapus (CASCADE), bukan jadi baris yatim.
    assert clean_store.count_messages(conv) == 0, "pesan tertinggal setelah hapus"


def test_pengguna_B_tidak_dapat_menghapus_percakapan_A(client, clean_store) -> None:
    """IDOR pada operasi destruktif: B tidak boleh menghapus milik A."""
    conv_a = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]
    client.post(
        f"/api/v1/conversations/{conv_a}/messages",
        json={"content": "Berapa jumlah kiriman pada Agustus 2026?"},
        headers=HEADERS_A,
    )

    response = client.delete(f"/api/v1/conversations/{conv_a}", headers=HEADERS_B)
    assert response.status_code == 404, "B seharusnya tidak dapat menghapus"

    # Percakapan A harus tetap ada dan utuh.
    after = client.get(f"/api/v1/conversations/{conv_a}", headers=HEADERS_A)
    assert after.status_code == 200, "percakapan A ikut terhapus"
    assert len(after.json()["messages"]) == 2, "pesan A berkurang"
    assert clean_store.count_messages(conv_a) == 2


def test_hapus_percakapan_tidak_ada_identik_dengan_milik_orang_lain(
    client, clean_store
) -> None:
    """404 untuk 'tidak ada' dan 'milik orang lain' harus identik."""
    conv_a = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]
    milik_orang_lain = client.delete(
        f"/api/v1/conversations/{conv_a}", headers=HEADERS_B
    )
    tidak_ada = client.delete(
        "/api/v1/conversations/00000000-0000-0000-0000-000000000000", headers=HEADERS_B
    )
    assert milik_orang_lain.status_code == tidak_ada.status_code == 404
    assert (
        milik_orang_lain.json() == tidak_ada.json()
    ), "respons berbeda dapat dipakai menebak keberadaan percakapan"


def test_hapus_tanpa_token_ditolak(client, clean_store) -> None:
    """Endpoint hapus wajib terautentikasi, sama seperti endpoint lain."""
    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]

    assert client.delete(f"/api/v1/conversations/{conv}").status_code == 401
    assert (
        client.delete(
            f"/api/v1/conversations/{conv}", headers={"Authorization": "Bearer salah"}
        ).status_code
        == 401
    )

    # Percakapan harus tetap ada setelah percobaan tanpa token.
    assert (
        client.get(f"/api/v1/conversations/{conv}", headers=HEADERS_A).status_code
        == 200
    )


def test_hapus_percakapan_tidak_menyentuh_percakapan_lain(client, clean_store) -> None:
    """Menghapus satu percakapan tidak boleh menghapus percakapan lain."""
    conv_1 = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]
    conv_2 = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]
    conv_b = client.post("/api/v1/conversations", json={}, headers=HEADERS_B).json()[
        "id"
    ]

    assert (
        client.delete(f"/api/v1/conversations/{conv_1}", headers=HEADERS_A).status_code
        == 204
    )

    assert (
        client.get(f"/api/v1/conversations/{conv_2}", headers=HEADERS_A).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/conversations/{conv_b}", headers=HEADERS_B).status_code
        == 200
    )


# --------------------------------------------------------------------------
# Ganti judul percakapan
# --------------------------------------------------------------------------


def _judul(client, conversation_id: str, headers: dict[str, str]) -> str:
    """Ambil judul percakapan dari daftar milik pemiliknya."""
    daftar = client.get("/api/v1/conversations", headers=headers).json()[
        "conversations"
    ]
    return next(item["title"] for item in daftar if item["id"] == conversation_id)


def test_ganti_judul_percakapan_sendiri(client, clean_store) -> None:
    """Pemilik dapat mengganti judul percakapannya."""
    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]

    response = client.patch(
        f"/api/v1/conversations/{conv}",
        json={"title": "Analisis keterlambatan Agustus"},
        headers=HEADERS_A,
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Analisis keterlambatan Agustus"
    assert _judul(client, conv, HEADERS_A) == "Analisis keterlambatan Agustus"


def test_ganti_judul_menimpa_judul_otomatis(client, clean_store) -> None:
    """Judul yang sudah terisi otomatis dari pertanyaan tetap bisa diganti."""
    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]
    client.post(
        f"/api/v1/conversations/{conv}/messages",
        json={"content": "Berapa jumlah kiriman pada Agustus 2026?"},
        headers=HEADERS_A,
    )
    otomatis = _judul(client, conv, HEADERS_A)
    assert otomatis not in {"New conversation", "Percakapan baru"}, "judul otomatis tidak terbentuk"

    client.patch(
        f"/api/v1/conversations/{conv}",
        json={"title": "Judul pilihan saya"},
        headers=HEADERS_A,
    )
    assert _judul(client, conv, HEADERS_A) == "Judul pilihan saya"


def test_pengguna_B_tidak_dapat_mengganti_judul_percakapan_A(
    client, clean_store
) -> None:
    """IDOR pada ganti judul: B tidak boleh mengubah judul milik A."""
    conv_a = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]
    judul_awal = _judul(client, conv_a, HEADERS_A)

    response = client.patch(
        f"/api/v1/conversations/{conv_a}",
        json={"title": "DIBAJAK OLEH B"},
        headers=HEADERS_B,
    )
    assert response.status_code == 404, "B seharusnya tidak dapat mengubah judul"
    assert _judul(client, conv_a, HEADERS_A) == judul_awal, "judul A berubah"


def test_ganti_judul_menormalkan_spasi(client, clean_store) -> None:
    """Spasi berlebih dirapikan agar daftar tidak berisi judul aneh."""
    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]
    response = client.patch(
        f"/api/v1/conversations/{conv}",
        json={"title": "   Kiriman    Agustus   "},
        headers=HEADERS_A,
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Kiriman Agustus"


@pytest.mark.parametrize(
    "judul",
    ["", "   ", "\t\n ", "x" * 200],
)
def test_ganti_judul_tidak_valid_ditolak(client, clean_store, judul: str) -> None:
    """Judul kosong, hanya spasi, atau terlalu panjang harus ditolak."""
    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]
    judul_awal = _judul(client, conv, HEADERS_A)

    response = client.patch(
        f"/api/v1/conversations/{conv}", json={"title": judul}, headers=HEADERS_A
    )
    assert response.status_code == 422, f"judul {judul[:12]!r} seharusnya ditolak"
    assert (
        _judul(client, conv, HEADERS_A) == judul_awal
    ), "judul berubah padahal ditolak"


def test_ganti_judul_percakapan_tidak_ada_identik_dengan_milik_orang_lain(
    client, clean_store
) -> None:
    """404 untuk 'tidak ada' dan 'milik orang lain' harus identik."""
    conv_a = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()[
        "id"
    ]

    milik_orang_lain = client.patch(
        f"/api/v1/conversations/{conv_a}", json={"title": "X"}, headers=HEADERS_B
    )
    tidak_ada = client.patch(
        "/api/v1/conversations/00000000-0000-0000-0000-000000000000",
        json={"title": "X"},
        headers=HEADERS_B,
    )
    assert milik_orang_lain.status_code == tidak_ada.status_code == 404
    assert milik_orang_lain.json() == tidak_ada.json()


def test_ganti_judul_tanpa_token_ditolak(client, clean_store) -> None:
    """Endpoint ganti judul wajib terautentikasi."""
    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]
    assert (
        client.patch(f"/api/v1/conversations/{conv}", json={"title": "X"}).status_code
        == 401
    )
    assert (
        client.patch(
            f"/api/v1/conversations/{conv}",
            json={"title": "X"},
            headers={"Authorization": "Bearer salah"},
        ).status_code
        == 401
    )


def test_ganti_judul_tidak_mengubah_pesan(client, clean_store) -> None:
    """Ganti judul tidak boleh menyentuh isi percakapan."""
    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]
    client.post(
        f"/api/v1/conversations/{conv}/messages",
        json={"content": "Berapa jumlah kiriman pada Agustus 2026?"},
        headers=HEADERS_A,
    )
    sebelum = client.get(f"/api/v1/conversations/{conv}", headers=HEADERS_A).json()

    client.patch(
        f"/api/v1/conversations/{conv}",
        json={"title": "Judul lain"},
        headers=HEADERS_A,
    )

    sesudah = client.get(f"/api/v1/conversations/{conv}", headers=HEADERS_A).json()
    assert sesudah["messages"] == sebelum["messages"], "isi percakapan berubah"


# --------------------------------------------------------------------------
# Penghapusan permanen
# --------------------------------------------------------------------------


def _bytes_di_sekitar(path) -> bytes:
    """Gabungan berkas SQLite utama dan berkas WAL-nya."""
    isi = b""
    for kandidat in (path, path.with_name(path.name + "-wal")):
        try:
            isi += kandidat.read_bytes()
        except OSError:
            pass
    return isi


def test_hapus_percakapan_menghilangkan_jejak_di_berkas(client, clean_store) -> None:
    """Isi percakapan yang dihapus tidak boleh dapat dipulihkan dari berkas.

    SQLite secara bawaan hanya menandai halaman sebagai "bebas" saat baris
    dihapus — isinya tetap ada di berkas dan bisa dibaca dengan editor hex.
    Tes ini membuktikan `secure_delete` + `VACUUM` benar-benar menghapusnya.
    """
    penanda = "RAHASIA-UNIK-8f3a1c9d2b7e4a10"

    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]
    client.post(
        f"/api/v1/conversations/{conv}/messages",
        json={"content": f"Mohon catat {penanda} pada percakapan ini."},
        headers=HEADERS_A,
    )

    # Prasyarat: teksnya memang tersimpan lebih dulu.
    assert penanda.encode() in _bytes_di_sekitar(
        clean_store.path
    ), "prasyarat gagal: teks tidak ditemukan sebelum penghapusan"

    assert (
        client.delete(f"/api/v1/conversations/{conv}", headers=HEADERS_A).status_code
        == 204
    )

    sisa = _bytes_di_sekitar(clean_store.path)
    assert penanda.encode() not in sisa, (
        "isi percakapan masih dapat dipulihkan dari berkas setelah dihapus; "
        "secure_delete/VACUUM tidak bekerja"
    )


def test_hapus_percakapan_membuang_halaman_bebas(client, clean_store) -> None:
    """Pemadatan benar-benar membuang halaman bebas, bukan sekadar menandainya.

    Diukur dengan `PRAGMA freelist_count`, BUKAN ukuran berkas. Ukuran berkas
    menyesatkan di sini karena dua hal:
      1. Sebelum checkpoint, sebagian data masih berada di berkas WAL, sehingga
         berkas utama terlihat lebih kecil daripada isi sebenarnya.
      2. SQLite mengalokasikan ruang per 4 KB, jadi selisih beberapa halaman
         adalah hal wajar dan bukan tanda keberhasilan atau kegagalan.
    """
    import sqlite3

    conv = client.post("/api/v1/conversations", json={}, headers=HEADERS_A).json()["id"]
    # Isi cukup banyak agar ada halaman yang benar-benar dibebaskan.
    for i in range(12):
        client.post(
            f"/api/v1/conversations/{conv}/messages",
            json={"content": f"Pertanyaan uji nomor {i} " + "x" * 200},
            headers=HEADERS_A,
        )

    assert (
        client.delete(f"/api/v1/conversations/{conv}", headers=HEADERS_A).status_code
        == 204
    )

    con = sqlite3.connect(str(clean_store.path))
    try:
        bebas = con.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        con.close()

    assert bebas == 0, (
        f"masih ada {bebas} halaman bebas setelah pemadatan; "
        "VACUUM tidak dijalankan atau gagal"
    )


def test_authenticated_health_detail_reports_agent_state(client):
    response = client.get("/api/v1/health/detail", headers=HEADERS_A)
    assert response.status_code == 200
    payload = response.json()
    assert payload["gold_reachable"] is True
    assert payload["llm_enabled"] is False
    assert "limits" in payload and "OPENAI_API_KEY" not in response.text
