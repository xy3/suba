# I Never Manually Sync Subtitles Anymore. Here's the Whisper AI Script I Use.

We've all been there. You find the perfect subtitle file for a movie. The first line shows up during the studio logo. By act three, characters are laughing at jokes that haven't been told yet. You open VLC, start tapping `h` and `g`, and twenty minutes later you're 90% sure you made it worse.

I got tired of this. So I wrote a tool that fixes subtitle sync automatically using OpenAI's Whisper. It takes two arguments — a subtitle file and a video file — and tells you the exact offset. Then it writes out a corrected file so you never have to think about it again.

## The problem with subtitle sync

Most out-of-sync subtitles suffer from a constant offset. Someone ripped them from a different release, or a frame rate mismatch shifted every timestamp by the same amount. The fix is trivial — add or subtract a constant number of seconds to every line — but *finding* that number is the hard part.

The traditional approach is manual. You jump to a dialogue-heavy scene, watch a character's lips, and nudge the subtitle track forward or backward until it "feels right." This works if you're patient and have good ears. I am neither.

## How suba works

The idea is simple: let Whisper transcribe a chunk of the actual audio, then compare its transcription to the subtitle file. If the subtitles say a character should be speaking at 23:36, but Whisper heard those words at 23:38, your offset is -2 seconds. Shift everything by 2 seconds, and you're done.

Here's the pipeline:

**1. Find the dialogue.** You don't want to transcribe the opening credits or a long action sequence with no talking. Suba scans the subtitle file and finds the densest 60-second window — the part with the most lines per minute, which almost certainly has people talking.

**2. Extract the audio.** ffmpeg pulls that one-minute segment as a mono 16kHz WAV file. Fast and lossless for Whisper's purposes.

**3. Transcribe with Whisper.** OpenAI's Whisper model converts the audio to text with timestamps. You get back something like:

```
[9.00s – 9.90s]  You guys suck.
[9.90s – 10.80s] Both of you guys suck.
```

**4. Match and score.** This is where it gets interesting. For each subtitle line that falls within the extracted window, suba slides a window over the Whisper transcription and scores how well they match. The scoring combines two things:

- **Text similarity** (70% weight): how closely do the words match? Uses Python's `SequenceMatcher` algorithm — the same engine behind `difflib`.
- **Temporal proximity** (30% weight): how close is the Whisper segment's timestamp to where the subtitle *says* it should be?

The combined score prevents false positives. A perfect text match at the wrong time is less convincing than a good text match at roughly the right time.

**5. Compute the offset.** Once the best match is found, the math is trivial:

```
offset = subtitle_start - (audio_extraction_start + whisper_segment_start)
```

Positive offset means subtitles are behind. Negative means they're ahead. Either way, suba shifts every timestamp by the negation of that offset and writes a corrected SRT or VTT file.

## Real example

I tested this on *Zoolander 2*. The subtitles I found were clearly from a different release — the sync drifted badly after the first few minutes.

Running suba:

```bash
$ suba zoolander2.srt Zoolander.2.mp4
```

Output:

```
Parsed 1474 subtitle entries
Extracting audio @ 00:23:29.608 for 60 s
Transcribing with Whisper (base) …
Got 43 whisper segments

==========================================================
  Match similarity:  1.000
  Subtitle line:     [00:23:36.615 → 00:23:37.914]
                     "You guys suck.
Both of you guys suck."
  Whisper segment:   [9.000s → 9.900s in extracted audio]
                     "You guys suck."
  Whisper in video:  00:23:38.608

  SYNC OFFSET: -1.993 s   (-1992 ms)
  → Subtitles are 1.993s AHEAD of audio
==========================================================
```

A perfect text match at 23:36 in the subtitle file versus 23:38 in the actual audio. The subtitles are roughly 2 seconds ahead — exactly the kind of constant offset that makes a movie unwatchable. One command, two seconds, problem solved.

To write the fixed subtitles:

```bash
$ suba zoolander2.srt Zoolander.2.mp4 -o fixed.srt
```

## Why Whisper?

Whisper is remarkably good at this task. It handles background noise, music, and overlapping dialogue better than any open-source speech-to-text model I've tried. The `base` model is small enough to run on a laptop CPU in under a minute for a 60-second clip. If you need higher accuracy, `small` or `medium` will slow things down but improve recognition of mumbled lines or thick accents.

## The algorithm details that matter

A few design decisions made this work reliably where simpler approaches failed:

**Multi-window matching.** A subtitle line might say "You guys suck. Both of you guys suck." but Whisper might split that into two segments or merge it into one. Suba tries matching each subtitle against 1-, 2-, and 3-segment windows of the transcription and takes the best score.

**Dialogue density heuristic.** Instead of requiring the user to pick a start time, suba scans the entire subtitle timeline in 5-second increments and picks the region with the most entries. More lines per minute means more talking, which means better transcription, which means higher confidence in the match.

**Similarity threshold.** The default threshold of 0.2 prevents garbage matches. If Whisper hallucinates or the audio region happens to be mostly music, suba exits with an error rather than silently producing a wrong offset.

## Install and use

The only prerequisites are `ffmpeg` and `uv` (a fast Python package manager):

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install suba (one command — this handles Python and Whisper too)
uv tool install git+https://github.com/xy3/suba --with openai-whisper
```

Then:

```bash
# Find the offset
suba subtitles.srt movie.mkv

# Write corrected subtitles
suba subtitles.vtt movie.mp4 -o fixed.srt

# Use a larger model for better accuracy
suba -m medium subtitles.srt movie.mkv
```

## What it doesn't do

Suba assumes a constant offset. If your subtitles drift progressively (the sync gets worse over time), this won't help. It also assumes the subtitle text roughly matches what's being said — it won't work for dubtitles or heavily edited fansubs. And it needs at least a few dialogue-heavy minutes in the video; a silent film with subtitles will fail the density check.

## The bottom line

I used to spend 10–15 minutes manually syncing subtitles for every foreign film I watched. Now I run one command to install and one command to sync. The tool is open source, a single Python file, and costs nothing to run. If you watch subtitled content with any regularity, this will save you real time.

[suba on GitHub](https://github.com/xy3/suba)
