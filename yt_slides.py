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


def sharpness(img):
    """Kekuatan tepi gambar — makin tinggi, makin tajam.

    Dipakai HANYA untuk membandingkan beberapa kandidat dari momen yang
    berdekatan. Sebagai ambang mutlak angka ini menyesatkan: slide berlatar
    polos wajar bernilai rendah walau tajam sempurna.
    """
    # `movie=` menanam path di dalam string filter, dan kurung/koma/titik-dua
    # pada nama folder merusak sintaksnya (ffprobe gagal diam-diam, skor 0
    # untuk semua kandidat). Jalankan dari dalam folder gambar dan sebut
    # namanya saja, supaya karakter bermasalah tak pernah masuk filter.
    img = Path(img)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-f", "lavfi",
         "-i", f"movie={img.name},format=gray,sobel,signalstats",
         "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
         "-of", "csv=p=0"],
        capture_output=True, text=True, cwd=str(img.parent)).stdout.strip()
    try:
        return float(out.splitlines()[0].rstrip(","))
    except (ValueError, IndexError):
        return 0.0


def grab_sharpest(video, secs, outdir, idx, window, limit):
    """Ambil frame paling tajam di sekitar `secs`, bukan tepat di detik itu.

    Sampling berkala bisa jatuh persis saat transisi (whip pan / flip)
    berlangsung, menghasilkan gambar kabur. Beberapa kandidat berjarak dekat
    dicoba, lalu yang paling tajam dipakai.
    """
    best_img, best_score = None, -1.0
    tmp = outdir / f"_probe_{idx:03d}.jpg"
    for off in (0.0, window, -window, window * 2):
        t = secs + off
        if t < 0 or (limit and t >= limit):
            continue
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-vframes", "1", "-q:v", "3", str(tmp)],
            capture_output=True, check=False)
        if not tmp.exists():
            continue
        score = sharpness(tmp)
        if score > best_score:
            best_score, best_img = score, (t, tmp.read_bytes())

    tmp.unlink(missing_ok=True)
    if not best_img:
        return None, secs
    shot, data = best_img
    out = outdir / f"slide_{idx:03d}.jpg"
    out.write_bytes(data)
    return out, shot


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
        target = t + wait

        # Titik ini bisa jatuh persis saat transisi (whip pan / flip) sedang
        # berlangsung -> gambar kabur. Coba beberapa kandidat berdekatan dan
        # ambil yang paling tajam, selama tidak menabrak slide berikutnya.
        room = max(0.0, nxt - target)
        img, shot = grab_sharpest(video, target, outdir, len(slides),
                                  min(0.5, room / 3) if room else 0.0, total)
        if img:
            # simpan detik GAMBAR DIAMBIL, bukan detik deteksi — transcript
            # harus cocok dengan yang tampil di layar, bukan dengan momen
            # transisi beberapa detik sebelumnya.
            slides.append((shot, img))
    return slides


def detect_transitions(video, outdir, min_score=0.25):
    """Momen transisi + tebakan jenisnya -> [(detik, skor, jenis)].

    Transisi memicu lonjakan scene_score jauh di atas gerakan biasa dalam satu
    adegan (~0.02). Jenisnya ditebak dari ketajaman frame di detik itu:
    transisi bergerak (whip pan, flip) meninggalkan frame kabur, sedangkan
    potongan langsung tetap tajam.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video),
         "-vf", f"select='gt(scene,{min_score})',metadata=print",
         "-vsync", "vfr", "-f", "null", "-"],
        capture_output=True, text=True)

    raw = []
    for blk in re.finditer(r"pts_time:([\d.]+).*?scene_score=([\d.]+)",
                           proc.stderr, flags=re.S):
        raw.append((float(blk.group(1)), float(blk.group(2))))
    if not raw:
        return []

    # Satu transisi memicu beberapa lonjakan berturut-turut (awal, tengah,
    # akhir gerakan). Gabungkan yang berdekatan, ambil skor tertingginya.
    hits = []
    for t, score in raw:
        if hits and t - hits[-1][0] < 1.5:
            if score > hits[-1][1]:
                hits[-1] = (hits[-1][0], score)
        else:
            hits.append((t, score))

    # Bandingkan ketajaman DI titik transisi vs sesaat sesudahnya. Frame kabur
    # -> transisi bergerak; sama tajam -> potongan langsung.
    tmp_a, tmp_b = outdir / "_tr_a.jpg", outdir / "_tr_b.jpg"
    out = []
    for t, score in hits:
        for path, at in ((tmp_a, t), (tmp_b, t + 0.6)):
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-y", "-ss", f"{at:.2f}",
                 "-i", str(video), "-vframes", "1", "-q:v", "3", str(path)],
                capture_output=True, check=False)
        if not (tmp_a.exists() and tmp_b.exists()):
            continue
        during, after = sharpness(tmp_a), sharpness(tmp_b)
        ratio = during / after if after else 1.0
        if ratio < 0.55:
            kind = "gerak (whip pan / flip)"
        elif ratio < 0.85:
            kind = "gerak halus (slide / fade)"
        else:
            kind = "potongan langsung (cut)"
        out.append((t, score, kind))

    tmp_a.unlink(missing_ok=True)
    tmp_b.unlink(missing_ok=True)
    return out


def transition_table(trans, total):
    """Bagian markdown berisi tabel transisi."""
    if not trans:
        return []
    lines = ["## Transisi terdeteksi", "",
             f"{len(trans)} transisi. Jenis ditebak dari ketajaman frame saat "
             "transisi berlangsung — bukan dari membaca gambar.", "",
             "| # | Waktu | Kekuatan | Jenis |", "|---|---|---|---|"]
    for i, (t, score, kind) in enumerate(trans, 1):
        lines.append(f"| {i} | {int(t // 60)}:{int(t % 60):02d} | "
                     f"{score:.2f} | {kind} |")
    gaps = [trans[i + 1][0] - trans[i][0] for i in range(len(trans) - 1)]
    if gaps:
        lines += ["", f"Jarak antar transisi: rata-rata {sum(gaps) / len(gaps):.0f} "
                      f"detik (tersingkat {min(gaps):.0f}s, terpanjang {max(gaps):.0f}s).", ""]
    return lines + [""]


def sample_times(total, every):
    """Titik waktu tiap N detik — tanpa deteksi, tidak ada yang terlewat."""
    out, t = [], 0.0
    while t < total:
        out.append(t)
        t += every
    return out


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
        # File gabungan: potong bagian milik video ini. Nama file sering berupa
        # slug ("real-estate-vs-stocks-...") sedangkan judul di transcript
        # memakai spasi dan tanda baca, jadi keduanya disederhanakan dulu
        # menjadi deretan kata agar bisa dibandingkan.
        def words(x):
            return re.sub(r"[^a-z0-9]+", " ", x.lower()).split()

        want = words(video_title)
        # buang penanda resolusi/codec yang bukan bagian judul
        drop = {"720p", "1080p", "480p", "360p", "h264", "h265", "mp4", "webm"}
        want = [w for w in want if w not in drop]

        blocks = re.split(r"^-{20,}$", text, flags=re.M)
        best, best_hit = None, 0
        for i, b in enumerate(blocks):
            have = set(words(b[:300]))
            hit = sum(1 for w in want if w in have)
            if hit > best_hit:
                best, best_hit = i, hit

        # perlu kecocokan meyakinkan; kalau tidak, lebih baik menolak daripada
        # diam-diam memakai transcript video lain
        if best is not None and best_hit >= max(3, len(want) * 0.5):
            text = "\n".join(blocks[best:best + 3])
        else:
            raise ValueError(
                f"transcript untuk '{video_title}' tidak ketemu di file itu "
                f"(cocok {best_hit}/{len(want)} kata). Pakai file transcript "
                f"video ini, atau ambil dulu dengan yt-script.")

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


def build_report(video, slides, cues, outdir, total, trans=None):
    lines = [f"# Slide: {video.stem}", "",
             f"Durasi {int(total // 60)}:{int(total % 60):02d} | "
             f"{len(slides)} slide", ""]
    if cues:
        lines += [
            "> **Catatan untuk pembaca (termasuk AI):** teks di bawah tiap",
            "> gambar adalah PERKIRAAN, bukan hasil membaca gambar.",
            ">",
            "> Cara kerjanya: gambar diambil pada detik tertentu, lalu diambil",
            "> potongan transcript yang jatuh di rentang waktu itu. Transcript",
            "> YouTube dikelompokkan per ~30 detik, sedangkan gambar diambil",
            "> lebih rapat, jadi potongan teks dibagi secara proporsional",
            "> menurut posisi waktu — bukan menurut isi.",
            ">",
            "> Akibatnya teks bisa bergeser 5-15 detik dari gambarnya: sebuah",
            "> kalimat mungkin merujuk gambar sebelum atau sesudahnya. Anggap",
            "> teks sebagai KONTEKS SEKITAR, bukan keterangan gambar.",
            ">",
            "> Isi gambar sendiri TIDAK pernah dianalisa — tak ada OCR maupun",
            "> pengenalan gambar. Untuk tahu isi sebuah slide, gambarnya harus",
            "> benar-benar dilihat.",
            "",
        ]
    else:
        lines += ["> Transcript tidak tersedia — hanya daftar gambar.", ""]

    lines += transition_table(trans, total)

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
    ap.add_argument("--every", type=float, metavar="DETIK",
                    help="ambil frame tiap N detik, abaikan deteksi. Tidak ada "
                         "adegan terlewat, tapi ada duplikat saat adegan lama "
                         "bertahan (mis. --every 10)")
    ap.add_argument("-o", "--out", help="folder hasil (default: di sebelah video)")
    ap.add_argument("--transitions", action="store_true",
                    help="tambahkan tabel transisi (kapan + jenisnya)")
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

    if args.every:
        print(f"Sampling tiap {args.every:g} detik...", file=sys.stderr)
    else:
        print(f"Deteksi pergantian slide (threshold {args.threshold})...",
              file=sys.stderr)
    if args.every:
        times = sample_times(total, args.every)
        settle = 0.0        # tidak ada transisi yang perlu ditunggu
    else:
        raw = detect_times(video, args.threshold)
        times = merge_times([0.0] + [t for t in raw if t > args.min_gap],
                            args.min_gap)
        settle = args.settle
        if not times:
            sys.exit("Tidak ada pergantian terdeteksi. Coba --threshold lebih kecil.")

    slides = capture_settled(video, times, outdir, settle, total)
    print(f"    {len(slides)} slide", file=sys.stderr)

    cues = []
    if args.transcript:
        cues = parse_transcript(args.transcript, video.stem)
        print(f"    transcript: {len(cues)} paragraf", file=sys.stderr)
    else:
        print("    tanpa transcript (pakai -t file.txt)", file=sys.stderr)

    trans = None
    if args.transitions:
        print("Deteksi transisi...", file=sys.stderr)
        trans = detect_transitions(video, outdir)
        print(f"    {len(trans)} transisi", file=sys.stderr)

    out = build_report(video, slides, cues, outdir, total, trans)
    size = sum(f.stat().st_size for f in outdir.glob("*.jpg")) / 1048576
    print(f"\n{len(slides)} slide -> {outdir} (~{size:.0f} MB)", file=sys.stderr)
    print(f"laporan -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
