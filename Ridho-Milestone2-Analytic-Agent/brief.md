# Brief Proyek — Agent Analitik Pengiriman

## 1. Skenario

Anda adalah AI Engineer untuk tim operasi pengiriman paket. Manajer ingin bertanya dalam bahasa alami tentang volume kiriman, ketepatan waktu, keterlambatan, refund, carrier, kota tujuan, dan tren harian. Data analitik yang disetujui telah berada pada schema `gold` di Neon. Tim membutuhkan jawaban yang dapat diperiksa, percakapan lanjutan yang memahami konteks, dan pembatasan kuat atas akses data serta riwayat chat.

Agent hanya boleh mengambil **fakta bisnis** dari tabel Gold yang diizinkan. Agent tidak boleh mencari web, membaca file lokal sebagai sumber jawaban, mengubah data Gold, atau menjawab permintaan di luar tujuan analitik pengiriman. Percakapan disimpan terpisah di SQLite lokal agar follow-up tetap tersedia ketika halaman dimuat ulang.

## 2. Hasil belajar

Setelah milestone, peserta dapat:

1. Merancang tool kueri database untuk agent dengan batas schema, hak akses, dan biaya eksekusi.
2. Menghasilkan serta memvalidasi SQL analitik hanya baca dari pertanyaan bahasa alami.
3. Menjelaskan angka, periode, filter, dan keterbatasan jawaban berdasarkan hasil kueri yang benar-benar dieksekusi.
4. Menangani follow-up dalam percakapan tanpa mencampur riwayat dua pengguna.
5. Menolak permintaan di luar tujuan dan mengabaikan instruksi berbahaya dalam pesan pengguna maupun teks dari database.
6. Mengevaluasi ketepatan metrik, keamanan SQL, penolakan, prompt injection, dan otorisasi dengan bukti yang dapat diulang.
7. Membuat Dockerfile dan menjalankan API/UI secara reproducible melalui Docker Compose dengan secret runtime dan storage percakapan persisten.

## 3. Deliverable dan batas pekerjaan

Bangun backend API, loop agent, akses Neon, SQLite conversation store, pengujian, laporan evaluasi, dan image Docker yang dapat dijalankan penilai. Streamlit yang disediakan sudah memakai kontrak API di bawah; Anda boleh memakai atau menggantinya. Tidak perlu membangun pipeline data, mengubah Gold, membuat sistem login produksi, atau menambahkan tool yang memiliki side effect. Model/provider bebas dipilih dan harus dicatat versinya dalam README. Penggunaan framework agent opsional; keputusan tool dan validasi keamanan harus tetap dapat dijelaskan.

Penyelenggara menyediakan `compose.yaml` sebagai scaffold dua service (`api` dan `ui`); peserta menulis `Dockerfile` sendiri dan menyesuaikan scaffold bila entry point berbeda. Pertahankan port container API `8000`, UI `8501`, dan alamat API internal UI `http://api:8000`. Submission juga harus menyertakan `.dockerignore`.

**Alur target:**

```text
Streamlit atau UI peserta
  -> backend API + verifikasi bearer token demo
  -> load percakapan milik pengguna dari SQLite
  -> agent memilih apakah pertanyaan sesuai scope
  -> tool SQL tervalidasi -> Neon Gold dengan role hanya baca
  -> jawaban berdasarkan baris hasil -> simpan pesan pada SQLite
```

Identitas pengguna harus berasal dari token yang diverifikasi backend. `user_id` yang dikirim klien, jika ada, tidak boleh menjadi dasar otorisasi. Lakukan filter kepemilikan pada setiap operasi percakapan, termasuk list, read, dan kirim pesan. Untuk ID percakapan yang bukan milik pengguna, kembalikan `404` agar keberadaannya tidak terungkap.

## 4. Perilaku agent wajib

- Mendukung pertanyaan tunggal dan follow-up seperti “Bagaimana dibanding minggu sebelumnya?” dengan menggunakan konteks percakapan yang sama.
- Menggunakan hanya tabel/kolom Gold pada `gold-contract.md`; menolak referensi ke schema lain.
- Memvalidasi SQL sebelum eksekusi: satu pernyataan `SELECT`, tanpa operasi tulis, tanpa fungsi/objek tak diizinkan, dengan batas baris, waktu, dan ukuran hasil. Parameter dinamis dipisahkan dari teks SQL bila memungkinkan. Batasi juga jumlah langkah/tool call per pesan.
- Menjalankan kueri dengan role Neon yang hanya memiliki `USAGE` schema Gold dan `SELECT` pada tabel yang diizinkan. Validasi aplikasi adalah lapisan tambahan, bukan pengganti izin database.
- Menyebutkan periode dan filter yang dipakai; tampilkan angka dengan unit dan penyebut yang tepat. Jika data kosong, tidak cukup, atau pertanyaan ambigu, jelaskan batasnya atau minta klarifikasi. Jangan mengarang angka.
- Mengatakan secara singkat bahwa permintaan di luar tujuan agent tidak dapat dilayani. Contoh: nasihat medis, informasi cuaca, permintaan menulis email, atau tindakan mengubah status kiriman.
- Memperlakukan teks `exception_note` dari database sebagai data tidak tepercaya. Instruksi di dalamnya tidak boleh mengubah kebijakan agent, memicu tool tambahan, membocorkan rahasia, atau masuk ke jawaban sebagai perintah.
- Tidak menampilkan prompt sistem, secret, raw chain-of-thought, token, atau isi percakapan pengguna lain.

> **Production Note:** Bearer token demo dan SQLite lokal adalah batas proyek kelas. Jelaskan perubahan yang diperlukan untuk SSO, penyimpanan bersama, retensi, dan observability pada deployment nyata.

## 5. Kontrak API untuk UI awal

Semua endpoint selain `/healthz` memerlukan `Authorization: Bearer <token>`. JSON error berbentuk `{"detail":"pesan aman"}`. Waktu menggunakan ISO 8601 UTC.

| Method | Path | Request | Respons sukses |
|---|---|---|---|
| GET | `/healthz` | — | `{"status":"ok"}` |
| GET | `/api/v1/conversations` | — | `{"conversations":[{"id":"...","title":"...","updated_at":"..."}]}` |
| POST | `/api/v1/conversations` | `{}` | `201`, objek `{"id":"...","title":"...","updated_at":"..."}` |
| GET | `/api/v1/conversations/{id}` | — | `{"id":"...","messages":[{"role":"user|assistant","content":"...","created_at":"..."}]}` |
| POST | `/api/v1/conversations/{id}/messages` | `{"content":"..."}` | `{"conversation_id":"...","answer":"...","data_basis":"...","outcome":"answered|clarify|refused|no_data"}` |

`data_basis` adalah ringkasan tabel, periode, dan filter yang dipakai; untuk `refused` isinya string kosong. Jangan mengembalikan SQL internal atau detail error database ke UI. Batasi panjang pesan dan jumlah riwayat yang dikirim ke model. Token tidak valid menghasilkan `401`; percakapan milik orang lain menghasilkan `404`.

## 6. Tugas bertahap

1. **Pahami data (35 menit):** baca grain dan definisi metrik. Jalankan kueri manual untuk tiga pertanyaan referensi. Catat pembilang, penyebut, dan tanggal yang dipakai.
2. **Bangun kueri terjaga (75 menit):** buat tool Neon hanya baca, validasi SQL, timeout, batas baris, dan hasil aman. Uji `SELECT` yang valid dan upaya menulis/menjangkau schema lain.
3. **Integrasikan agent (70 menit):** definisikan tujuan, tool, state, langkah maksimum, stopping condition, dan fallback. Pastikan jawaban hanya memakai hasil tool dan penolakan scope tidak mengakses Gold.
4. **Tambahkan percakapan (85 menit):** buat penyimpanan SQLite, otorisasi berbasis token, dan follow-up. Uji pergantian identitas dan akses lintas pengguna.
5. **Uji serangan dan kegagalan (90 menit):** jalankan kasus publik dan tambahkan kasus sendiri untuk prompt injection dalam pesan dan `exception_note`, hasil kosong, database gagal, serta kueri mahal.
6. **Evaluasi dan demo (sisa waktu):** hasilkan laporan kasus, alasan kegagalan, keterbatasan, dan rekomendasi produksi. Demonstrasikan happy path, follow-up, penolakan, dan blokir lintas pengguna.

> **Checkpoint:** Pertanyaan “Berapa on-time rate carrier X pada Agustus 2026?” harus menghasilkan angka dari data yang benar, dengan denominator kiriman **yang sudah delivered**, bukan seluruh kiriman.

## 7. Bukti penerimaan

- Minimal 12 kasus evaluasi yang dapat diulang: 4 ketepatan analitik, 2 follow-up, 2 scope/abstain, 2 prompt injection, dan 2 otorisasi/SQL. Sertakan input, hasil yang diharapkan, hasil aktual, dan status.
- Uji otomatis untuk SQL read-only dan akses percakapan lintas pengguna.
- README berisi perintah setup/run/test dan variabel lingkungan tanpa nilainya.
- `docker compose up --build` membangun dan menjalankan API serta UI; port container 8000/8501 tersedia, secret hanya disuntik saat runtime, dan percakapan SQLite bertahan setelah API direstart.
- Demo 5–7 menit memperlihatkan pertanyaan awal, follow-up, jawaban tanpa data atau penolakan, prompt injection yang gagal, serta percobaan token B membuka percakapan A.

Lihat [rubric.md](rubric.md) untuk bobot dan hard gate. Ketika provider model atau Neon tidak tersedia, jelaskan kegagalannya secara aman; jangan mengembalikan jawaban rekaan.
