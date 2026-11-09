#!/usr/bin/env bash
# 真跑評測（會呼叫 API、要 key）的固定入口：由這裡把 .env 載入評核框架的行程，再執行 translation_eval。
# agent 的 permission 設定禁止它讀 .env、也禁止它的指令帶 --env-file；Pan 同意 agent 經這個入口代跑，
# 所以 key 只在這個行程的環境變數裡，不會出現在 agent 的指令或輸出。
# 入口只執行會用到 key 的子命令，不能拿來執行別的指令。
# 用法：tools/run-real.sh translate|judge|calibrate|crosscheck|providers [參數...]
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

case "${1:-}" in
  translate | judge | calibrate | crosscheck | providers) ;;
  *)
    echo "用法：tools/run-real.sh translate|judge|calibrate|crosscheck|providers [參數...]" >&2
    exit 2
    ;;
esac

exec uv run --locked --env-file .env python -I -m translation_eval "$@"
