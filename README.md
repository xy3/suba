# suba

> Fix subtitle sync with Whisper, because `h` and `g` are for cowards.

---

You downloaded subtitles. The first line pops up during the studio logo. By the third act, characters are reacting to things that haven't happened yet. You've spent 20 minutes in VLC tapping keys and you're 90% sure you made it worse.

Suba rips a minute of audio from a dialogue-heavy part of your video, transcribes it with Whisper, matches the transcription against the subtitle file, and tells you the offset. Then it writes out a corrected SRT so you can stop thinking about this.

## How it works

- Finds the densest 60 seconds of subtitles (more lines = more talking, probably)
- Extracts that audio with ffmpeg
- Transcribes it with Whisper
- Slides windows of the transcription over each candidate subtitle line, scoring by text similarity and timestamp proximity
- Shifts all timestamps by the detected offset and writes a fixed SRT

## Install

```bash
git clone https://github.com/xy3/suba
cd suba
uv tool install . --with openai-whisper
```

If you already have openai-whisper installed system-wide and don't want to download it again:

```bash
pip install --break-system-packages .
```

Prerequisites: `ffmpeg`, Python 3.12+.

## Use

```bash
suba subs.srt movie.mkv
```

```bash
# output the fixed subtitles to a file (srt or vtt)
suba subs.vtt movie.mp4 -o fixed.srt
```

| Flag | What |
|------|------|
| `-o` | Write corrected SRT |
| `-m` | Whisper model (`base`, `small`, `medium`, `large`) |
| `-d` | Audio duration in seconds (default 60) |
| `-t` | Manual start time in seconds |
| `--threshold` | Minimum similarity to accept a match (default 0.2) |
