"""子命令的分派。每個子命令都可以注入 transport：測試傳假的，正式執行用真的。

用法（在 repo 根目錄）：
  uv run --locked python -I -m translation_eval translate --config <實驗設定> [--dry-run] [--article <id>] [--limit <N>] [--redo-errors]
  uv run --locked python -I -m translation_eval judge --config <實驗設定> [--task mqm|fact|screen] [--dry-run] [--article <id>] [--limit <N>] [--redo-errors]
  uv run --locked python -I -m translation_eval providers <作者/名稱> [--dry-run]   （OpenRouter 的 provider 清單，需要 key）
  uv run --locked python -I -m translation_eval import-cms --csv <CMS 匯出> [--out data/cms-articles]
  uv run --locked python -I -m translation_eval import-baseline --config <實驗設定>
  uv run --locked python -I -m translation_eval check --config <實驗設定>
  uv run --locked python -I -m translation_eval report --config <實驗設定>
  uv run --locked python -I -m translation_eval review --config <實驗設定>
  uv run --locked python -I -m translation_eval showcase --config <實驗設定>   （給非技術讀者的 HTML 展示報告，只有計數）
  uv run --locked python -I -m translation_eval showcase-text --config <實驗設定>   （含原文與譯文片段的展示頁，只在本機看，不要轉發）
  uv run --locked python -I -m translation_eval sample --config <實驗設定> --id <新實驗 id> [--per-group 10] [--controls 3] [--seed 1]   （抽樣成新實驗，縮小 Judge 的範圍）
  uv run --locked python -I -m translation_eval calibrate --config <實驗設定> [--judge <id>] [--dry-run] [--limit <N>] [--redo-errors]
  uv run --locked python -I -m translation_eval crosscheck --config <實驗設定> [--dry-run] [--article <id>]... [--limit <N>] [--redo-errors]
  uv run --locked python -I -m translation_eval gold check|sample --config <實驗設定> [--count 30] [--seed 1]
有輸出的子命令有 --data-dir（預設 data）；providers、import-cms（用 --out）、gold check 沒有。會呼叫 API、需要 key 的：translate、judge、calibrate --judge、crosscheck、
providers，一律經 tools/run-real.sh 執行（它把 key 放進環境變數再呼叫這個 package；真跑的指令見 docs/data-pipeline.md）。
"""
import argparse
import sys

from . import baselines, calibrate, check, cms, crosscheck, gold, judging, provider_listing, report, review, sample, showcase, showcase_text, translate
from .errors import InputError, StoppedAfterSending
from .schemas import Task
from .transport import Transport, TransportForbidden, UrllibTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="translation_eval", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("translate", help="把文章送去各個 Approach 翻譯")
    j = sub.add_parser("judge", help="Judge 評核每個 Component（MQM），每個評兩次")
    j.add_argument("--task", choices=[t.value for t in Task], help="只跑這個任務（預設：全部；沒有 ground_truth 時沒有 fact，judging.screening 是 false 時沒有 screen）")
    j.add_argument("--dry-run", action="store_true", help="不送出請求，只印出請求數和 endpoint")
    j.add_argument("--article", action="append", default=[], help="只評這篇文章（可重覆）")
    j.add_argument("--limit", type=_positive_int, help="最多執行幾個請求")
    j.add_argument("--redo-errors", action="store_true", help="重做結果為 error 或 empty 的請求")
    b = sub.add_parser("import-baseline", help="把方案 E 等 baseline 目錄匯入成 Run")
    c = sub.add_parser("check", help="對 ok 的 Run 做 Deterministic check")
    r = sub.add_parser("report", help="產生 Block／Refusal 報告")
    v = sub.add_parser("review", help="產生審閱清單（必看與抽查）")
    sm = sub.add_parser("sample", help="抽幾篇文章、每個模型只取第 1 次的完整文章，複製成新的實驗（縮小 Judge 的範圍）")
    sm.add_argument("--id", required=True, help="新實驗的 id")
    sm.add_argument("--per-group", type=int, default=10, help="每組（題材）抽幾篇（預設 10）")
    sm.add_argument("--controls", type=int, default=3, help="對照文章抽幾篇（預設 3）")
    sm.add_argument("--seed", type=int, default=1, help="種子（預設 1）；同樣的種子抽出來的一樣")
    st = sub.add_parser("showcase-text", help="產生含原文與譯文片段的展示頁（含真實文章內容，只在本機看，不要轉發）")
    sc = sub.add_parser("showcase", help="產生給非技術讀者看的 HTML 展示報告（只有計數，不含文章內容）")
    cal = sub.add_parser("calibrate", help="對 Gold set 量度 Judge 的準確度（有 --judge 才送請求）")
    cal.add_argument("--judge", help="要校準的 Judge 的 id；不寫就只重算結果")
    cal.add_argument("--dry-run", action="store_true", help="不送出請求，只印出請求數和 endpoint")
    cal.add_argument("--limit", type=_positive_int, help="最多執行幾個請求")
    cal.add_argument("--redo-errors", action="store_true", help="重做結果為 error 或 empty 的請求")
    xc = sub.add_parser("crosscheck", help="模型盲選最敏感的句子、盲抽事實，與 ground truth 對照（要 key）")
    xc.add_argument("--dry-run", action="store_true", help="不送出請求，只印出請求數和 endpoint")
    xc.add_argument("--article", action="append", default=[], help="只跑這篇文章（可重覆）")
    xc.add_argument("--limit", type=_positive_int, help="最多執行幾個請求")
    xc.add_argument("--redo-errors", action="store_true", help="重做結果為 error 或 empty 的請求")
    g = sub.add_parser("gold", help="Gold set：驗證並列出數量，或抽樣給 Pan 審閱")
    g_sub = g.add_subparsers(dest="gold_command", required=True)
    g_check = g_sub.add_parser("check", help="驗證 Gold set，列出各類別、Direction、domain 的數量")
    g_sample = g_sub.add_parser("sample", help="以固定種子抽項目，渲染成 Markdown")
    g_sample.add_argument("--count", type=_positive_int, default=30, help="抽幾條（預設 30）")
    g_sample.add_argument("--seed", type=int, default=1, help="種子（預設 1）")
    pv = sub.add_parser("providers", help="列出 OpenRouter 上某個模型的 provider 清單，供 Pan 確認")
    pv.add_argument("model", help="OpenRouter 的模型，寫成「作者/名稱」")
    pv.add_argument("--dry-run", action="store_true", help="不送出請求，只印出要查的網址")
    m = sub.add_parser("import-cms", help="把 CMS 匯出（CSV）轉成文章檔")
    m.add_argument("--csv", required=True, help="CMS 匯出的 CSV")
    m.add_argument("--out", default="data/cms-articles", help="文章檔的輸出目錄（預設 data/cms-articles）")
    for p in (t, j, b, c, r, v, sm, sc, st, cal, xc, g_sample):
        p.add_argument("--config", required=True, help="實驗設定（JSON）")
        p.add_argument("--data-dir", default="data", help="輸出目錄（預設 data）")
    g_check.add_argument("--config", required=True, help="實驗設定（JSON）")   # 只驗證，不寫任何東西
    t.add_argument("--dry-run", action="store_true", help="不送出請求，只印出請求數、估計輸入字數和 endpoint")
    t.add_argument("--article", action="append", default=[], help="只跑這篇文章（可重覆）")
    t.add_argument("--limit", type=_positive_int, help="最多執行幾個 Run")
    t.add_argument("--redo-errors", action="store_true", help="重做結果為 error 或 empty 的 Run")
    return parser


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("必須是 1 或以上")
    return value


def main(argv=None, transport: Transport | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "translate":
            return translate.handle(args, transport or UrllibTransport())
        if args.command == "judge":
            return judging.handle(args, transport or UrllibTransport())
        if args.command == "providers":
            return provider_listing.handle(args, transport or UrllibTransport())
        if args.command == "import-cms":
            return cms.handle(args)
        if args.command == "import-baseline":
            return baselines.handle(args)
        if args.command == "check":
            return check.handle(args)
        if args.command == "report":
            return report.handle(args)
        if args.command == "review":
            return review.handle(args)
        if args.command == "showcase":
            return showcase.handle(args)
        if args.command == "sample":
            return sample.handle(args)
        if args.command == "showcase-text":
            return showcase_text.handle(args)
        if args.command == "crosscheck":
            return crosscheck.handle(args, transport or UrllibTransport())
        if args.command == "calibrate":
            return calibrate.handle(args, transport or UrllibTransport())
        if args.command == "gold":
            return gold.handle_check(args) if args.gold_command == "check" else gold.handle_sample(args)
    except StoppedAfterSending as e:
        print(f"{args.command}：已停止；請求已經送出，已寫的紀錄保留（下次續跑會略過）：", file=sys.stderr)
        for problem in e.problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    except InputError as e:
        print(f"{args.command}：已停止，沒有送出請求，也沒有寫出任何檔案：", file=sys.stderr)
        for problem in e.problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    except TransportForbidden as e:
        print(f"{args.command}：已停止，transport 拒絕送出請求：{e}", file=sys.stderr)
        return 1
    raise AssertionError(args.command)
