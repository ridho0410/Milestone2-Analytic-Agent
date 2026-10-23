# Image tunggal untuk service `api` dan `ui` (lihat compose.yaml).
# Dibangun dari root submission; TIDAK ada credential di build context.

FROM python:3.13-slim

# - PYTHONDONTWRITEBYTECODE: image bersih tanpa .pyc
# - PYTHONUNBUFFERED: log langsung keluar (penting untuk `docker compose logs`)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Lapisan dependensi dipisah agar cache build tidak invalid saat kode berubah.
COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

# Kode aplikasi. Folder `data/` SENGAJA tidak disalin: dataset Gold tidak boleh
# ikut ke dalam image. Jalur produksi memakai Neon melalui GOLD_BACKEND=neon.
# Untuk demo offline, mount folder data penyelenggara sebagai volume (lihat
# bagian opsional di compose.yaml).
COPY src/ ./src/
COPY app.py ./
COPY .streamlit/config.toml ./.streamlit/config.toml

# Direktori data untuk SQLite percakapan. Dibuat dan diberi hak tulis SEBELUM
# beralih ke user non-root, karena volume bernama di-mount ke sini saat runtime
# dan harus dapat ditulis oleh user yang menjalankan container.
RUN mkdir -p /app/data && \
    useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app
USER appuser

# Port internal yang diwajibkan grader.
EXPOSE 8000 8501

# Default: API. Service `ui` menimpa perintah ini di compose.yaml.
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
