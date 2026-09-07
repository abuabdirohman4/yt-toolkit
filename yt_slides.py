#!/usr/bin/env python3
"""Ekstrak slide dari video + pasangkan dengan transcript di detik itu.

Untuk video bergaya slideshow: deteksi kapan gambar berganti, simpan frame-nya,
lalu ambil potongan transcript yang diucapkan saat slide itu tampil.

Usage:
    python3 yt_slides.py video.mp4                        # transcript dari YouTube
    python3 yt_slides.py video.mp4 -t transcript.txt      # transcript dari file
    python3 yt_slides.py video.mp4 --threshold 0.4        # kurang/lebih peka
    python3 yt_slides.py video.mp4 --selftest
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


def ffmpeg_ok():
    for tool in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            sys.exit(f"{tool} tidak ditemukan. Install: brew install ffmpeg")


def duration(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def detect_times(video, threshold):
    """Detik-detik saat gambar berubah drastis (belum ambil gambarnya).

    Filter `select=gt(scene,T)` membandingkan tiap frame dengan sebelumnya dan
    lolos kalau bedanya > T. Untuk slideshow, pergantian slide = beda besar.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video),
         "-vf", f"select='gt(scene,{threshold})',showinfo",
         "-vsync", "vfr", "-f", "null", "-"],
        capture_output=True, text=True)
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", proc.stderr)]


def grab_at(video, secs, outdir, idx):
    """Ambil satu frame pada detik tertentu."""
    out = outdir / f"slide_{idx:03d}.jpg"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-ss", f"{secs:.2f}", "-i", str(video),
         "-vframes", "1", "-q:v", "3", str(out)],
        capture_output=True, check=False)
    return out if out.exists() else None


def capture_settled(video, times, outdir, settle, total):
    """Ambil frame SETELAH transisi selesai, bukan di tengahnya.

    Transisi (whip pan, flip, fade) terdeteksi sebagai perubahan besar, tapi
    frame di detik itu masih kabur/terbalik. Menunggu beberapa detik memberi
    gambar yang sudah mapan — selama slide berikutnya belum keburu datang.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("slide_*.jpg"):
        old.unlink()

    slides = []
    for i, t in enumerate(times):
        nxt = times[i + 1] if i + 1 < len(times) else total
        # jangan melewati slide berikutnya; sisakan sedikit jarak aman
        wait = min(settle, max(0.0, (nxt - t) / 2))
        img = grab_at(video, t + wait, outdir, len(slides))
        if img:
            slides.append((t, img))
    return slides


def merge_times(times, min_gap):
    """Buang deteksi yang terlalu dekat dengan sebelumnya.

    Satu transisi memicu beberapa deteksi berturut-turut (awal, tengah, akhir
    putaran), semuanya untuk pergantian yang sama. Ambil yang pertama tiap
    kelompok.
    """
    kept = []
    for t in sorted(times):
        if not kept or t - kept[-1] >= min_gap:
            kept.append(t)
    return kept


def parse_transcript(path, video_title=None):
    """File transcript -> [(detik, teks)].

    Menerima format keluaran yt-script: paragraf berawalan [M:SS] atau
    [H:MM:SS]. Kalau file memuat banyak video, ambil bagian milik video ini.
    """
    text = Path(path).read_text(encoding="utf-8")

    if video_title:
        # File gabungan: potong bagian video yang cocok judulnya
        blocks = re.split(r"^-{20,}$", text, flags=re.M)
        for i, b in enumerate(blocks):
            if video_title.lower()[:30] in b.lower():
                text = "\n".join(blocks[i:i + 3])
                break

    out = []
    for m in re.finditer(r"^\[(\d+):(\d+)(?::(\d+))?\]\s*(.+?)(?=^\[|\Z)",
                         text, flags=re.M | re.S):
        a, b, c, body = m.groups()
        secs = (int(a) * 3600 + int(b) * 60 + int(c)) if c else (int(a) * 60 + int(b))
        out.append((secs, " ".join(body.split())))
    return out


def text_between(cues, start, end):
    """Teks yang diucapkan saat slide tampil, rentang [start, end).

    Paragraf transcript (~30 detik) sering lebih panjang dari jeda antar slide,
    jadi banyak slide tidak punya paragraf yang mulai di rentangnya sendiri.
    Slide itu tetap mewarisi paragraf yang sedang berjalan — itulah yang
    terdengar saat gambarnya tampil.
    """
    inside = [t for s, t in cues if start <= s < end]
    if inside:
        return " ".join(inside).strip()

    # Slide jatuh di tengah satu paragraf. Ambil bagian paragraf yang kira-kira
    # terucap selama slide tampil — dihitung proporsional dari posisi slide,
    # supaya slide berurutan tidak menampilkan teks yang sama persis.
    prev = [(s, t) for s, t in cues if s <= start]
    if not prev:
        return ""
    p_start, para = prev[-1]
    later = [s for s, _ in cues if s > p_start]
    p_end = later[0] if later else max(end, p_start + 1)

    span = p_end - p_start
    if span <= 0:
        return para.strip()
    words = para.split()
    a = int(len(words) * max(0.0, (start - p_start) / span))
    b = int(len(words) * min(1.0, (end - p_start) / span))
    return " ".join(words[a:b]).strip() or para.strip()


def build_report(video, slides, cues, outdir, total):
    lines = [f"# Slide: {video.stem}", "",
             f"Durasi {int(total // 60)}:{int(total % 60):02d} | "
             f"{len(slides)} slide terdeteksi", ""]
    if not cues:
        lines += ["> Transcript tidak tersedia — hanya daftar gambar.", ""]

    for i, (secs, img) in enumerate(slides):
        nxt = slides[i + 1][0] if i + 1 < len(slides) else total
        stamp = f"{int(secs // 60)}:{int(secs % 60):02d}"
        lines += [f"## Slide {i + 1} — {stamp}", "",
                  f"![slide {i + 1}]({img.name})", ""]
        said = text_between(cues, secs, nxt) if cues else ""
        if said:
            lines += [f"**Dibicarakan saat ini:** {said}", ""]
        lines.append("")

    out = outdir / "slides.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def selftest():
    """Cek pencocokan transcript <-> rentang slide."""
    cues = [(0, "buka"), (30, "tengah"), (75, "akhir")]
    assert text_between(cues, 0, 30) == "buka"
    assert text_between(cues, 30, 80) == "tengah akhir"
    # Setelah paragraf terakhir: warisi paragraf yang sedang berjalan.
    assert text_between(cues, 100, 200) == "akhir"
    # Sebelum paragraf pertama: memang tidak ada yang diucapkan.
    assert text_between([(50, "nanti")], 0, 10) == ""

    # Dua slide di dalam SATU paragraf harus dapat potongan berbeda, bukan
    # paragraf penuh yang sama diulang.
    para = [(0, "satu dua tiga empat lima enam tujuh delapan"), (40, "berikutnya")]
    awal = text_between(para, 0, 20)
    akhir = text_between(para, 20, 40)
    assert awal and akhir, (awal, akhir)
    assert awal != akhir, "slide berurutan dapat teks identik"
    assert "satu" in awal and "delapan" in akhir, (awal, akhir)

    sample = "[0:00] halo dunia\n\n[1:30] bagian dua\n\n[1:02:05] jauh\n"
    p = Path("/tmp/_yt_slides_test.txt")
    p.write_text(sample)
    got = parse_transcript(p)
    p.unlink()
    assert [s for s, _ in got] == [0, 90, 3725], got
    assert got[0][1] == "halo dunia", got[0]
    print("selftest ok: rentang transcript + parse timestamp benar")


def main():
    ap = argparse.ArgumentParser(description="Ekstrak slide + transcript dari video")
    ap.add_argument("video", nargs="?", help="file video (.mp4/.webm/.mkv)")
    ap.add_argument("-t", "--transcript", help="file transcript (.txt dari yt-script)")
    ap.add_argument("--threshold", type=float, default=0.3,
                    help="kepekaan deteksi 0.1-0.6 (default 0.3; kecil=lebih banyak slide)")
    ap.add_argument("--min-gap", type=float, default=8.0,
                    help="jarak minimum antar slide dalam detik (default 8; "
                         "menyaring animasi dalam slide yang sama)")
    ap.add_argument("--settle", type=float, default=1.5,
                    help="tunggu N detik setelah pergantian sebelum ambil gambar "
                         "(default 1.5; melewati frame transisi yang buram)")
    ap.add_argument("-o", "--out", help="folder hasil (default: di sebelah video)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.video:
        ap.error("butuh file video (atau --selftest)")

    ffmpeg_ok()
    video = Path(args.video).expanduser()
    if not video.is_file():
        sys.exit(f"Tidak ketemu: {video}")

    outdir = Path(args.out).expanduser() if args.out else video.parent / f"{video.stem}_slides"
    total = duration(video)
    print(f"{video.name} — {int(total // 60)}:{int(total % 60):02d}", file=sys.stderr)

    print(f"Deteksi pergantian slide (threshold {args.threshold})...", file=sys.stderr)
    times = detect_times(video, args.threshold)
    raw = len(times)
    times = [0.0] + [t for t in times if t > args.min_gap]
    times = merge_times(times, args.min_gap)
    if not times:
        sys.exit("Tidak ada pergantian terdeteksi. Coba --threshold lebih kecil.")

    slides = capture_settled(video, times, outdir, args.settle, total)
    dropped = raw + 1 - len(slides)
    print(f"    {len(slides)} slide" +
          (f" ({dropped} frame transisi/duplikat dibuang)" if dropped > 0 else ""),
          file=sys.stderr)

    cues = []
    if args.transcript:
        cues = parse_transcript(args.transcript, video.stem)
        print(f"    transcript: {len(cues)} paragraf", file=sys.stderr)
    else:
        print("    tanpa transcript (pakai -t file.txt)", file=sys.stderr)

    out = build_report(video, slides, cues, outdir, total)
    size = sum(f.stat().st_size for f in outdir.glob("*.jpg")) / 1048576
    print(f"\n{len(slides)} slide -> {outdir} (~{size:.0f} MB)", file=sys.stderr)
    print(f"laporan -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
