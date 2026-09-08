# suba

> Fix subtitle sync with Whisper, because `h` and `g` are for cowards.

---

You downloaded subtitles. The first line pops up during the studio logo. By the third act, characters are reacting to things that haven't happened yet. You've spent 20 minutes in VLC tapping keys and you're 90% sure you made it worse.

Suba rips a minute of audio from a dialogue-heavy part of your video, transcribes it with Whisper, matches the transcription against the subtitle file, and tells you the offset. Then it writes out a corrected SRT so you can stop thinking about this.

## Install

You need `ffmpeg` and [`uv`](https://docs.astral.sh/uv/). Then:

```bash
uv tool install suba --with openai-whisper
```

Or with pip:

```bash
pip install suba openai-whisper
```

That's it. One command — no clone, no build step.

## Use

```bash
suba subs.srt movie.mkv -o fixed.srt
```

```bash
suba <subtitles> <video> -o <output.srt|vtt>
```

| Flag | What |
|------|------|
| `-o`, `--output` | Write corrected SRT/VTT |
| `-m`, `--model` | Whisper model (`base`, `small`, `medium`, `large`) |
| `-d`, `--duration` | Audio seconds to transcribe for text matching (default 60) |
| `-t`, `--start` | Manual start time in seconds, for text matching |
| `--threshold` | Minimum similarity to accept a match |
| `-A`, `--audio-lang` | Language spoken in the video (`en`, `english`, `eng`) |
| `-S`, `--sub-lang` | Language of the subtitle file (`it`, `italian`, `ita`) |
| `--align` | Force a strategy: `auto`, `text`, `translate`, `timing` |
| `--max-shift` | Largest offset to search for in timing mode (default 60 s) |
| `--min-peak-ratio` | How clearly the winning offset must beat the runner-up (default 1.15) |
| `--no-refine` | Don't sharpen a text match against the waveform |

## Different languages

English movie, Italian subtitles? Tell it so:

```bash
suba movie.ita.srt movie.mkv -A en -S it -o fixed.srt
```

You usually don't have to. Suba reads the language off the filename
(`movie.ita.srt`), falls back to sniffing the subtitle text itself, and asks
Whisper what the audio is. The flags are there for when it guesses wrong.

Depending on the pair, it picks one of three strategies:

| Audio | Subtitles | Strategy | How |
|-------|-----------|----------|-----|
| en | en | `text` | Transcribe and compare the words |
| it | en | `translate` | Whisper translates the audio to English, then compare the words |
| en | it | `timing` | Compare *when* people speak, ignoring what they say |

The awkward case is the third one, because Whisper only translates **into**
English — it can't produce Italian to match against. So suba stops reading and
starts listening to the rhythm: it measures the energy of the waveform to find
every stretch where somebody is talking, marks every stretch where a subtitle is
on screen, and slides one over the other until the talking lines up with the
talking *and* the silence lines up with the silence. Dialogue has a fingerprint,
and translation doesn't change it.

Three things worth knowing about timing mode:

- **It reads the whole film**, not a sample. A five-minute window is enough for
  clean audio, but a scored feature film needs everything: the pauses that carry
  the signal are rarer and noisier, and a short window reads as noise. On a
  3h53m movie that costs about 30 seconds end to end, most of it ffmpeg.
- **It would rather say nothing than lie.** A correct alignment beats every
  other candidate offset by a wide margin — measured at 5-8x on real films,
  against 1.01x for a subtitle file belonging to a different cut. When the best
  offset only edges out the runner-up, suba refuses instead of handing you a
  confidently wrong answer. `--min-peak-ratio` is the control that matters here;
  `--threshold` barely discriminates in this mode.
- **It's fast**, because it never transcribes. Pass both languages and Whisper
  is skipped entirely.

It's also *precise*, and that precision is now lent to the other two strategies.
Matching text tells you which line is playing, but only as accurately as the
Whisper segment it landed in — and those can run ten seconds long. So after a
text or translate match, suba shifts the subtitles by its estimate and does a
short correlation search to recover the leftover error:

```
Match similarity:  0.875
Refined on waveform: -0.314 s → -4.264 s (correlation 0.988)
```

That example is a real run against Italian audio with English subtitles, where
the true offset was -4.250 s. Pass `--no-refine` to turn it off.

## How it works

- Finds the densest 60 seconds of subtitles (more lines = more talking, probably)
- Extracts that audio with ffmpeg
- Transcribes it with Whisper
- Slides windows of the transcription over each candidate subtitle line, scoring by text similarity and timestamp proximity
- Shifts all timestamps by the detected offset and writes a fixed SRT

When the subtitles aren't in the language being spoken, the middle steps change
— see [Different languages](#different-languages) — but the last one doesn't.
Your subtitle text is never rewritten, only re-timed.


**🌟 Star this repository if you find it helpful!**
