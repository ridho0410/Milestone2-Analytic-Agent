# Menjalankan dan menguji proyek

Panduan ini melengkapi dokumen soal root yang dipertahankan. Arsitektur ada di [architecture.md](architecture.md), hasil pengujian di [evaluation.md](evaluation.md), dan demo di [demo.md](demo.md). `docs/reference/` menyimpan arsip awal untuk pemeriksaan referensi, bukan panduan runtime.

## Docker Compose

Dari root proyek, buat `.env` dari `.env.example` hanya bila belum ada, lalu isi konfigurasi lokal. Jangan menimpa `.env` yang sudah dikonfigurasi.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose -p ridho-milestone2-analytic-agent up -d --build
docker compose -p ridho-milestone2-analytic-agent logs --tail 50 api
# Berhenti tanpa menghapus named volume percakapan:
docker compose -p ridho-milestone2-analytic-agent down
```

UI: `http://localhost:8501`; API: `http://localhost:8000`. UI container memakai `http://api:8000`. Dataset CSV dipasang read-only; `.env`, dataset dan SQLite tidak disalin ke image. Folder docs/tests/evidence tidak diperlukan saat chatbot berjalan dan dikecualikan dari image. Perubahan ketiga folder ini tidak memerlukan rebuild atau restart chatbot.

## Tampilan dan light/dark mode

Tema berada di `.streamlit/config.toml` dan ikut disalin oleh Dockerfile. Buka menu **⋮ → Theme**, lalu pilih **Light** atau **Dark**. Tema bawaan Streamlit menjaga warna input, sidebar, dialog, tabel dan grafik tetap konsisten; tidak ada switch CSS terpisah.

Chat kosong menampilkan empat contoh analisis. Klik contoh hanya mengisi kolom pertanyaan; tekan kirim untuk menjalankan analisis. Draf dibersihkan saat berganti token atau percakapan. Indikator proses tampil saat permintaan dikirim. Perubahan UI tidak mengubah kontrak API, fungsi render grafik, maupun isi jawaban dari backend.

## Python lokal

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m uvicorn src.api:app --host 127.0.0.1 --port 8000
# Terminal kedua, dari folder yang sama:
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
if [ ! -f .env ]; then cp .env.example .env; fi
.venv/bin/python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
# Terminal kedua:
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

## Konfigurasi tanpa nilai rahasia

- `LLM_ENABLED=true`: model memahami pertanyaan, menyusun SQL melalui native tool, meninjau SQL, dan bila memenuhi batas menyusun paragraf dari ID fakta. Alur normal tersebut menggunakan tiga model call. Kegagalan editorial memakai kalimat fakta deterministik. `false` menonaktifkan analisis dan mengembalikan `no_data`, bukan berpindah ke template offline.
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL`, `LLM_REASONING_EFFORT`, `LLM_API_MODE`: konfigurasi ChatOpenAI. Provider/model harus mendukung tool calling dan structured output. `auto` memilih Responses untuk model GPT-5.6, sedangkan `responses`/`chat_completions` dapat dipilih eksplisit. Kesesuaian reasoning mengikuti model. Tidak ada perpindahan provider otomatis.
- Konfigurasi lokal yang diperiksa pada 19 Oktober 2026 memilih `gpt-5.6-luna` dengan reasoning `high`; default model pada kode adalah `gpt-4o-mini`. Perapian ini tidak mengganti konfigurasi tersebut dan tidak menguji provider nyata; verifikasi otomatis memakai model tiruan.
- `GOLD_BACKEND=neon`, `GOLD_DATABASE_URL`: sumber Gold milestone dengan role hanya baca. `GOLD_BACKEND=sqlite` memakai CSV sintetis; dalam container gunakan `GOLD_DATA_DIR=/app/gold-data`. Backend tidak berganti otomatis saat database gagal.
- `DEMO_TOKEN_A`, `DEMO_TOKEN_B`: token berbeda yang diverifikasi server; masukkan salah satunya pada UI. `SQLITE_PATH` menentukan lokasi riwayat lokal; Compose menempatkannya pada named volume.
- `MAX_TOOL_CALLS`, `MAX_MODEL_CALLS`, `MAX_QUERY_CALLS`, `MAX_REPAIR_ATTEMPTS`, `AGENT_TIMEOUT_SECONDS`: budget per pesan, termasuk percobaan gagal. Kode membatasi repair SQL paling banyak satu. Default kode: 3 tool, 6 model, 3 query, 1 repair, dan 60 detik; nilai `.env` pengguna dapat berbeda.
- `GOLD_ROW_LIMIT`, `GOLD_RESULT_BYTE_LIMIT`, `GOLD_TIMEOUT_SECONDS`: default 500 baris, 200.000 byte, 8 detik. Hasil yang terpotong tidak diterbitkan sebagai analisis lengkap.

Jangan kumpulkan `.env`, kredensial, SQLite percakapan, dump database, atau chain-of-thought. Daftar variabel tersedia di `.env.example` tanpa menyalin nilai lokal ke dokumentasi.

## Pengujian yang dapat diulang

Windows PowerShell, dari root proyek:

```powershell
.\.venv\Scripts\python.exe -m pytest -vv --color=no -p no:cacheprovider --junitxml=evidence/tests.xml 2>&1 | Tee-Object -FilePath evidence/unit-test-output.txt
.\.venv\Scripts\python.exe -m scripts.evaluate
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.verify_reference
```

macOS/Linux:

```bash
set -o pipefail
.venv/bin/python -m pytest -vv --color=no -p no:cacheprovider --junitxml=evidence/tests.xml 2>&1 | tee evidence/unit-test-output.txt
.venv/bin/python -m scripts.evaluate
.venv/bin/python -m pip check
.venv/bin/python -m scripts.verify_reference
```

`tests/conftest.py` dan `scripts.evaluate` memakai CSV sintetis, penyimpanan sementara, tracing nonaktif, serta model tiruan/mock HTTP. Keduanya tidak memakai API berbayar atau riwayat pengguna. Jalur API diuji melalui TestClient, bukan layanan Docker produksi.

Tes dikelompokkan dalam `test_analysis.py` (angka CSV), `test_query_engine.py` (batas query/integrasi), `test_answers.py` (fakta/narasi), `test_limits_and_langchain.py` (budget/protokol model), `test_sql_guard.py` (SQL read-only), `test_authorization.py` (pemilik percakapan), `test_chart.py` dan `test_chart_history.py` (grafik/riwayat/UI). Semua pemeriksaan lama yang masih relevan dipertahankan.

Opsional setelah mengizinkan biaya/provider: `python -m scripts.verify_live --allow-model-call` menguji provider dengan CSV sintetis dan menulis `evidence/live-provider.json`. Skrip itu tidak dijalankan dalam perapian ini. `scripts.verify_neon` terpisah dan mencoba operasi tulis untuk menguji izin role; jangan menganggapnya pemeriksaan SELECT saja atau menjalankannya otomatis pada database yang belum diverifikasi.

## Batas dan kesesuaian submission

SQL agent hanya satu tabel tanpa join/CTE/subquery/window. CSV menerjemahkan bucket day/week/month/year; grain lain dapat ditolak. Review model bukan pembuktian formal atas makna pertanyaan, dan kebenaran hasil tetap bergantung pada data sumber. Autentikasi demo dan SQLite lokal memerlukan penggantian untuk SSO, penyimpanan bersama, retensi, pembatasan permintaan, serta observability produksi. Uji beban/multi-instance belum dilakukan.

Dokumen wajib `architecture.md`, `evaluation.md`, `demo.md`, unit/integration tests, dan bukti `evidence/test-output.txt` tersedia sesuai `required_files.participant.md`. Ada batas yang tetap terbuka: soal meminta panduan setup/model/test/batas produksi berada di README, sedangkan pengguna meminta README tidak diubah. Panduan aktif ditempatkan di sini. Verifier referensi juga mencatat perbedaan README yang sudah ada terhadap arsip awal; jangan mengubah hash arsip untuk menyembunyikannya.
