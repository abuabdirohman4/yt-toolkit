#!/usr/bin/env python3
"""Analisa audio video: kualitas teknis + gaya narasi -> laporan .md.

Mengukur loudness, dinamika, dan pola jeda bicara, lalu menerjemahkannya jadi
penilaian yang bisa dipelajari — bukan sekadar deretan angka.

Usage:
    python3 yt_audio.py video.mp4
    python3 yt_audio.py video.mp4 -o folder/
    python3 yt_audio.py video.mp4 --selftest
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Ambang jeda: -30dB cukup longgar untuk menangkap napas antar kalimat tanpa
# ikut menghitung musik latar yang pelan.
SILENCE_DB = -30
SILENCE_MIN = 0.35
YT_TARGET_LUFS = -14.0     # target normalisasi YouTube


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def duration(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def has_audio(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True).stdout.strip()
    return bool(out)


def measure_loudness(video):
    """LUFS terintegrasi, jangkauan dinamis, puncak."""
    err = run(["ffmpeg", "-hide_banner", "-i", str(video),
               "-af", "ebur128=framelog=quiet:peak=true", "-f", "null", "-"])
    def grab(pat):
        m = re.search(pat, err)
        return float(m.group(1)) if m else None
    return {
        "lufs": grab(r"I:\s*(-?[\d.]+)\s*LUFS"),
        "lra": grab(r"LRA:\s*(-?[\d.]+)\s*LU"),
        "lra_low": grab(r"LRA low:\s*(-?[\d.]+)"),
        "lra_high": grab(r"LRA high:\s*(-?[\d.]+)"),
        "peak": grab(r"Peak:\s*(-?[\d.]+)\s*dBFS"),
    }


def measure_volume(video):
    """Rata-rata dan puncak volume sederhana."""
    err = run(["ffmpeg", "-hide_banner", "-i", str(video),
               "-af", "volumedetect", "-f", "null", "-"])
    def grab(key):
        m = re.search(rf"{key}:\s*(-?[\d.]+) dB", err)
        return float(m.group(1)) if m else None
    return {"mean": grab("mean_volume"), "max": grab("max_volume")}


def measure_pauses(video):
    """Jeda bicara -> [(mulai, durasi)]."""
    err = run(["ffmpeg", "-hide_banner", "-i", str(video),
               "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}",
               "-f", "null", "-"])
    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", err)]
    durs = [float(m) for m in re.findall(r"silence_duration:\s*([\d.]+)", err)]
    return list(zip(starts, durs))


def measure_pitch(video, total, sample_sec=180):
    """Nada bicara -> dict, atau None kalau librosa belum terpasang.

    pyin akurat tapi lambat, jadi untuk video panjang hanya sebagian yang
    diproses: beberapa potongan yang tersebar merata, bukan satu blok di awal,
    supaya mewakili keseluruhan video.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        return None

    import tempfile
    import warnings
    warnings.filterwarnings("ignore")

    # Ambil potongan tersebar: awal, tengah, akhir (masing-masing 60 detik).
    chunks, want = [], min(sample_sec, total)
    n_chunk = max(1, int(want // 60))
    step = total / (n_chunk + 1)

    with tempfile.TemporaryDirectory() as td:
        for i in range(n_chunk):
            start = step * (i + 1) - 30
            start = max(0.0, min(start, max(0.0, total - 60)))
            wav = Path(td) / f"c{i}.wav"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-y", "-ss", f"{start:.1f}",
                 "-t", "60", "-i", str(video), "-vn", "-ac", "1",
                 "-ar", "16000", str(wav)],
                capture_output=True, check=False)
            if not wav.exists():
                continue
            y, sr = librosa.load(str(wav), sr=16000)
            f0, _, _ = librosa.pyin(y, sr=sr, fmin=60, fmax=350,
                                    frame_length=2048, hop_length=512)
            chunks.append(f0)

    if not chunks:
        return None
    f0 = np.concatenate(chunks)
    voiced = f0[~np.isnan(f0)]
    if len(voiced) < 50:
        return None

    return {
        "median": float(np.median(voiced)),
        "p10": float(np.percentile(voiced, 10)),
        "p90": float(np.percentile(voiced, 90)),
        "sd": float(np.std(voiced)),
        "voiced_pct": float(len(voiced) / len(f0) * 100),
        "sampled": n_chunk * 60,
    }


def judge_pitch(p):
    """Terjemahkan angka nada jadi penilaian."""
    if not p:
        return []
    med, sd = p["median"], p["sd"]

    if med < 100:
        who = "sangat rendah — suara pria berat, kesan berwibawa dan menenangkan"
    elif med < 135:
        who = "rendah — suara pria dewasa, khas narator penjelas"
    elif med < 180:
        who = "sedang — bisa pria bernada tinggi atau wanita bernada rendah"
    elif med < 230:
        who = "tinggi — suara wanita dewasa, kesan ramah dan energik"
    else:
        who = "sangat tinggi — bersemangat, kadang terdengar muda"

    # Variasi nada relatif terhadap nada dasarnya, bukan angka mutlak:
    # 30 Hz pada suara 110 Hz jauh lebih ekspresif daripada pada 220 Hz.
    # Ambang dikalibrasi dari narasi penjelas profesional (voice-over YouTube
    # bergaya edukasi), yang terukur di kisaran 35%. Angka itu WAJAR, bukan
    # berlebihan — ambang naif "di atas 30% = sangat ekspresif" salah melabeli
    # narasi normal sebagai berlebihan.
    rel = sd / med * 100 if med else 0
    if rel < 22:
        how = ("Datar dan terkendali. Nada nyaris tidak naik-turun — terdengar "
               "tenang dan berwibawa, tapi berisiko monoton kalau videonya panjang.")
    elif rel < 30:
        how = ("Cukup terkendali. Naik-turunnya hemat — cocok untuk materi "
               "serius, terdengar tenang tanpa jatuh jadi datar.")
    elif rel < 42:
        how = ("Ekspresif wajar. Ini wilayah narasi penjelas profesional: cukup "
               "hidup untuk menjaga perhatian sepanjang video panjang, tanpa "
               "terdengar dibuat-buat. Target yang bagus untuk ditiru.")
    else:
        how = ("Sangat ekspresif. Nada banyak bergerak — bersemangat dan hidup, "
               "cocok untuk konten ringan atau bercerita, bisa melelahkan untuk "
               "materi teknis yang panjang.")

    return [
        ("Nada dasar", f"{med:.0f} Hz", who),
        ("Variasi nada", f"±{sd:.0f} Hz ({rel:.0f}% dari nada dasar)", how),
    ]


def speech_stats(pauses, total):
    """Ubah daftar jeda jadi angka yang menggambarkan gaya bicara."""
    quiet = sum(d for _, d in pauses)
    talk = max(0.0, total - quiet)
    n = len(pauses)
    return {
        "count": n,
        "quiet_total": quiet,
        "talk_total": talk,
        "talk_pct": talk / total * 100 if total else 0,
        "per_min": n / (total / 60) if total else 0,
        "avg_pause": quiet / n if n else 0,
        "longest": max((d for _, d in pauses), default=0.0),
        # rata-rata panjang satu semburan bicara antar jeda
        "avg_burst": talk / (n + 1) if n else talk,
    }


def judge(loud, vol, sp):
    """Terjemahkan angka jadi penilaian. Ini inti laporannya."""
    out = []

    lufs = loud.get("lufs")
    if lufs is not None:
        gap = lufs - YT_TARGET_LUFS
        if -1 <= gap <= 1:
            v = f"Pas dengan target YouTube ({YT_TARGET_LUFS} LUFS) — tidak akan diubah platform."
        elif gap < -1:
            v = (f"{abs(gap):.1f} LU di bawah target YouTube. Aman: YouTube tidak "
                 f"menaikkan volume, jadi terdengar sedikit lebih pelan dari video lain.")
        else:
            v = (f"{gap:.1f} LU di atas target. YouTube akan menurunkannya otomatis — "
                 f"headroom terbuang, lebih baik master di {YT_TARGET_LUFS} LUFS.")
        out.append(("Loudness", f"{lufs:.1f} LUFS", v))

    lra = loud.get("lra")
    if lra is not None:
        if lra < 4:
            v = ("Sangat sempit — dikompresi berat. Volume nyaris rata sepanjang video. "
                 "Khas voice-over YouTube: jelas terdengar di HP dan tempat berisik, "
                 "tapi tanpa dinamika dramatis.")
        elif lra < 8:
            v = "Sedang — ada variasi tapi tetap terkendali. Umum untuk narasi bercerita."
        else:
            v = ("Lebar — perbedaan keras-pelan mencolok. Bagus untuk sinematik, "
                 "berisiko bagian pelan tenggelam saat ditonton di HP.")
        out.append(("Jangkauan dinamis", f"{lra:.1f} LU", v))

    peak = loud.get("peak") or vol.get("max")
    if peak is not None:
        if peak > -0.3:
            v = "Mepet 0 dBFS — sudah di-limit habis. Ciri audio yang dimaster serius."
        elif peak > -3:
            v = "Headroom sehat, tidak ada risiko clipping."
        else:
            v = f"Masih ada {abs(peak):.1f} dB ruang kosong — bisa dinaikkan saat master."
        out.append(("Puncak", f"{peak:.1f} dBFS", v))

    if sp["count"]:
        if sp["per_min"] > 18:
            v = ("Sangat rapat — potongan jeda agresif. Gaya edit modern: setiap napas "
                 "dan keraguan dipotong supaya penonton tidak sempat bosan.")
        elif sp["per_min"] > 10:
            v = "Rapat — jeda dirapikan tapi masih terdengar alami."
        else:
            v = "Longgar — irama bicara dibiarkan mengalir apa adanya."
        out.append(("Kerapatan jeda", f"{sp['per_min']:.0f} per menit", v))

        if sp["talk_pct"] > 88:
            v = ("Nyaris tanpa ruang kosong. Padat informasi, menuntut perhatian penuh — "
                 "efektif untuk retensi tapi melelahkan kalau videonya panjang.")
        elif sp["talk_pct"] > 75:
            v = "Padat tapi masih memberi ruang bernapas."
        else:
            v = "Banyak ruang hening — biasanya diisi musik atau visual."
        out.append(("Porsi bicara", f"{sp['talk_pct']:.0f}%", v))

    return out


def build_report(video, total, loud, vol, sp, pauses, pitch=None):
    mm = f"{int(total // 60)}:{int(total % 60):02d}"
    L = [f"# Analisa Audio: {video.stem}", "",
         f"Durasi {mm}", "",
         "> Semua angka diukur langsung dari file audio dengan ffmpeg. "
         "Penilaian di kolom kanan adalah tafsir dari angka itu — berguna "
         "untuk membandingkan antar video, bukan vonis mutlak.", "",
         "## Ringkasan", "",
         "| Aspek | Nilai | Artinya |", "|---|---|---|"]
    for name, val, why in judge(loud, vol, sp) + judge_pitch(pitch):
        L.append(f"| **{name}** | {val} | {why} |")

    L += ["", "## Angka mentah", "",
          "| Metrik | Nilai |", "|---|---|"]
    rows = [
        ("Loudness terintegrasi", f"{loud['lufs']:.1f} LUFS" if loud.get("lufs") else "-"),
        ("Jangkauan dinamis (LRA)", f"{loud['lra']:.1f} LU" if loud.get("lra") else "-"),
        ("LRA rendah / tinggi",
         f"{loud['lra_low']:.1f} / {loud['lra_high']:.1f} LUFS"
         if loud.get("lra_low") is not None else "-"),
        ("True peak", f"{loud['peak']:.1f} dBFS" if loud.get("peak") is not None else "-"),
        ("Volume rata-rata", f"{vol['mean']:.1f} dB" if vol.get("mean") is not None else "-"),
        ("Jumlah jeda", f"{sp['count']}"),
        ("Jeda per menit", f"{sp['per_min']:.1f}"),
        ("Rata-rata panjang jeda", f"{sp['avg_pause']:.2f} detik"),
        ("Jeda terpanjang", f"{sp['longest']:.1f} detik"),
        ("Rata-rata bicara antar jeda", f"{sp['avg_burst']:.1f} detik"),
        ("Total bicara", f"{sp['talk_total']:.0f} detik ({sp['talk_pct']:.0f}%)"),
        ("Total hening", f"{sp['quiet_total']:.0f} detik"),
    ]
    L += [f"| {k} | {v} |" for k, v in rows]

    # Jeda panjang sering menandai pergantian babak — berguna untuk melihat
    # struktur video tanpa menonton.
    long_ones = sorted([p for p in pauses if p[1] >= 1.0],
                       key=lambda x: -x[1])[:12]
    if long_ones:
        L += ["", "## Jeda panjang (≥1 detik)", "",
              "Sering menandai pergantian babak atau penekanan.", "",
              "| Waktu | Lama |", "|---|---|"]
        for t, d in sorted(long_ones):
            L.append(f"| {int(t // 60)}:{int(t % 60):02d} | {d:.1f}s |")

    L += ["", "## Cara meniru", "",
          "Untuk mendekati karakter audio ini:", ""]
    if loud.get("lufs") is not None:
        L.append(f"- Master di sekitar **{loud['lufs']:.0f} LUFS** dengan limiter di −0.3 dBTP")
    if loud.get("lra") is not None and loud["lra"] < 4:
        L.append("- Pakai kompresi kuat (ratio 4:1 ke atas) supaya volume rata")
    if sp["count"]:
        L.append(f"- Potong jeda sampai tersisa sekitar **{sp['avg_pause']:.2f} detik**; "
                 f"target ~{sp['per_min']:.0f} jeda per menit")
        L.append(f"- Bicara mengalir ~{sp['avg_burst']:.0f} detik sebelum jeda berikutnya")
    L.append("")
    return "\n".join(L)


def selftest():
    """Cek perhitungan gaya bicara dari daftar jeda."""
    pauses = [(10.0, 0.5), (20.0, 1.5), (30.0, 1.0)]   # total hening 3.0s
    sp = speech_stats(pauses, 60.0)
    assert sp["count"] == 3, sp
    assert abs(sp["quiet_total"] - 3.0) < 1e-9, sp
    assert abs(sp["talk_total"] - 57.0) < 1e-9, sp
    assert abs(sp["talk_pct"] - 95.0) < 1e-9, sp
    assert abs(sp["per_min"] - 3.0) < 1e-9, sp
    assert abs(sp["avg_pause"] - 1.0) < 1e-9, sp
    assert sp["longest"] == 1.5, sp

    # tanpa jeda sama sekali: jangan bagi nol
    z = speech_stats([], 60.0)
    assert z["count"] == 0 and z["talk_pct"] == 100.0, z
    assert z["avg_pause"] == 0 and z["longest"] == 0, z

    # penilaian harus menghasilkan baris, bukan meledak saat nilai kosong
    rows = judge({"lufs": -16.0, "lra": 2.5, "peak": -0.2}, {"max": -0.2}, sp)
    assert len(rows) >= 4, rows
    assert judge({}, {}, z) is not None

    # Penilaian nada: variasi dinilai RELATIF terhadap nada dasar, jadi simpangan
    # sama pada suara rendah harus terbaca lebih ekspresif daripada suara tinggi.
    assert judge_pitch(None) == []
    low = judge_pitch({"median": 110.0, "sd": 38.0})
    high = judge_pitch({"median": 220.0, "sd": 38.0})
    assert len(low) == 2 and len(high) == 2, (low, high)
    assert "rendah" in low[0][2], low[0]
    assert "tinggi" in high[0][2], high[0]
    assert low[1][2] != high[1][2], "variasi nada tidak dinilai relatif"

    # nada datar vs sangat ekspresif harus jatuh ke penilaian berbeda
    flat = judge_pitch({"median": 120.0, "sd": 12.0})[1][2]
    wide = judge_pitch({"median": 120.0, "sd": 60.0})[1][2]
    assert flat != wide and "monoton" in flat, (flat, wide)
    # narasi penjelas profesional (~35%) harus terbaca WAJAR, bukan berlebihan
    normal = judge_pitch({"median": 112.0, "sd": 40.0})[1][2]
    assert "wajar" in normal.lower(), normal
    assert normal != wide, "35% dan 50% tidak boleh sama penilaiannya"

    # laporan tetap terbentuk walau pitch tidak ada (librosa belum terpasang)
    md = build_report(Path("x.mp4"), 60.0,
                      {"lufs": -16.0, "lra": 2.5, "peak": -0.2},
                      {"mean": -19.0, "max": -0.2}, sp, pauses, None)
    assert "# Analisa Audio" in md and "Cara meniru" in md
    print("selftest ok: hitungan jeda + penilaian + nada benar")


def main():
    ap = argparse.ArgumentParser(description="Analisa audio video -> laporan .md")
    ap.add_argument("video", nargs="?", help="file video atau audio")
    ap.add_argument("-o", "--out", help="folder hasil (default: di sebelah video)")
    ap.add_argument("--no-pitch", action="store_true",
                    help="lewati analisa nada (lebih cepat)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.video:
        ap.error("butuh file video (atau --selftest)")

    video = Path(args.video).expanduser()
    if not video.is_file():
        sys.exit(f"Tidak ketemu: {video}")
    if not has_audio(video):
        sys.exit("File ini tidak punya stream audio.")

    total = duration(video)
    print(f"{video.name} — {int(total // 60)}:{int(total % 60):02d}", file=sys.stderr)

    print("Ukur loudness...", file=sys.stderr)
    loud = measure_loudness(video)
    vol = measure_volume(video)

    print("Deteksi jeda bicara...", file=sys.stderr)
    pauses = measure_pauses(video)
    sp = speech_stats(pauses, total)
    print(f"    {sp['count']} jeda, bicara {sp['talk_pct']:.0f}%", file=sys.stderr)

    pitch = None
    if not args.no_pitch:
        print("Analisa nada (butuh librosa)...", file=sys.stderr)
        pitch = measure_pitch(video, total)
        if pitch:
            print(f"    nada dasar {pitch['median']:.0f} Hz", file=sys.stderr)
        else:
            print("    dilewati (librosa belum terpasang / audio tak cukup)",
                  file=sys.stderr)

    outdir = Path(args.out).expanduser() if args.out else video.parent
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "audio.md"
    out.write_text(build_report(video, total, loud, vol, sp, pauses, pitch),
                   encoding="utf-8")
    print(f"\nlaporan -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
