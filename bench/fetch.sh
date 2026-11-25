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
