import random
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402

SRT = """1
00:00:01,000 --> 00:00:02,500
<i>Hello</i> there.

2
00:01:03,250 --> 00:01:05,000
Two
lines.
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:02.500
Hello there.

01:03.250 --> 01:05.000
<v Bob>Short form.</v>
"""


def test_parse_srt(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text(SRT, encoding="utf-8")
    e = main.parse_subtitle(str(p))
    assert [x["start"] for x in e] == [1.0, 63.25]
    assert e[0]["text"] == "Hello there."
    assert e[1]["text"] == "Two\nlines."


def test_parse_vtt_with_short_timestamps(tmp_path):
    p = tmp_path / "a.vtt"
    p.write_text(VTT, encoding="utf-8")
    e = main.parse_subtitle(str(p))
    assert [(x["start"], x["end"]) for x in e] == [(1.0, 2.5), (63.25, 65.0)]
    assert e[1]["text"] == "Short form."


@pytest.mark.parametrize("s", [0.0, 0.29, 3.3, 59.9995, 3599.999, 7384.123])
def test_timestamps_round_trip(s):
    assert main.ts_to_seconds(main.seconds_to_srt_ts(s)) == pytest.approx(s, abs=6e-4)
    assert main.ts_to_seconds(main.seconds_to_vtt_ts(s)) == pytest.approx(s, abs=6e-4)


def test_timestamp_rounding_carries():
    assert main.seconds_to_srt_ts(3.3) == "00:00:03,300"
    assert main.seconds_to_srt_ts(59.9999) == "00:01:00,000"
    assert main.seconds_to_vtt_ts(-2) == "00:00:00.000"


def test_write_round_trip(tmp_path):
    entries = [
        {"start": 1.0, "end": 2.0, "text": "a"},
        {"start": 3.3, "end": 4, "text": "b"},
    ]
    for ext in ("srt", "vtt"):
        p = tmp_path / f"out.{ext}"
        main.write_subtitle(str(p), entries)
        assert main.parse_subtitle(str(p)) == [
            {"start": 1.0, "end": 2.0, "text": "a"},
            {"start": 3.3, "end": 4.0, "text": "b"},
        ]


@pytest.mark.parametrize(
    "audio,sub,expected",
    [
        ("en", "en", "text"),
        ("it", "en", "translate"),
        ("en", "it", "timing"),
        (None, "it", "text"),
    ],
)
def test_choose_strategy(audio, sub, expected):
    assert main.choose_strategy(audio, sub) == expected


@pytest.mark.parametrize(
    "name,code",
    [
        ("movie.ita.srt", "it"),
        ("movie.en.hi.srt", "en"),
        ("Movie.2016.ger.forced.srt", "de"),
        ("movie.srt", None),
    ],
)
def test_lang_from_filename(name, code):
    assert main.lang_from_filename(name) == code


def test_detect_subtitle_language():
    it = [{"text": "Non lo so, perché questo è il mio posto e non ti voglio qui."}] * 10
    en = [{"text": "I know that you are not here, but what is this all about?"}] * 10
    assert main.detect_subtitle_language(it) == "it"
    assert main.detect_subtitle_language(en) == "en"
    assert main.detect_subtitle_language([{"text": "Hi"}]) is None


def _dialogue(seed=1, length=600.0):
    rng = random.Random(seed)
    t, out = 5.0, []
    while t < length:
        d = rng.uniform(0.8, 4.0)
        out.append({"start": t, "end": t + d, "text": "x"})
        t += d + rng.expovariate(1 / 2.5)
    return out


@pytest.mark.parametrize("true_shift", [-12.35, 0.0, 3.2, 41.0])
def test_timing_finds_known_shift(true_shift):
    subs = _dialogue()
    a0, dur = 60.0, 400.0
    # Speech in the audio happens true_shift seconds after the subtitles say.
    audio = main._build_mask(
        [(e["start"] + true_shift, e["end"] + true_shift) for e in subs], a0, a0 + dur
    )
    correction, score, ratio = main.find_offset_by_timing(audio, subs, a0, dur, 60.0)
    assert correction == pytest.approx(true_shift, abs=0.1)
    assert score > 0.9
    assert ratio > 2


def test_timing_refuses_unrelated_subtitles():
    audio = main._build_mask(
        [(e["start"], e["end"]) for e in _dialogue(seed=1)], 60.0, 460.0
    )
    found = main.find_offset_by_timing(audio, _dialogue(seed=2), 60.0, 400.0, 60.0)
    assert found is None or found[2] < 1.4  # below the default --min-peak-ratio


def test_fft_path_matches_direct():
    rng = np.random.default_rng(0)
    sub = (rng.random(5000) > 0.6).astype(np.float64)
    audio = sub[700:3700].copy()
    direct = main._lag_scores(audio, sub)
    assert int(np.argmax(direct)) == 700
    # Large enough that _lag_scores switches to the FFT path.
    big_sub = (rng.random(200_000) > 0.6).astype(np.float64)
    big = main._lag_scores(big_sub[12_345:42_345].copy(), big_sub)
    assert int(np.argmax(big)) == 12_345
    assert float(big[12_345]) == pytest.approx(1.0, abs=1e-6)


def test_dialogue_region_picks_dense_part():
    sparse = [
        {"start": float(t), "end": t + 1.0, "text": ""} for t in range(0, 600, 30)
    ]
    dense = [
        {"start": 300 + i * 2.0, "end": 301 + i * 2.0, "text": ""} for i in range(30)
    ]
    entries = sorted(sparse + dense, key=lambda e: e["start"])
    start = main.find_dialogue_region(entries, 60.0)
    assert 240 <= start <= 300
