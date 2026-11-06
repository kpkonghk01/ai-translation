#!/usr/bin/env bash
# 跑這個 repo 的全部檢查：所有測試，以及（本機有資料時）只能在本機跑的資料檢查。
# 用法：tools/check.sh
# 會先跑完全部，再列出失敗的項目；有任何一項失敗就以非 0 結束。
# 檢查的是工作目錄裡的檔案，不是 staged 的快照。
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

logdir=$(mktemp -d)
trap 'rm -rf "$logdir"' EXIT
n_ok=0
n_fail=0
failed=""

run() {
  local name=$1
  shift
  if "$@" >"$logdir/$name.log" 2>&1; then
    n_ok=$((n_ok + 1))
    printf '  ok    %s\n' "$name"
  else
    n_fail=$((n_fail + 1))
    failed="$failed $name"
    printf '  FAIL  %s\n' "$name"
    sed 's/^/        /' "$logdir/$name.log"
  fi
}

# 已批准的 CMS ground truth 草稿，用現在的腳本重新渲染，必須與存檔完全相同
cms_ground_truth_unchanged() {
  python3 -I tools/render_ground_truth.py data/ground-truth-draft.json --csv data-prohibited.csv --out "$logdir/gt.md"
  cmp "$logdir/gt.md" data/ground-truth-draft.md
}

echo "測試："
for t in tools/test_*.py; do
  run "$(basename "$t" .py)" python3 -I "$t"
done
if command -v node >/dev/null 2>&1; then
  run test_preview node tools/test_preview.js preview.html
else
  n_fail=$((n_fail + 1))
  failed="$failed test_preview"
  echo "  FAIL  test_preview：找不到 node"
fi

echo "本機資料檢查："
if [ -f data-prohibited.csv ] && [ -f data/ground-truth-draft.json ] && [ -f data/ground-truth-draft.md ]; then
  run cms-ground-truth-unchanged cms_ground_truth_unchanged
else
  echo "  略過 cms-ground-truth-unchanged：本機沒有 data/ground-truth-draft.* 或 data-prohibited.csv"
fi
# 每個抓取保存的原始物件目錄（data/web-articles*-raw），轉換結果都不可以漏掉原文
n_raw=0
for raw in data/web-articles*-raw; do
  [ -d "$raw" ] || continue
  n_raw=$((n_raw + 1))
  run "verify-$(basename "${raw%-raw}")" python3 -I tools/hk01_collect.py verify --raw "$raw" --out "${raw%-raw}"
done
if [ "$n_raw" -eq 0 ]; then
  echo "  略過 verify：本機沒有 data/web-articles*-raw"
fi

echo
if [ "$n_fail" -eq 0 ]; then
  echo "全部通過（$n_ok 項）"
else
  echo "失敗 $n_fail 項：$failed"
  exit 1
fi
