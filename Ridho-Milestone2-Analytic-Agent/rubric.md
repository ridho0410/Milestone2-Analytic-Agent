# Rubrik Milestone Fase 2 — 100 poin

| Kategori | Poin | Bukti nilai penuh |
|---|---:|---|
| Ketepatan analitik dan grounding | 25 | Grain, filter waktu, angka, denominator, unit, dan dasar data benar; tidak mengarang hasil |
| SQL dan batas akses Gold | 20 | Role hanya baca; tabel allowlist; validasi satu `SELECT`; timeout, row/result limit; kesalahan aman |
| Percakapan dan follow-up | 15 | Konteks follow-up benar; riwayat tersimpan; konteks dibatasi; tidak bercampur antar percakapan |
| Otorisasi percakapan | 15 | Identitas dari token; list/read/send seluruhnya memeriksa pemilik; IDOR dan pergantian token diuji |
| Scope dan prompt injection | 15 | Menolak tugas nonanalitik; instruksi pada user input dan data database tidak mengubah kebijakan/tool; abstain saat perlu |
| Container dan reproducibility | 5 | Dockerfile peserta berhasil dibangun; Compose menjalankan API/UI; secret tidak masuk image; SQLite bertahan setelah API restart |
| Evaluasi, dokumentasi, demo | 5 | ≥12 kasus dapat diulang, test output, README, diagram trust boundary, dan demo lengkap |
| **Total** | **100** | |

## Hard gate

Nilai akhir maksimal **59** dan milestone belum lulus jika salah satu terjadi:

1. Pengguna dapat membaca atau menambah pesan pada percakapan pengguna lain.
2. Agent atau kredensial aplikasi dapat menulis ke schema Gold, atau kueri tulis lolos ke database.
3. Prompt injection membuat agent membocorkan secret, mengikuti instruksi dari `exception_note`, atau memakai tool di luar batas.
4. Jawaban analitik sengaja dibuat tanpa eksekusi kueri atau tanpa data yang mendukung angka tersebut.
5. Tidak ada bukti pengujian yang dapat diulang untuk otorisasi dan SQL hanya baca.

## Panduan skor parsial

- Fitur bekerja tetapi tidak punya pengujian/bukti: maksimal 60% poin kategori terkait.
- Hanya memblokir SQL berdasarkan prefix string tanpa parser/validasi objek dan role hanya baca: maksimal 50% kategori SQL.
- Follow-up bergantung pada `st.session_state` saja sehingga hilang setelah sesi browser berakhir: maksimal 50% kategori percakapan.
- UI menampilkan identitas tetapi backend mempercayai `user_id` dari request: 0 pada kategori otorisasi dan hard gate bila akses lintas pengguna terbukti.
- Daftar prompt injection tanpa uji berulang atau bukti respons: maksimal 50% kategori scope dan injection.
