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
import sys
from pathlib import Path

from yt_transcript import detect_cookie_browser

DEFAULT_DIR = Path.home() / "Downloads/yt"
WARN_COUNT = 10          # di atas ini, minta konfirmasi dulu


def build_format(quality, audio_only):
    """Selector format yt-dlp."""
    if audio_only:
        return "bestaudio/best"
    if quality == "best":
        return "bv*+ba/best"
    # Video <= tinggi yang diminta, digabung audio terbaik. Fallback ke
    # format tunggal kalau penggabungan tak tersedia.
    return f"bv*[height<={quality}]+ba/b[height<={quality}]/best"


def main():
    ap = argparse.ArgumentParser(description="Unduh video YouTube")
    ap.add_argument("url", help="URL video atau playlist")
    ap.add_argument("-q", "--quality", default="480",
                    help="tinggi video: 360/480/720/1080 atau 'best' (default 480)")
    ap.add_argument("-o", "--out", help=f"folder tujuan (default {DEFAULT_DIR})")
    ap.add_argument("--limit", type=int, help="batasi jumlah video dari playlist")
    ap.add_argument("--audio", action="store_true", help="audio saja, jadikan mp3")
    ap.add_argument("--yes", action="store_true", help="jangan tanya walau video banyak")
    args = ap.parse_args()

    from yt_dlp import YoutubeDL

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
        "outtmpl": str(outdir / "%(title).80s.%(ext)s"),
        "restrictfilenames": True,       # nama file aman untuk shell
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

    files = sorted(outdir.glob("*"), key=lambda p: p.stat().st_mtime)[-count:]
    total = sum(f.stat().st_size for f in files if f.is_file()) / 1048576
    print(f"\nSelesai -> {outdir} (~{total:.0f} MB)", file=sys.stderr)


if __name__ == "__main__":
    main()
