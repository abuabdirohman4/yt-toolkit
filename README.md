# yt-toolkit

Kumpulan tool untuk mengambil data YouTube: transcript, data channel, dan slide dari file video.

Dipakai sendiri lewat terminal — bukan web app, tidak perlu API key, tidak perlu server.

---

## Setup

Sudah terpasang. Kalau pindah mesin atau venv-nya hilang:

```bash
cd ~/Documents/applications/3_resources/yt-toolkit
uv venv
uv pip install yt-dlp
```

`yt-slides` butuh ffmpeg: `brew install ffmpeg`

Alias sudah ada di `~/.zshrc`. Terminal baru langsung bisa; terminal yang sedang terbuka perlu `source ~/.zshrc`.

---

## Ringkasan command

| Command | Masukan | Keluaran |
|---|---|---|
| `yt-transcript` | URL playlist / video | `.txt` transcript per paragraf |
| `yt-channel` | URL channel | `.csv` data video (views, likes, dll) |
| `yt-slides` | file video `.mp4` | folder gambar slide + `slides.md` |
| `yt_download.py` | URL | file video (**belum terbukti jalan**) |

---

## 1. `yt-transcript` — ambil transcript

```bash
yt-transcript "https://youtube.com/playlist?list=XXXX"   # seluruh playlist
yt-transcript "https://youtube.com/watch?v=XXXX"         # satu video
yt-transcript "URL" -o ~/lain.txt                        # tujuan lain
```

Default keluaran: `second-brain/0.Inbox/{NamaPlaylist}_all_transcripts.txt`

**Bahasa otomatis.** Video Indonesia keluar Indonesia, video Inggris keluar Inggris. YouTube menyodorkan ~157 bahasa per video (1 asli + sisanya terjemahan mesin); yang diambil selalu yang **asli**.

**Format paragraf.** Caption mentah YouTube dipotong tiap 1–3 detik di tengah kalimat. Tool ini menyambungnya jadi paragraf utuh dengan timestamp tiap ~30 detik — lebih enak dibaca dan lebih hemat token kalau diberikan ke AI. Pemotongan hanya terjadi di akhir kalimat, jadi kalimat tidak pernah terbelah.

Ubah kerapatan paragraf lewat `PARA_SECONDS` di baris atas `yt_transcript.py`.

**Kalau URL punya `&list=`**, tool memilih playlist. Untuk satu video saja, potong bagian `&list=...`-nya.

---

## 2. `yt-channel` — data channel ke CSV

```bash
yt-channel "https://youtube.com/@NamaChannel"                  # cepat
yt-channel "URL" --deep                                        # angka persis
yt-channel "URL" --deep --transcript                           # + transcript
yt-channel urls.txt                                            # banyak channel
yt-channel "URL" --limit 20                                    # batasi video
```

Default keluaran: `second-brain/0.Inbox/yt_channels_{tanggal}.csv`

### Dua mode

| | Cepat (default) | `--deep` |
|---|---|---|
| Kecepatan | 1 request per channel | ~4 detik per video |
| Views | dibulatkan (85000) | persis (85873) |
| Likes, komentar, deskripsi | kosong | ada |
| Tanggal upload | kosong | ada |

Mode cepat memakai angka bulat yang sama seperti yang terbaca di layar YouTube. `--deep` membuka tiap video, jadi dapat angka sebenarnya.

### Flag

- `--limit N` — batasi jumlah video per channel
- `--delay N` — jeda antar video, default 4 detik. Jangan diturunkan untuk channel besar (kena rate-limit)
- `--transcript` — ikut ambil transcript, butuh `--deep`
- `-o file.csv` — tujuan lain

### Banyak channel sekaligus

Buat file teks berisi satu URL per baris (baris diawali `#` diabaikan), lalu:

```bash
yt-channel daftar.txt --deep
```

### Kolom CSV

`No.`, `Channel`, `Subscribers`, `Video Title`, `Video URL`, `Views`, `Likes`, `Comments`, `Duration`, `Upload Date`, `Days Ago`, `Description`

---

## 3. `yt-slides` — slide dari file video

Untuk video bergaya slideshow: ambil gambar tiap adegan, pasangkan dengan transcript di detik itu.

**Butuh file video di disk** (unduh manual dulu — lihat catatan di bawah).

```bash
yt-slides "video.mp4" -t "transcript.txt" --every 10               # disarankan
yt-slides "video.mp4" -t "transcript.txt" --every 10 --transitions # + tabel transisi
yt-slides "video.mp4"                                              # tanpa transcript
```

Keluaran: folder `{nama video}_slides/` berisi `slide_XXX.jpg` + `slides.md`.
Pakai `-o folder` untuk menaruh hasil di folder tertentu.

### Dua mode pengambilan

**`--every N` (sampling) — disarankan.** Ambil gambar tiap N detik. Tidak ada adegan yang terlewat, tapi ada duplikat kalau satu adegan bertahan lama.

**Tanpa `--every` (deteksi).** Hanya ambil saat gambar berubah drastis. Lebih sedikit gambar, tapi **bisa melewatkan adegan** — video dengan palet warna seragam (latar dan gaya mirip antar adegan) sering tidak terdeteksi berubah.

Untuk video bergaya konsisten seperti channel animasi, pakai `--every 10`.

### Flag

- `--every N` — sampling tiap N detik (10 = seimbang, 5 = rapat, 15 = jarang)
- `-t file.txt` — file transcript
- `--transitions` — tambahkan tabel kapan transisi terjadi + jenisnya
- `--threshold 0.3` — kepekaan deteksi (hanya mode deteksi)
- `--min-gap 8` — jarak minimum antar slide (hanya mode deteksi)
- `--settle 1.5` — tunggu N detik setelah pergantian sebelum ambil gambar

### Transcript otomatis dicocokkan

Satu file transcript gabungan (berisi banyak video) bisa dipakai untuk video mana pun — bagian yang benar dipilih berdasarkan kecocokan judul. Nama file boleh berupa slug (`real-estate-vs-stocks-...`) atau mengandung embel-embel (`(720p, h264)`).

Kalau kecocokannya kurang meyakinkan, tool **menolak dengan pesan jelas** daripada diam-diam memakai transcript video lain.

### Tabel transisi

`--transitions` menghasilkan tabel: kapan tiap transisi terjadi, kekuatannya, dan tebakan jenisnya (potongan langsung / whip pan / slide-fade). Berguna untuk membedah gaya editing sebuah channel.

Jenis transisi ditebak dari ketajaman frame saat transisi berlangsung — transisi bergerak meninggalkan frame kabur, potongan langsung tetap tajam. Pembedaan **gerak vs cut** cukup andal; pembagian antara "whip pan" dan "slide/fade" lebih longgar.

---

## Catatan penting

### Cookie browser

YouTube kini minta bukti "bukan bot". Tool otomatis meminjam cookie dari **Brave**, lalu Chrome, lalu Safari. Selama masih login YouTube di salah satunya, jalan terus.

Kalau muncul `tanpa cookie - YouTube mungkin menolak`, buka YouTube di Brave dan pastikan masih login.

### Rate limit (HTTP 429)

Server caption YouTube punya jatah terpisah yang lebih ketat dari API metadata. Kalau muncul `429 Too Many Requests`, itu jatah IP habis — bukan tool rusak. Tool sudah mencoba ulang otomatis (20s → 40s → 60s); kalau masih gagal, tunggu beberapa jam.

Untuk scraping banyak video, biarkan `--delay` di 4 detik atau lebih.

### Unduh video

`yt_download.py` ada, tapi **belum pernah berhasil diuji** — YouTube menolak menyerahkan stream video. Untuk sekarang unduh manual lewat situs downloader, pilih **480p** atau **SD** (cukup untuk `yt-slides`, teks slide masih terbaca, file ~40 MB per video).

### Batas yang perlu diketahui

**`yt-slides` tidak membaca isi gambar.** Tool memasangkan gambar dengan omongan berdasarkan **waktu**, bukan isi. Untuk tahu "slide ini grafik apa", gambarnya harus benar-benar dilihat — oleh kamu atau AI vision.

**Teks di `slides.md` adalah perkiraan.** Transcript dikelompokkan per ~30 detik sedangkan gambar diambil lebih rapat, jadi potongan teks dibagi proporsional menurut posisi waktu. Pergeseran 5–15 detik itu wajar — anggap sebagai konteks sekitar, bukan keterangan gambar. Penjelasan ini juga ditulis otomatis di kepala tiap `slides.md` supaya AI yang membacanya tidak salah simpul.

**`yt-channel --deep --transcript`**: bagian metadata sudah terbukti; bagian transcript belum pernah lolos pengujian karena rate limit. Kemungkinan besar jalan — mekanismenya sama dengan `yt-transcript` yang sudah terbukti.

---

## Struktur file

```
yt-toolkit/
├── yt_transcript.py    # transcript playlist/video      -> yt-transcript
├── yt_channel.py       # data channel ke CSV            -> yt-channel
├── yt_slides.py        # slide dari file video          -> yt-slides
├── yt_download.py      # unduh video (belum terbukti)
├── .venv/              # Python + yt-dlp
└── README.md
```

`yt_channel.py` dan `yt_download.py` mengimpor dari `yt_transcript.py` (cookie, pengambilan transcript). Kalau file itu di-rename, ketiganya harus ikut disesuaikan.

## Cek cepat kalau ada yang aneh

```bash
yt-transcript --selftest   # cek logika penyusunan paragraf
yt-slides --selftest       # cek pencocokan transcript dengan slide
```

Dua-duanya jalan tanpa jaringan.

---

## Tool YouTube lain

Repo terpisah, tidak saling bergantung — tiap tool berdiri sendiri:

- **[yt-toolkit](https://github.com/abuabdirohman4/yt-toolkit)** (repo ini) — transcript, data channel, ekstraksi slide. Python CLI.
- **[yt-research](https://github.com/abuabdirohman4/yt-research)** — riset channel kompetitor: cari channel dari niche, bedah channel orang lain. Chrome extension.
- **[yt-studio-scrape](https://github.com/abuabdirohman4/yt-studio-scrape)** — ambil data analytics dari YouTube Studio channel sendiri. Chrome extension.

Pembagiannya: `yt-research` untuk channel **orang lain**, `yt-studio-scrape` untuk channel **sendiri**, `yt-toolkit` untuk isi video (transcript, slide) dan data publik channel mana pun.
