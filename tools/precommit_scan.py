"""Commit 前掃描 staged 的變更，擋下不該進 repo 的東西。由 tools/hooks/pre-commit 呼叫。

用法：
  python3 -I tools/precommit_scan.py          掃描 staged 的變更（只看新增的行）
  python3 -I tools/precommit_scan.py --all    掃描所有已追蹤檔案的全部內容（一次性檢查用）

規則（任何一項命中即以非 0 結束）：
  1. 路徑：data/、refs/、.venv/、.env 與 .env.*（*.example 除外）、根目錄的 *.csv
  2. 香港法院案件編號格式；年份為 FICTITIOUS_FROM_YEAR 或以後的是測試用的虛構編號，放行
  3. API key 與私鑰格式
  4. 真實文章的完整標題（MIN_TITLE_CHARS 字以上，忽略空白）。標題在執行時從 data/ 與 data-prohibited.csv 讀取；
     這兩者都被 git 排除，所以名單本身不會進 repo。本機沒有資料時略過此項，並印出說明。

git 指令失敗會直接拋出錯誤，不會被當成「沒有發現」。
"""
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

FICTITIOUS_FROM_YEAR = 2030
MIN_TITLE_CHARS = 12
BLOCKED_DIRS = ("data", "refs", ".venv")
TITLE_DIRS = ("web-articles*", "baseline-service*")   # data/ 底下，每個 JSON 頂層有 title 的目錄

_CASE_NO = re.compile(r"(?<![A-Za-z])[A-Z]{2,6}\s?\d{1,5}\s?/\s?((?:19|20)\d{2})(?!\d)")
_KEYS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "API key（sk- 開頭）"),
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "Google API key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私鑰"),
    (re.compile(r"""(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*["'][^"'\s]{16,}["']"""), "寫死的密鑰"),
]


def _squash(s):
    return re.sub(r"\s+", "", s)


def _unescape(s):
    """把 \\uXXXX 還原成字元：JSON（ensure_ascii）與 Python repr 會這樣寫中文和全形空白。"""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def path_violations(paths):
    out = []
    for p in paths:
        parts = p.split("/")
        name = parts[-1]
        if parts[0] in BLOCKED_DIRS:
            out.append(f"{p}：{parts[0]}/ 不可進 repo")
        elif (name == ".env" or name.startswith(".env.")) and not name.endswith(".example"):
            out.append(f"{p}：.env 檔不可進 repo")
        elif len(parts) == 1 and name.lower().endswith(".csv"):
            out.append(f"{p}：根目錄的 CSV（資料匯出）不可進 repo")
    return out


def added_lines(diff_text):
    """解析 `git diff -U0` 的輸出 → [(路徑, 行號, 內容)]，只取新增的行。"""
    out, path, line, in_header = [], None, 0, False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            in_header, path = True, None
        elif in_header and raw.startswith("+++ "):
            target = raw[4:].strip('"')
            path = None if target == "/dev/null" else (target[2:] if target.startswith("b/") else target)
        elif raw.startswith("@@"):
            in_header = False
            m = re.match(r"@@ -\S+ \+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif not in_header and raw.startswith("+") and path is not None:
            out.append((path, line, raw[1:]))
            line += 1
    return out


def content_violations(lines, titles=()):
    norm_titles = sorted({t for t in (_squash(t) for t in titles) if len(t) >= MIN_TITLE_CHARS})
    out = []
    for path, n, text in lines:
        where = f"{path}:{n}"
        for m in _CASE_NO.finditer(text):
            if int(m.group(1)) < FICTITIOUS_FROM_YEAR:
                out.append(f"{where}：疑似真實的法院案件編號 {m.group(0)}（測試請用 {FICTITIOUS_FROM_YEAR} 年或以後的虛構編號）")
        for pattern, label in _KEYS:
            if pattern.search(text):
                out.append(f"{where}：疑似{label}")
        if norm_titles:
            squashed = _squash(_unescape(text))
            hit = next((t for t in norm_titles if t in squashed), None)
            if hit:
                out.append(f"{where}：含真實文章的完整標題「{hit[:20]}…」")
    return out


def load_titles(root):
    """回傳 (標題集合, 說明清單)。只讀被 git 排除的本機資料。"""
    root = Path(root)
    titles, notes = set(), []
    csv_path = root / "data-prohibited.csv"
    if csv_path.exists():
        csv.field_size_limit(sys.maxsize)
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                try:
                    turn = next(t for t in json.loads(row[1]) if t.get("role") == "user")
                    titles.add(json.loads("".join(p.get("text", "") for p in turn["parts"])).get("title", ""))
                except (ValueError, StopIteration, KeyError, TypeError, AttributeError) as e:
                    notes.append(f"警告：{csv_path.name} 有一列讀不到標題（{type(e).__name__}）")
    for pattern in TITLE_DIRS:
        for d in sorted((root / "data").glob(pattern)):
            if d.name.endswith("-raw"):   # 與轉換後的目錄標題相同
                continue
            for p in sorted(d.glob("*.json")):
                try:
                    title = json.loads(p.read_text(encoding="utf-8")).get("title")
                except (ValueError, OSError, AttributeError) as e:
                    notes.append(f"警告：讀不到 {p.relative_to(root)}（{type(e).__name__}）")
                    continue
                if isinstance(title, str):
                    titles.add(title)
    titles.discard("")
    if not titles:
        notes.append("略過標題檢查：本機沒有 data/ 或 data-prohibited.csv")
    return titles, notes


def git(*args, cwd):
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def tracked_lines(root):
    """所有已追蹤檔案在 index 裡的內容（工作目錄裡被刪掉或改過的檔案，仍以 index 為準）。"""
    paths = [p for p in git("ls-files", "-z", cwd=root).split("\0") if p]
    lines, skipped = [], 0
    for p in paths:
        blob = subprocess.run(["git", "show", f":{p}"], cwd=root, check=True, capture_output=True).stdout
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        lines += [(p, i, line) for i, line in enumerate(text.splitlines(), 1)]
    return paths, lines, skipped


def main(argv=None, cwd=None):
    argv = sys.argv[1:] if argv is None else argv
    root = git("rev-parse", "--show-toplevel", cwd=cwd or os.getcwd()).strip()
    skipped = 0
    if "--all" in argv:
        paths, lines, skipped = tracked_lines(root)
    else:
        paths = [p for p in git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", cwd=root).split("\0") if p]
        lines = added_lines(git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR",
                                "--src-prefix=a/", "--dst-prefix=b/", cwd=root))
    titles, notes = load_titles(root)
    problems = path_violations(paths) + content_violations(lines, titles)
    for note in notes:
        print(note, file=sys.stderr)
    if problems:
        print(f"precommit_scan：發現 {len(problems)} 個問題，已停止。", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        print("請把這些內容移除，或改成虛構資料，然後重新 commit。", file=sys.stderr)
        return 1
    extra = f"、略過 {skipped} 個二進位檔" if skipped else ""
    print(f"precommit_scan：OK（{len(paths)} 個檔案、{len(lines)} 行、對照 {len(titles)} 個真實標題{extra}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
