#!/usr/bin/env python3
"""suba — Detect subtitle sync offset using Whisper transcription."""

import argparse
import difflib
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import wave
from typing import Optional
from pathlib import Path

try:
    import whisper
except ImportError:
    sys.exit(
        "openai-whisper is not installed.\n"
        "Install it first:  pip install openai-whisper"
    )


def ts_to_seconds(ts: str) -> float:
    """Parse SRT/VTT timestamp (HH:MM:SS,mmm or HH:MM:SS.mmm) to float seconds."""
    h, m, rest = 0, 0, ts
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, rest = parts
    elif len(parts) == 2:
        m, rest = parts
    s, ms = (
        rest.replace(",", ".").split(".")
        if ("." in rest or "," in rest)
        else (rest, "0")
    )
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def strip_tags(text: str) -> str:
    """Remove VTT/HTML-style tags like <c>, <v>, </c>, etc."""
    return re.sub(r"<[^>]+>", "", text)


def parse_subtitle(path: str) -> list[dict]:
    """Parse SRT or VTT file into [{'start': float, 'end': float, 'text': str}]."""
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    entries = []
    # Split on blank lines
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")

        # Find the line containing '-->'
        ts_idx = None
        for i, line in enumerate(lines):
            if "-->" in line:
                ts_idx = i
                break
        if ts_idx is None:
            continue

        ts_line = lines[ts_idx]
        m = re.match(r"([\d:,.-]+?)\s*-->\s*([\d:,.-]+)", ts_line)
        if not m:
            continue

        start = ts_to_seconds(m.group(1).strip())
        end = ts_to_seconds(m.group(2).strip())
        text = strip_tags("\n".join(lines[ts_idx + 1 :])).strip()
        entries.append({"start": start, "end": end, "text": text})

    return entries


def find_dialogue_region(entries: list[dict], duration: float) -> float:
    """Pick a start time in the video where subtitle density is highest."""
    if not entries:
        return 0.0

    t_min = entries[0]["start"]
    t_max = entries[-1]["end"]

    # A window longer than the dialogue itself has only one sensible position.
    if t_max - t_min <= duration:
        return t_min

    best_start = entries[0]["start"]
    best_count = 0
    step = 5.0

    t = t_min
    while t + duration <= t_max:
        count = sum(1 for e in entries if t <= e["start"] < t + duration)
        if count > best_count:
            best_count = count
            best_start = t
        t += step

    # If absolutely nothing found (sparse subtitles), fall back to 25% into the range
    if best_count == 0:
        best_start = t_min + (t_max - t_min) * 0.25

    return best_start


def media_duration(path: str) -> Optional[float]:
    """Length of a media file in seconds, or None if ffprobe can't say."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def wav_duration(path: str) -> Optional[float]:
    """Length of the WAV we extracted, which may be shorter than we asked for."""
    try:
        with wave.open(path) as w:
            rate = w.getframerate()
            return w.getnframes() / rate if rate else None
    except (wave.Error, OSError, EOFError):  # missing, empty or truncated
        return None


def extract_audio(
    video_path: str,
    start_s: float,
    duration_s: float,
    output_path: str,
    rate: int = 16000,
) -> None:
    """Extract a mono WAV audio segment via ffmpeg.

    Whisper wants 16 kHz.  The speech mask only needs enough bandwidth for the
    voice band, so it asks for 8 kHz and halves the bytes on a feature film.
    """
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_s),
            "-t",
            str(duration_s),
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(rate),
            "-ac",
            "1",
            output_path,
        ],
        check=True,
        capture_output=True,
    )


# --------------------------------------------------------------------------- #
# Language handling
# --------------------------------------------------------------------------- #

# Tags subtitle sites append that are not languages.  'hi' is the trap: it means
# hearing-impaired here, but it is also the code for Hindi.
_NON_LANG_TAGS = {"hi", "sdh", "cc", "forced", "full", "srt", "vtt", "sub"}


def normalize_lang(name: Optional[str]) -> Optional[str]:
    """Normalize a language name or code ('Italian', 'ita', 'IT') to 'it'.

    Returns None if the value is empty or Whisper doesn't recognise it.
    """
    if not name:
        return None
    key = name.strip().lower()
    try:
        from whisper.tokenizer import LANGUAGES, TO_LANGUAGE_CODE
    except ImportError:  # pragma: no cover — whisper always ships these
        return key[:2]

    if key in LANGUAGES:  # already a code like 'it'
        return key
    if key in TO_LANGUAGE_CODE:  # full name like 'italian', or an alias
        return TO_LANGUAGE_CODE[key]
    # 3-letter codes ('ita', 'ger'): match against the English language names
    for code, full in LANGUAGES.items():
        if full.startswith(key):
            return code
    return None


def lang_from_filename(path: str) -> Optional[str]:
    """Guess a language from a subtitle filename such as 'movie.ita.srt'.

    Walks the dot/underscore-separated tags from the right so that names like
    'movie.en.hi.srt' still resolve to English rather than to the 'hi' tag.
    """
    tokens = re.split(r"[._\-\s]+", Path(path).stem)
    for tok in reversed(tokens[1:]):  # never treat the whole name as a code
        tok = tok.lower()
        if not tok.isalpha() or len(tok) > 3:
            continue
        if tok in _NON_LANG_TAGS:
            continue
        code = normalize_lang(tok)
        if code:
            return code
    return None


# Function words that give a language away.  Deliberately small: we only need
# to tell languages apart, not identify one from scratch.
_STOPWORDS = {
    "en": "the and you that this what with have not for are was your they but know just its there here all who how",
    "it": "che non di il la le per con una sono cosa questo come quando perche gli mio piu tutto niente bene qui sei siamo mi ti",
    "es": "que no de la el es lo un una los las por con para pero mas como cuando porque esta muy todo nada bien aqui eres tienes",
    "fr": "que ne pas de le la les des un une pour avec vous nous est mais comme quand tout rien bien ici moi toi cest je",
    "de": "der die das und nicht ich du sie ist ein eine mit fur auf aber was wenn haben wir sich noch nur hier alles",
    "pt": "que nao de da do os as uma para com mas como quando porque esta muito tudo nada bem aqui voce isso entao agora sao ele",
    "nl": "de het een en niet ik je is dat met voor maar wat als hebben we zijn op naar hij ook nog dit",
    "sv": "att och inte det en jag du ar som med for men vad nar har vi hon han den har bara",
    "da": "at og ikke det en jeg du er som med for men hvad har vi han hun den bare noget",
    "no": "at og ikke det en jeg du er som med for men hva har vi han hun den bare noe",
    "fi": "on ei ja se etta mina sina mutta kun niin vain han ole tama nyt me te ne",
    "pl": "nie ze to jest na do sie jak ale co tak mnie ciebie jestem tu ja bym juz",
    "cs": "se na je to ne ze co ale jsem jak tak me te jsi tady jen uz",
    "tr": "bir ve bu icin ama ne cok var yok ben sen bana seni daha sey degil",
    "ro": "sa nu ca de si este pentru cu care dar ce cand mai bine eu tu ai",
    "hu": "az es nem hogy egy ez de mit van meg csak nekem te en jo most",
}
_STOPWORDS = {k: set(v.split()) for k, v in _STOPWORDS.items()}

# Non-Latin scripts identify themselves, which is far more reliable than words.
_SCRIPTS = [
    ("ru", 0x0400, 0x04FF),
    ("el", 0x0370, 0x03FF),
    ("he", 0x0590, 0x05FF),
    ("ar", 0x0600, 0x06FF),
    ("ko", 0xAC00, 0xD7AF),
    ("ja", 0x3040, 0x30FF),
    ("zh", 0x4E00, 0x9FFF),
]


def _deaccent(text: str) -> str:
    """Strip diacritics so 'perché' matches the plain 'perche' in the tables."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def detect_subtitle_language(entries: list[dict]) -> Optional[str]:
    """Guess the subtitle language from its own text.

    This exists to stop the tool silently producing a confident, wrong offset:
    two Latin-script languages score deceptively well on character similarity,
    so without this a forgotten --sub-lang looks like a successful match.
    Returns None whenever the evidence is thin, so callers fall back to flags.
    """
    sample = " ".join(e["text"] for e in entries[:500])
    if not sample.strip():
        return None

    # Script wins outright when a decent share of the text is in one.
    letters = [c for c in sample if c.isalpha()]
    if letters:
        for code, lo, hi in _SCRIPTS:
            hits = sum(1 for c in letters if lo <= ord(c) <= hi)
            if hits / len(letters) > 0.2:
                return code

    words = re.findall(r"[^\W\d_]+", _deaccent(sample).lower())
    if len(words) < 20:  # too little text to be sure of anything
        return None

    ranked = sorted(
        ((sum(w in sw for w in words) / len(words), code)
         for code, sw in _STOPWORDS.items()),
        reverse=True,
    )
    (best, code), (runner_up, _) = ranked[0], ranked[1]
    # Demand both a real hit rate and a win over the next language.  The margin
    # is deliberately loose: mixing up close relatives (es/pt, da/no) is
    # harmless here, since either way the answer differs from the audio and we
    # fall back to timing.  Failing to detect anything is the costly outcome.
    if best < 0.04 or best < runner_up * 1.25:
        return None
    return code


def detect_audio_language(model, audio_path: str) -> Optional[str]:
    """Ask Whisper which language the extracted audio is in (first 30 s)."""
    try:
        audio = whisper.pad_or_trim(whisper.load_audio(audio_path))
        mel = whisper.log_mel_spectrogram(
            audio, getattr(model.dims, "n_mels", 80)
        ).to(model.device)
        _, probs = model.detect_language(mel)
        return max(probs, key=probs.get)
    except Exception:
        return None


def choose_strategy(audio_lang: Optional[str], sub_lang: Optional[str]) -> str:
    """Pick an alignment strategy from the two language codes.

    'text'      — same language, compare transcription to subtitle text
    'translate' — Whisper translates the audio to English, compare to English subs
    'timing'    — compare speech/silence patterns only, ignoring the words
    """
    if not audio_lang or not sub_lang or audio_lang == sub_lang:
        return "text"
    # Whisper can only translate *into* English, so that is the one cross-language
    # pair where we still get comparable text.
    if sub_lang == "en":
        return "translate"
    return "timing"


# --------------------------------------------------------------------------- #
# Text matching
# --------------------------------------------------------------------------- #


def text_similarity(a: str, b: str) -> float:
    """Calculate similarity ratio (0–1) between two strings, ignoring punctuation."""

    def clean(s: str) -> str:
        return re.sub(r"[^\w\s]", "", s.lower())

    a, b = clean(a), clean(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def token_similarity(a: str, b: str) -> float:
    """Word-level similarity, for comparing a translation against a subtitle.

    A machine translation rarely reproduces a subtitler's exact phrasing, so
    character-level matching is too strict.  This scores shared vocabulary
    (Dice coefficient) blended with word-order agreement.
    """

    def words(s: str) -> list[str]:
        return re.findall(r"\w+", s.lower())

    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0

    sa, sb = set(wa), set(wb)
    dice = 2 * len(sa & sb) / (len(sa) + len(sb))
    order = difflib.SequenceMatcher(None, wa, wb).ratio()
    return dice * 0.6 + order * 0.4


def compress_ws(s: str) -> str:
    """Collapse whitespace."""
    return re.sub(r"\s+", " ", s).strip()


def find_best_match(
    transcription: dict,
    entries: list[dict],
    seg_start: float,
    seg_dur: float,
    sim_fn=text_similarity,
) -> Optional[tuple[dict, dict, float]]:
    """Return the (subtitle_entry, whisper_segment, similarity_score) or None.

    Only subtitle entries whose *start* falls within the extracted audio segment
    are candidates.  The algorithm tries matching against 1-, 2-, and 3-segment
    windows of the Whisper output and weights results by temporal proximity.
    """
    seg_end = seg_start + seg_dur
    candidates = [e for e in entries if seg_start <= e["start"] < seg_end]
    if not candidates:
        return None

    segments = transcription.get("segments", [])
    if not segments:
        return None

    best = None  # (sub_entry, whisper_seg, text_similarity)
    best_score = 0.0

    for sub in candidates:
        sub_text = compress_ws(sub["text"])
        if not sub_text:
            continue
        expected_pos = (
            sub["start"] - seg_start
        )  # where in the audio the line should appear

        for i in range(len(segments)):
            for win in (1, 2, 3):
                if i + win > len(segments):
                    break
                combo = " ".join(s["text"].strip() for s in segments[i : i + win])
                combo = compress_ws(combo)
                if not combo:
                    continue

                text_score = sim_fn(sub_text, combo)
                if text_score == 0:
                    continue

                # Temporal weight: closer whisper position → higher weight
                wseg_pos = segments[i]["start"]
                time_diff = abs(wseg_pos - expected_pos)
                max_diff = seg_dur * 0.5
                temporal_weight = max(0.0, 1.0 - time_diff / max_diff)

                combined = text_score * 0.7 + temporal_weight * 0.3
                if combined > best_score:
                    best_score = combined
                    best = (sub, segments[i], text_score)

    return best


# --------------------------------------------------------------------------- #
# Timing matching (language-agnostic)
# --------------------------------------------------------------------------- #

MASK_RESOLUTION = 0.05  # seconds per frame in the speech masks
VAD_SAMPLE_RATE = 8000  # plenty for the speech band, half the bytes


def _build_mask(intervals, t0: float, t1: float, res: float = MASK_RESOLUTION):
    """Rasterize (start, end) pairs into a binary 'is someone talking' array."""
    import numpy as np

    n = max(1, int(round((t1 - t0) / res)))
    mask = np.zeros(n, dtype=np.float32)
    for start, end in intervals:
        a = int(round((start - t0) / res))
        b = int(round((end - t0) / res))
        a, b = max(0, a), min(n, b)
        if b > a:
            mask[a:b] = 1.0
    return mask


SPEECH_LO, SPEECH_HI = 300.0, 3400.0  # the band human speech lives in

# Fraction of frames the mask calls speech.  Measured across two very different
# films, anything from 0.3 to 0.5 behaves the same, so this is not delicate.
SPEECH_FRACTION = 0.40

# Rival lags nearer than this to the winner are the shoulders of the same peak,
# not competing alignments, so they don't count when judging confidence.
PEAK_GUARD = 15.0


def _speech_envelope(path: str, res: float):
    """Speech-band energy per frame, in dB. Read in chunks; None if unusable."""
    import numpy as np

    try:
        w = wave.open(path)
    except (wave.Error, OSError, EOFError, ValueError):
        return None
    with w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            return None
        rate, total = w.getframerate(), w.getnframes()
        if not rate or total <= 0:
            return None
        size = max(1, int(round(res * rate)))
        count = total // size
        if count < 40:
            return None

        freq = np.fft.rfftfreq(size, 1.0 / rate)
        band = (freq >= SPEECH_LO) & (freq <= min(SPEECH_HI, rate / 2.0 - 1.0))
        if not band.any():
            return None
        window = np.hanning(size)

        db = np.empty(count, dtype=np.float32)
        filled = 0
        while filled < count:
            want = min(20000, count - filled)
            raw = np.frombuffer(w.readframes(want * size), dtype=np.int16)
            got = len(raw) // size
            if got == 0:
                break
            block = raw[: got * size].astype(np.float32).reshape(got, size) * window
            power = np.abs(np.fft.rfft(block, axis=1)) ** 2
            db[filled : filled + got] = 10 * np.log10(power[:, band].sum(axis=1) + 1e-6)
            filled += got
    return db[:filled] if filled >= 40 else None


def _threshold_mask(feature, fraction: float = SPEECH_FRACTION):
    """Take the top `fraction` of frames by some speech-likeness score."""
    import numpy as np

    mask = (feature > np.quantile(feature, 1.0 - fraction)).astype(np.float64)
    return mask if 0.05 < mask.mean() < 0.95 else None


def _modulation(db, res: float, window_s: float = 0.5):
    """How much the speech-band energy wobbles around its own local average.

    Speech pulses at the syllable rate; a sustained orchestral bed does not.
    """
    import numpy as np

    span = max(2, int(round(window_s / res)))
    n = len(db)
    idx = np.arange(n)
    lo = np.maximum(0, idx - span // 2)
    hi = np.minimum(n, idx + span // 2)
    width = (hi - lo).astype(np.float64)

    csum = np.concatenate(([0.0], np.cumsum(db, dtype=np.float64)))
    swing = db - (csum[hi] - csum[lo]) / width
    csum2 = np.concatenate(([0.0], np.cumsum(swing.astype(np.float64) ** 2)))
    return np.sqrt((csum2[hi] - csum2[lo]) / width)


def speech_mask_candidates(
    path: str, res: float = MASK_RESOLUTION
) -> list[tuple[str, object]]:
    """Two ways of calling a frame speech, because neither wins everywhere.

    *Loudness* is near-perfect on clean audio with real silence between lines,
    and useless on a scored action film: there the loudest frames are music and
    effects, so the mask tracks the score and *anti*-correlates with dialogue —
    measured on one film, every candidate offset scored negative.

    *Modulation* finds dialogue under music by looking for syllable-rate wobble
    rather than level, but smoothing costs it about a second of precision.

    On clean audio loudness scores 0.996 against modulation's 0.69; on the
    action film modulation scores 0.52 against loudness's -0.09.  So return
    both and let the correlation decide, the same way frame rates are chosen.
    """
    db = _speech_envelope(path, res)
    if db is None:
        return []
    import numpy as np

    if float(np.percentile(db, 95) - np.percentile(db, 5)) < 3.0:
        return []  # no contrast: silence, or a solid wall of noise

    out = []
    for label, feature in (
        ("loudness", db),
        ("speech modulation", _modulation(db, res)),
    ):
        mask = _threshold_mask(feature)
        if mask is not None:
            out.append((label, mask))
    return out


def _lag_scores(audio_mask, sub_mask):
    """Pearson correlation of the audio mask against every lag of the subtitles.

    A whole film is ~280k frames, where a direct O(n*m) correlation would take
    minutes; via FFT it is a tenth of a second.  Normalizing per lag (rather
    than scoring raw overlap) is what makes matching silence count as evidence.
    """
    import numpy as np

    n, total = len(audio_mask), len(sub_mask)
    lags = total - n + 1
    if lags <= 0:
        return None

    a_mean = float(audio_mask.mean())
    a_std = float(audio_mask.std())
    if a_std == 0:  # all speech or all silence — nothing to correlate
        return None

    if n * lags <= 4_000_000:
        dot = np.correlate(sub_mask, audio_mask, mode="valid")
    else:
        size = 1 << int(np.ceil(np.log2(n + total)))
        spectrum = np.fft.rfft(audio_mask[::-1], size) * np.fft.rfft(sub_mask, size)
        dot = np.fft.irfft(spectrum, size)[n - 1 : n - 1 + lags]

    csum = np.concatenate(([0.0], np.cumsum(sub_mask)))
    s_mean = (csum[n : n + lags] - csum[:lags]) / n
    # The mask is binary, so sum(x**2) == sum(x) and the variance simplifies.
    s_std = np.sqrt(np.maximum(s_mean - s_mean**2, 0.0))

    denom = a_std * s_std
    return np.where(
        denom > 1e-9, (dot / n - a_mean * s_mean) / np.maximum(denom, 1e-9), 0.0
    )


def find_offset_by_timing(
    audio_mask,
    entries: list[dict],
    seg_start: float,
    seg_dur: float,
    max_shift: float,
) -> Optional[tuple[float, float, float]]:
    """Align on speech rhythm alone. Returns (correction, score, peak_ratio).

    Words are useless across languages, but *when* people speak is the same in
    every dub and translation.  We rasterize the Whisper segments and the
    subtitle entries into binary speech masks and slide the subtitle mask over
    the audio mask.

    Scoring is a Pearson correlation rather than a plain overlap count: dense
    dialogue overlaps well at almost any lag, so we have to reward matching
    *silence* too and penalize speech landing on silence.  That turns a broad
    mushy hill into a sharp peak.

    `correction` is the number of seconds to add to every subtitle timestamp.
    `peak_ratio` compares the winning lag to the best rival lag elsewhere; it is
    what separates a real alignment from a coincidence.
    """
    import numpy as np

    if audio_mask is None or not len(audio_mask) or not entries:
        return None

    a0, a1 = seg_start, seg_start + seg_dur
    audio_mask = np.asarray(audio_mask, dtype=np.float64)

    # Subtitle mask spans the audio window plus the search margin on both sides.
    sub_mask = _build_mask(
        [(e["start"], e["end"]) for e in entries],
        a0 - max_shift,
        a1 + max_shift,
    ).astype(np.float64)
    if len(sub_mask) < len(audio_mask):
        return None

    scores = _lag_scores(audio_mask, sub_mask)
    if scores is None or not len(scores):
        return None

    best_j = int(np.argmax(scores))
    best_score = float(scores[best_j])
    if best_score <= 0:
        return None

    # Lags close to the winner belong to the same peak, so skip them.
    guard = int(round(PEAK_GUARD / MASK_RESOLUTION))
    rivals = np.concatenate(
        (scores[: max(0, best_j - guard)], scores[best_j + guard + 1 :])
    )
    runner_up = float(rivals.max()) if rivals.size else 0.0
    peak_ratio = best_score / runner_up if runner_up > 0 else float("inf")

    correction = max_shift - best_j * MASK_RESOLUTION
    return correction, best_score, peak_ratio


# Subtitles ripped from a PAL disc run 4% fast against a 23.976 fps video, and
# no single shift can fix that — the error grows through the film.  Trying a
# handful of standard conversions costs one extra correlation each.
FRAME_RATE_SCALES = (
    ("native", 1.0),
    ("25 → 23.976 fps (PAL)", 25.0 / 23.976),
    ("23.976 → 25 fps", 23.976 / 25.0),
    ("25 → 24 fps", 25.0 / 24.0),
    ("24 → 25 fps", 24.0 / 25.0),
)


def timing_alignment(
    video_path: str,
    entries: list[dict],
    max_shift: float,
    try_scales: bool = True,
    verbose: bool = True,
) -> Optional[tuple[float, float, float, float, str]]:
    """Align on speech rhythm across the whole film.

    Returns (diff, score, peak_ratio, scale, scale_label); a subtitle timestamp
    maps to video time as `t * scale - diff`.

    A five-minute sample is enough when the audio is clean, but a scored feature
    film needs the whole thing: the pauses that carry the signal are rarer and
    noisier, and a short window reads as noise.  Reading the waveform is cheap
    and needs no transcription, so there is no reason to look at less.

    The range is clipped to where the subtitles actually are, which keeps a
    partial track (one disc of a two-disc rip, say) aligning on its own region
    instead of being diluted by an hour of film it never covers.
    """
    total = media_duration(video_path)
    lo = max(0.0, entries[0]["start"] - max_shift - 30.0)
    hi = entries[-1]["end"] + max_shift + 30.0
    if total:
        hi = min(hi, total)
    span = hi - lo
    if span < 30.0:
        return None

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = tmp.name
    try:
        if verbose:
            print(f"Reading speech from {fmt_time(lo)} to {fmt_time(hi)} …")
        extract_audio(video_path, lo, span, path, rate=VAD_SAMPLE_RATE)
        candidates = speech_mask_candidates(path)
        if not candidates:
            return None

        scales = FRAME_RATE_SCALES if try_scales else FRAME_RATE_SCALES[:1]
        scaled_cache = {}
        best = None
        for mask_label, mask in candidates:
            seg_dur = len(mask) * MASK_RESOLUTION
            for label, scale in scales:
                if scale not in scaled_cache:
                    scaled_cache[scale] = (
                        entries
                        if scale == 1.0
                        else [
                            {"start": e["start"] * scale, "end": e["end"] * scale}
                            for e in entries
                        ]
                    )
                found = find_offset_by_timing(
                    mask, scaled_cache[scale], lo, seg_dur, max_shift
                )
                if found is None:
                    continue
                correction, score, ratio = found
                if best is None or score > best[1]:
                    best = (-correction, score, ratio, scale, label, mask_label, mask)

        if best is None:
            return None
        if verbose:
            print(
                f"  matched on {best[5]} "
                f"({100 * best[6].mean():.0f}% of frames look like speech)"
            )
            if best[3] != 1.0:
                print(f"  frame-rate conversion detected: {best[4]}")
        return best[:5]
    finally:
        if os.path.exists(path):
            os.unlink(path)


def refine_offset(
    audio_path: str,
    entries: list[dict],
    diff: float,
    seg_start: float,
    seg_dur: float,
    window: float = 6.0,
) -> Optional[tuple[float, float]]:
    """Sharpen a text-derived offset against the waveform.

    Matching text tells us *which* subtitle line the audio is playing, but only
    to the precision of a Whisper segment start — and those can span ten seconds
    or more, especially on the translate task.  Shifting the subtitles by the
    text estimate should land them nearly on top of the speech, so a short
    correlation search around zero recovers the remaining error at frame
    resolution.  Returns (new_diff, score), or None if it isn't confident.
    """
    candidates = speech_mask_candidates(audio_path)
    if not candidates:
        return None

    correction = -diff
    shifted = [
        {"start": e["start"] + correction, "end": e["end"] + correction}
        for e in entries
    ]
    found = None
    for _, mask in candidates:
        got = find_offset_by_timing(
            mask,
            shifted,
            seg_start,
            min(seg_dur, len(mask) * MASK_RESOLUTION),
            window,
        )
        if got is not None and (found is None or got[1] > found[1]):
            found = got
    if found is None:
        return None

    delta, score, _ = found
    # A weak correlation, or a peak pinned to the edge of the search window,
    # means the waveform disagrees with the text match. Keep the text answer.
    if score < 0.25 or abs(delta) > window * 0.9:
        return None
    return -(correction + delta), score


def seconds_to_srt_ts(s: float) -> str:
    """Convert float seconds to SRT timestamp HH:MM:SS,mmm."""
    s = max(0, s)
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    ms = int((s - int(s)) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def seconds_to_vtt_ts(s: float) -> str:
    """Convert float seconds to VTT timestamp HH:MM:SS.mmm."""
    s = max(0, s)
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    ms = int((s - int(s)) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


def write_subtitle(path: str, entries: list[dict]) -> None:
    """Write shifted entries to SRT or VTT, auto-detected from extension."""
    ext = Path(path).suffix.lower()
    with open(path, "w", encoding="utf-8") as f:
        if ext == ".vtt":
            f.write("WEBVTT\n\n")
            ts_fn = seconds_to_vtt_ts
        else:
            ts_fn = seconds_to_srt_ts

        for i, e in enumerate(entries, 1):
            if ext == ".srt":
                f.write(f"{i}\n")
            f.write(f"{ts_fn(e['start'])} --> {ts_fn(e['end'])}\n")
            f.write(f"{e['text']}\n\n")


def fmt_time(s: float) -> str:
    """Format seconds as HH:MM:SS.mmm (with optional leading minus)."""
    sign = "-" if s < 0 else ""
    a = abs(s)
    h, r = divmod(int(a), 3600)
    m, sec = divmod(r, 60)
    ms = int((a - int(a)) * 1000)
    return f"{sign}{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


# With the modulation mask the correlation itself separates cleanly: across two
# films, correct alignments scored 0.41-0.58 and wrong ones -0.02 to 0.13.
TIMING_THRESHOLD = 0.25

TIMING_HINT = (
    "The audio and the subtitles don't line up anywhere. Usually that means "
    "they aren't the same cut of the film — a different release, or one disc "
    "of a two-disc rip."
)


def announce_offset(diff: float) -> None:
    """Print the headline offset the same way for every strategy."""
    print()
    print(f"  SYNC OFFSET: {diff:+.3f} s   ({int(diff * 1000):+d} ms)")
    if diff > 0:
        print(f"  → Subtitles are {diff:.3f}s BEHIND audio")
    elif diff < 0:
        print(f"  → Subtitles are {abs(diff):.3f}s AHEAD of audio")
    else:
        print("  → Subtitles are perfectly synced")
    print("=" * 58)


def write_output(
    args, entries: list[dict], diff: float, scale: float = 1.0
) -> None:
    """Map every timestamp onto video time and write the file.

    A subtitle time `t` becomes `t * scale - diff`.  `scale` is 1.0 unless a
    frame-rate conversion was detected, in which case stretching has to happen
    before the shift or the two corrections fight each other.
    """
    if not args.output:
        return
    correction = -diff
    shifted = [
        {
            "start": e["start"] * scale + correction,
            "end": e["end"] * scale + correction,
            "text": e["text"],
        }
        for e in entries
    ]
    write_subtitle(args.output, shifted)
    print(f"\nFixed subtitles written to: {args.output}")


def report_timing(args, entries: list[dict], found) -> None:
    """Print a timing result and write the output, or explain the refusal."""
    if found is None:
        print(
            f"\nCouldn't find an alignment. {TIMING_HINT}", file=sys.stderr
        )
        sys.exit(1)

    diff, score, peak_ratio, scale, scale_label = found
    threshold = args.threshold if args.threshold is not None else TIMING_THRESHOLD

    if score < threshold:
        print(
            f"\nSpeech correlation ({score:.3f}) is below the threshold "
            f"({threshold}). {TIMING_HINT}",
            file=sys.stderr,
        )
        sys.exit(1)
    # A correct alignment stands clear of every other lag.  A peak that barely
    # beats its rivals is usually a coincidence, and a confidently wrong offset
    # is worse than no answer at all.
    if peak_ratio < args.min_peak_ratio:
        print(
            f"\nAmbiguous alignment: the best offset beats the runner-up by only "
            f"{peak_ratio:.2f}x (need {args.min_peak_ratio}). {TIMING_HINT}",
            file=sys.stderr,
        )
        sys.exit(1)

    print()
    print("=" * 58)
    print(f"  Speech correlation:  {score:.3f}")
    print(f"  Peak / runner-up:    {peak_ratio:.2f}x")
    print(f"  Search window:       +/-{args.max_shift:g} s")
    if scale != 1.0:
        print(f"  Frame rate:          {scale_label}  (x{scale:.6f})")
    announce_offset(diff)
    if scale != 1.0:
        print(
            "  Note: these subtitles also run at the wrong speed. The output is "
            "rescaled as well as shifted; the original could not have been "
            "fixed by any single offset."
        )
    write_output(args, entries, diff, scale)


def main():
    ap = argparse.ArgumentParser(
        description="subsync — detect subtitle sync offset using Whisper"
    )
    ap.add_argument("subtitle", help="Path to SRT or VTT subtitle file")
    ap.add_argument("video", help="Path to video file")
    ap.add_argument(
        "--duration",
        "-d",
        type=float,
        default=None,
        help="Length of audio to extract for text/translate matching [default: 60 s]",
    )
    ap.add_argument(
        "--model", "-m", default="base", help="Whisper model to use [default: base]"
    )
    ap.add_argument(
        "--start",
        "-t",
        type=float,
        default=None,
        help="Manual start time for text/translate matching (auto-detected if omitted)",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Minimum match score to accept [default: 0.2 text, 0.12 translate]",
    )
    ap.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write fixed subtitles to this file (.srt or .vtt)",
    )
    ap.add_argument(
        "--audio-lang",
        "-A",
        default=None,
        help="Language spoken in the video, e.g. 'en' or 'english' (auto-detected if omitted)",
    )
    ap.add_argument(
        "--sub-lang",
        "-S",
        default=None,
        help="Language of the subtitle file, e.g. 'it' (guessed from the filename if omitted)",
    )
    ap.add_argument(
        "--align",
        choices=("auto", "text", "translate", "timing"),
        default="auto",
        help="Alignment strategy [default: auto, chosen from the languages]",
    )
    ap.add_argument(
        "--max-shift",
        type=float,
        default=60.0,
        help="Largest offset to search for [default: 60 s]",
    )
    ap.add_argument(
        "--no-refine",
        action="store_true",
        help="Skip refining a text match against the waveform",
    )
    ap.add_argument(
        "--min-coverage",
        type=float,
        default=0.7,
        help="Reject subtitles reaching less than this fraction of the runtime "
        "[default: 0.7; use 0 to allow any]",
    )
    ap.add_argument(
        "--no-rescale",
        action="store_true",
        help="Don't test for PAL/NTSC frame-rate conversions, only a shift",
    )
    ap.add_argument(
        "--min-peak-ratio",
        type=float,
        default=1.4,
        help="How far the best timing offset must beat the runner-up [default: 1.4]",
    )
    args = ap.parse_args()

    # 1. Parse subtitles
    entries = parse_subtitle(args.subtitle)
    if not entries:
        print("error: no subtitle entries found", file=sys.stderr)
        sys.exit(1)
    print(f"Parsed {len(entries)} subtitle entries")

    # A track that stops a third of the way in is one disc of a multi-disc rip,
    # not a subtitle file for this film.  Aligning it would succeed on the part
    # it has and leave the rest of the movie silent, so refuse it up front,
    # before spending anything on audio.
    runtime = media_duration(args.video)
    if runtime and args.min_coverage > 0:
        covered = entries[-1]["end"] / runtime
        if covered < args.min_coverage:
            print(
                f"error: these subtitles run out at {fmt_time(entries[-1]['end'])}, "
                f"only {covered:.0%} of the {fmt_time(runtime)} film.\n"
                f"       That is usually one disc of a multi-disc rip. Syncing "
                f"cannot fill in the missing part —\n"
                f"       find a complete file, or pass --min-coverage 0 to align "
                f"the part that is there anyway.",
                file=sys.stderr,
            )
            sys.exit(1)
        if covered < 0.9:
            print(f"  (subtitles reach {covered:.0%} of the runtime)")

    # 2. Resolve languages as far as we can before touching the audio
    audio_lang = normalize_lang(args.audio_lang)
    if args.audio_lang and not audio_lang:
        print(f"error: unknown audio language {args.audio_lang!r}", file=sys.stderr)
        sys.exit(1)

    sub_lang = normalize_lang(args.sub_lang)
    if args.sub_lang and not sub_lang:
        print(f"error: unknown subtitle language {args.sub_lang!r}", file=sys.stderr)
        sys.exit(1)
    if sub_lang is None:
        sub_lang = lang_from_filename(args.subtitle)
        if sub_lang:
            print(f"Guessed subtitle language from filename: {sub_lang}")
    if sub_lang is None:
        sub_lang = detect_subtitle_language(entries)
        if sub_lang:
            print(f"Detected subtitle language from text: {sub_lang}")

    # Whisper needs a task before it transcribes, so if the subtitles are in a
    # known language and the audio is not, we have to sniff the audio first.
    need_probe = args.align == "auto" and audio_lang is None and sub_lang is not None

    duration = args.duration
    if duration is None:
        # Timing alignment correlates speech patterns, and a 60 s window rarely
        # carries enough of them to beat a coincidental match.
        provisional = (
            args.align if args.align != "auto"
            else choose_strategy(audio_lang, sub_lang)
        )
        duration = 300.0 if (provisional == "timing" or need_probe) else 60.0

    # 3. Timing alignment reads the whole film off the waveform, so it needs
    # neither a sample window nor Whisper.  Settle it here and skip both.
    if args.align == "timing":
        early = "timing"
    elif args.align == "auto" and audio_lang and sub_lang:
        early = choose_strategy(audio_lang, sub_lang)
    else:
        early = None

    if early == "timing":
        print(
            f"Languages: audio={audio_lang or 'n/a'} "
            f"subtitles={sub_lang or 'n/a'}  →  timing alignment"
        )
        print("  (comparing speech rhythm, so no transcription is needed)")
        found = timing_alignment(
            args.video, entries, args.max_shift, try_scales=not args.no_rescale
        )
        report_timing(args, entries, found)
        return

    # Asking for more audio than the file holds leaves
    # a tail of phantom silence that drags the timing correlation flat, so clamp
    # the request to what actually exists before picking where to listen.
    total = media_duration(args.video)
    if total and duration > total:
        duration = max(30.0, total - (args.start or 0.0))
        print(f"Video is only {fmt_time(total)}; using {duration:.0f} s of audio")

    start_s = (
        args.start
        if args.start is not None
        else find_dialogue_region(entries, duration)
    )
    if total and start_s + duration > total:
        start_s = max(0.0, total - duration)
    print(f"Extracting audio @ {fmt_time(start_s)} for {duration} s")

    # 4. Extract audio
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name

    try:
        extract_audio(args.video, start_s, duration, audio_path)

        # ffmpeg gives us whatever was there, which can still be less than asked
        # (a truncated file, a stream that ends early).  Match on what we got.
        actual = wav_duration(audio_path)
        if actual and actual < duration - 0.5:
            print(f"Got {actual:.0f} s of audio (asked for {duration:.0f} s)")
            duration = actual

        # Anything still here needs Whisper: timing alignment already returned.
        segments: list[dict] = []
        result: dict = {}

        print(f"Loading Whisper model ({args.model}) …")
        model = whisper.load_model(args.model)

        if need_probe:
            audio_lang = detect_audio_language(model, audio_path)
            if audio_lang:
                print(f"Detected audio language: {audio_lang}")

        strategy = (
            choose_strategy(audio_lang, sub_lang)
            if args.align == "auto"
            else args.align
        )
        print(
            f"Languages: audio={audio_lang or 'auto'} "
            f"subtitles={sub_lang or 'unknown'}  →  {strategy} alignment"
        )

        # The probe may have just revealed a cross-language pair.  Timing reads
        # the waveform, so there is nothing left to transcribe.
        if strategy == "timing":
            print("  (comparing speech rhythm, so no transcription is needed)")
            report_timing(
                args,
                entries,
                timing_alignment(
                    args.video,
                    entries,
                    args.max_shift,
                    try_scales=not args.no_rescale,
                ),
            )
            return

        task = "translate" if strategy == "translate" else "transcribe"
        print(f"Transcribing with Whisper (task={task}) …")
        result = model.transcribe(
            audio_path, verbose=False, task=task, language=audio_lang
        )
        detected = result.get("language")
        if audio_lang is None and detected:
            audio_lang = detected
            print(f"Detected audio language: {audio_lang}")

        segments = result.get("segments", [])
        print(f"Got {len(segments)} whisper segments\n")
        for seg in segments:
            print(
                f"  [{seg['start']:7.2f}s – {seg['end']:7.2f}s]  "
                f"{seg['text'].strip()}"
            )

        if not segments:
            print("error: whisper produced no segments", file=sys.stderr)
            sys.exit(1)

        # Transcribing pins down the audio language, sometimes for the first
        # time.  Comparing text across two languages yields a plausible but
        # wrong offset, so re-decide rather than report nonsense.
        if args.align == "auto" and strategy != "timing" and audio_lang and sub_lang:
            revised = choose_strategy(audio_lang, sub_lang)
            if revised != strategy:
                print(
                    f"\nAudio is {audio_lang} but the subtitles are {sub_lang}"
                    f" — switching to {revised} alignment."
                )
                strategy = revised
                if strategy == "timing":
                    found = timing_alignment(
                        args.video, entries, args.max_shift
                    )
                    report_timing(args, entries, found)
                    return
                if strategy == "translate":
                    # Only this path needs Whisper again, to get English out.
                    print("Re-running Whisper (task=translate) …")
                    result = model.transcribe(
                        audio_path,
                        verbose=False,
                        task="translate",
                        language=audio_lang,
                    )
                    segments = result.get("segments", [])
                    if not segments:
                        print(
                            "error: whisper produced no segments",
                            file=sys.stderr,
                        )
                        sys.exit(1)

        threshold = args.threshold
        if threshold is None:
            threshold = {"text": 0.2, "translate": 0.12, "timing": 0.15}[strategy]

        # 6. Find the offset
        sim_fn = token_similarity if strategy == "translate" else text_similarity
        match = find_best_match(result, entries, start_s, duration, sim_fn)

        if match is None:
            print(
                "\nNo subtitle entries found within the transcribed region.",
                file=sys.stderr,
            )
            sys.exit(1)

        sub_entry, wseg, sim = match

        if sim < threshold:
            print(
                f"\nBest similarity ({sim:.3f}) below threshold ({threshold}).",
                file=sys.stderr,
            )
            print(f"  Sub: [{fmt_time(sub_entry['start'])}]  {sub_entry['text']}")
            print(f"  Wsp: [{wseg['start']:.2f}s]  {wseg['text'].strip()}")
            if strategy == "text" and sub_lang != audio_lang:
                print(
                    "  Different languages? Pass --audio-lang and --sub-lang.",
                    file=sys.stderr,
                )
            sys.exit(1)

        video_time_of_whisper = start_s + wseg["start"]
        diff = sub_entry["start"] - video_time_of_whisper

        refined = (
            None if args.no_refine
            else refine_offset(audio_path, entries, diff, start_s, duration)
        )

        print()
        print("=" * 58)
        print(f"  Match similarity:  {sim:.3f}")
        print(
            f"  Subtitle line:     [{fmt_time(sub_entry['start'])} → {fmt_time(sub_entry['end'])}]"
        )
        print(f'                     "{sub_entry["text"]}"')
        print(
            f"  Whisper segment:   [{wseg['start']:.3f}s → {wseg['end']:.3f}s in extracted audio]"
        )
        print(f'                     "{wseg["text"].strip()}"')
        print(f"  Whisper in video:  {fmt_time(video_time_of_whisper)}")
        if refined is not None:
            new_diff, rscore = refined
            print(
                f"  Refined on waveform: {diff:+.3f} s → {new_diff:+.3f} s "
                f"(correlation {rscore:.3f})"
            )
            diff = new_diff

        announce_offset(diff)
        write_output(args, entries, diff)

    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


if __name__ == "__main__":
    main()
