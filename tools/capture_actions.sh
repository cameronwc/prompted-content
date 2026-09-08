#!/bin/bash
# Records the "tiny app action" clip for each reel-eligible pose:
# open pose -> tap tone chip -> the line appears. One simulator recording per
# pose, normalised to 30 fps CFR and trimmed to the last ACTION_SECONDS.
# Reads dist/appshots/plan-full.json ({tone: [slug, ...]}); writes
# dist/actions/<slug>__<tone>.mp4. Skips poses that already have a clip.
set -uo pipefail
cd "$(dirname "$0")/.."
UDID="${UDID:-778D6572-717F-45EE-BE0D-12A847F92736}"
APP=~/Dev/Prompted
OUT="$PWD/dist/actions"; mkdir -p "$OUT"
ACTION_SECONDS="${ACTION_SECONDS:-7.0}"
LIMIT="${LIMIT:-0}"
xcrun simctl boot "$UDID" 2>/dev/null || true
xcrun simctl status_bar "$UDID" override --time 9:41 --batteryState charged --batteryLevel 100 --wifiBars 3 --cellularBars 4 --operatorName ""
python3 - "$OUT" > "$OUT/todo.txt" <<'PY'
import json,sys,os
plan=json.load(open('dist/appshots/plan-full.json'))
for tone,slugs in plan.items():
    for s in slugs:
        if not os.path.exists(os.path.join(sys.argv[1], f"{s}__{tone}.mp4")): print(tone, s)
PY
n=0
while read -r tone slug; do
  n=$((n+1)); [ "$LIMIT" != "0" ] && [ "$n" -gt "$LIMIT" ] && break
  raw="$OUT/.raw-$slug.mov"; cfr="$OUT/.cfr-$slug.mp4"; final="$OUT/${slug}__${tone}.mp4"
  echo "== $n $slug ($tone)"
  xcrun simctl io "$UDID" recordVideo --codec h264 --mask ignored "$raw" & REC=$!
  sleep 1.5
  ( cd "$APP" && TEST_RUNNER_PROMPTED_ACTION_TAKE=1 TEST_RUNNER_PROMPTED_ACTION_SLUG="$slug" TEST_RUNNER_PROMPTED_ACTION_TONE="$tone" \
    xcodebuild -project Prompted.xcodeproj -scheme Prompted -destination "platform=iOS Simulator,id=$UDID" \
    -only-testing:PromptedUITests/ActionTake test-without-building 2>&1 </dev/null | grep -E "error:|passed|failed" | head -2 )
  kill -INT $REC 2>/dev/null; wait $REC 2>/dev/null; sleep 1
  if [ ! -s "$raw" ]; then echo "   no recording"; continue; fi
  ffmpeg -nostdin -v error -y -i "$raw" -vf fps=30 -fps_mode cfr -c:v libx264 -preset fast -crf 17 -an "$cfr" </dev/null || { echo "   cfr failed"; continue; }
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$cfr")
  # The app quits at the end of the test (a hard cut to the Home Screen);
  # the action is the ACTION_SECONDS just before that last scene change.
  lastcut=$(ffmpeg -nostdin -v info -i "$cfr" -vf "select='gt(scene,0.3)',showinfo" -f null - 2>&1 </dev/null | grep -o "pts_time:[0-9.]*" | sed 's/pts_time://' | tail -1)
  end=$(python3 -c "d=$dur; c=float('${lastcut:-0}' or 0); print(round((c-0.25) if c > d-8 and c > $ACTION_SECONDS else d, 2))")
  start=$(python3 -c "print(max(0.0, $end - $ACTION_SECONDS))")
  ffmpeg -nostdin -v error -y -ss "$start" -to "$end" -i "$cfr" -c:v libx264 -preset fast -crf 17 -movflags +faststart -an "$final" </dev/null && echo "   wrote $(basename "$final") ($(du -h "$final" | cut -f1), $start-$end of $dur)"
  rm -f "$raw" "$cfr"
done < "$OUT/todo.txt"
xcrun simctl status_bar "$UDID" clear
echo "actions: $(ls "$OUT"/*__*.mp4 2>/dev/null | wc -l | tr -d ' ') clips"
