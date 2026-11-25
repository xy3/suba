#!/usr/bin/env python3
"""Render a recorded terminal session as an animated GIF.

    python bench/render_gif.py session.txt docs/demo.gif

The first line of session.txt is the command (typed out character by
character); every following line is real output, shown a line at a time.
A line starting with '##pause N' holds the frame for N seconds (e.g. while
the real tool was working).
"""

import sys

from PIL import Image, ImageDraw, ImageFont

FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 15)
BG, FG, DIM, PROMPT, ACCENT = "#0d1117", "#e6edf3", "#8b949e", "#7ee787", "#ffa657"
COLS, ROWS, PAD = 84, 28, 18
CW = FONT.getbbox("M")[2]
LH = 21


def colour(line):
    if "SYNC OFFSET" in line or "conversion detected" in line:
        return ACCENT
    if line.startswith(("=", "  →", "Reading", "Loading", "Extract", "Detected")):
        return DIM
    return FG


def frame(lines, cursor):
    w, h = PAD * 2 + CW * COLS, PAD * 2 + LH * ROWS
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    for i, (text, col) in enumerate(lines[-ROWS:]):
        y = PAD + i * LH
        if col == "cmd":
            d.text((PAD, y), "$ ", font=FONT, fill=PROMPT)
            d.text((PAD + 2 * CW, y), text, font=FONT, fill=FG)
            if cursor:
                x = PAD + (2 + len(text)) * CW
                d.rectangle((x, y + 2, x + CW - 2, y + LH - 3), fill=FG)
        else:
            d.text((PAD, y), text, font=FONT, fill=col)
    return img.quantize(colors=16)


def main(src, dst):
    raw = open(src, encoding="utf-8").read().splitlines()
    cmd, out = raw[0], raw[1:]
    frames, durations, shown = [], [], []

    def emit(ms, cursor=False):
        frames.append(frame(shown, cursor))
        durations.append(ms)

    shown.append(("", "cmd"))
    emit(600, True)
    for i in range(1, len(cmd) + 1, 2):
        shown[-1] = (cmd[:i], "cmd")
        emit(45, True)
    shown[-1] = (cmd, "cmd")
    emit(500, True)
    for line in out:
        if line.startswith("##pause"):
            durations[-1] += int(float(line.split()[1]) * 1000)
            continue
        shown.append((line, colour(line)))
        emit(110)
    durations[-1] = 6000
    frames[0].save(
        dst,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )


if __name__ == "__main__":
    main(*sys.argv[1:3])
