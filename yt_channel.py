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

from yt_transcript import (BROWSERS, OUT_DIR, PLAYER_CLIENT, detect_cookie_browser,
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


def list_channel(url, limit=None, popular=None):
    """(info_channel, [entry]) — cepat, satu request, tanpa buka tiap video.

    view_count di sini angka BULAT hasil pembulatan YouTube (1.9K -> 1900),
    sama seperti yang terbaca di layar. Angka persis butuh --deep.

    popular=N -> N video dengan views terbanyak. Diurutkan di sini karena
    parameter sort YouTube (?sort=p) diabaikan yt-dlp.
    """
    from yt_dlp import YoutubeDL

    url = url.rstrip("/")
    if not re.search(r"/(videos|streams|shorts|playlists)$", url):
        url += "/videos"

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
    if limit and not popular:
        # Saat mode populer, JANGAN potong di server: harus ambil semua dulu
        # baru diurutkan, kalau tidak yang tersisa cuma N video terbaru.
        opts["playlistend"] = limit
    from yt_transcript import _COOKIE_BROWSER
    if _COOKIE_BROWSER:
        opts["cookiesfrombrowser"] = (_COOKIE_BROWSER,)

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = [e for e in (info.get("entries") or []) if e]
    if popular:
        entries.sort(key=lambda e: e.get("view_count") or 0, reverse=True)
        entries = entries[:popular]
    return info, entries


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


def download(url, dest):
    """Unduh satu file. True kalau berhasil."""
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            dest.write_bytes(r.read())
        return True
    except Exception:
        return False


def pick_thumb(thumbs, square):
    """Thumbnail terbesar. square=True -> avatar (rasio 1:1), else banner."""
    best, best_px = None, -1
    for t in thumbs or []:
        w, h = t.get("width"), t.get("height")
        if not (w and h):
            continue
        is_sq = w == h
        if is_sq != square:
            continue
        if w * h > best_px:
            best, best_px = t.get("url"), w * h
    return best


def write_channel_info(info, outdir, name, entries=None, partial=False):
    """CSV berisi satu baris: profil channel.

    Total Views dijumlahkan dari daftar video — yt-dlp tidak menyediakan angka
    itu di level channel. Kalau daftarnya dipotong (--limit / --popular),
    hasilnya hanya sebagian, jadi ditandai supaya tidak disangka total penuh.
    """
    desc = (info.get("description") or "").replace("\n", " | ").strip()

    views = sum(e.get("view_count") or 0 for e in (entries or []))
    views_txt = ""
    if views:
        views_txt = f"{views:,}" + (" (sebagian)" if partial else "")

    row = {
        "Channel Name": info.get("channel") or name,
        "Subscribers": info.get("channel_follower_count") or "",
        "Total Videos": info.get("playlist_count") or len(entries or []) or "",
        "Total Views": views_txt,
        "Channel URL": info.get("channel_url") or "",
        "Handle": info.get("uploader_id") or "",
        "Channel ID": info.get("channel_id") or "",
        "Tags": ", ".join(info.get("tags") or [])[:300],
        "Channel Description": desc,
    }
    out = outdir / "channel-info.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)
    return out


def save_channel_images(info, outdir, name):
    """Avatar + banner channel."""
    d = outdir / "channel-images"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w-]", "_", name)[:40]
    got = []
    for kind, square in (("avatar", True), ("banner", False)):
        u = pick_thumb(info.get("thumbnails"), square)
        if u and download(u, d / f"{safe}_{kind}.jpg"):
            got.append(kind)
    return d, got


def save_thumbnails(entries, outdir, name, delay):
    """Thumbnail tiap video."""
    d = outdir / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w-]", "_", name)[:40]
    n = 0
    for i, e in enumerate(entries, 1):
        vid = e.get("id")
        if not vid:
            continue
        # maxres tidak selalu ada; hq720 hampir selalu tersedia
        for q in ("maxresdefault", "hq720", "hqdefault"):
            if download(f"https://i.ytimg.com/vi/{vid}/{q}.jpg",
                        d / f"{safe}_{i:03d}.jpg"):
                n += 1
                break
        time.sleep(min(delay, 1.0))
    return d, n


def scrape_channel(url, args, transcripts, collect=None):
    """-> list baris CSV untuk satu channel."""
    info, entries = list_channel(url, args.limit, args.popular)
    name = info.get("channel") or info.get("title") or url
    print(f'\n"{name}" — {len(entries)} video', file=sys.stderr)
    if collect is not None:
        collect.append((info, entries, name))

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
    ap.add_argument("--popular", type=int, metavar="N",
                    help="ambil N video paling banyak ditonton (bukan terbaru)")
    ap.add_argument("--info", action="store_true",
                    help="simpan channel-info.csv (profil channel)")
    ap.add_argument("--images", action="store_true",
                    help="unduh avatar + banner channel")
    ap.add_argument("--thumbnails", action="store_true",
                    help="unduh thumbnail tiap video")
    ap.add_argument("--all", action="store_true",
                    help="sama dengan --info --images --thumbnails")
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
    from yt_transcript import _COOKIE_BROWSER
    print(f"cookie: {_COOKIE_BROWSER or 'tidak ada'}", file=sys.stderr)

    all_rows, transcripts, collected = [], {}, []
    for url in urls:
        try:
            all_rows += scrape_channel(url, args, transcripts, collected)
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

    want_info = args.info or args.all
    want_img = args.images or args.all
    want_thumb = args.thumbnails or args.all
    if want_info or want_img or want_thumb:
        base = out.parent
        for info, entries, name in collected:
            if want_info:
                partial = bool(args.limit or args.popular)
                f = write_channel_info(info, base, name, entries, partial)
                print(f"profil   -> {f}", file=sys.stderr)
            if want_img:
                d, got = save_channel_images(info, base, name)
                print(f"gambar   -> {d} ({', '.join(got) or 'gagal'})",
                      file=sys.stderr)
            if want_thumb:
                print(f"unduh {len(entries)} thumbnail...", file=sys.stderr)
                d, n = save_thumbnails(entries, base, name, args.delay)
                print(f"thumbnail-> {d} ({n}/{len(entries)})", file=sys.stderr)


if __name__ == "__main__":
    main()
