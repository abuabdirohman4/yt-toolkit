# Penamaan File — yt-toolkit & yt-research

Mengikuti aturan vault `second-brain/CLAUDE.md` §2 dan pola yang sudah dipakai
di `1.Projects/youtube-new/references/`.

Terakhir diperbarui: 11 September 2026

---

## Aturan dasar (dari vault §2)

| Objek | Aturan |
|---|---|
| Folder topik/project | kebab-case — `wealth-logic/`, `channel-images/` |
| Folder berprefix angka | `<angka>_` lalu snake_case — `1_script/` |
| File `.md` | snake_case |
| File non-`.md` | jangan rename kalau sudah dirujuk file lain |

---

## Pola nama file hasil

```
{jenis}_{cakupan}_{channel}.{ext}
```

- **jenis** — `transcripts`, `video_data`, `channel_info`, `analytics`
- **cakupan** — `all`, atau `{N}_{urutan}` seperti `10_popular`, `5_latest`
- **channel** — nama channel, lowercase snake_case

Cakupan itu bagian terpenting: dari namanya langsung ketahuan isinya berapa
video dan urutan apa, tanpa perlu membuka file.

### Contoh yang sudah dipakai

```
transcripts_10_popular_wealth_logic.txt
video_data_10_popular_wealth_logic.csv
video_data_all_logical_money.csv
analytics_28d_quick_in_explain.csv        ← channel sendiri, cakupan = periode
analytics_28d_quick_in_explain.md
```

Untuk `analytics` cakupannya **periode**, bukan jumlah video: `28d`, `90d`.
Yang membedakan dua laporan channel yang sama adalah rentang waktunya.

### Struktur folder referensi channel

```
references/wealth-logic/                     ← kebab-case
  transcripts_10_popular_wealth_logic.txt
  video_data_10_popular_wealth_logic.csv
  video_data_all_wealth_logic.csv
  channel_info_wealth_logic.csv
  channel-images/
    wealth_logic_avatar.jpg
    wealth_logic_banner.jpg
  thumbnails/
    wealth_logic_001.jpg … _010.jpg
  videos/
    {slug-judul-video}/                      ← kebab-case
      {slug-judul-video}.mp4
      slides.md
      audio.md
      slide_000.jpg …
```

File di dalam folder video (`slides.md`, `audio.md`, `slide_000.jpg`) tidak
perlu memuat nama video — folder induknya sudah menyebutkan.

### Folder kebab-case, FILE snake_case

| Objek | Gaya | Contoh |
|---|---|---|
| Folder topik/channel/video | **kebab-case** (`-`) | `marcus-explains/`, `the-economics-of-nightclubs/` |
| Semua file hasil tool | **snake_case** (`_`) | `the_economics_of_nightclubs.mp4`, `transcripts_10_popular_wealth_logic.txt` |

Jadi nama file video **sengaja berbeda** dari nama foldernya:

```
the-economics-of-nightclubs/          ← folder, tanda hubung
  the_economics_of_nightclubs.mp4     ← file, underscore
  slides.md
  audio.md
```

Aturan ini ditambahkan ke `second-brain/CLAUDE.md` §2 pada 12 Sep 2026, supaya
berlaku seragam di seluruh vault — bukan cuma di sini.

Berlaku untuk file yang **kita hasilkan sendiri** (unduhan video, thumbnail,
transcript, CSV). File kiriman orang atau unduhan pihak ketiga tetap memakai
nama aslinya, sesuai aturan vault yang sudah ada.

**Tanda kurung dibuang.** `Real Estate vs Stocks (The Real Math)` menjadi
`real_estate_vs_stocks_which_makes_more_money_the_real_math`. Kurung di nama
file merepotkan shell — kemarin sempat membuat pengukur ketajaman di
`yt-slides` gagal diam-diam. Folder lama `logical-money/` masih memakai kurung;
biarkan, file di dalamnya sudah dirapikan.

---

## Kondisi tool sekarang vs pola di atas

### yt-toolkit

| Command | Menghasilkan | Sesuai pola? |
|---|---|---|
| `yt-transcript` | `{channel}_all_transcripts.txt` | ✗ urutan terbalik, tanpa cakupan |
| `yt-channel` | `yt_channels_{timestamp}.csv` | ✗ tanpa nama channel |
| `yt-channel --info` | `channel-info.csv` | ✗ tanpa nama channel |
| `yt-channel --images` | `{channel}_avatar.jpg` | ✓ |
| `yt-channel --thumbnails` | `{channel}_001.jpg` | ✓ |
| `yt-channel --transcript` | `{channel}_transcripts.txt` | ✗ |
| `yt-slides` | `slides.md` + `slide_000.jpg` | ✓ (di dalam folder video) |
| `yt-audio` | `audio.md` | ✓ |

### Extension yt-research

| Mode | Menghasilkan | Sesuai pola? |
|---|---|---|
| Transcript | `{channel}_all_transcripts.txt` | ✗ |
| Research Niche | `yt-niche-research_{timestamp}.csv` | ✗ |
| Deep Dive — profil | `yt-channel-info_{timestamp}.csv` | ✗ |
| Deep Dive — video | `yt-video-data_{timestamp}.csv` | ✗ |

---

## Perubahan yang perlu dilakukan

| Sekarang | Jadi |
|---|---|
| `{channel}_all_transcripts.txt` | `transcripts_all_{channel}.txt` |
| (mode popular/latest) | `transcripts_{N}_{urutan}_{channel}.txt` |
| `yt_channels_{ts}.csv` | `video_data_all_{channel}.csv` |
| `channel-info.csv` | `channel_info_{channel}.csv` |
| `yt-niche-research_{ts}.csv` | `niche_research_{YYYYMMDD}.csv` |
| `yt-channel-info_{ts}.csv` | `channel_info_{channel}.csv` |
| `yt-video-data_{ts}.csv` | `video_data_{N}_{urutan}_{channel}.csv` |

Tidak berubah: `{channel}_avatar.jpg`, `{channel}_001.jpg`, `slides.md`,
`audio.md`, `slide_000.jpg`.

### Dua perbaikan kecil

**Underscore ganda.** File lama bernama `Wealth_Logic__001.jpg` karena nama
channel dari YouTube punya spasi di ujung ("Wealth Logic "). Sudah tertangani
oleh `slug()` yang merapatkan underscore beruntun dan memangkas ujungnya.

**Huruf besar.** File lama pakai `Wealth_Logic_`, sekarang `wealth_logic_`
mengikuti snake_case. File lama tidak perlu di-rename (aturan vault: file
non-`.md` jangan di-rename kalau sudah dirujuk).

---

## Timestamp

Dipakai **hanya** kalau tidak ada pembeda lain — yaitu `niche_research`, yang
tidak terikat satu channel. Sisanya tidak perlu: nama channel plus cakupan
sudah cukup membedakan.

Kalau file dengan nama sama sudah ada, Chrome otomatis menambah `(1)`, dan di
Python file lama tertimpa. Itu bisa diterima karena pengambilan ulang biasanya
memang dimaksudkan untuk memperbarui.

---

## Catatan penerapan

Hanya memengaruhi file baru. File lama tetap seperti sekarang dan tidak rusak:

- `yt-slides` mencocokkan transcript dari **judul di dalam file**, bukan dari
  nama filenya
- Tidak ada script yang membaca file berdasarkan pola nama

---

## Analytics channel sendiri

Keluar dari pola `references/` — ini channel milik sendiri, bukan referensi
kompetitor. Mendarat di `second-brain/0.Inbox/`:

| Tool | Keluaran |
|---|---|
| `yt-analytics` | `analytics_{N}d_{channel}.csv` + `.md` |
| `yt-dashboard` | `youtube_dashboard.html` (semua channel dalam satu halaman) |

Dashboard sengaja satu file yang ditimpa tiap kali — dipakai untuk dilihat
sekarang, bukan diarsipkan. CSV/markdown `yt-analytics` yang diarsipkan.

---

## Transcript file lokal (`yt-stt`)

Sumbernya berkas, bukan channel — jadi namanya ikut **nama berkasnya**, bukan
pola `{jenis}_{cakupan}_{channel}`:

```
01 CARA BERLANGGANAN MUREKA AI.mp4  ->  01_cara_berlangganan_mureka_ai.txt
```

Awalan angka dari nama asli dipertahankan supaya urutan modulnya tidak hilang.

Dengan `--gabung`, file gabungannya kembali ke pola biasa:

```
transcripts_12_modul_creatube_studio.txt
```
