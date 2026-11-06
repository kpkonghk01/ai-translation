"""子命令的分派。每個子命令都可以注入 transport：測試傳假的，正式執行用真的。"""
import argparse
import sys

from . import check, report, translate
from .errors import InputError
from .transport import Transport, TransportRefused, UrllibTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="translation_eval", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("translate", help="把文章送去各個 Approach 翻譯")
    c = sub.add_parser("check", help="對 ok 的 Run 做 Deterministic check")
    r = sub.add_parser("report", help="產生 Block／Refusal 報告")
    for p in (t, c, r):
        p.add_argument("--config", required=True, help="實驗設定（JSON）")
        p.add_argument("--data-dir", default="data", help="輸出目錄（預設 data）")
    t.add_argument("--dry-run", action="store_true", help="不送出請求，只印出請求數、估計輸入字數和 endpoint")
    t.add_argument("--article", action="append", default=[], help="只跑這篇文章（可重覆）")
    t.add_argument("--limit", type=_positive_int, help="最多執行幾個 Run")
    t.add_argument("--redo-errors", action="store_true", help="重做結果為 error 的 Run")
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
            return translate.run(args, transport or UrllibTransport())
        if args.command == "check":
            return check.run(args)
        if args.command == "report":
            return report.run(args)
    except InputError as e:
        print(f"{args.command}：已停止，沒有送出請求，也沒有寫出任何檔案：", file=sys.stderr)
        for problem in e.problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    except TransportRefused as e:
        print(f"{args.command}：已停止，transport 拒絕送出請求：{e}", file=sys.stderr)
        return 1
    raise AssertionError(args.command)
