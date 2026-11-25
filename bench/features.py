#!/usr/bin/env python3
"""Benchmark suba against ffsubsync on two full-length feature films.

- Charade (1963), public domain, with volunteer subtitle tracks from
  Wikimedia Commons, timed to that exact file.  A scored studio thriller.
- Sita Sings the Blues (2008, CC-BY-SA), with the community Portuguese
  translation.  An animated musical: much of its dialogue is 1920s jazz
  recordings.

Each track is broken in a known way and both tools try to repair it.  Both
tools put the untouched tracks within 0.3 s of their stated timing, so those
are used as ground truth (see README).

    bench/fetch_features.sh
    python bench/features.py          # writes bench/features.md
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as B  # noqa: E402  (score, tools, run, PAL, suba)

suba = B.suba
MEDIA = B.MEDIA
WORK = MEDIA / "work-features"

CHARADE = MEDIA / "charade" / "charade.webm"
SITA = MEDIA / "sita" / "sita.mp4"

# (label, video, subtitle file, language tag, shift, scale)
CASES = []
for lang in ("en", "es", "fr"):
    src = MEDIA / "charade" / f"charade.{lang}.srt"
    CASES += [
        (f"Charade, {lang} +7.3 s", CHARADE, src, lang, 7.3, 1.0),
        (f"Charade, {lang} PAL drift", CHARADE, src, lang, 1.0, B.PAL),
        (f"Charade, {lang} +95 s", CHARADE, src, lang, 95.0, 1.0),
        (f"Charade, {lang} +150 s", CHARADE, src, lang, 150.0, 1.0),
    ]
sita = MEDIA / "sita" / "sita.pt.srt"
CASES += [
    ("Sita, pt +7.3 s", SITA, sita, "pt", 7.3, 1.0),
    ("Sita, pt PAL drift", SITA, sita, "pt", 1.0, B.PAL),
    ("Sita, pt +95 s", SITA, sita, "pt", 95.0, 1.0),
]

# Real subtitles from a different feature: the right answer is no file.
NOTLD = MEDIA / "subs" / "notld.en.srt"
MISMATCH = [
    ("Night of the Living Dead's subtitles on Charade, en", CHARADE, NOTLD, "en"),
    ("Night of the Living Dead's subtitles on Charade, as es", CHARADE, NOTLD, "es"),
]


def attempt(src, prefix, video):
    out = {}
    for name, cmd in B.tools(src, prefix, video).items():
        dst = Path(cmd[cmd.index("-o") + 1])
        dst.unlink(missing_ok=True)
        code, secs, log = B.run(cmd)
        (WORK / f"{dst.name}.log").write_text(log)
        out[name] = (dst, code, secs)
    return out


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    results = []
    for label, video, path, tag, shift, scale in CASES:
        ref = suba.parse_subtitle(str(path))
        bad = [
            {**e, "start": e["start"] * scale + shift, "end": e["end"] * scale + shift}
            for e in ref
        ]
        src = WORK / f"{label.replace(' ', '_').replace(',', '')}.{tag}.srt"
        suba.write_subtitle(str(src), bad)
        row = {"case": label, "before": B.score(ref, src)}
        for name, (dst, code, secs) in attempt(src, WORK / src.stem, video).items():
            row[name] = {"score": B.score(ref, dst), "secs": secs, "code": code}
        results.append(row)
        print(json.dumps(row), flush=True)

    for label, video, path, tag in MISMATCH:
        src = WORK / f"mismatch.{tag}.srt"
        src.write_bytes(path.read_bytes())
        row = {"case": label, "kind": "mismatch"}
        for name, (dst, code, secs) in attempt(src, WORK / src.stem, video).items():
            row[name] = {"wrote_file": dst.exists(), "secs": secs, "code": code}
        results.append(row)
        print(json.dumps(row), flush=True)

    (HERE / "features.json").write_text(json.dumps(results, indent=2))
    B.write_markdown(results, HERE / "features.md")


if __name__ == "__main__":
    main()
