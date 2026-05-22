# Contributing

Thanks for wanting to help. Here's what you need to know.

## Setup

```bash
git clone https://github.com/xy3/suba
cd suba
uv tool install . --with openai-whisper
```

You'll also need `ffmpeg` on your system.

## Running from source

```bash
python main.py subtitles.srt video.mkv
```

No build step needed — it's a single file.

## Project structure

Everything is in `main.py` (~340 lines). There are no external Python dependencies beyond `openai-whisper`, and even that is intentionally left out of `pyproject.toml` to keep the package dependency-free.

## What to work on

- Support for more subtitle formats (ASS, SSA, SUB)
- Progressive sync (non-constant drift correction)
- Better language detection for mixed-language subtitles
- Tests (there are currently none)
- CI/CD for automated testing

## Before submitting

- Make sure `python main.py subs.srt video.mkv` works end-to-end
- Keep the single-file structure unless there's a compelling reason to split
- Don't add `openai-whisper` to `pyproject.toml` — it's handled by `uv tool install --with`
