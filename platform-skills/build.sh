#!/usr/bin/env bash
# 产出 dist/ = vendor（上游原样）+ patches（我们的改动）+ overlay（我们自己的 skill）
# dist/ 就是烘焙进 workspace 容器镜像 ~/.config/opencode/skills/ 的内容（§14.3 L1）
set -euo pipefail
cd "$(dirname "$0")"

rm -rf dist && mkdir -p dist
cp -R vendor/* dist/
rm -f dist/LICENSE dist/_invocation-convention.md

for p in patches/*.patch; do
  [ -e "$p" ] || continue
  echo "applying $(basename "$p")"
  # patch 里的路径是 vendor/...，在 dist 上应用需要剥掉两层再补回
  sed 's#/vendor/#/#g' "$p" | patch -p1 -d dist -s
done

# 第二个上游：ego-browser（浏览器自动化）。放在 overlay 之前 ——
# overlay 里我们自己的 skill 可以覆盖上游同名文件。
if [ -d vendor-ego ]; then
  cp -R vendor-ego/ego-browser dist/ego-browser
fi

cp -R overlay/* dist/

echo "--- dist ---"
for d in dist/*/; do
  n=$(basename "$d")
  desc=$(awk -F': ' '/^description:/{print substr($0,14,70);exit}' "$d/SKILL.md" 2>/dev/null || echo '?')
  printf '%-28s %s\n' "$n" "$desc"
done
echo
echo "共 $(ls -d dist/*/ | wc -l | tr -d ' ') 个 skill · $(du -sh dist | cut -f1)"
