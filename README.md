# Agent Analitik Pengiriman

Chatbot untuk menganalisis pengiriman paket melalui pertanyaan bahasa alami. Pengguna dapat menanyakan jumlah kiriman, ketepatan waktu carrier, tren per kota, dan refund, lalu melanjutkan pertanyaan dalam percakapan yang sama.

Aplikasi menggunakan **Streamlit** sebagai antarmuka, **FastAPI** sebagai backend, dan **LangChain/ChatOpenAI** untuk memahami pertanyaan serta menyusun proposal SQL. Data analitik dibaca dari tabel Gold di **Neon PostgreSQL** atau CSV lokal. Riwayat percakapan disimpan terpisah di **SQLite** dan dibatasi berdasarkan identitas dari token demo.

## Fitur utama

- Analisis berdasarkan hasil kueri, disertai periode, filter, dan dasar data.
- Pertanyaan lanjutan yang memakai konteks percakapan sebelumnya.
- Grafik untuk hasil yang sesuai, serta riwayat pesan dan grafik yang tersimpan.
- Membuat, membuka, mengganti judul, dan menghapus percakapan milik pengguna sendiri.
- Validasi SQL hanya baca, pembatasan eksekusi, dan penolakan permintaan di luar analitik pengiriman.

```text
Pengguna → Streamlit → FastAPI + verifikasi token → Agent → SQL guard → Gold
                           ↕
                    SQLite percakapan
```

## 1. Persiapan

Unduh atau clone proyek, lalu buka folder utamanya di VS Code. Jalankan semua perintah dari folder yang berisi `app.py`, `src/`, `requirements.lock.txt`, dan `compose.yaml`.

Pilih salah satu cara menjalankan:

- **Docker:** gunakan Docker Desktop/Engine yang sudah berjalan dan Docker Compose v2. Python di komputer host tidak diperlukan.
- **Python lokal:** gunakan Python **3.13** agar sesuai dengan lingkungan Dockerfile dan lock file proyek.

Untuk menjawab pertanyaan, siapkan API key dan akses model yang mendukung **tool calling** serta **structured output**. Untuk jalur Neon, siapkan juga URL koneksi Gold dengan role **hanya baca** dari penyelenggara atau database yang sudah disiapkan sendiri.

## 2. Buat konfigurasi `.env`

Salin `.env.example` menjadi `.env` jika belum ada.

**Windows PowerShell:**

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

**macOS/Linux:**

```bash
if [ ! -f .env ]; then cp .env.example .env; fi
```

Buka `.env` dan ubah nilai yang sesuai. Berikut konfigurasi untuk **analisis dengan data Neon dan model aktif**:

```dotenv
GOLD_BACKEND=neon
GOLD_DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
DEMO_TOKEN_A=GANTI_DENGAN_TOKEN_ACAK_A
DEMO_TOKEN_B=GANTI_DENGAN_TOKEN_ACAK_B
LLM_ENABLED=true
OPENAI_API_KEY=GANTI_DENGAN_API_KEY_ANDA
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
SQLITE_PATH=./data/conversations.sqlite3
```

Ganti seluruh placeholder dengan nilai milik sendiri. Gunakan dua token demo yang panjang, acak, tidak kosong, dan berbeda; token tersebut nantinya dimasukkan pada UI. Jangan menambahkan nama variabel yang sama dua kali.

`gpt-4o-mini` adalah default konfigurasi proyek, bukan jaminan akses model pada setiap akun. Sesuaikan model/provider dengan akses yang tersedia dan kompatibilitas tool calling/structured output. Pemakaian model dapat dikenai biaya.

**Penting:** `.env.example` memulai dengan `LLM_ENABLED=false`. Pada implementasi saat ini, nilai tersebut menonaktifkan analisis dan menghasilkan `no_data` dengan pesan bahwa layanan AI dinonaktifkan. Ini bukan mode chatbot analitik offline. Atur `true` dan isi API key untuk mencoba pertanyaan analitik.

### Alternatif: data CSV lokal

Untuk mencoba tanpa koneksi Neon, ubah pengaturan sumber data:

```dotenv
GOLD_BACKEND=sqlite
GOLD_DATABASE_URL=
GOLD_DATA_DIR=./data
```

Pastikan dataset sintetis berikut tersedia dari paket proyek atau penyelenggara:

```text
data/
├── shipment_fact.csv
├── carrier_daily.csv
└── city_daily.csv
```

Backend memuat CSV tersebut ke SQLite dalam memori untuk kueri analitik. Ini berbeda dari file SQLite yang menyimpan percakapan. Model tetap diperlukan untuk memahami pertanyaan; `LLM_ENABLED=true` dan API key tetap berlaku.

**Jika memakai Docker dengan CSV, atur `GOLD_DATA_DIR=/app/gold-data`.** Compose sudah memasang folder `./data` pada lokasi tersebut secara read-only. Dataset tidak disalin ke image. Untuk demonstrasi milestone dengan fakta dari Neon, gunakan kembali `GOLD_BACKEND=neon`.

## 3. Jalankan dengan Docker

Setelah `.env` siap:

```bash
docker compose up -d --build
docker compose ps
```

Buka:

| Layanan | Alamat |
|---|---|
| Aplikasi Streamlit | http://localhost:8501 |
| Health API | http://localhost:8000/healthz |
| Dokumentasi API | http://localhost:8000/docs |

**Tanda berhasil:** service `api` sehat dan `ui` berjalan. Jika belum siap, periksa log:

```bash
docker compose logs --tail 100 api
docker compose logs --tail 100 ui
```

UI container mengakses API melalui `http://api:8000`. Percakapan disimpan pada named volume `conversation-data`, dengan path container `/app/data/conversations.sqlite3`.

Setelah mengubah `.env`, jalankan kembali `docker compose up -d` agar konfigurasi container diperbarui. Untuk menghentikan aplikasi tanpa menghapus volume:

```bash
docker compose down
```

Hindari `docker compose down -v` jika riwayat percakapan masih diperlukan karena opsi tersebut menghapus named volume.

## 4. Alternatif: jalankan dengan Python lokal

Gunakan `.env` dari bagian 2. Jika memakai CSV, pastikan `GOLD_DATA_DIR=./data`. Hentikan service Docker yang memakai port 8000/8501 sebelum menjalankan API/UI lokal.

### Windows PowerShell

**Terminal pertama — instal dependensi dan jalankan API:**

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m uvicorn src.api:app --host 127.0.0.1 --port 8000
```

Pastikan `python --version` menunjukkan Python 3.13. Biarkan terminal API tetap berjalan.

**Terminal kedua — jalankan UI dari folder proyek yang sama:**

```powershell
$env:AGENT_API_URL = 'http://localhost:8000'
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

### macOS/Linux

**Terminal pertama:**

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
```

**Terminal kedua, dari folder proyek yang sama:**

```bash
AGENT_API_URL=http://localhost:8000 .venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Buka `http://localhost:8501`. Hentikan API/UI dengan **Ctrl+C** pada masing-masing terminal. Perintah di atas memakai interpreter `.venv` secara langsung sehingga tidak perlu mengaktifkan virtual environment secara manual.

## 5. Coba aplikasi

1. Pada sidebar **Demo bearer token**, masukkan nilai `DEMO_TOKEN_A` dari `.env`, bukan tulisan nama variabelnya.
2. Klik **New conversation**.
3. Kirim: **“Berapa jumlah kiriman pada Agustus 2026?”**
4. Coba: **“Bagaimana tren kiriman ke Bandung per minggu pada Agustus 2026?”**, kemudian lanjutkan **“Bagaimana dibanding minggu sebelumnya?”** dalam percakapan yang sama.
5. Buka **View data details** untuk melihat dasar data. Grafik muncul bila hasil kueri mendukung visualisasi.

Mengganti token ke `DEMO_TOKEN_B` menampilkan daftar percakapan pengguna B. Token salah menghasilkan HTTP `401`; mengakses percakapan milik pengguna lain menghasilkan `404`.

Respons agent memakai empat status:

| Status | Makna |
|---|---|
| `answered` | Jawaban dihasilkan dari hasil kueri yang lolos pemeriksaan. |
| `clarify` | Informasi belum cukup atau batas pemrosesan tercapai; pengguna diminta memperjelas/mempersempit permintaan. |
| `refused` | Permintaan berada di luar lingkup analitik pengiriman. |
| `no_data` | Data tidak tersedia, model dinonaktifkan, atau proses model/validasi/akses Gold gagal; baca pesan yang menyertainya. |

`GET /healthz` hanya menunjukkan API hidup. Respons `{"status":"ok"}` belum membuktikan koneksi Gold atau panggilan model berhasil. Untuk memeriksa Gold dan apakah model aktif, gunakan `GET /api/v1/health/detail` dengan header `Authorization: Bearer <TOKEN_DEMO>` melalui dokumentasi API. Nilai `llm_enabled=true` menunjukkan klien model aktif, bukan bukti provider sudah berhasil menjawab.

## 6. Data dan definisi metrik

Kontrak dataset mencakup tanggal order **1 Juli–20 September 2026**, sehingga September tidak mencakup satu bulan penuh.

| Tabel | Isi utama |
|---|---|
| `gold.shipment_fact` | Satu baris per kiriman, termasuk status, biaya, refund, dan catatan operasional. |
| `gold.carrier_daily` | Ringkasan per tanggal order dan carrier. |
| `gold.city_daily` | Ringkasan per tanggal order dan kota tujuan. |

On-time rate memakai **kiriman delivered** sebagai penyebut. Refund rate adalah **nilai refund dibagi biaya kirim**, bukan persentase jumlah kiriman. Definisi lengkap ada di [gold-contract.md](gold-contract.md).

## 7. Jalankan pengujian

Tes offline memakai tiga CSV sintetis sebagai sumber data, SQLite sementara untuk percakapan, serta model tiruan. Setelah dataset dan dependensi tersedia, tes tidak memerlukan koneksi Neon, API key, atau server API/UI yang sedang berjalan.

### 7.1. Dapatkan dataset CSV

Dataset tersedia di [folder data repository](https://github.com/ridho0410/Milestone2-Analytic-Agent/tree/main/Ridho-Milestone2-Analytic-Agent/data).

**Jika baru mengambil proyek**, clone repository lalu masuk ke folder aplikasi:

```bash
git clone https://github.com/ridho0410/Milestone2-Analytic-Agent.git
cd Milestone2-Analytic-Agent/Ridho-Milestone2-Analytic-Agent
```

Ketiga CSV sudah disertakan pada repository. Jika sudah clone atau mengunduh ZIP proyek, masuk ke folder `Ridho-Milestone2-Analytic-Agent` dan periksa `data/`; tidak perlu mengunduh ulang jika filenya sudah ada.

**Jika CSV belum tersedia pada salinan proyek**, gunakan tautan berikut:

| File | Tautan unduhan CSV |
|---|---|
| `shipment_fact.csv` | [Unduh shipment_fact.csv](https://raw.githubusercontent.com/ridho0410/Milestone2-Analytic-Agent/main/Ridho-Milestone2-Analytic-Agent/data/shipment_fact.csv) |
| `carrier_daily.csv` | [Unduh carrier_daily.csv](https://raw.githubusercontent.com/ridho0410/Milestone2-Analytic-Agent/main/Ridho-Milestone2-Analytic-Agent/data/carrier_daily.csv) |
| `city_daily.csv` | [Unduh city_daily.csv](https://raw.githubusercontent.com/ridho0410/Milestone2-Analytic-Agent/main/Ridho-Milestone2-Analytic-Agent/data/city_daily.csv) |

Simpan file dengan nama aslinya, tanpa tambahan `.txt`. Untuk mengunduh langsung melalui **Windows PowerShell**, jalankan dari folder aplikasi:

```powershell
New-Item -ItemType Directory -Path 'data' -Force | Out-Null
$csvBaseUrl = 'https://raw.githubusercontent.com/ridho0410/Milestone2-Analytic-Agent/main/Ridho-Milestone2-Analytic-Agent/data'
Invoke-WebRequest -Uri "$csvBaseUrl/shipment_fact.csv" -OutFile 'data/shipment_fact.csv'
Invoke-WebRequest -Uri "$csvBaseUrl/carrier_daily.csv" -OutFile 'data/carrier_daily.csv'
Invoke-WebRequest -Uri "$csvBaseUrl/city_daily.csv" -OutFile 'data/city_daily.csv'
```

**Tanda siap:** susunan folder aplikasi menjadi seperti ini:

```text
Ridho-Milestone2-Analytic-Agent/
├── app.py
├── requirements.lock.txt
├── src/
├── tests/
└── data/
    ├── shipment_fact.csv
    ├── carrier_daily.csv
    └── city_daily.csv
```

Snapshot CSV yang diperiksa berisi 600 baris kiriman, 246 baris ringkasan carrier, dan 323 baris ringkasan kota, tidak termasuk header. Checksum snapshot tersebut cocok dengan `data/manifest.json` pada paket proyek.

### 7.2. Jalankan tes offline

Jalankan dari folder aplikasi yang berisi `tests/`, setelah dependensi terpasang melalui `requirements.lock.txt`. Untuk instalasi Docker saja, siapkan lingkungan Python bagian 4 jika ingin menjalankan tes di host.

**Windows PowerShell:**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

**macOS/Linux:**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
```

**Tanda berhasil:** pytest selesai tanpa test gagal; ringkasan menampilkan jumlah test yang lulus, dan `pip check` tidak melaporkan dependensi yang bermasalah. Gunakan hasil eksekusi sendiri saat melaporkan kelulusan.

`tests/conftest.py` otomatis memilih backend CSV/SQLite, mengosongkan API key, dan mengisolasi percakapan. Tidak perlu mengganti `.env` yang biasa dipakai untuk demo Neon atau model aktif. Jika muncul pesan CSV tidak ditemukan, periksa nama file dan susunan folder pada bagian 7.1.

Panduan ini menggunakan pytest karena folder `scripts/` belum disertakan pada versi repository yang diperiksa. Perintah `python -m scripts.evaluate` tidak digunakan pada paket tersebut.

Tes offline memeriksa perilaku aplikasi dengan data dan model uji; kelulusannya tidak membuktikan koneksi Neon atau provider model nyata. Untuk mencoba chatbot interaktif dengan CSV, model aktif dan API key tetap diperlukan seperti dijelaskan pada bagian 2.

## 8. Troubleshooting

| Masalah | Pemeriksaan |
|---|---|
| HTTP `401` | Cocokkan token UI dengan `.env`. Pastikan kedua token di server tidak kosong dan berbeda. |
| Pesan layanan AI dinonaktifkan | Ubah `LLM_ENABLED=true`, isi API key, lalu restart API atau terapkan ulang Compose. |
| Gold tidak tersedia / `gold_reachable=false` | Untuk Neon, periksa URL, jaringan, role, dan tiga tabel Gold. Untuk CSV, periksa file dan `GOLD_DATA_DIR` sesuai lokasi host/container. |
| Model gagal memproses | Periksa API key, akses model, base URL, dukungan tool calling/structured output, dan log API. |
| UI tidak terhubung ke backend | Periksa API hidup dan `AGENT_API_URL`: `http://localhost:8000` untuk UI lokal, `http://api:8000` di Compose. |
| Port sudah digunakan | Hentikan instance API/UI lain yang memakai 8000/8501. |
| Konfigurasi `.env` tidak diterapkan | Environment variable pada terminal lebih diprioritaskan. Restart proses lokal; untuk Docker jalankan kembali `docker compose up -d`. |
| Hasil kosong atau batas pemrosesan tercapai | Gunakan periode dalam cakupan dataset dan persempit filter/periode. |

## 9. Struktur dan dokumentasi

```text
app.py                  UI Streamlit
src/api.py              Endpoint API
src/auth.py             Verifikasi token demo
src/conversations.py    SQLite dan kepemilikan percakapan
src/agent.py             Alur agent dan konteks follow-up
src/tools.py             Tool query, klarifikasi, dan pelaporan unavailable
src/llm_client.py        Klien model LangChain
src/sql_guard.py         Validasi SQL
src/query_engine.py      Validasi metrik dan eksekusi kueri
src/gold.py              Akses Neon atau CSV lokal
tests/                   Pengujian otomatis
docs/                    Dokumentasi teknis dan demo
```

README ini menjadi panduan utama menjalankan aplikasi. Bacaan lanjutan:

- [Arsitektur](docs/architecture.md), [detail implementasi](docs/implementation.md), dan [langkah demo](docs/demo.md).
- [Laporan evaluasi](docs/evaluation.md), dengan batas bukti pengujiannya.
- [Brief tugas](brief.md), [rubrik](rubric.md), dan [berkas wajib submission](required_files.participant.md).

`docs/reference/` berisi arsip historis, bukan konfigurasi runtime. Verifier referensi membandingkan dokumen dengan arsip, sehingga perubahan README aktif dapat terdeteksi sebagai perbedaan referensi.

## Batas penggunaan

SQL agent dibatasi ke satu tabel Gold per kueri, tanpa join, CTE, subquery, atau window function. Validasi aplikasi tetap harus disertai role Neon hanya baca. Model dapat salah memahami maksud pertanyaan; pemeriksaan SQL dan hasil tidak menjadi jaminan seluruh makna analisis benar.

Gunakan token demo dan SQLite percakapan untuk latihan. Deployment produksi memerlukan autentikasi nyata, pengelolaan secret, retensi data, penyimpanan bersama, pembatasan permintaan, dan monitoring. Uji beban/multi-instance belum dibuktikan dalam panduan ini.

Jangan mengunggah `.env`, API key, token, URL koneksi berkredensial, atau SQLite berisi percakapan. Pesan, konteks percakapan, dan informasi yang dibutuhkan penyusunan kueri dapat dikirim ke provider model; pertimbangkan isinya sebelum memakai informasi sensitif.
