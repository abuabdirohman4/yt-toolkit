#!/usr/bin/env python3
"""Bedah channel YouTube -> CSV (+ transcript opsional).

Usage:
    python3 yt_channel.py "https://youtube.com/@channel"          # cepat, angka bulat
    python3 yt_channel.py "URL" --deep                            # angka persis + deskripsi
    python3 yt_channel.py "URL" --deep --transcript               # + file transcript
    python3 yt_channel.py urls.txt                                # banyak channel, 1 URL per baris
    python3 yt_channel.py "URL" --limit 20                        # batasi jumlah video
"""

import argparse
import csv
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from yt_bulk import (BROWSERS, OUT_DIR, PLAYER_CLIENT, detect_cookie_browser,
                     extract_video, fetch_transcript, ts)

COLUMNS = [
    "No.", "Channel", "Subscribers", "Video Title", "Video URL", "Views",
    "Likes", "Comments", "Duration", "Upload Date", "Days Ago", "Description",
]


def days_ago(upload_date):
    """'20260903' -> berapa hari lalu."""
    if not upload_date:
        return ""
    try:
        d = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except ValueError:
        return ""


def fmt_date(upload_date):
    if not upload_date or len(upload_date) != 8:
        return ""
    return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"


def list_channel(url, limit=None):
    """(info_channel, [entry]) — cepat, satu request, tanpa buka tiap video.

    view_count di sini angka BULAT hasil pembulatan YouTube (1.9K -> 1900),
    sama seperti yang terbaca di layar. Angka persis butuh --deep.
    """
    from yt_dlp import YoutubeDL

    url = url.rstrip("/")
    if not re.search(r"/(videos|streams|shorts|playlists)$", url):
        url += "/videos"

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
    if limit:
        opts["playlistend"] = limit
    from yt_bulk import _COOKIE_BROWSER
    if _COOKIE_BROWSER:
        opts["cookiesfrombrowser"] = (_COOKIE_BROWSER,)

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info, [e for e in (info.get("entries") or []) if e]


def row_from_flat(info, entry, n):
    """Baris CSV dari data daftar (cepat, tanpa buka video)."""
    return {
        "No.": n,
        "Channel": info.get("channel") or info.get("title") or "",
        "Subscribers": info.get("channel_follower_count") or "",
        "Video Title": entry.get("title") or "",
        "Video URL": f"https://youtube.com/watch?v={entry.get('id')}",
        "Views": entry.get("view_count") or "",
        "Likes": "", "Comments": "",
        "Duration": ts(entry["duration"]) if entry.get("duration") else "",
        "Upload Date": "", "Days Ago": "", "Description": "",
    }


def row_from_deep(info, n):
    """Baris CSV dari data lengkap satu video (angka persis)."""
    up = info.get("upload_date")
    desc = (info.get("description") or "").replace("\n", " ").strip()
    return {
        "No.": n,
        "Channel": info.get("channel") or "",
        "Subscribers": info.get("channel_follower_count") or "",
        "Video Title": info.get("title") or "",
        "Video URL": f"https://youtube.com/watch?v={info.get('id')}",
        "Views": info.get("view_count") or "",
        "Likes": info.get("like_count") or "",
        "Comments": info.get("comment_count") or "",
        "Duration": ts(info["duration"]) if info.get("duration") else "",
        "Upload Date": fmt_date(up),
        "Days Ago": days_ago(up),
        "Description": desc[:500],
    }


def scrape_channel(url, args, transcripts):
    """-> list baris CSV untuk satu channel."""
    info, entries = list_channel(url, args.limit)
    name = info.get("channel") or info.get("title") or url
    print(f'\n"{name}" — {len(entries)} video', file=sys.stderr)

    if not args.deep:
        return [row_from_flat(info, e, i) for i, e in enumerate(entries, 1)]

    rows = []
    for i, e in enumerate(entries, 1):
        vid = e.get("id")
        vurl = f"https://youtube.com/watch?v={vid}"
        print(f"  [{i}/{len(entries)}] {(e.get('title') or '')[:52]}", file=sys.stderr)
        try:
            vinfo = extract_video(vurl)
            row = row_from_deep(vinfo, i)
            row["Channel"] = row["Channel"] or name
            row["Subscribers"] = row["Subscribers"] or (info.get("channel_follower_count") or "")
            rows.append(row)

            # Transcript ditangani terpisah: kalau gagal, baris metadata yang
            # sudah berhasil tetap dipakai — jangan bikin baris kedua.
            if args.transcript:
                try:
                    body, lang = fetch_transcript(vurl)
                    if body:
                        transcripts.setdefault(name, []).append(
                            (vinfo.get("title") or vid, vurl, body))
                        print(f"       transcript ok ({lang})", file=sys.stderr)
                    else:
                        print("       tanpa transcript", file=sys.stderr)
                except Exception as exc:
                    print(f"       transcript gagal: {str(exc)[:50]}", file=sys.stderr)
        except Exception as exc:
            print(f"       gagal: {str(exc)[:60]}", file=sys.stderr)
            rows.append({**row_from_flat(info, e, i), "Description": f"[ERROR: {exc}]"})
        time.sleep(args.delay)
    return rows


def write_transcripts(transcripts):
    bar, dash = "=" * 52, "-" * 52
    for channel, items in transcripts.items():
        safe = re.sub(r"[^\w\s-]", "", channel).strip().replace(" ", "_") or "channel"
        out = OUT_DIR / f"{safe}_transcripts.txt"
        chunks = [f"{bar}\n{channel.upper()} - CHANNEL TRANSCRIPTS\n"
                  f"Total Videos: {len(items)} | Export Date: "
                  f"{datetime.now().strftime('%-m/%-d/%Y')}\n{bar}\n"]
        for i, (title, url, body) in enumerate(items, 1):
            chunks.append(f"\n{dash}\nVIDEO {i}: {title}\nURL: {url}\n{dash}\n\n{body}\n")
        out.write_text("\n".join(chunks), encoding="utf-8")
        print(f"transcript -> {out} ({out.stat().st_size / 1024:.0f}K)", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Bedah channel YouTube -> CSV")
    ap.add_argument("target", help="URL channel, atau file .txt berisi URL (1 per baris)")
    ap.add_argument("--deep", action="store_true",
                    help="buka tiap video: angka persis + likes/komentar/deskripsi (lambat)")
    ap.add_argument("--transcript", action="store_true",
                    help="ikut ambil transcript (butuh --deep)")
    ap.add_argument("--limit", type=int, help="batasi jumlah video per channel")
    ap.add_argument("--delay", type=float, default=4.0,
                    help="jeda detik antar video (default 4; turunkan kalau buru-buru)")
    ap.add_argument("-o", "--out", help="path file CSV")
    args = ap.parse_args()

    if args.transcript and not args.deep:
        ap.error("--transcript butuh --deep")

    # target: file daftar URL, atau satu URL langsung
    p = Path(args.target).expanduser()
    if p.is_file():
        urls = [l.strip() for l in p.read_text().splitlines()
                if l.strip() and not l.startswith("#")]
    else:
        urls = [args.target]
    if not urls:
        sys.exit("Tidak ada URL.")

    print(f"{len(urls)} channel | mode: {'deep' if args.deep else 'cepat'}", file=sys.stderr)
    detect_cookie_browser("https://youtube.com/watch?v=e82mT1UnZTw")
    from yt_bulk import _COOKIE_BROWSER
    print(f"cookie: {_COOKIE_BROWSER or 'tidak ada'}", file=sys.stderr)

    all_rows, transcripts = [], {}
    for url in urls:
        try:
            all_rows += scrape_channel(url, args, transcripts)
        except Exception as exc:
            print(f"  channel gagal ({url}): {str(exc)[:70]}", file=sys.stderr)

    if not all_rows:
        sys.exit("Tidak ada data.")

    out = Path(args.out).expanduser() if args.out else (
        OUT_DIR / f"yt_channels_{datetime.now():%Y%m%d_%H%M}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:  # BOM: Excel-friendly
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(all_rows)

    print(f"\n{len(all_rows)} video -> {out}", file=sys.stderr)
    if transcripts:
        write_transcripts(transcripts)


if __name__ == "__main__":
    main()
