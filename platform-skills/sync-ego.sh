#!/usr/bin/env bash
# 拉取 ego-browser skill（第二个上游）。
#
# 为什么单独一个脚本：sync.sh 是 mattpocock/skills 专用的，它 `rm -rf vendor`
# 再整体重建 —— 把第二个上游混进去会在下次同步时被抹掉。两个上游各管各的
# vendor 目录，各记各的 SHA，谁升级都不影响对方。
set -euo pipefail
cd "$(dirname "$0")"

UPSTREAM=https://github.com/citrolabs/ego-lite.git
SHA=${1:-$(cat UPSTREAM-EGO.sha 2>/dev/null || echo HEAD)}

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git clone -q "$UPSTREAM" "$tmp/e"
git -C "$tmp/e" checkout -q "$SHA"

rm -rf vendor-ego && mkdir -p vendor-ego
cp -R "$tmp/e/skills/ego-browser" vendor-ego/ego-browser
cp "$tmp/e/LICENSE" vendor-ego/LICENSE

git -C "$tmp/e" rev-parse HEAD > UPSTREAM-EGO.sha
awk -F'"' '/^  version:/{print $2; exit}' vendor-ego/ego-browser/SKILL.md > UPSTREAM-EGO.version

echo "ego-browser synced to $(cat UPSTREAM-EGO.sha) (skill v$(cat UPSTREAM-EGO.version))"
