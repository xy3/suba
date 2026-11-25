#!/usr/bin/env python3
"""Benchmark suba against ffsubsync on Tears of Steel (CC-BY, Blender Foundation).

Each case takes an official, correctly timed subtitle track, breaks it in a
known way (a shift, a PAL frame-rate conversion, or both), hands the broken
file to each tool, and measures how far every output line lands from where
the original track had it.

    bench/fetch.sh                  # download the film and subtitle tracks
    python bench/run.py             # writes bench/results.md

Needs ffmpeg, the suba checkout (with openai-whisper) and `ffsubsync` on PATH.
"""

import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MEDIA = HERE / "media"
WORK = MEDIA / "work"
VIDEO = MEDIA / "tos.mov"
sys.path.insert(0, str(ROOT))
import main as suba  # noqa: E402  (parse_subtitle / write_subtitle)

PAL = 23.976 / 25.0  # a PAL-sped track runs 4% fast against 23.976 fps video

# (case id, language, shift seconds, time scale)
CASES = [
    ("en +7.3 s", "en", 7.3, 1.0),
    ("en -4.1 s", "en", -4.1, 1.0),
    ("de +7.3 s", "de", 7.3, 1.0),
    ("it -4.1 s", "it", -4.1, 1.0),
    ("fr +12.0 s", "fr-orig", 12.0, 1.0),
    ("ru +7.3 s", "ru", 7.3, 1.0),
    ("ja +7.3 s", "JP", 7.3, 1.0),
    ("en PAL drift", "en", 1.0, PAL),
    ("de PAL drift", "de", 1.0, PAL),
    ("es PAL drift +5 s", "es", 5.0, PAL),
    # A different intro or recap moves everything by minutes, not seconds.
    ("en +95 s", "en", 95.0, 1.0),
    ("de +95 s", "de", 95.0, 1.0),
    ("en +150 s", "en", 150.0, 1.0),
    ("it +150 s", "it", 150.0, 1.0),
    # The film remixed with its own music-and-effects stem added on top, so
    # the score drowns the dialogue (see fetch.sh).
    ("en +7.3 s, loud score", "en", 7.3, 1.0, "tos_loud2.mkv"),
    ("de +7.3 s, loud score", "de", 7.3, 1.0, "tos_loud2.mkv"),
    ("en +7.3 s, louder score", "en", 7.3, 1.0, "tos_loud3.mkv"),
    ("de +7.3 s, louder score", "de", 7.3, 1.0, "tos_loud3.mkv"),
]

LANG_TAG = {"fr-orig": "fr", "JP": "ja", "CH": "zh"}
TOLERANCE = 0.5  # seconds: a line this close looks right to a viewer


def broken_track(lang, shift, scale):
    ref = suba.parse_subtitle(str(MEDIA / "subs" / f"TOS-{lang}.srt"))
    bad = [
        {**e, "start": e["start"] * scale + shift, "end": e["end"] * scale + shift}
        for e in ref
    ]
    return ref, bad


def score(ref, out_path):
    """How close each original line landed, or None if no file was written.

    Lines are paired by their text, not their position: a tool may drop lines
    (ffsubsync drops any it shifts before 0:00), and a dropped line counts as
    a miss rather than silently shrinking the denominator.
    """
    if not out_path.exists():
        return None
    queues = {}
    for o in suba.parse_subtitle(str(out_path)):
        queues.setdefault(o["text"], []).append(o["start"])
    errs = []
    for r in ref:
        q = queues.get(r["text"])
        errs.append(abs(q.pop(0) - r["start"]) if q else float("inf"))
    return {
        "within": sum(e <= TOLERANCE for e in errs) / len(errs),
        "median": statistics.median(errs),
        "worst": max(errs),
        "dropped": sum(e == float("inf") for e in errs),
    }


def run(cmd):
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, time.time() - t, p.stdout + p.stderr


def tools(src, prefix, video=VIDEO):
    """Command for each tool; the output path is the argument after -o."""
    return {
        "suba": [
            sys.executable,
            str(ROOT / "main.py"),
            str(src),
            str(video),
            "-o",
            f"{prefix}.suba.srt",
        ],
        "ffsubsync": [
            "ffsubsync",
            str(video),
            "-i",
            str(src),
            "-o",
            f"{prefix}.ffsubsync.srt",
        ],
    }


def wrong_film():
    """A plausible dialogue track that belongs to some other film."""
    rng = random.Random(7)
    t, out = 20.0, []
    while t < 700:
        d = rng.uniform(1.0, 4.0)
        out.append({"start": t, "end": t + d, "text": "Somebody else's line."})
        t += d + rng.expovariate(1 / 6)
    return out


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    results = []
    for case, lang, shift, scale, *video in CASES:
        video = MEDIA / video[0] if video else VIDEO
        ref, bad = broken_track(lang, shift, scale)
        tag = LANG_TAG.get(lang, lang)
        src = WORK / f"{case.replace(' ', '_').replace(',', '')}.{tag}.srt"
        suba.write_subtitle(str(src), bad)
        before = score(ref, src)
        row = {"case": case, "before": before}
        for name, cmd in tools(src, WORK / src.stem, video).items():
            dst = Path(cmd[cmd.index("-o") + 1])
            dst.unlink(missing_ok=True)
            code, secs, log = run(cmd)
            row[name] = {"score": score(ref, dst), "secs": secs, "code": code}
            (WORK / f"{dst.name}.log").write_text(log)
        results.append(row)
        print(json.dumps(row), flush=True)

    # Subtitles that don't belong to this video at all: the right answer is
    # to write nothing.  Sintel is another Blender film (fetch.sh).
    sintel = suba.parse_subtitle(str(MEDIA / "subs" / "sintel_en.srt"))
    for label, stem, entries in [
        ("Sintel's subtitles (another film), en", "sintel.en", sintel),
        ("Sintel's subtitles (another film), as de", "sintel.de", sintel),
        ("made-up dialogue track, en", "madeup.en", wrong_film()),
    ]:
        src = WORK / f"{stem}.srt"
        suba.write_subtitle(str(src), entries)
        row = {"case": label, "kind": "mismatch"}
        for name, cmd in tools(src, WORK / stem).items():
            dst = Path(cmd[cmd.index("-o") + 1])
            dst.unlink(missing_ok=True)
            code, secs, log = run(cmd)
            row[name] = {"wrote_file": dst.exists(), "secs": secs, "code": code}
            (WORK / f"{dst.name}.log").write_text(log)
        results.append(row)
        print(json.dumps(row), flush=True)

    # One disc of a two-disc rip: the first 43% of the lines, shifted.  suba
    # refuses by design (--min-coverage); ffsubsync syncs the half it has.
    ref = suba.parse_subtitle(str(MEDIA / "subs" / "TOS-en.srt"))
    ref = ref[: int(len(ref) * 0.43)]
    src = WORK / "half.en.srt"
    suba.write_subtitle(
        str(src), [{**e, "start": e["start"] + 7.3, "end": e["end"] + 7.3} for e in ref]
    )
    row = {"case": "first 43% of the track (CD1), en +7.3 s", "before": score(ref, src)}
    for name, cmd in tools(src, WORK / "half").items():
        dst = Path(cmd[cmd.index("-o") + 1])
        dst.unlink(missing_ok=True)
        code, secs, log = run(cmd)
        row[name] = {"score": score(ref, dst), "secs": secs, "code": code}
        (WORK / f"{dst.name}.log").write_text(log)
    results.append(row)
    print(json.dumps(row), flush=True)

    (HERE / "results.json").write_text(json.dumps(results, indent=2))
    write_markdown(results)


def cell(r):
    s = r["score"]
    if s is None:
        return "refused"
    med = "lines dropped" if s["median"] == float("inf") else f"{s['median']:.2f} s"
    return f"{100 * s['within']:.0f}% · {med}"


def write_markdown(results, path=HERE / "results.md"):
    lines = [
        "| Case | Before | suba | ffsubsync |",
        "|------|--------|------|-----------|",
    ]
    verdict = {True: "**wrote a wrong file**", False: "refused"}
    for r in results:
        if r.get("kind") == "mismatch":
            lines.append(
                f"| {r['case']} | — | {verdict[r['suba']['wrote_file']]} "
                f"| {verdict[r['ffsubsync']['wrote_file']]} |"
            )
            continue
        b = r["before"]
        lines.append(
            f"| {r['case']} | {100 * b['within']:.0f}% · {b['median']:.2f} s "
            f"| {cell(r['suba'])} | {cell(r['ffsubsync'])} |"
        )
    t = {
        n: statistics.median(r[n]["secs"] for r in results)
        for n in ("suba", "ffsubsync")
    }
    lines += [
        "",
        f"Cells: share of lines within {TOLERANCE} s of the official timing · "
        "median error. Median run time: "
        f"suba {t['suba']:.0f} s, ffsubsync {t['ffsubsync']:.0f} s.",
    ]
    path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
