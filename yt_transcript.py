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


class QuotaExhausted(Exception):
    """Jatah caption per-IP habis — bukan error sesaat, mengulang percuma."""


def slug(name, fallback="channel", limit=None):
    """Nama file: lowercase snake_case. Dipakai bersama oleh semua tool di sini
    supaya hasil yt-transcript, yt-channel, dan extension bernama seragam."""
    s = re.sub(r"[^\w\s-]", "", (name or "").lower())
    s = re.sub(r"[\s-]+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if limit:
        s = s[:limit].strip("_")
    return s or fallback


def hms(seconds):
    """Detik -> '2m 05s' / '45s', untuk tampilan progres."""
    s = int(seconds)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


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


def fetch_transcript(url, on_wait=None):
    """(teks_paragraf, lang) atau (None, None) kalau tak ada caption."""
    lang, cap_url = pick_track(extract_video(url))
    if not cap_url:
        return None, None

    # Server caption punya jatah sendiri, jauh lebih ketat dari API metadata.
    # 429 yang datang SEKETIKA (<2 detik) = jatah IP habis: mengulang tidak
    # menolong dan justru menambah beban ke endpoint yang sedang menolak, yang
    # pada sebagian sistem memperpanjang masa blokir. Yang layak diulang hanya
    # 429 yang datang setelah jeda — itu tanda server sedang sibuk sesaat.
    for attempt in range(2):
        t0 = time.time()
        try:
            with urllib.request.urlopen(cap_url, timeout=30) as r:
                raw = r.read().decode("utf-8")
            return to_paragraphs(parse_json3(raw)), lang
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 1:
                raise
            if time.time() - t0 < 2:
                raise QuotaExhausted(
                    "jatah caption YouTube habis (429 seketika)")
            if on_wait:
                on_wait(20, 1)
            time.sleep(20)
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
        out = OUT_DIR / f"{slug(playlist, 'playlist')}_all_transcripts.txt"
    out.parent.mkdir(parents=True, exist_ok=True)

    bar = "=" * 52
    dash = "-" * 52
    chunks = [
        f"{bar}\n{playlist.upper()} - BULK TRANSCRIPT EXPORT\n"
        f"Total Videos: {len(videos)} | Export Date: "
        f"{datetime.now().strftime('%-m/%-d/%Y')}\n{bar}\n"
    ]

    ok = failed = 0
    total = len(videos)
    started = time.time()
    quota_done = False
    for i, (vid, title) in enumerate(videos, 1):
        pct = i / total * 100
        elapsed = time.time() - started
        # perkiraan sisa dari kecepatan rata-rata sejauh ini
        eta = ""
        if i > 1:
            per = elapsed / (i - 1)
            eta = f" | sisa ~{hms(per * (total - i + 1))}"
        print(f"[{i}/{total}] {pct:.0f}%{eta}  {title[:52]}", file=sys.stderr)

        url = f"https://youtube.com/watch?v={vid}"
        t0 = time.time()

        def waiting(sec, n):
            # Tanpa ini prosesnya diam sampai 2 menit dan terlihat seperti hang.
            print(f"    kena batas YouTube, tunggu {sec}s "
                  f"(percobaan {n}/3)...", file=sys.stderr, flush=True)

        try:
            body, lang = fetch_transcript(url, on_wait=waiting)
            if body:
                ok += 1
                print(f"    ok ({lang}) {hms(time.time() - t0)}", file=sys.stderr)
            else:
                body, failed = "[NO TRANSCRIPT AVAILABLE]", failed + 1
                print("    tidak ada caption", file=sys.stderr)
        except QuotaExhausted as e:
            body, failed = "[DILEWATI: jatah YouTube habis]", failed + 1
            print(f"    {e}", file=sys.stderr)
            quota_done = True
        except Exception as e:
            body, failed = f"[ERROR: {e}]", failed + 1
            print(f"    gagal: {str(e)[:64]}", file=sys.stderr)

        chunks.append(f"\n{dash}\nVIDEO {i}: {title}\nURL: {url}\n{dash}\n\n{body}\n")

        # Jatah habis -> berhenti seketika. Melanjutkan hanya menambah
        # permintaan ke endpoint yang sedang menolak.
        if quota_done or (failed == i and i >= 3 and ok == 0):
            print(f"\nJatah caption YouTube habis (berhenti di video {i}). "
                  f"Coba lagi beberapa jam lagi, atau ganti jaringan.",
                  file=sys.stderr)
            for j in range(i + 1, total + 1):
                v2, t2 = videos[j - 1]
                chunks.append(f"\n{dash}\nVIDEO {j}: {t2}\n"
                              f"URL: https://youtube.com/watch?v={v2}\n{dash}\n\n"
                              f"[DILEWATI: jatah YouTube habis]\n")
            failed = total
            break

        if i < total:
            time.sleep(1)  # jeda sopan, hindari rate-limit

    if not ok:
        # Nol transcript: file cuma berisi header + penanda dilewati. Menulisnya
        # hanya mengotori folder dan mudah disangka hasil yang berhasil.
        print(f"\n{failed} gagal, tidak ada transcript — file tidak ditulis.",
              file=sys.stderr)
        return

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
