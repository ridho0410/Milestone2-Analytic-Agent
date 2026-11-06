# Berkas Wajib Submission Peserta

Struktur nama modul internal boleh berbeda, tetapi submission harus memuat artefak berikut:

```text
submission/
├── README.md                  # setup, run, test, pilihan model, batas produksi
├── .env.example               # nama variabel saja, tanpa secret
├── requirements.txt           # atau pyproject.toml + lock file
├── Dockerfile                  # dibuat peserta untuk membangun image aplikasi
├── compose.yaml                # scaffold peserta sesuaikan untuk API/UI
├── .dockerignore               # mengecualikan secret, environment lokal, dan SQLite
├── app.py                     # UI yang disediakan atau UI pengganti
├── src/                       # API, auth demo, agent, SQL guard, Neon client, SQLite store
├── tests/                     # unit dan integration tests
├── docs/
│   ├── architecture.md        # diagram data flow dan trust boundary
│   ├── evaluation.md          # kasus, expected/actual, skor, analisis kegagalan
│   └── demo.md                # langkah demo 5–7 menit
└── evidence/
    └── test-output.txt        # output uji yang disamarkan
```

`README.md` harus memuat perintah Windows PowerShell serta macOS/Linux, konfigurasi Neon dan provider model, perintah menjalankan API/UI/test, serta daftar keterbatasan. `docs/architecture.md` harus menunjukkan identitas, kepemilikan percakapan, validasi SQL, role Gold hanya baca, dan perlakuan terhadap input tak tepercaya.

`docs/evaluation.md` harus mempunyai minimal 12 kasus sesuai brief. Sertakan minimal satu kasus yang menggunakan teks berbahaya dari kolom `exception_note`. Bukti boleh berupa test otomatis, transkrip yang disamarkan, atau keduanya; hasil harus dapat diulang oleh penilai.

`Dockerfile` harus dapat dibangun dari root submission tanpa credential di build context. `compose.yaml` harus memulai service `api` dan `ui` dengan `docker compose up --build`; UI mengakses API melalui `http://api:8000`, port container adalah `8000` dan `8501`, serta SQLite memakai named volume. Neon tetap layanan eksternal. `.dockerignore` harus mencegah `.env`, virtual environment, cache, dan file SQLite masuk ke image.

Jangan kumpulkan `.env`, token, API key, kredensial Neon, file SQLite berisi percakapan, raw dump database, atau chain-of-thought. Screenshot harus disamarkan.
