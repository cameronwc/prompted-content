#!/bin/bash
# Captures the real pose-detail screen for every reel-eligible pose, on the
# tone its reel uses, via the Prompted PoseShots UI test. Reads
# dist/appshots/plan.json ({tone: [slug, ...]}) and writes
# dist/appshots/<slug>__<tone>.png. Chunks of 25 per xcodebuild run.
set -uo pipefail
cd "$(dirname "$0")/.."
UDID="${UDID:-778D6572-717F-45EE-BE0D-12A847F92736}"
APP=~/Dev/Prompted
OUT="$PWD/dist/appshots"
STAMP=$(date +%s)
xcrun simctl boot "$UDID" 2>/dev/null || true
xcrun simctl status_bar "$UDID" override --time 9:41 --batteryState charged --batteryLevel 100 --wifiBars 3 --cellularBars 4 --operatorName ""
python3 - "$OUT/plan.json" > "$OUT/chunks.txt" <<'PY'
import json,sys
plan=json.load(open(sys.argv[1]))
for tone,slugs in plan.items():
    for i in range(0,len(slugs),25):
        print(tone, ",".join(slugs[i:i+25]))
PY
n=0
while read -r tone slugs; do
  n=$((n+1)); bundle="$OUT/run-$STAMP-$n.xcresult"
  echo "== chunk $n ($tone): $(echo "$slugs" | tr ',' '\n' | wc -l | tr -d ' ') poses"
  ( cd "$APP" && TEST_RUNNER_PROMPTED_POSE_SHOTS=1 TEST_RUNNER_PROMPTED_POSE_SLUGS="$slugs" TEST_RUNNER_PROMPTED_POSE_TONE="$tone" \
    xcodebuild -project Prompted.xcodeproj -scheme Prompted -destination "platform=iOS Simulator,id=$UDID" \
    -only-testing:PromptedUITests/PoseShots test-without-building -resultBundlePath "$bundle" 2>&1 | grep -E "error:|passed|failed" | head -3 )
  xcrun xcresulttool export attachments --path "$bundle" --output-path "$bundle.raw" >/dev/null 2>&1
  python3 - "$bundle.raw" "$OUT" <<'PY'
import json,shutil,re,sys,os
raw,out=sys.argv[1],sys.argv[2]
m=json.load(open(os.path.join(raw,'manifest.json')))
k=0
for g in m:
    for a in g['attachments']:
        n=re.sub(r'_0_.*','',a['suggestedHumanReadableName']); shutil.copy(os.path.join(raw,a['exportedFileName']),os.path.join(out,n+'.png')); k+=1
print(f"   exported {k}")
PY
done < "$OUT/chunks.txt"
xcrun simctl status_bar "$UDID" clear
echo "captured: $(ls "$OUT"/*__*.png | wc -l | tr -d ' ') screenshots"
