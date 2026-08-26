#!/usr/bin/env bash
# 从 upstream 重新拉取 vendor/。vendor/ 永远是上游原样，不要手改。
# 我们的改动一律走 patches/，自己的 skill 一律放 overlay/。
set -euo pipefail
cd "$(dirname "$0")"

UPSTREAM=https://github.com/mattpocock/skills.git
SHA=${1:-$(cat UPSTREAM.sha)}
SKILLS="triage grill-with-docs to-spec to-tickets implement tdd wayfinder
        diagnosing-bugs code-review resolving-merge-conflicts domain-modeling codebase-design"

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git clone -q "$UPSTREAM" "$tmp/s"
git -C "$tmp/s" checkout -q "$SHA"

rm -rf vendor && mkdir -p vendor
for s in $SKILLS; do cp -R "$tmp/s/skills/engineering/$s" "vendor/$s"; done
cp -R "$tmp/s/skills/productivity/grilling" vendor/grilling
cp "$tmp/s/LICENSE" vendor/LICENSE
cp "$tmp/s/.agents/invocation.md" vendor/_invocation-convention.md

printf '%s' "$SHA" > UPSTREAM.sha
python3 -c "import json;print(json.load(open('$tmp/s/.claude-plugin/plugin.json'))['version'])" > UPSTREAM.version

echo "synced to $SHA (plugin v$(cat UPSTREAM.version))"
echo "接下来跑 ./build.sh —— 若 patch 冲突，说明上游改了对应段落，需要重做 patch"
