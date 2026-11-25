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
    if not out_path.exists():
        return None
    out = suba.parse_subtitle(str(out_path))
    if len(out) != len(ref):
        return None
    errs = [abs(o["start"] - r["start"]) for o, r in zip(out, ref, strict=True)]
    return {
        "within": sum(e <= TOLERANCE for e in errs) / len(errs),
        "median": statistics.median(errs),
        "worst": max(errs),
    }


def run(cmd):
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, time.time() - t, p.stdout + p.stderr


def tools(src, prefix):
    """Command for each tool; the output path is the argument after -o."""
    return {
        "suba": [
            sys.executable,
            str(ROOT / "main.py"),
            str(src),
            str(VIDEO),
            "-o",
            f"{prefix}.suba.srt",
        ],
        "ffsubsync": [
            "ffsubsync",
            str(VIDEO),
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
    for case, lang, shift, scale in CASES:
        ref, bad = broken_track(lang, shift, scale)
        tag = LANG_TAG.get(lang, lang)
        src = WORK / f"{case.replace(' ', '_')}.{tag}.srt"
        suba.write_subtitle(str(src), bad)
        before = score(ref, src)
        row = {"case": case, "before": before}
        for name, cmd in tools(src, WORK / src.stem).items():
            dst = Path(cmd[cmd.index("-o") + 1])
            dst.unlink(missing_ok=True)
            code, secs, log = run(cmd)
            row[name] = {"score": score(ref, dst), "secs": secs, "code": code}
            (WORK / f"{dst.name}.log").write_text(log)
        results.append(row)
        print(json.dumps(row), flush=True)

    src = WORK / "wrong_film.en.srt"
    suba.write_subtitle(str(src), wrong_film())
    row = {"case": "subtitles from another film"}
    for name, cmd in tools(src, WORK / "wrong").items():
        dst = Path(cmd[cmd.index("-o") + 1])
        dst.unlink(missing_ok=True)
        code, secs, log = run(cmd)
        row[name] = {"wrote_file": dst.exists(), "secs": secs, "code": code}
        (WORK / f"{dst.name}.log").write_text(log)
    results.append(row)
    print(json.dumps(row), flush=True)

    (HERE / "results.json").write_text(json.dumps(results, indent=2))
    write_markdown(results)


def cell(r):
    s = r["score"]
    if s is None:
        return "failed" if r["code"] else "refused"
    return f"{100 * s['within']:.0f}% · {s['median']:.2f} s"


def write_markdown(results):
    lines = [
        "| Case | Before | suba | ffsubsync |",
        "|------|--------|------|-----------|",
    ]
    for r in results[:-1]:
        b = r["before"]
        lines.append(
            f"| {r['case']} | {100 * b['within']:.0f}% · {b['median']:.2f} s "
            f"| {cell(r['suba'])} | {cell(r['ffsubsync'])} |"
        )
    w = results[-1]
    verdict = {True: "wrote a file", False: "refused"}
    lines.append(
        f"| {w['case']} | — | {verdict[w['suba']['wrote_file']]} "
        f"| {verdict[w['ffsubsync']['wrote_file']]} |"
    )
    t = {
        n: statistics.median(r[n]["secs"] for r in results[:-1])
        for n in ("suba", "ffsubsync")
    }
    lines += [
        "",
        f"Cells: share of lines within {TOLERANCE} s of the official timing · "
        "median error. Median run time: "
        f"suba {t['suba']:.0f} s, ffsubsync {t['ffsubsync']:.0f} s.",
    ]
    (HERE / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
