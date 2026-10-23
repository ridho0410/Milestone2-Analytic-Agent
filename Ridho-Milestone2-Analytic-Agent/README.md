# Milestone Fase 2 — Agent Analitik Pengiriman

## 1. Skenario

Anda adalah AI Engineer untuk tim operasi pengiriman paket. Manajer operasional ingin menanyakan performa carrier dan kota tujuan dalam bahasa alami, misalnya:

- “Berapa jumlah kiriman pada Agustus 2026?”
- “Carrier mana yang paling tepat waktu pada bulan tersebut?”
- “Bagaimana tren kiriman ke Bandung per minggu? Bagaimana dibanding minggu sebelumnya?”
- “Berapa refund rate untuk layanan express?”

Penyelenggara sudah menyiapkan **dataset Gold sintetis di Neon** dan aplikasi Streamlit awal. Tugas Anda adalah membangun agent dan backend yang mengubah pertanyaan menjadi kueri analitik yang aman, mengambil fakta dari database, lalu menjawab berdasarkan hasilnya. Agent harus bisa melanjutkan percakapan tanpa membocorkan riwayat pengguna lain.

Catatan operasional dalam database dapat mengandung instruksi berbahaya. Perlakukan catatan itu sebagai **data**, bukan perintah untuk agent. Pertanyaan yang tidak berhubungan dengan analitik pengiriman harus ditolak dengan jelas dan singkat.

- **Level:** Intermediate
- **Waktu:** 3 sesi mentoring × 180 menit, ditambah demo 5–7 menit
- **Hasil utama:** backend agent, UI yang terhubung, image Docker yang dapat dijalankan, tes, laporan evaluasi, dan dokumentasi keputusan teknis

## 2. Tujuan Milestone

Bangun alur berikut:

```text
Pengguna A atau B
       ↓
Streamlit yang disediakan atau UI buatan Anda
       ↓
Backend API: verifikasi bearer token demo
       ├── SQLite: percakapan milik pengguna yang terverifikasi
       └── Agent: pahami pertanyaan dan konteks follow-up
                    ↓
              SQL guard + batas eksekusi
                    ↓
              Neon Gold (role hanya baca)
                    ↓
              Jawaban yang didukung hasil kueri
```

Semua **fakta bisnis** dalam jawaban harus berasal dari tabel Gold Neon yang disediakan. SQLite hanya menyimpan percakapan. UI tidak boleh menyimpan kredensial Neon atau mengakses Gold secara langsung. Agent tidak mempunyai tool untuk mencari web, membaca sumber analitik lain, atau mengubah data.

## 3. Apa yang Harus Anda Bangun

### A. Backend API dan identitas demo

Buat API yang dipanggil oleh Streamlit. API memverifikasi dua bearer token demo yang diberikan penyelenggara dan memetakan masing-masing token ke identitas pengguna pada **server**. Nilai `user_id` dari browser, bila ada, tidak boleh menentukan hak akses. Token tidak valid harus ditolak dengan `401`.

Implementasikan endpoint pada [brief.md](brief.md#5-kontrak-api-untuk-ui-awal): health check, membuat percakapan, menampilkan daftar percakapan, membaca satu percakapan, dan mengirim pesan. Berikan error yang aman tanpa stack trace, connection string, prompt sistem, atau isi percakapan orang lain.

### B. Penyimpanan percakapan dan follow-up

Simpan percakapan serta pesan pada SQLite lokal. Setiap percakapan memiliki satu pemilik. Pada operasi **list, read, dan send**, cek bahwa ID percakapan memang milik identitas dari token. Jika pengguna B mencoba membuka atau menambah pesan pada percakapan A, kembalikan `404` dan jangan ubah jumlah pesan A.

Agent harus memakai konteks dari percakapan yang sama untuk follow-up. Contoh: setelah bertanya “Berapa kiriman ke Bandung pada Agustus 2026?”, pengguna dapat bertanya “Bagaimana dibanding minggu sebelumnya?”. Jika konteks yang dibutuhkan tidak ada atau ambigu, minta klarifikasi. Batasi panjang riwayat yang dikirim ke model agar biaya, latency, dan risiko dari pesan lama tetap terkendali.

### C. Agent analitik dan akses Gold

Gunakan model/provider yang Anda pilih; catat nama dan versi model dalam submission. Penggunaan LangChain atau framework lain opsional. Agent harus memiliki tujuan, tool, state percakapan, batas langkah, kondisi berhenti, dan perilaku ketika tool gagal. Jelaskan keputusan ini pada diagram atau README submission.

Agent perlu mengubah pertanyaan analitik menjadi kueri SQL hanya baca, atau representasi kueri terstruktur yang setara. Tool database hanya boleh membaca tabel dan kolom pada [gold-contract.md](gold-contract.md). Backend harus memvalidasi SQL **sebelum** mengirimnya ke Neon: satu pernyataan `SELECT`, tabel/kolom allowlist, tanpa operasi tulis atau akses schema lain, serta batas waktu, jumlah baris, dan ukuran hasil. Gunakan parameter untuk nilai filter yang berasal dari pengguna bila memungkinkan. Prefix `SELECT` saja tidak cukup sebagai validator.

Koneksi Neon peserta memakai role yang hanya memiliki akses baca ke Gold. Validasi backend dan izin database harus bekerja bersama. Jangan menjalankan SQL hasil model menggunakan kredensial admin penyelenggara.

### D. Jawaban, scope, dan prompt injection

Jawaban harus menyebutkan angka, unit, periode, dan filter yang dipakai. Untuk persentase, jelaskan pembilang serta penyebutnya. Contohnya, **on-time rate** menggunakan kiriman yang sudah delivered sebagai penyebut; kiriman `in_transit` tidak otomatis dianggap terlambat. Definisi lengkap ada di [gold-contract.md](gold-contract.md).

Jika hasil kueri kosong, periode di luar dataset, atau pertanyaan ambigu, berikan `no_data` atau minta klarifikasi. Jangan meminta model menebak angka. Tolak permintaan seperti ramalan cuaca, nasihat medis, menulis email, atau mengubah status kiriman karena semuanya di luar tujuan agent.

Uji dua sumber prompt injection: pesan pengguna dan teks `exception_note` yang dibaca dari Gold. Teks database tidak boleh menaikkan hak akses, mengganti instruksi agent, memicu `UPDATE`, atau membuat agent mengungkap secret. Jika pengguna meminta ringkasan catatan, agent boleh memperlakukannya sebagai isi data yang dikutip secara aman.

### E. Pengujian dan evaluasi

Buat minimal **12 kasus evaluasi yang dapat diulang**: empat ketepatan analitik, dua follow-up, dua scope/abstain, dua prompt injection, dan dua otorisasi/SQL. Untuk setiap kasus, catat input, expected, actual, pass/fail, dan alasan bila gagal. Tambahkan tes otomatis khusus untuk SQL hanya baca dan akses lintas pengguna. Gunakan [public-checks.md](public-checks.md) sebagai awal.

> **Checkpoint:** Pertanyaan analitik harus dapat ditelusuri ke hasil kueri yang benar-benar dijalankan. Jawaban yang terdengar masuk akal tetapi tidak didukung baris Gold tidak memenuhi milestone.

## 4. Yang Disediakan dan Batas Pekerjaan

| Disediakan penyelenggara | Dibangun peserta |
|---|---|
| Dataset Gold sintetis di Neon dan URL/role hanya baca | Backend API dan koneksi Gold |
| Kontrak schema, grain, dan definisi metrik | Agent, tool kueri, dan SQL guard |
| Dua token identitas demo melalui kanal terpisah | Verifikasi token dan otorisasi percakapan |
| `app.py` sebagai klien Streamlit yang bekerja dengan kontrak API | SQLite conversation store dan dukungan follow-up |
| Kasus uji publik dan rubrik | Tes, evaluasi, dokumentasi, dan demo |

Anda boleh mengganti UI Streamlit, tetapi seluruh perilaku wajib dan kontrak API untuk penilaian harus tetap dapat didemonstrasikan. Anda tidak perlu membuat dataset, pipeline Bronze/Silver/Gold, login produksi, atau tool dengan side effect.

## 5. Data dan Metrik yang Boleh Digunakan

| Tabel | Grain | Gunakan untuk |
|---|---|---|
| `gold.shipment_fact` | Satu baris per shipment | Filter service level, status, timestamp delivery, nilai refund, dan catatan exception |
| `gold.carrier_daily` | Satu tanggal order dan carrier | Volume serta on-time/late rate per carrier |
| `gold.city_daily` | Satu tanggal order dan kota tujuan | Tren harian/mingguan serta rate per kota |

Periode dataset adalah **1 Juli sampai 20 September 2026** berdasarkan tanggal order. `metric_date` pada tabel agregat sama dengan `order_date`. Jangan menggabungkan tabel agregat carrier dan kota lalu menjumlahkan metrik dari keduanya; tindakan itu menggandakan fakta. Periksa [gold-contract.md](gold-contract.md) sebelum menulis query dan gunakan pembilang/penyebut yang tepat.

## 6. Rekomendasi Struktur Implementasi

Anda bebas menyusun modul sendiri. Struktur berikut menunjukkan tanggung jawab yang perlu ada:

```text
submission/
├── app.py                  # Streamlit awal atau UI pengganti
├── Dockerfile              # image API dan UI buatan peserta
├── compose.yaml            # dua service, SQLite named volume
├── .dockerignore           # tidak mengirim secret/SQLite ke build
├── src/
│   ├── api.py              # endpoint dan validasi request/response
│   ├── auth.py             # token demo -> identitas server-side
│   ├── conversations.py    # SQLite dan pemeriksaan pemilik
│   ├── agent.py            # scope, konteks, tool loop, jawaban
│   ├── sql_guard.py        # validasi tabel/kolom dan batas SQL
│   └── gold.py             # koneksi Neon hanya baca
├── tests/
├── docs/
└── README.md
```

Nama file di atas hanya saran; artefak yang wajib dikumpulkan tercantum di [required_files.participant.md](required_files.participant.md).

## 7. Setup dan Menjalankan UI Awal

Gunakan Python 3.11 atau lebih baru. Penyelenggara akan memberikan URL Neon role hanya baca serta dua token demo melalui kanal aman. Anda menyiapkan credential model sendiri atau menggunakan fasilitas yang disediakan kelas. Simpan semua nilai rahasia dalam environment lokal, bukan kode atau Git.

Contoh nama konfigurasi backend: `GOLD_DATABASE_URL`, `DEMO_TOKEN_A`, `DEMO_TOKEN_B`, `SQLITE_PATH`, dan variabel API key/model sesuai provider. Nama variabel backend boleh berbeda jika didokumentasikan. `AGENT_API_URL` dipakai oleh UI awal dan default ke `http://localhost:8000`.

Jalankan UI pada Windows PowerShell setelah backend Anda tersedia:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:AGENT_API_URL = "http://localhost:8000"
python -m streamlit run app.py
```

Di macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export AGENT_API_URL=http://localhost:8000
python -m streamlit run app.py
```

File `requirements.txt` yang disediakan hanya berisi dependency **UI awal**. Tambahkan dependency backend pilihan Anda pada submission. Mulai API dengan perintah yang sesuai implementasi Anda dan dokumentasikan perintah tepatnya dalam README submission. UI berkomunikasi dengan API; UI tidak dapat menjawab sebelum backend berjalan.

### Menjalankan submission dengan Docker

Dockerfile adalah pekerjaan peserta. Mulai dari `compose.yaml` scaffold dan `.dockerignore` yang disediakan di folder milestone. Sesuaikan perintah `api` dengan entry point backend Anda bila perlu. Tetap gunakan nama service `api` dan `ui`, port internal API `8000`, port internal UI `8501`, serta `AGENT_API_URL=http://api:8000` agar grader dapat menjalankan semua submission dengan satu perintah.

Buat `.env` lokal dari `.env.example`, lalu isi konfigurasi runtime backend. Jangan masukkan credential admin, `.env`, token, atau file SQLite ke image; jangan gunakan build arguments untuk secret. API harus mendengar pada `0.0.0.0:8000`, UI pada `0.0.0.0:8501`, dan SQLite harus diarahkan ke `/app/data/conversations.sqlite3` atau path lain yang dipasang pada named volume. Pastikan direktori data dapat ditulis oleh user yang menjalankan container.

Windows PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build
```

macOS/Linux:

```bash
test -f .env || cp .env.example .env
docker compose up --build
```

Buka `http://127.0.0.1:8501`; API health check tersedia di `http://127.0.0.1:8000/healthz`. Jalankan `docker compose restart api`, lalu pastikan percakapan masih ada. `docker compose down` menghentikan service dan mempertahankan named volume; jangan gunakan `-v` sebelum verifikasi persistence selesai. Grader akan memakai Docker Compose v2 dan menjalankan perintah build/up yang sama dari root submission.

## 8. Kontrak dan Contoh Alur API

Endpoint dan bentuk JSON lengkap ada di [brief.md](brief.md#5-kontrak-api-untuk-ui-awal). Satu alur yang harus berhasil:

1. Pengguna A membuat percakapan melalui `POST /api/v1/conversations`.
2. A mengirim “Berapa jumlah kiriman pada Agustus 2026?” ke `/api/v1/conversations/{id}/messages`.
3. Backend memvalidasi token dan kepemilikan ID, lalu agent mengambil data Gold.
4. Respons mengandung `answer`, `data_basis`, dan `outcome` (`answered`, `clarify`, `refused`, atau `no_data`).
5. A mengirim follow-up ke ID yang sama; agent mempertahankan konteks yang relevan.
6. Pengguna B memakai token B untuk membaca ID A; backend mengembalikan `404`.

Token salah menghasilkan `401`. Permintaan di luar scope menghasilkan `refused` tanpa menjalankan kueri Gold. Saat Neon atau provider gagal, berikan respons error aman; jangan mengisi jawaban dengan angka rekaan.

## 9. Rencana Kerja dan Checkpoint

| Sesi | Pekerjaan utama | Bukti checkpoint |
|---|---|---|
| 1 | Pahami Gold, buat query pembanding, koneksi role hanya baca, SQL guard, dan agent dasar | Dua pertanyaan analitik menghasilkan angka yang dapat dihitung ulang; SQL tulis ditolak |
| 2 | API, token demo, SQLite, UI, dan follow-up | Follow-up bekerja; B tidak dapat list/read/send pada percakapan A |
| 3 | Penolakan scope, prompt injection, error handling, Docker packaging, evaluasi, dan demo | Minimal 12 kasus tercatat; image dan Compose berjalan; serangan user dan database tidak mengubah perilaku agent |

Pada setiap sesi, simpan bukti kegagalan dan perbaikannya. Lakukan pengujian langsung melalui API selain UI; menyembunyikan elemen di UI tidak cukup untuk mencegah akses lintas pengguna.

## 10. File yang Wajib Dikumpulkan

Kumpulkan implementasi API, UI, dependency/config contoh tanpa secret, tes, diagram arsitektur, laporan evaluasi, demo script, dan output tes yang disamarkan. Struktur dan deskripsi tiap berkas ada di [required_files.participant.md](required_files.participant.md). Gunakan [submission-checklist.md](submission-checklist.md) sebelum mengunggah.

Jangan kumpulkan `.env`, token demo, API key, koneksi Neon, file SQLite berisi percakapan, dump Gold, atau raw chain-of-thought.

## 11. Penilaian dan Demonstrasi

Rubrik [100 poin](rubric.md) mencakup ketepatan analitik (25), keamanan SQL/Gold (20), percakapan (15), otorisasi (15), scope dan injection (15), container/reproducibility (5), serta evaluasi/dokumentasi/demo (5). Kebocoran percakapan, write ke Gold, injection yang berhasil, atau angka tanpa bukti kueri dapat memicu **hard gate** dan membatasi nilai akhir.

Dalam demo 5–7 menit, tunjukkan:

1. Satu pertanyaan analitik dengan angka, periode, dan dasar data.
2. Satu follow-up dalam percakapan yang sama.
3. Pertanyaan di luar scope atau tanpa data yang dijawab dengan tepat.
4. Prompt injection dari pesan atau catatan database yang tidak diikuti.
5. Pengguna B gagal membaca serta mengirim pesan ke percakapan A.
6. Bukti SQL hanya baca dan ringkasan hasil evaluasi.

> **Production Note:** Token demo dan SQLite lokal cukup untuk latihan ini. Dalam sistem nyata, identitas, retensi percakapan, penyimpanan bersama, audit, pemantauan, dan pengelolaan secret memerlukan desain tambahan. Jelaskan gap paling penting untuk solusi Anda.
