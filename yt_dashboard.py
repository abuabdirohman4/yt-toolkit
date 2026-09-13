#!/usr/bin/env python3
"""Dashboard HTML dari YouTube Analytics API — semua channel dalam satu halaman.

Usage:
    python3 yt_dashboard.py                    # semua profil
    python3 yt_dashboard.py quick-in-explain   # satu profil
    python3 yt_dashboard.py --days 90          # periode lain (default 28)
    python3 yt_dashboard.py -o ~/dash.html
    python3 yt_dashboard.py --selftest

Butuh login dulu:
    python3 ~/.config/yt-analytics/login.py <profil>
"""

import argparse
import html
import sys
import webbrowser
from datetime import date, timedelta
from pathlib import Path

CFG = Path.home() / ".config/yt-analytics"
DEFAULT_OUT = Path.home() / "Documents/second-brain/0.Inbox/youtube_dashboard.html"

# Analytics tertinggal 2-3 hari dari realtime; data hari ini belum ada.
LAG_DAYS = 3

# Terbukti jalan 13 Sep 2026. impressions & impressionClickThroughRate TIDAK
# ADA di API ini (dijawab "Unknown identifier") — untuk itu perlu YouTube Studio.
METRICS = ("views,estimatedMinutesWatched,averageViewDuration,"
           "averageViewPercentage,subscribersGained,subscribersLost")


def profiles():
    return sorted(p.stem.replace("token_", "") for p in CFG.glob("token_*.json"))


def load_creds(profile):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token = CFG / f"token_{profile}.json"
    if not token.exists():
        raise FileNotFoundError(f"profil '{profile}' belum login")
    creds = Credentials.from_authorized_user_file(str(token))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json())
    return creds


def fmt_num(n):
    """1234567 -> 1,2 jt · 12345 -> 12,3 rb"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} jt".replace(".", ",")
    if n >= 1_000:
        return f"{n / 1_000:.1f} rb".replace(".", ",")
    return f"{n:.0f}"


def fmt_dur(seconds):
    """Detik -> 5:07"""
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return "—"
    return f"{s // 60}:{s % 60:02d}"


def collect(profile, days):
    """Ambil ringkasan + per-video + tren harian untuk satu profil."""
    from googleapiclient.discovery import build

    creds = load_creds(profile)
    yta = build("youtubeAnalytics", "v2", credentials=creds)
    yt = build("youtube", "v3", credentials=creds)

    end = date.today() - timedelta(days=LAG_DAYS)
    start = end - timedelta(days=days)
    rng = dict(ids="channel==MINE", startDate=start.isoformat(),
               endDate=end.isoformat())

    ch = yt.channels().list(part="snippet,statistics", mine=True).execute()
    item = (ch.get("items") or [{}])[0]
    snip, stats = item.get("snippet", {}), item.get("statistics", {})

    summary = {}
    r = yta.reports().query(metrics=METRICS, **rng).execute()
    if r.get("rows"):
        cols = [h["name"] for h in r["columnHeaders"]]
        summary = dict(zip(cols, r["rows"][0]))

    # per video
    videos = []
    rv = yta.reports().query(
        metrics="views,estimatedMinutesWatched,averageViewDuration,"
                "averageViewPercentage,subscribersGained",
        dimensions="video", sort="-views", maxResults=25, **rng).execute()
    vrows = rv.get("rows") or []
    if vrows:
        cols = [h["name"] for h in rv["columnHeaders"]]
        ids = [row[0] for row in vrows]
        meta = {}
        # Data API v3 dibatasi 50 id per panggilan
        for i in range(0, len(ids), 50):
            batch = yt.videos().list(part="snippet",
                                     id=",".join(ids[i:i + 50])).execute()
            for it in batch.get("items", []):
                meta[it["id"]] = it["snippet"]
        for row in vrows:
            d = dict(zip(cols, row))
            sn = meta.get(d["video"], {})
            d["title"] = sn.get("title", d["video"])
            d["published"] = (sn.get("publishedAt") or "")[:10]
            d["thumb"] = (sn.get("thumbnails", {}).get("medium", {}).get("url", ""))
            videos.append(d)

    # tren harian
    daily = []
    rd = yta.reports().query(metrics="views", dimensions="day",
                             sort="day", **rng).execute()
    daily = [(row[0], row[1]) for row in (rd.get("rows") or [])]

    return {
        "profile": profile,
        "title": snip.get("title", profile),
        "subs": stats.get("subscriberCount"),
        "total_views": stats.get("viewCount"),
        "total_videos": stats.get("videoCount"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "summary": summary,
        "videos": videos,
        "daily": daily,
    }


def sparkline(daily, w=560, h=48):
    """Garis tren sederhana — SVG inline, tanpa library."""
    vals = [v for _, v in daily]
    if len(vals) < 2:
        return ""
    top = max(vals) or 1
    step = w / (len(vals) - 1)
    pts = " ".join(f"{i * step:.1f},{h - (v / top * (h - 6)) - 3:.1f}"
                   for i, v in enumerate(vals))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linejoin="round"/></svg>')


def render(channels, days):
    esc = html.escape
    now = date.today().strftime("%d %b %Y")

    cards = []
    for c in channels:
        s = c["summary"]
        gained = s.get("subscribersGained", 0) or 0
        lost = s.get("subscribersLost", 0) or 0
        net = gained - lost

        stat = lambda label, val, sub="": (
            f'<div class="stat"><span class="lbl">{label}</span>'
            f'<span class="val">{val}</span>'
            + (f'<span class="sub">{sub}</span>' if sub else "") + "</div>")

        stats_html = "".join([
            stat("Views", fmt_num(s.get("views")), f"{days} hari"),
            stat("Menit ditonton", fmt_num(s.get("estimatedMinutesWatched"))),
            stat("Rata-rata tonton", fmt_dur(s.get("averageViewDuration")),
                 f'{float(s.get("averageViewPercentage") or 0):.0f}% durasi'),
            stat("Subscriber", f'{"+" if net >= 0 else ""}{net}',
                 f"{gained} masuk · {lost} keluar"),
        ])

        rows = []
        for i, v in enumerate(c["videos"], 1):
            pct = float(v.get("averageViewPercentage") or 0)
            # >50% ditonton = kuat, <30% = lemah
            cls = "good" if pct >= 50 else ("weak" if pct < 30 else "")
            rows.append(
                f'<tr><td class="n">{i}</td>'
                f'<td class="t"><span title="{esc(v["title"])}">{esc(v["title"][:64])}</span>'
                f'<em>{v["published"]}</em></td>'
                f'<td class="r">{fmt_num(v.get("views"))}</td>'
                f'<td class="r">{fmt_dur(v.get("averageViewDuration"))}</td>'
                f'<td class="r {cls}">{pct:.0f}%</td>'
                f'<td class="r">{v.get("subscribersGained", 0) or 0}</td></tr>')

        table = ("".join(rows) if rows else
                 '<tr><td colspan="6" class="empty">Belum ada data video '
                 'pada periode ini</td></tr>')

        cards.append(f"""
    <section class="card">
      <header>
        <div>
          <h2>{esc(c["title"])}</h2>
          <p class="meta">{fmt_num(c["subs"])} subscriber ·
             {c["total_videos"] or "—"} video ·
             {fmt_num(c["total_views"])} total views ·
             <code>{esc(c["profile"])}</code></p>
        </div>
      </header>
      <div class="stats">{stats_html}</div>
      {f'<div class="trend">{sparkline(c["daily"])}<span>tren views harian</span></div>'
       if len(c["daily"]) > 1 else ''}
      <table>
        <thead><tr><th></th><th>Judul</th><th class="r">Views</th>
          <th class="r">Rata2</th><th class="r">% tonton</th>
          <th class="r">Subs</th></tr></thead>
        <tbody>{table}</tbody>
      </table>
    </section>""")

    periode = channels[0] if channels else {}
    return f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard YouTube</title>
<style>
  :root {{
    --bg:#faf9f7; --card:#fff; --ink:#1a1a1a; --dim:#6b7280;
    --line:#e5e7eb; --good:#059669; --weak:#dc2626; --accent:#2563eb;
  }}
  @media (prefers-color-scheme:dark) {{
    :root {{ --bg:#14151a; --card:#1c1e26; --ink:#e8e8ea; --dim:#9199a8;
             --line:#2b2e38; --good:#34d399; --weak:#f87171; --accent:#60a5fa; }}
  }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; padding:28px 20px 56px; background:var(--bg); color:var(--ink);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
  .wrap {{ max-width:1000px; margin:0 auto }}
  h1 {{ font-size:22px; margin:0 0 4px }}
  .sub {{ color:var(--dim); font-size:13px; margin:0 0 26px }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
    padding:20px 22px; margin-bottom:22px; }}
  .card header {{ display:flex; justify-content:space-between; align-items:baseline;
    gap:12px; margin-bottom:16px }}
  h2 {{ font-size:17px; margin:0 }}
  .meta {{ color:var(--dim); font-size:12.5px; margin:3px 0 0 }}
  .meta code {{ background:var(--bg); padding:1px 5px; border-radius:4px; font-size:11.5px }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:12px; margin-bottom:18px }}
  .stat {{ background:var(--bg); border-radius:9px; padding:11px 13px }}
  .stat .lbl {{ display:block; color:var(--dim); font-size:11.5px;
    text-transform:uppercase; letter-spacing:.04em }}
  .stat .val {{ display:block; font-size:21px; font-weight:600; margin-top:3px;
    font-variant-numeric:tabular-nums }}
  .stat .sub {{ display:block; color:var(--dim); font-size:11.5px; margin-top:1px }}
  .trend {{ display:flex; align-items:center; gap:10px; margin-bottom:16px;
    color:var(--accent) }}
  .trend span {{ color:var(--dim); font-size:11.5px; white-space:nowrap }}
  .spark {{ width:100%; height:44px }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px }}
  th {{ text-align:left; color:var(--dim); font-weight:500; font-size:11.5px;
    text-transform:uppercase; letter-spacing:.04em;
    padding:0 8px 7px; border-bottom:1px solid var(--line) }}
  td {{ padding:9px 8px; border-bottom:1px solid var(--line);
    font-variant-numeric:tabular-nums }}
  tr:last-child td {{ border-bottom:0 }}
  .r {{ text-align:right; white-space:nowrap }}
  .n {{ color:var(--dim); width:26px; font-size:12px }}
  .t span {{ display:block }}
  .t em {{ color:var(--dim); font-size:11.5px; font-style:normal }}
  .good {{ color:var(--good); font-weight:600 }}
  .weak {{ color:var(--weak) }}
  .empty {{ color:var(--dim); text-align:center; padding:22px }}
  footer {{ color:var(--dim); font-size:12px; margin-top:30px; line-height:1.7 }}
</style></head><body><div class="wrap">
  <h1>Dashboard YouTube</h1>
  <p class="sub">Periode {periode.get("start","")} s/d {periode.get("end","")}
     ({days} hari) · dibuat {now}</p>
  {"".join(cards) if cards else '<p class="empty">Belum ada profil. Jalankan login.py dulu.</p>'}
  <footer>
    Data dari YouTube Analytics API, tertinggal {LAG_DAYS} hari dari realtime —
    itu bawaan YouTube, bukan kesalahan pengambilan.<br>
    <strong>Impressions dan CTR tidak ada di API ini</strong> — dua angka itu
    hanya tersedia di YouTube Studio.<br>
    % tonton: <span class="good">hijau ≥50%</span> ·
    <span class="weak">merah &lt;30%</span>
  </footer>
</div></body></html>"""


def selftest():
    assert fmt_num(1_500_000) == "1,5 jt"
    assert fmt_num(12_345) == "12,3 rb"
    assert fmt_num(830) == "830"
    assert fmt_num(None) == "—"
    assert fmt_dur(307) == "5:07"
    assert fmt_dur(59) == "0:59"
    assert fmt_dur(None) == "—"
    assert sparkline([]) == ""
    assert sparkline([("d", 1)]) == ""          # <2 titik: tak ada garis
    assert "polyline" in sparkline([("a", 1), ("b", 5), ("c", 3)])
    # halaman tetap terbentuk walau tak ada channel
    out = render([], 28)
    assert "<!doctype html>" in out and "Belum ada profil" in out
    # judul dengan karakter HTML tidak merusak halaman
    ch = {"profile": "p", "title": '<script>x</script>', "subs": 10,
          "total_views": 5, "total_videos": 2, "start": "a", "end": "b",
          "summary": {"views": 3}, "videos": [], "daily": []}
    assert "<script>x</script>" not in render([ch], 28)
    print("selftest ok: format angka, sparkline, escaping HTML")


def main():
    ap = argparse.ArgumentParser(description="Dashboard HTML YouTube Analytics")
    ap.add_argument("profile", nargs="?", help="nama profil (default: semua)")
    ap.add_argument("--days", type=int, default=28, help="periode hari (default 28)")
    ap.add_argument("-o", "--out", help=f"file keluaran (default {DEFAULT_OUT})")
    ap.add_argument("--no-open", action="store_true", help="jangan buka browser")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    names = [args.profile] if args.profile else profiles()
    if not names:
        sys.exit("Belum ada profil. Jalankan: "
                 f"python3 {CFG / 'login.py'} <nama>")

    channels = []
    for name in names:
        print(f"Ambil data: {name}…", file=sys.stderr)
        try:
            channels.append(collect(name, args.days))
        except Exception as e:
            print(f"  gagal: {str(e)[:90]}", file=sys.stderr)

    if not channels:
        sys.exit("Tidak ada data yang berhasil diambil.")

    out = Path(args.out).expanduser() if args.out else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(channels, args.days), encoding="utf-8")

    print(f"\n{len(channels)} channel -> {out}", file=sys.stderr)
    if not args.no_open:
        webbrowser.open(f"file://{out}")


if __name__ == "__main__":
    main()
