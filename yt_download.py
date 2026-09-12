#!/usr/bin/env python3
"""Unduh video YouTube (satu video / playlist) dengan resolusi terpilih.

Usage:
    python3 yt_download.py "URL"                    # 480p, default
    python3 yt_download.py "URL" -q 720             # resolusi lain
    python3 yt_download.py "URL" -q best            # terbaik yang ada
    python3 yt_download.py "URL_PLAYLIST"           # seluruh playlist
    python3 yt_download.py "URL" --limit 5          # batasi jumlah video
    python3 yt_download.py "URL" -o ~/folder        # folder tujuan
    python3 yt_download.py "URL" --audio            # audio saja (mp3)
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from yt_transcript import detect_cookie_browser

DEFAULT_DIR = Path.home() / "Downloads/yt"
WARN_COUNT = 10          # di atas ini, minta konfirmasi dulu


def kebab(name, limit=80):
    """Judul video -> nama file snake_case lowercase.

    SEMUA file pakai snake_case (aturan vault second-brain CLAUDE.md §2);
    yang kebab-case hanya nama FOLDER. Jadi di dalam folder
    `the-economics-of-nightclubs/` filenya `the_economics_of_nightclubs.mp4`.

    Nama fungsi dipertahankan demi pemanggil lama; yang berubah keluarannya.
    """
    s = re.sub(r"[^\w\s-]", "", (name or "").lower())
    s = re.sub(r"[\s-]+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:limit].strip("_") or "video"


def ensure_deno():
    """Pastikan Deno terlihat di PATH.

    YouTube menyembunyikan URL stream di balik tantangan JavaScript ("n
    challenge"). yt-dlp memecahkannya lewat yt-dlp-ejs, TAPI hanya mendaftarkan
    Deno sebagai runtime — Node dan Bun terdeteksi "unavailable" walau
    terpasang. Tanpa Deno hasilnya: "n challenge solving failed" lalu
    "The page needs to be reloaded", dan nol format video muncul.
    """
    for cand in (Path.home() / ".deno/bin", Path("/opt/homebrew/bin")):
        if (cand / "deno").exists() and str(cand) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{cand}:{os.environ.get('PATH', '')}"
    return shutil.which("deno")


def build_format(quality, audio_only):
    """Selector format yt-dlp.

    WAJIB pilih H.264 (avc1) secara eksplisit. Untuk 720p YouTube menyediakan
    tiga codec dalam wadah .mp4 yang sama — avc1, vp9, dan av01 — dan filter
    [ext=mp4] saja bisa memilih AV1 (ukurannya paling kecil, jadi menang).
    File AV1 tidak bisa diputar QuickTime: video kosong, hanya audio terdengar.
    """
    if audio_only:
        return "bestaudio[ext=m4a]/bestaudio/best"
    if quality == "best":
        return "bv*[vcodec^=avc1]+ba[ext=m4a]/bv*+ba/best"
    return (f"bv*[height<={quality}][vcodec^=avc1]+ba[ext=m4a]/"
            f"b[height<={quality}][vcodec^=avc1]/"
            f"bv*[height<={quality}]+ba/b[height<={quality}]/best")


def main():
    ap = argparse.ArgumentParser(description="Unduh video YouTube")
    ap.add_argument("url", help="URL video atau playlist")
    ap.add_argument("-q", "--quality", default="720",
                    help="tinggi video: 360/480/720/1080 atau 'best' (default 720)")
    ap.add_argument("-o", "--out", help=f"folder tujuan (default {DEFAULT_DIR})")
    ap.add_argument("--limit", type=int, help="batasi jumlah video dari playlist")
    ap.add_argument("--audio", action="store_true", help="audio saja, jadikan mp3")
    ap.add_argument("--yes", action="store_true", help="jangan tanya walau video banyak")
    args = ap.parse_args()

    from yt_dlp import YoutubeDL

    if not args.audio and not ensure_deno():
        print("PERINGATAN: Deno tidak ditemukan. YouTube kemungkinan menolak "
              "menyerahkan format video.\n"
              "  Pasang: curl -fsSL https://deno.land/install.sh | sh",
              file=sys.stderr)

    outdir = Path(args.out).expanduser() if args.out else DEFAULT_DIR
    outdir.mkdir(parents=True, exist_ok=True)

    print("Cek akses YouTube...", file=sys.stderr)
    browser = detect_cookie_browser(
        args.url if "list=" not in args.url
        else "https://youtube.com/watch?v=e82mT1UnZTw")
    print(f"    cookie: {browser or 'tidak ada'}", file=sys.stderr)

    # Hitung dulu berapa video, supaya playlist besar tidak diunduh diam-diam.
    common = {"quiet": True, "no_warnings": True}
    if browser:
        common["cookiesfrombrowser"] = (browser,)

    if "list=" in args.url:
        opts = {**common, "extract_flat": True}
        if args.limit:
            opts["playlistend"] = args.limit
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(args.url, download=False)
        count = len([e for e in (info.get("entries") or []) if e])
        name = info.get("title", "playlist")
        print(f'"{name}" — {count} video', file=sys.stderr)

        if count > WARN_COUNT and not args.yes:
            est = count * (0.05 if args.audio else 0.05 * int(
                args.quality if args.quality.isdigit() else 720) / 10)
            print(f"\n{count} video, perkiraan ~{est:.1f} GB.", file=sys.stderr)
            print("Lanjut? Tambahkan --yes, atau batasi dengan --limit N",
                  file=sys.stderr)
            sys.exit(1)
    else:
        count = 1

    opts = {
        **common,
        "format": build_format(args.quality, args.audio),
        "merge_output_format": "mp4",
        # Nama file dibentuk sendiri jadi kebab-case; restrictfilenames bawaan
        # yt-dlp menghasilkan underscore ("The_Economics_of_Nightclubs") yang
        # tidak cocok dengan pola folder references/.
        "outtmpl": str(outdir / "%(title).80s.%(ext)s"),
        "ignoreerrors": True,            # satu video gagal, sisanya lanjut
        "quiet": False,
        "noprogress": False,
    }
    if args.limit:
        opts["playlistend"] = args.limit
    if args.audio:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    mode = "audio mp3" if args.audio else f"{args.quality}p"
    print(f"Unduh {count} video ({mode}) -> {outdir}\n", file=sys.stderr)

    with YoutubeDL(opts) as ydl:
        ydl.download([args.url])

    # Rapikan nama: kebab-case mengikuti pola references/<channel>/videos/<slug>/
    for f in sorted(outdir.glob("*"), key=lambda p: p.stat().st_mtime)[-count:]:
        if not f.is_file():
            continue
        target = f.with_name(f"{kebab(f.stem)}{f.suffix}")
        if target != f and not target.exists():
            f.rename(target)

    files = sorted(outdir.glob("*"), key=lambda p: p.stat().st_mtime)[-count:]
    total = sum(f.stat().st_size for f in files if f.is_file()) / 1048576
    for f in files:
        if f.is_file():
            print(f"  {f.name}", file=sys.stderr)
    print(f"\nSelesai -> {outdir} (~{total:.0f} MB)", file=sys.stderr)


if __name__ == "__main__":
    main()
