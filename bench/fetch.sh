#!/bin/sh
# Download Tears of Steel (CC-BY 3.0, Blender Foundation) and its subtitle tracks.
set -e
cd "$(dirname "$0")"
mkdir -p media/subs
base=https://download.blender.org/demo/movies/ToS
for l in en de es fr-orig it nl ru JP; do
  curl -sfo "media/subs/TOS-$l.srt" "$base/subtitles/TOS-$l.srt"
done
[ -f media/tos.mov ] || curl -fo media/tos.mov "$base/tears_of_steel_720p.mov"
curl -sfo media/subs/sintel_en.srt https://durian.blender.org/wp-content/content/subtitles/sintel_en.srt
# Music-and-effects stem on top of the stereo mix: the score drowns the dialogue.
[ -f media/me.aif ] || curl -fo media/me.aif "$base/TOS_MUSIC%2BFX_NO_DIALOGUE.aif"
[ -f media/mix.aif ] || curl -fo media/mix.aif "$base/TOS_DVDSTEREOMIX.aif"
for k in 2 3; do
  [ -f media/tos_loud$k.mkv ] || ffmpeg -v error -i media/tos.mov -i media/mix.aif -i media/me.aif \
    -filter_complex "[1:a][2:a]amix=inputs=2:weights=1 $k:normalize=0,alimiter=limit=0.95[a]" \
    -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 160k media/tos_loud$k.mkv
done
