# AGENTS.md

## Project overview

Single-file Python CLI tool that detects subtitle-to-audio sync offset. Extracts a dialogue-dense audio segment from a video, transcribes it with Whisper, matches the transcription against subtitle lines, computes the offset, and optionally writes corrected SRT/VTT output.

## Essential commands

```bash
# Install (one command)
uv tool install . --with openai-whisper

# Run
suba subs.srt video.mkv
suba subs.vtt video.mp4 -o fixed.srt

# Run from source without installing
python main.py subs.srt video.mkv
```

There is no test suite, no lint config, no Makefile, and no CI in this project.

## Files

```
main.py        — Everything: CLI, subtitle parsing, audio extraction,
                 whisper transcription, matching algorithm, output writing
                 (~340 lines)
pyproject.toml — Package metadata, entry point (suba = main:main)
uv.lock        — Lockfile for uv package manager
```

## Architecture

Everything lives in `main.py`. The entry point is `main()` which is registered in `pyproject.toml` as `suba = "main:main"`.

Control flow (`main()` → lines 239–340):

1. `parse_subtitle()` — Parse SRT or VTT into `[{'start': float, 'end': float, 'text': str}]`
2. `find_dialogue_region()` — Find the densest subtitle interval for audio extraction
3. `extract_audio()` — Run ffmpeg to extract mono 16kHz WAV
4. `whisper.load_model()` + `model.transcribe()` — Transcribe audio
5. `find_best_match()` — Match subtitle lines to whisper segments via similarity + temporal weighting
6. Compute offset and print results
7. `write_subtitle()` (if `--output`) — Write time-shifted entries in SRT or VTT format

## Dependencies

- **ffmpeg** — system dependency. Not checked at runtime; crashes via `subprocess.run(..., check=True)`.
- **openai-whisper** — declared in the install command (`uv tool install . --with openai-whisper`), NOT in `pyproject.toml` dependencies. Imported at runtime with a friendly error if missing. Do NOT add it to `pyproject.toml` — this is intentional to keep the core package dependency-free.
- **uv** — Python package manager used for installation. Project requires Python 3.12+.

## Key conventions

### Whisper dependency

`openai-whisper` is imported at runtime (`import whisper` on line 15). If missing, prints a helpful message and exits. The install command uses `--with openai-whisper` to inject it into the `uv` environment. Do not add to `pyproject.toml` dependencies.

### Subtitle parsing

`parse_subtitle()` (line 40) handles both SRT and VTT with a unified parser:
- Splits on blank lines (`\n\s*\n`)
- Finds the line containing `-->` for timestamps
- `ts_to_seconds()` normalizes both `,` and `.` as millisecond separators
- VTT tags (`<c>`, `<v>`, etc.) stripped via `strip_tags()` regex
- SRT index numbers silently skipped

### Matching algorithm

`find_best_match()` (line 137):
- Only considers subtitle entries where `entry.start` falls within the extracted audio window
- For each candidate, tries matching against 1-, 2-, and 3-segment windows of whisper output
- Score = `text_similarity * 0.7 + temporal_weight * 0.3`
- `text_similarity`: `SequenceMatcher(None, a, b).ratio()` on lowercase, punctuation-stripped, whitespace-collapsed text
- `temporal_weight`: linear decay from 1.0 to 0.0 based on distance from expected position (half-duration max)

### Text similarity

Uses Python's `difflib.SequenceMatcher.ratio()`. This is a greedy longest-contiguous-substring algorithm, NOT LCS. Any Go/other-language port MUST replicate this exactly. 66% of random text pairs differ between LCS and SequenceMatcher.

### Timestamp formats

- SRT: `HH:MM:SS,mmm` (comma) — `seconds_to_srt_ts()`
- VTT: `HH:MM:SS.mmm` (dot) — `seconds_to_vtt_ts()`
- Display: `HH:MM:SS.mmm` — `fmt_time()`
- Output format auto-detected from file extension

### Edge cases

- No subtitle entries in window → exits with error
- Similarity below `--threshold` (default 0.2) → exits with diagnostic info
- Temp WAV file cleaned up in `finally` block
- `find_dialogue_region()` falls back to 25% into range if density scan finds nothing
- Offset sign: `diff = sub_entry['start'] - video_time_of_whisper`. Positive = subtitles behind audio. Output applies `-diff` as correction.
