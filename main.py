#!/usr/bin/env python3
"""suba — Detect subtitle sync offset using Whisper transcription."""

import argparse
import difflib
import os
import re
import subprocess
import sys
import tempfile
from typing import Optional

import whisper


def ts_to_seconds(ts: str) -> float:
    """Parse SRT/VTT timestamp (HH:MM:SS,mmm or HH:MM:SS.mmm) to float seconds."""
    h, m, rest = 0, 0, ts
    parts = ts.split(':')
    if len(parts) == 3:
        h, m, rest = parts
    elif len(parts) == 2:
        m, rest = parts
    s, ms = rest.replace(',', '.').split('.') if ('.' in rest or ',' in rest) else (rest, '0')
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, '0')) / 1000


def strip_tags(text: str) -> str:
    """Remove VTT/HTML-style tags like <c>, <v>, </c>, etc."""
    return re.sub(r'<[^>]+>', '', text)


def parse_subtitle(path: str) -> list[dict]:
    """Parse SRT or VTT file into [{'start': float, 'end': float, 'text': str}]."""
    with open(path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    entries = []
    # Split on blank lines
    blocks = re.split(r'\n\s*\n', content.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split('\n')

        # Find the line containing '-->'
        ts_idx = None
        for i, line in enumerate(lines):
            if '-->' in line:
                ts_idx = i
                break
        if ts_idx is None:
            continue

        ts_line = lines[ts_idx]
        m = re.match(r'([\d:,.-]+?)\s*-->\s*([\d:,.-]+)', ts_line)
        if not m:
            continue

        start = ts_to_seconds(m.group(1).strip())
        end = ts_to_seconds(m.group(2).strip())
        text = strip_tags('\n'.join(lines[ts_idx + 1:])).strip()
        entries.append({'start': start, 'end': end, 'text': text})

    return entries


def find_dialogue_region(entries: list[dict], duration: float) -> float:
    """Pick a start time in the video where subtitle density is highest."""
    if not entries:
        return 0.0

    t_min = entries[0]['start']
    t_max = entries[-1]['end']

    best_start = entries[0]['start']
    best_count = 0
    step = 5.0

    t = t_min
    while t + duration <= t_max:
        count = sum(1 for e in entries if t <= e['start'] < t + duration)
        if count > best_count:
            best_count = count
            best_start = t
        t += step

    # If absolutely nothing found (sparse subtitles), fall back to 25% into the range
    if best_count == 0:
        best_start = t_min + (t_max - t_min) * 0.25

    return best_start


def extract_audio(video_path: str, start_s: float, duration_s: float, output_path: str) -> None:
    """Extract mono 16 kHz WAV audio segment via ffmpeg."""
    subprocess.run(
        [
            'ffmpeg', '-y',
            '-ss', str(start_s),
            '-t', str(duration_s),
            '-i', video_path,
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', '16000',
            '-ac', '1',
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def text_similarity(a: str, b: str) -> float:
    """Calculate similarity ratio (0–1) between two strings, ignoring punctuation."""
    clean = lambda s: re.sub(r'[^\w\s]', '', s.lower())
    a, b = clean(a), clean(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def compress_ws(s: str) -> str:
    """Collapse whitespace."""
    return re.sub(r'\s+', ' ', s).strip()


def find_best_match(
    transcription: dict,
    entries: list[dict],
    seg_start: float,
    seg_dur: float,
) -> Optional[tuple[dict, dict, float]]:
    """Return the (subtitle_entry, whisper_segment, similarity_score) or None.

    Only subtitle entries whose *start* falls within the extracted audio segment
    are candidates.  The algorithm tries matching against 1-, 2-, and 3-segment
    windows of the Whisper output and weights results by temporal proximity.
    """
    seg_end = seg_start + seg_dur
    candidates = [e for e in entries if seg_start <= e['start'] < seg_end]
    if not candidates:
        return None

    segments = transcription.get('segments', [])
    if not segments:
        return None

    best = None  # (sub_entry, whisper_seg, text_similarity)
    best_score = 0.0

    for sub in candidates:
        sub_text = compress_ws(sub['text'])
        if not sub_text:
            continue
        expected_pos = sub['start'] - seg_start  # where in the audio the line should appear

        for i in range(len(segments)):
            for win in (1, 2, 3):
                if i + win > len(segments):
                    break
                combo = ' '.join(s['text'].strip() for s in segments[i: i + win])
                combo = compress_ws(combo)
                if not combo:
                    continue

                text_score = text_similarity(sub_text, combo)
                if text_score == 0:
                    continue

                # Temporal weight: closer whisper position → higher weight
                wseg_pos = segments[i]['start']
                time_diff = abs(wseg_pos - expected_pos)
                max_diff = seg_dur * 0.5
                temporal_weight = max(0.0, 1.0 - time_diff / max_diff)

                combined = text_score * 0.7 + temporal_weight * 0.3
                if combined > best_score:
                    best_score = combined
                    best = (sub, segments[i], text_score)

    return best


def fmt_time(s: float) -> str:
    """Format seconds as HH:MM:SS.mmm (with optional leading minus)."""
    sign = '-' if s < 0 else ''
    a = abs(s)
    h, r = divmod(int(a), 3600)
    m, sec = divmod(r, 60)
    ms = int((a - int(a)) * 1000)
    return f'{sign}{h:02d}:{m:02d}:{sec:02d}.{ms:03d}'


def main():
    ap = argparse.ArgumentParser(description='subsync — detect subtitle sync offset using Whisper')
    ap.add_argument('subtitle', help='Path to SRT or VTT subtitle file')
    ap.add_argument('video', help='Path to video file')
    ap.add_argument('--duration', '-d', type=float, default=60.0,
                    help='Length of audio to extract [default: 60 s]')
    ap.add_argument('--model', '-m', default='base',
                    help='Whisper model to use [default: base]')
    ap.add_argument('--start', '-t', type=float, default=None,
                    help='Manual start time in the video (auto-detected if omitted)')
    ap.add_argument('--threshold', type=float, default=0.2,
                    help='Minimum text-similarity to accept a match [default: 0.2]')
    args = ap.parse_args()

    # 1. Parse subtitles
    entries = parse_subtitle(args.subtitle)
    if not entries:
        print('error: no subtitle entries found', file=sys.stderr)
        sys.exit(1)
    print(f'Parsed {len(entries)} subtitle entries')

    # 2. Determine start time
    start_s = args.start if args.start is not None else find_dialogue_region(entries, args.duration)
    print(f'Extracting audio @ {fmt_time(start_s)} for {args.duration} s')

    # 3. Extract audio
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        audio_path = tmp.name

    try:
        extract_audio(args.video, start_s, args.duration, audio_path)

        # 4. Transcribe with Whisper
        print(f'Transcribing with Whisper ({args.model}) …')
        model = whisper.load_model(args.model)
        result = model.transcribe(audio_path, verbose=False)
        segments = result.get('segments', [])
        print(f'Got {len(segments)} whisper segments\n')

        for seg in segments:
            print(f'  [{seg["start"]:7.2f}s – {seg["end"]:7.2f}s]  {seg["text"].strip()}')

        if not segments:
            print('error: whisper produced no segments', file=sys.stderr)
            sys.exit(1)

        # 5. Find best matching subtitle line
        match = find_best_match(result, entries, start_s, args.duration)

        if match is None:
            print('\nNo subtitle entries found within the transcribed region.', file=sys.stderr)
            sys.exit(1)

        sub_entry, wseg, sim = match

        if sim < args.threshold:
            print(f'\nBest similarity ({sim:.3f}) below threshold ({args.threshold}).', file=sys.stderr)
            print(f'  Sub: [{fmt_time(sub_entry["start"])}]  {sub_entry["text"]}')
            print(f'  Wsp: [{wseg["start"]:.2f}s]  {wseg["text"].strip()}')
            sys.exit(1)

        # 6. Calculate offset
        video_time_of_whisper = start_s + wseg['start']
        diff = sub_entry['start'] - video_time_of_whisper

        print()
        print('=' * 58)
        print(f'  Match similarity:  {sim:.3f}')
        print(f'  Subtitle line:     [{fmt_time(sub_entry["start"])} → {fmt_time(sub_entry["end"])}]')
        print(f'                     "{sub_entry["text"]}"')
        print(f'  Whisper segment:   [{wseg["start"]:.3f}s → {wseg["end"]:.3f}s in extracted audio]')
        print(f'                     "{wseg["text"].strip()}"')
        print(f'  Whisper in video:  {fmt_time(video_time_of_whisper)}')
        print()
        print(f'  SYNC OFFSET: {diff:+.3f} s   ({int(diff * 1000):+d} ms)')
        if diff > 0:
            print(f'  → Subtitles are {diff:.3f}s BEHIND audio')
        elif diff < 0:
            print(f'  → Subtitles are {abs(diff):.3f}s AHEAD of audio')
        else:
            print('  → Subtitles are perfectly synced')
        print('=' * 58)

    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


if __name__ == '__main__':
    main()
