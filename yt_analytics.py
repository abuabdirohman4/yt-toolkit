#!/usr/bin/env python3
"""
Tarik data YouTube Analytics channel sendiri ke CSV + ringkasan markdown.

Dashboard HTML (yt_dashboard.py) untuk dilihat mata; ini untuk diarsipkan dan
dibandingkan antar-waktu — CSV bisa dibuka spreadsheet, markdown bisa dibaca AI.

    ./.venv/bin/python yt_analytics.py                    # semua profil, 28 hari
    ./.venv/bin/python yt_analytics.py quick-in-explain   # satu profil
    ./.venv/bin/python yt_analytics.py --days 90
    ./.venv/bin/python yt_analytics.py -o ~/folder/lain

Profil dibuat lewat ~/.config/yt-analytics/login.py. Lihat tutorial
second-brain/0.Inbox/setup_youtube_analytics_api.md.

Impressions dan CTR TIDAK tersedia di Analytics API — untuk dua angka itu
satu-satunya sumber adalah YouTube Studio.
"""

import argparse
import csv
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# collect() sudah menarik ringkasan, per-video, dan tren harian — dipakai ulang
# supaya dashboard dan CSV tidak pernah melaporkan angka yang berbeda.
from yt_dashboard import CFG, LAG_DAYS, collect, load_creds, profiles

DEFAULT_OUT = Path.home() / "Documents/second-brain/0.Inbox"

# Nama mentah API tidak terbaca manusia; ini yang muncul di laporan.
SUMBER = {
    "YT_SEARCH": "Pencarian YouTube",
    "RELATED_VIDEO": "Video terkait",
    "SUBSCRIBER": "Beranda / subscriber",
    "PLAYLIST": "Playlist",
    "EXT_URL": "Situs luar",
    "NO_LINK_OTHER": "Langsung / lainnya",
    "NO_LINK_EMBEDDED": "Tersemat di situs lain",
    "YT_CHANNEL": "Halaman channel",
    "NOTIFICATION": "Notifikasi",
    "SHORTS": "Feed Shorts",
    "ADVERTISING": "Iklan",
    "END_SCREEN": "Layar akhir",
    "ANNOTATION": "Kartu / anotasi",
}


def slug(name, fallback="channel"):
    """Nama profil/channel -> snake_case lowercase, ikut NAMING.md."""
    s = re.sub(r"[^\w\s-]", "", (name or "").lower())
    s = re.sub(r"[\s-]+", "_", s.strip())
    return re.sub(r"_+", "_", s).strip("_") or fallback


def traffic(profile, days):
    """Dari mana penonton datang. Dimensi ini tidak diambil collect()."""
    from googleapiclient.discovery import build

    yta = build("youtubeAnalytics", "v2", credentials=load_creds(profile))
    end = date.today() - timedelta(days=LAG_DAYS)
    start = end - timedelta(days=days)
    r = yta.reports().query(
        ids="channel==MINE",
        startDate=start.isoformat(), endDate=end.isoformat(),
        metrics="views,estimatedMinutesWatched",
        dimensions="insightTrafficSourceType", sort="-views",
    ).execute()
    return [(row[0], int(row[1]), int(row[2])) for row in (r.get("rows") or [])]


def tulis_csv(data, path):
    """Satu baris per video."""
    kolom = ["video_id", "judul", "tayang", "views", "menit_ditonton",
             "durasi_tonton_detik", "persen_ditonton", "subscriber_bertambah"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(kolom)
        for v in data["videos"]:
            w.writerow([
                v.get("video", ""),
                v.get("title", ""),
                v.get("published", ""),
                v.get("views", 0),
                v.get("estimatedMinutesWatched", 0),
                round(float(v.get("averageViewDuration", 0))),
                round(float(v.get("averageViewPercentage", 0)), 1),
                v.get("subscribersGained", 0),
            ])
    return len(data["videos"])


def tulis_md(data, sumber, days, path):
    """Ringkasan yang bisa dibaca sekali lihat — dan disuapkan ke AI."""
    s = data["summary"]
    views = s.get("views", 0)
    b = [
        "---", "type: note", f"date: {date.today().isoformat()}",
        "status: active", "domain: youtube", "---",
        f"# Analytics — {data['title']}", "",
        f"Periode {data['start']} s/d {data['end']} ({days} hari). "
        f"Data YouTube tertinggal {LAG_DAYS} hari dari realtime.", "",
        "## Ringkasan", "",
        "| Angka | Nilai |", "|---|---|",
        f"| Views | {views:,} |".replace(",", "."),
        f"| Menit ditonton | {s.get('estimatedMinutesWatched', 0):,} |".replace(",", "."),
        f"| Rata-rata durasi tonton | {round(float(s.get('averageViewDuration', 0)))} detik |",
        f"| Rata-rata persen ditonton | {round(float(s.get('averageViewPercentage', 0)), 1)}% |",
        f"| Subscriber bertambah | {s.get('subscribersGained', 0)} |",
        f"| Subscriber berkurang | {s.get('subscribersLost', 0)} |",
        f"| Total subscriber | {data.get('subs', '?')} |",
        "",
    ]

    if sumber:
        total = sum(v for _, v, _ in sumber) or 1
        b += ["## Dari mana penonton datang", "",
              "| Sumber | Views | Porsi |", "|---|---:|---:|"]
        b += [f"| {SUMBER.get(k, k)} | {v} | {v / total * 100:.0f}% |"
              for k, v, _ in sumber if v]
        b.append("")

    if data["videos"]:
        b += ["## Per video", "",
              "| Judul | Tayang | Views | %tonton | Sub |",
              "|---|---|---:|---:|---:|"]
        for v in data["videos"]:
            judul = (v.get("title", "") or "").replace("|", "\\|")[:60]
            b.append(f"| {judul} | {v.get('published', '')} | {v.get('views', 0)} | "
                     f"{round(float(v.get('averageViewPercentage', 0)), 1)}% | "
                     f"{v.get('subscribersGained', 0)} |")
        b.append("")

    b += ["> Impressions dan CTR tidak tersedia di Analytics API — untuk dua",
          "> angka itu buka YouTube Studio.", ""]
    Path(path).write_text("\n".join(b), encoding="utf-8")


def selftest():
    """Cek bagian yang bisa diam-diam salah: slug dan penulisan CSV."""
    assert slug("Quick In Explain") == "quick_in_explain"
    assert slug("Wealth-Logic!") == "wealth_logic"
    assert slug("") == "channel"

    import tempfile
    data = {"videos": [{"video": "abc", "title": "Judul, berkoma", "published": "2026-01-02",
                        "views": 10, "estimatedMinutesWatched": 5,
                        "averageViewDuration": 30.7, "averageViewPercentage": 44.44,
                        "subscribersGained": 1}]}
    with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as f:
        n = tulis_csv(data, f.name)
        baris = list(csv.reader(open(f.name, encoding="utf-8")))
    assert n == 1 and len(baris) == 2, baris
    # Koma dalam judul tidak boleh memecah kolom.
    assert baris[1][1] == "Judul, berkoma", baris[1]
    assert baris[1][5] == "31" and baris[1][6] == "44.4", baris[1]
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser(description="Analytics channel sendiri -> CSV + markdown")
    ap.add_argument("profile", nargs="?", help="nama profil (default: semua)")
    ap.add_argument("--days", type=int, default=28, help="periode hari (default 28)")
    ap.add_argument("-o", "--out", help=f"folder keluaran (default {DEFAULT_OUT})")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    names = [args.profile] if args.profile else profiles()
    if not names:
        sys.exit(f"Belum ada profil. Jalankan: python3 {CFG / 'login.py'} <nama>")

    out = Path(args.out).expanduser() if args.out else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    gagal = 0

    for name in names:
        print(f"Ambil data: {name}…", file=sys.stderr)
        try:
            data = collect(name, args.days)
            sumber = traffic(name, args.days)
        except Exception as e:
            print(f"  gagal: {str(e)[:120]}", file=sys.stderr)
            gagal += 1
            continue

        ch = slug(data["title"], slug(name))
        dasar = f"analytics_{args.days}d_{ch}"
        n = tulis_csv(data, out / f"{dasar}.csv")
        tulis_md(data, sumber, args.days, out / f"{dasar}.md")
        views = data["summary"].get("views", 0)
        print(f"  {data['title']}: {views} views · {n} video -> {dasar}.csv + .md",
              file=sys.stderr)

    if gagal == len(names):
        sys.exit("Semua profil gagal.")
    print(f"\nKeluaran: {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
