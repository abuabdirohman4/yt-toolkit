#!/usr/bin/env python3
"""
Transcript dari file video/audio apa pun — bukan dari YouTube.

yt_transcript.py mengunduh caption yang SUDAH dibuat YouTube. Ini mendengarkan
audionya sendiri (whisper-cpp lokal), jadi jalan untuk rekaman kelas, webinar,
meeting, voice note — apa saja yang punya suara.

    yt-stt video.mp4
    yt-stt folder/                  # semua video/audio di dalamnya
    yt-stt folder/ -o transcripts/  # keluaran ke folder lain
    yt-stt video.mp4 --lang en      # default: id

Keluarannya sengaja sama persis dengan yt_transcript.py (paragraf + [M:SS]),
supaya bisa dipasangkan ke yt_slides.py dan dibaca AI dengan pola yang sama.

Butuh: brew install whisper-cpp, lalu model di ~/.local/share/whisper-models/
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import date
from pathlib import Path

MODEL_DIR = Path.home() / ".local/share/whisper-models"
MEDIA = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v",
         ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus"}
PARAGRAF_DETIK = 30          # sama dengan yt_transcript.py
PARAGRAF_PAKSA = 2.5         # batas keras = kelipatan jendela normal (75 detik saat 30)
JEDA_PARAGRAF = 1.2          # jeda antar-segmen yang dianggap ganti napas
WRAP = 80


def slug(name, fallback="transcript", limit=80):
    """Nama file: lowercase snake_case, ikut NAMING.md."""
    s = re.sub(r"[^\w\s-]", "", (name or "").lower())
    s = re.sub(r"[\s-]+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:limit].strip("_") or fallback


def cari_model(nama=None):
    """Model yang dipakai. Tanpa --model, ambil yang paling besar (paling teliti)."""
    if nama:
        p = Path(nama).expanduser()
        if p.exists():
            return p
        p = MODEL_DIR / (nama if nama.endswith(".bin") else f"ggml-{nama}.bin")
        if p.exists():
            return p
        sys.exit(f"Model tak ditemukan: {nama}\nAda di {MODEL_DIR}: "
                 f"{[f.name for f in MODEL_DIR.glob('ggml-*.bin')] or '(kosong)'}")
    ada = sorted(MODEL_DIR.glob("ggml-*.bin"), key=lambda f: f.stat().st_size)
    if not ada:
        sys.exit(f"Belum ada model di {MODEL_DIR}.\n"
                 "Unduh: curl -L -o ~/.local/share/whisper-models/ggml-medium.bin \\\n"
                 "  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin")
    return ada[-1]


def ke_wav(src, dst):
    """Whisper hanya menerima WAV 16 kHz mono."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        check=True, capture_output=True)


def jalankan_whisper(wav, model, lang, threads):
    """-> list (mulai_detik, teks, akhir_detik). Pakai keluaran JSON, bukan teks polos.

    Sengaja TANPA --prompt (daftar istilah). Diuji 17 Sep 2026 di video 09 kelas
    Mas Tian: "ChatGPT" membaik, tapi "tahanan"->"Taman" dan "deskripsi"->"dan
    subjek" — dua kata biasa jadi rusak. Prompt whisper bukan kamus; ia dibaca
    sebagai kalimat yang baru diucapkan, jadi model ikut menirunya. Rugi bersih.
    """
    with tempfile.TemporaryDirectory() as d:
        keluar = Path(d) / "hasil"
        subprocess.run(
            ["whisper-cli", "-m", str(model), "-f", str(wav),
             "-l", lang, "-t", str(threads), "-oj", "-of", str(keluar),
             "-np", "-pp"],
            check=True, capture_output=True)
        data = json.loads((keluar.with_suffix(".json")).read_text(encoding="utf-8"))

    keluaran = []
    for seg in data.get("transcription", []):
        teks = (seg.get("text") or "").strip()
        if not teks:
            continue
        # offsets dalam milidetik; "from" kadang tak ada di segmen pertama
        off = seg.get("offsets") or {}
        mulai = off.get("from", 0) / 1000.0
        keluaran.append((mulai, teks, off.get("to", off.get("from", 0)) / 1000.0))
    return keluaran


def paragraf(cues, tiap=PARAGRAF_DETIK, paksa=None):
    """Sambung cue jadi paragraf, potong di tempat yang masuk akal.

    Memotong tiap 30 detik apa adanya membelah kalimat di tengah. Tapi menunggu
    tanda baca saja tidak cukup untuk hasil whisper: narasi lisan sering mengalir
    tanpa titik sama sekali, dan seluruh video jadi satu paragraf raksasa. Jadi
    ada tiga tempat boleh memotong, makin longgar makin lama menunggu:
    akhir kalimat, jeda panjang antar-segmen, lalu batas keras.
    """
    if not cues:
        return ""
    batas = paksa if paksa is not None else tiap * PARAGRAF_PAKSA
    blok, mulai, isi = [], cues[0][0], []
    for i, cue in enumerate(cues):
        detik, teks = cue[0], cue[1]
        akhir = cue[2] if len(cue) > 2 else detik
        isi.append(teks)
        lewat = detik - mulai >= tiap
        selesai = teks.rstrip().endswith((".", "?", "!"))
        # Jeda SUNYI: dari akhir segmen ini ke mulai segmen berikutnya. Mengukur
        # jarak antar-waktu-mulai keliru — itu termasuk durasi bicaranya sendiri,
        # jadi setiap segmen tampak seperti ganti napas.
        berikut = cues[i + 1][0] if i + 1 < len(cues) else None
        jeda = berikut is not None and berikut - akhir >= JEDA_PARAGRAF
        if (lewat and (selesai or jeda)) or (detik - mulai >= batas and not selesai):
            blok.append((mulai, " ".join(isi)))
            isi, mulai = [], berikut if berikut is not None else detik
    if isi:
        blok.append((mulai, " ".join(isi)))

    hasil = []
    for detik, teks in blok:
        teks = re.sub(r"\s+", " ", teks).strip()
        cap = f"[{int(detik) // 60}:{int(detik) % 60:02d}]"
        hasil.append(textwrap.fill(f"{cap} {teks}", WRAP,
                                   subsequent_indent=""))
    return "\n\n".join(hasil)


def daftar_media(target):
    p = Path(target).expanduser()
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(f for f in p.iterdir()
                      if f.is_file() and f.suffix.lower() in MEDIA)
    sys.exit(f"Tak ada: {p}")


def selftest():
    """Cek bagian yang bisa diam-diam salah: paragraf dan slug."""
    assert slug("01 CARA BERLANGGANAN MUREKA AI") == "01_cara_berlangganan_mureka_ai"
    assert slug("Judul: Aneh! (v2)") == "judul_aneh_v2"
    assert slug("") == "transcript"

    def periksa(out, cues):
        """Tiga jaminan yang berlaku apa pun aturan potongnya."""
        caps = re.findall(r"\[(\d+):(\d\d)\]", out)
        detik = [int(m) * 60 + int(s) for m, s in caps]
        assert detik == sorted(detik), detik          # timestamp naik urut
        gabung = re.sub(r"\[\d+:\d\d\]\s*", "", out).replace("\n", " ")
        for c in cues:                                # tak ada teks hilang
            assert c[1] in gabung, c[1]
        return caps

    # Rapat, bertanda baca: potong di akhir kalimat.
    cues = [(i * 2.0, "Kalimat panjang sekali" if i % 5 else "Selesai di sini.",
             i * 2.0 + 1.9) for i in range(30)]
    out = paragraf(cues, tiap=20)
    assert len(periksa(out, cues)) >= 2, out
    for blok in out.split("\n\n")[:-1]:
        assert blok.rstrip().endswith((".", "?", "!")), blok[-40:]

    # Narasi lisan tanpa tanda baca sama sekali — inilah keluaran whisper yang
    # sebenarnya. Dulu jadi satu paragraf raksasa; sekarang harus terpecah.
    lisan = [(i * 2.0, "terus kita klik yang ini ya", i * 2.0 + 1.9)
             for i in range(90)]
    out = paragraf(lisan, tiap=30)          # batas keras jatuh di 75 detik
    assert len(periksa(out, lisan)) >= 2, "narasi tanpa titik tidak terpecah"

    # Jeda sunyi panjang = ganti napas: boleh potong walau tanpa titik, dan
    # lebih awal daripada batas keras. Bicara rapat sampai detik 30, lalu diam
    # 5 detik.
    jeda = [(i * 2.0, "bicara terus", i * 2.0 + 1.9) for i in range(16)]
    jeda += [(37.0, "lanjut setelah diam", 39.0)]
    out = paragraf(jeda, tiap=30)
    caps = periksa(out, jeda)
    assert len(caps) == 2, out
    assert caps[1] == ("0", "37"), caps                # potong tepat di jeda

    # Tanpa jeda itu, potongan mundur ke batas keras (75 detik) — bukan 37.
    rapat = [(i * 2.0, "bicara terus", i * 2.0 + 1.9) for i in range(20)]
    assert len(periksa(paragraf(rapat, tiap=30), rapat)) == 1, "harusnya utuh"

    assert paragraf([]) == ""

    print("selftest OK")


def main():
    ap = argparse.ArgumentParser(
        description="Transcript file video/audio lokal (whisper-cpp)")
    ap.add_argument("target", nargs="?", help="file atau folder")
    ap.add_argument("-o", "--out", help="folder keluaran (default: di samping sumber)")
    ap.add_argument("--lang", default="id", help="bahasa (default id; en, auto, …)")
    ap.add_argument("--model", help="nama/berkas model (default: yang terbesar)")
    ap.add_argument("-t", "--threads", type=int, default=8)
    ap.add_argument("--gabung", metavar="NAMA",
                    help="tulis juga satu file gabungan dengan nama ini")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.target:
        ap.error("target wajib diisi (file atau folder)")
    if not shutil.which("whisper-cli"):
        sys.exit("whisper-cli tak ada. Pasang: brew install whisper-cpp")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg tak ada. Pasang: brew install ffmpeg")

    model = cari_model(args.model)
    berkas = daftar_media(args.target)
    if not berkas:
        sys.exit("Tak ada file video/audio di situ.")

    asal = Path(args.target).expanduser()
    out = (Path(args.out).expanduser() if args.out
           else (asal if asal.is_dir() else asal.parent))
    out.mkdir(parents=True, exist_ok=True)

    print(f"Model  : {model.name}", file=sys.stderr)
    print(f"Bahasa : {args.lang}", file=sys.stderr)
    print(f"Berkas : {len(berkas)}\n", file=sys.stderr)

    semua, gagal = [], 0
    mulai_semua = time.time()
    for i, f in enumerate(berkas, 1):
        print(f"[{i}/{len(berkas)}] {f.name}", file=sys.stderr)
        t0 = time.time()
        try:
            with tempfile.TemporaryDirectory() as d:
                wav = Path(d) / "audio.wav"
                ke_wav(f, wav)
                cues = jalankan_whisper(wav, model, args.lang, args.threads)
        except subprocess.CalledProcessError as e:
            pesan = (e.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            print(f"    GAGAL: {pesan[-1] if pesan else e}", file=sys.stderr)
            gagal += 1
            continue

        teks = paragraf(cues)
        nama = slug(f.stem)
        tujuan = out / f"{nama}.txt"
        tujuan.write_text(
            f"{f.stem}\n{'=' * len(f.stem)}\n\n{teks}\n", encoding="utf-8")
        semua.append((f.stem, teks))
        print(f"    {len(cues)} segmen · {len(teks):,} karakter · "
              f"{time.time() - t0:.0f} detik -> {tujuan.name}", file=sys.stderr)

    if args.gabung and semua:
        judul = args.gabung.upper()
        b = ["=" * 52, f"{judul} - TRANSCRIPT",
             f"Total: {len(semua)} berkas | {date.today().strftime('%-d/%-m/%Y')}",
             "=" * 52, "", ""]
        for n, (judul_f, teks) in enumerate(semua, 1):
            b += ["-" * 52, f"VIDEO {n}: {judul_f}", "-" * 52, "", teks, ""]
        g = out / f"transcripts_{len(semua)}_{slug(args.gabung)}.txt"
        g.write_text("\n".join(b), encoding="utf-8")
        print(f"\nGabungan -> {g.name}", file=sys.stderr)

    total = time.time() - mulai_semua
    print(f"\n{len(semua)}/{len(berkas)} berhasil · {total / 60:.0f} menit · {out}",
          file=sys.stderr)
    if gagal:
        sys.exit(f"{gagal} berkas gagal.")


if __name__ == "__main__":
    main()
