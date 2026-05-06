# suba

> *"Because manually adjusting subtitle offsets by 200ms at 3am builds character"*
>
> — Nobody, ever

---

**suba** (Subtitles Use Better Algorithms) solves the oldest problem in digital piracy — sorry, *home media archival* — subs that drift out of sync so gradually you don't notice until an explosion happens three seconds before the sound.

You know the drill. You grab a subtitle file from OpenSubtitles. It claims to be for your exact release. It was uploaded by a user named `xXx_D4rkL0rd_xXx` in 2007 with the description "perfect sync enjoy :)". The first line appears while the studio logo is still fading in. By the climax, characters are reacting to events that happened during the opening credits. You spend 45 minutes in VLC tapping `h` and `g` like a pianist with a grudge, only to realize you've been adjusting the wrong direction the whole time.

**suba** says: let a machine do it, badly but fast.

## How It Works

1. **Finds the chattiest 60 seconds** of your video by picking the region with the highest subtitle density. (If your movie is *A Quiet Place*, this step may underperform.)
2. **Rips the audio** from that region with ffmpeg, because decoding an entire 5.1 DTS-HD Master Audio track just to check sync would be insane.
3. **Feeds it to OpenAI Whisper** running locally, which transcribes it with something approaching competence.
4. **Fuzzy-matches** the Whisper output against the subtitle file using a sliding window algorithm that weighs both text similarity and temporal proximity. This is the "smart" part — smart in the sense that a Roomba is smart about not falling down stairs.
5. **Calculates the offset** between where the subtitles *claim* the dialogue happens and where your ears say it happens.
6. **Rewrites the SRT** with corrected timestamps, so you can finally watch your legally-owned backup without losing your mind.

## Installation

```bash
# Prerequisites (probably already on your system if you've ever watched a video file)
ffmpeg           # audio extraction
openai-whisper   # pip install openai-whisper (or however you installed it, you clearly know what you're doing)

# The tool itself
git clone https://github.com/anomalyco/suba
cd suba
uv run python main.py --help
```

Wait, you don't have `uv`? It's 2026. Go install it. I'll wait.

## Usage

```bash
uv run python main.py subtitles.srt movie.mkv
```

That's it. It'll pick a dialogue-heavy chunk, transcribe it, find the best match, and tell you exactly how wrong your subtitles are.

```bash
# Fancy options
uv run python main.py subtitles.vtt movie.mp4 \
  --duration 120 \        # transcribe 2 minutes instead of 1
  --model small \         # use the small Whisper model (better accuracy, slower)
  --start 1800 \          # start at exactly 30 minutes in (you know where the problem is)
  --threshold 0.15 \      # accept looser text matches
  --output fixed.srt      # write out the corrected subtitles
```

## Output Example

```
Parsed 842 subtitle entries
Extracting audio @ 00:22:15.000 for 60.0 s
Transcribing with Whisper (base) …
Got 8 whisper segments

  [   0.00s –    8.24s]  I can't believe you did that
  [   9.12s –   14.56s]  What was I supposed to do just let them walk all over us
  ...

==========================================================
  Match similarity:  0.847
  Subtitle line:     [00:22:16.500 → 00:22:19.200]
                     "I can't believe you did that."
  Whisper segment:   [0.000s → 8.240s in extracted audio]
  Whisper in video:  00:22:15.000

  SYNC OFFSET: +1.500 s   (+1500 ms)
  → Subtitles are 1.500s BEHIND audio
==========================================================

Fixed subtitles written to: fixed.srt
```

Notice how the subtitle said the line starts at 22:16.5 but Whisper heard it at 22:15.0? That's 1.5 seconds of your life you'll never get back, multiplied by 842 subtitle entries. You're welcome.

## FAQ

**Q: Does it always get the right offset?**
A: It tries very hard and sometimes succeeds. If your subtitles are professional-grade and perfectly synced, it'll tell you there's a 0ms offset and you'll feel silly for running it. If your subtitles are fan-made and the first line is "HELOOO" with three O's, expect mixed results.

**Q: Why does it only check one minute?**
A: Because checking the entire movie would take forever, cost GPU compute, and quite frankly you have other things to do. The offset is almost always constant — the subtitle file was either shifted by a fixed amount or it wasn't. If your subtitles drift non-linearly (speeding up/slowing down relative to the video), you have bigger problems and should probably just find a different subtitle file.

**Q: Can it handle VTT files?**
A: Yes. WebVTT, SRT, whatever. It finds lines containing `-->` and parses timestamps around them. It doesn't care about your styling tags, your cue identifiers, or your artistic vision.

**Q: What if there's no dialogue in the analyzed chunk?**
A: Then Whisper produces nothing useful and the script fails with a sad error message. Try again with `--start` pointed at a scene where people actually talk. Or lower `--threshold` to 0.05 and roll the dice.

**Q: Will this work with [obscure language]?**
A: Whisper supports ~100 languages. Whether it transcribes your specific dialect of rural Walloon with any accuracy is between you and your GPU.

**Q: Does it require a GPU?**
A: No, but Whisper on CPU is like watching paint dry — if the paint were computing matrix multiplications. The `base` model on a 60-second clip is tolerable. The `large` model might finish before the heat death of the universe.

**Q: Why is it called "suba"?**
A: Because all the good CLI tool names are taken, and this one is short enough to type without carpal tunnel. It's an acronym for something I'll figure out later.

## License

MIT. If this tool saves your movie night, you owe me nothing. If it ruins it, you also owe me nothing, but please don't tweet about it.
