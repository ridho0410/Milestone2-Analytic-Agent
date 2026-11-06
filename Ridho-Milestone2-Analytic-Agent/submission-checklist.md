# Checklist Pengumpulan

- [ ] Semua berkas pada `required_files.participant.md` tersedia.
- [ ] UI awal atau pengganti dapat memulai, membuka, dan melanjutkan percakapan.
- [ ] Semua jawaban numerik dapat diperiksa terhadap Gold dan menyebutkan filter/periode.
- [ ] Kredensial Gold hanya baca dan kueri dijaga oleh validator serta batas eksekusi.
- [ ] Permintaan di luar scope dan instruksi pada `exception_note` ditangani dengan aman.
- [ ] Token B tidak dapat list/read/send pada percakapan A; bukti test disertakan.
- [ ] Minimal 12 kasus evaluasi dengan expected, actual, dan status terdokumentasi.
- [ ] README memuat setup Windows dan macOS/Linux, test, serta keterbatasan produksi.
- [ ] Dockerfile buatan peserta berhasil dibangun dengan `docker compose build`.
- [ ] `docker compose up --build` menjalankan API dan UI pada port container 8000 dan 8501; UI memakai `http://api:8000`.
- [ ] `.dockerignore` mengecualikan `.env`, virtual environment, cache, dan file SQLite; credential hanya diberikan saat runtime.
- [ ] Percakapan tetap tersimpan setelah service API direstart karena SQLite memakai named volume.
- [ ] Demo 5–7 menit mencakup happy path, follow-up, refusal/no data, injection, dan IDOR.
- [ ] Tidak ada secret, file SQLite, atau data pribadi nyata dalam submission.
