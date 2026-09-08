#!/usr/bin/env python3
"""Ambil transcript semua video dalam playlist YouTube jadi satu file .txt.

Usage:
    python3 yt_transcript.py "https://youtube.com/playlist?list=XXXX"
    python3 yt_transcript.py "https://youtube.com/watch?v=XXXX" -o ~/lain.txt
    python3 yt_transcript.py --selftest
"""

import argparse
import json
import re
import sys
import textwrap
import time
import urllib.request
from datetime import datetime
from pathlib import Path

OUT_DIR = Path.home() / "Documents/second-brain/0.Inbox"
PARA_SECONDS = 30          # jarak minimum antar paragraf
WRAP = 80

# YouTube minta bukti "bukan bot" untuk client biasa (web/ios/android) dan
# client itu tidak menyajikan caption. web_embedded lolos dan punya caption.
PLAYER_CLIENT = "web_embedded"
BROWSERS = ("brave", "chrome", "safari")   # sumber cookie, dicoba berurutan


def ts(seconds):
    """Detik -> [M:SS] atau [H:MM:SS]."""
    s = int(seconds)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_json3(raw):
    """json3 caption -> [(start_detik, teks)].

    Cue berisi "\\n" saja adalah pemisah antar baris caption, bukan sampah:
    kalau dibuang begitu saja, kata di kedua sisinya menempel ("stockmarket").
    Diperlakukan sebagai spasi, lalu spasi berlebih dirapikan di to_paragraphs.
    """
    cues = []
    for ev in json.loads(raw).get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(seg.get("utf8", "") for seg in segs)
        if not text.strip():
            continue  # cue pemisah: dilewati, spasi dijamin saat penggabungan
        cues.append((ev.get("tStartMs", 0) / 1000, " ".join(text.split())))
    return cues


def to_paragraphs(cues):
    """Sambung cue jadi paragraf. Potong tiap ~30 detik TAPI hanya di akhir
    kalimat, supaya kalimat tidak terbelah di tengah."""
    if not cues:
        return ""

    paras, words, start = [], [], cues[0][0]

    def flush():
        if words:
            body = textwrap.fill(" ".join(words), WRAP)
            paras.append(f"[{ts(start)}] {body}")

    for at, text in cues:
        words.append(text)
        # cukup lama DAN kalimat sudah selesai -> potong
        if at - start >= PARA_SECONDS and text.rstrip().endswith((".", "?", "!")):
            flush()
            words, start = [], at
    flush()
    return "\n\n".join(paras)


def pick_track(info):
    """Pilih caption bahasa ASLI video, bukan terjemahan mesin.

    YouTube menyodorkan ~157 bahasa untuk tiap video: 1 asli + sisanya hasil
    terjemahan mesin (URL-nya bertanda `tlang=`). Kita hanya mau yang asli,
    jadi video Indonesia keluar Indonesia dan video Inggris keluar Inggris.
    """
    for source in (info.get("subtitles"), info.get("automatic_captions")):
        if not source:
            continue
        # "en-orig" menandai track asli; kalau ada, itu yang benar.
        langs = sorted(source, key=lambda L: (not L.endswith("-orig"), len(L)))
        for lang in langs:
            for fmt in source[lang]:
                if fmt.get("ext") == "json3" and "tlang=" not in fmt.get("url", ""):
                    return lang, fmt["url"]
    return None, None


_COOKIE_BROWSER = None   # browser yang terbukti jalan, dipakai ulang


def _opts(**extra):
    o = {"quiet": True, "no_warnings": True, "skip_download": True, **extra}
    if _COOKIE_BROWSER:
        o["cookiesfrombrowser"] = (_COOKIE_BROWSER,)
    return o


def extract_video(url):
    """Metadata + caption satu video.

    Panggil extractor langsung, bukan lewat YoutubeDL.extract_info: jalur normal
    berhenti di pemilihan format video ("Requested format is not available")
    padahal kita cuma butuh caption, tanpa video sama sekali.
    """
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.youtube import YoutubeIE

    opts = _opts(extractor_args={"youtube": {"player_client": [PLAYER_CLIENT]}})
    with YoutubeDL(opts) as ydl:
        ie = YoutubeIE()
        ie.set_downloader(ydl)
        return ie.extract(url)


def fetch_transcript(url):
    """(teks_paragraf, lang) atau (None, None) kalau tak ada caption."""
    lang, cap_url = pick_track(extract_video(url))
    if not cap_url:
        return None, None

    # Server caption punya jatah sendiri, lebih ketat dari API metadata, dan
    # membalas 429 kalau terlalu sering. Tunggu makin lama tiap gagal.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(cap_url, timeout=30) as r:
                raw = r.read().decode("utf-8")
            return to_paragraphs(parse_json3(raw)), lang
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(20 * (attempt + 1))  # 20s, 40s, 60s
    return None, None


def detect_cookie_browser(probe_url):
    """Cari browser yang cookie YouTube-nya lolos gerbang bot.

    Probe pakai video pertama dari daftar yang mau diambil — bukan video uji
    eksternal, yang bisa dihapus sewaktu-waktu dan bikin deteksi salah vonis.
    """
    global _COOKIE_BROWSER
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.youtube import YoutubeIE

    for browser in BROWSERS:
        try:
            opts = {
                "quiet": True, "no_warnings": True, "skip_download": True,
                "cookiesfrombrowser": (browser,),
                "extractor_args": {"youtube": {"player_client": [PLAYER_CLIENT]}},
            }
            with YoutubeDL(opts) as ydl:
                ie = YoutubeIE()
                ie.set_downloader(ydl)
                ie.extract(probe_url)
            _COOKIE_BROWSER = browser
            return browser
        except Exception:
            continue
    return None


def is_multi(url):
    """URL ini berisi banyak video? (playlist atau channel)

    Channel bisa ditulis bermacam bentuk: /@handle, /channel/UC..., /c/nama,
    /user/nama — dengan atau tanpa akhiran /videos, /streams, /shorts.
    """
    return bool(re.search(
        r"list=|/@[^/]+|/channel/|/c/|/user/|/(videos|streams|shorts|playlists)/?$",
        url))


def list_videos(url, popular=None, limit=None):
    """(judul, [(id, judul)]) dari playlist, channel, atau satu video.

    popular=N -> ambil N video dengan views terbanyak. Diurutkan di sini,
    bukan lewat parameter sort YouTube: `?sort=p` diabaikan yt-dlp (terbukti
    mengembalikan urutan yang sama persis dengan tab biasa).
    """
    from yt_dlp import YoutubeDL

    # Video tunggal: jalur playlist kena gerbang bot, jadi pakai extractor
    # langsung seperti saat ambil caption.
    if not is_multi(url):
        info = extract_video(url)
        vid = info.get("id") or url.rsplit("v=", 1)[-1][:11]
        return info.get("title") or vid, [(vid, info.get("title") or vid)]

    # Channel tanpa akhiran -> arahkan ke tab video, kalau tidak yt-dlp
    # mengembalikan halaman depan (campur playlist dan konten lain).
    if "list=" not in url and not re.search(
            r"/(videos|streams|shorts|playlists)/?$", url):
        url = url.rstrip("/") + "/videos"

    with YoutubeDL(_opts(extract_flat=True)) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") != "playlist":
        return info.get("title", "video"), [(info["id"], info.get("title", info["id"]))]

    entries = [e for e in info.get("entries", []) if e]
    if popular:
        entries.sort(key=lambda e: e.get("view_count") or 0, reverse=True)
        entries = entries[:popular]
    elif limit:
        entries = entries[:limit]

    vids = [(e["id"], e.get("title") or e["id"]) for e in entries]
    return info.get("title", "playlist"), vids


def main():
    ap = argparse.ArgumentParser(description="Bulk YouTube transcript -> satu .txt")
    ap.add_argument("url", nargs="?", help="URL playlist atau video")
    ap.add_argument("-o", "--out", help="path file output")
    ap.add_argument("--popular", type=int, metavar="N",
                    help="ambil N video paling banyak ditonton (bukan terbaru)")
    ap.add_argument("--limit", type=int, metavar="N",
                    help="ambil N video pertama sesuai urutan aslinya")
    ap.add_argument("--selftest", action="store_true", help="cek logika paragraf, tanpa jaringan")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.url:
        ap.error("butuh URL (atau --selftest)")

    # Daftar playlist/channel bisa dibaca tanpa cookie; video individual butuh
    # cookie. Urutan ini penting: daftar dulu, baru probe cookie pakai video
    # pertama — probe butuh URL video, bukan URL channel.
    print("Ambil daftar video...", file=sys.stderr)
    if is_multi(args.url):
        playlist, videos = list_videos(args.url, args.popular, args.limit)
        if not videos:
            sys.exit("Tidak ada video ditemukan.")
        detect_cookie_browser(f"https://youtube.com/watch?v={videos[0][0]}")
    else:
        detect_cookie_browser(args.url)
        playlist, videos = list_videos(args.url, args.popular, args.limit)
        if not videos:
            sys.exit("Tidak ada video ditemukan.")

    if _COOKIE_BROWSER:
        print(f"    cookie: {_COOKIE_BROWSER}", file=sys.stderr)
    else:
        print("    tanpa cookie - YouTube mungkin menolak", file=sys.stderr)
    print(f'"{playlist}" - {len(videos)} video\n', file=sys.stderr)

    if args.out:
        out = Path(args.out).expanduser()
    else:
        safe = re.sub(r"[^\w\s-]", "", playlist).strip().replace(" ", "_")
        out = OUT_DIR / f"{safe or 'playlist'}_all_transcripts.txt"
    out.parent.mkdir(parents=True, exist_ok=True)

    bar = "=" * 52
    dash = "-" * 52
    chunks = [
        f"{bar}\n{playlist.upper()} - BULK TRANSCRIPT EXPORT\n"
        f"Total Videos: {len(videos)} | Export Date: "
        f"{datetime.now().strftime('%-m/%-d/%Y')}\n{bar}\n"
    ]

    ok = failed = 0
    for i, (vid, title) in enumerate(videos, 1):
        print(f"[{i}/{len(videos)}] {title[:60]}", file=sys.stderr)
        url = f"https://youtube.com/watch?v={vid}"
        try:
            body, lang = fetch_transcript(url)
            if body:
                ok += 1
                print(f"    ok ({lang})", file=sys.stderr)
            else:
                body, failed = "[NO TRANSCRIPT AVAILABLE]", failed + 1
                print("    tidak ada caption", file=sys.stderr)
        except Exception as e:
            body, failed = f"[ERROR: {e}]", failed + 1
            print(f"    gagal: {str(e)[:70]}", file=sys.stderr)

        chunks.append(f"\n{dash}\nVIDEO {i}: {title}\nURL: {url}\n{dash}\n\n{body}\n")
        if i < len(videos):
            time.sleep(1)  # jeda sopan, hindari rate-limit

    out.write_text("\n".join(chunks), encoding="utf-8")
    size = out.stat().st_size / 1024
    print(f"\n{ok} berhasil, {failed} gagal -> {out} ({size:.0f}K)", file=sys.stderr)


def selftest():
    """Cek logika paragraf: pecah di batas kalimat, timestamp urut, teks utuh."""
    # cue tiap 2 detik, 90 detik; kalimat berakhir di 40s dan 80s
    events = []
    for i in range(45):
        t = i * 2
        word = "end." if t in (40, 80) else f"w{i}"
        events.append({"tStartMs": t * 1000, "segs": [{"utf8": word}]})
    events.insert(3, {"tStartMs": 6000})  # event tanpa segs -> harus di-skip
    # cue pemisah "\n" (nyata ada di caption YouTube): dilewati, TAPI kata di
    # kedua sisinya tidak boleh menempel jadi satu.
    events.insert(2, {"tStartMs": 3000, "segs": [{"utf8": "\n"}]})

    cues = parse_json3(json.dumps({"events": events}))
    assert len(cues) == 45, f"expected 45 cues, got {len(cues)}"
    assert " w1 w2 " in " " + " ".join(t for _, t in cues) + " ", "cue pemisah bikin kata menempel"

    out = to_paragraphs(cues)
    paras = out.split("\n\n")
    assert len(paras) == 3, f"expected 3 paragraphs, got {len(paras)}"

    # potong HANYA di akhir kalimat: tiap paragraf (kecuali terakhir) diakhiri "end."
    for p in paras[:-1]:
        assert p.rstrip().endswith("end."), f"paragraf tidak pecah di batas kalimat: {p[-40:]!r}"

    # timestamp naik urut
    stamps = [re.match(r"\[(\d+):(\d+)\]", p).groups() for p in paras]
    secs = [int(m) * 60 + int(s) for m, s in stamps]
    assert secs == sorted(secs) and len(set(secs)) == 3, f"timestamp tidak urut: {secs}"
    assert secs[0] == 0, f"paragraf pertama harus mulai 0:00, dapat {secs[0]}"

    # tidak ada teks hilang
    words_in = [t for _, t in cues]
    words_out = re.sub(r"\[\d+:\d+\]", "", out).split()
    assert words_out == words_in, f"teks hilang/berubah: {len(words_in)} -> {len(words_out)}"

    print("selftest ok: 3 paragraf, pecah di batas kalimat, timestamp urut, teks utuh")


if __name__ == "__main__":
    main()
