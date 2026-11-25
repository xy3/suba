#!/bin/sh
# Two full-length features for bench/features.py (about 750 MB).
set -e
cd "$(dirname "$0")/media"
mkdir -p charade sita subs
ua="suba-benchmark (https://github.com/xy3/suba)"
tt="https://commons.wikimedia.org/w/index.php?action=raw&title=TimedText:"
# Charade (1963): public domain; volunteer subtitles timed to this file.
[ -f charade/charade.webm ] || curl -fL -A "$ua" -o charade/charade.webm \
  "https://upload.wikimedia.org/wikipedia/commons/d/dc/Charade_%281963%29.webm"
for l in en es fr; do
  curl -sfL -A "$ua" -o "charade/charade.$l.srt" "${tt}Charade_(1963).webm.$l.srt"
done
curl -sfL -A "$ua" -o subs/notld.en.srt "${tt}Night_of_the_Living_Dead_(1968).webm.en.srt"
# Sita Sings the Blues (2008, CC-BY-SA) and its community pt-BR translation.
[ -f sita/sita.mp4 ] || curl -fL -o sita/sita.mp4 \
  https://archive.org/download/Sita_Sings_the_Blues/Sita_Sings_the_Blues.mp4
curl -sfL -o sita/sita.pt.srt \
  https://raw.githubusercontent.com/fczuardi/sita/master/subtitles/Sita_Sings_The_Blues_PT_BR.srt
