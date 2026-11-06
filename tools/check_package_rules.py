"""評核框架 package 的兩項機械式檢查。由 tools/check.sh 呼叫。

用法：python3 -I tools/check_package_rules.py [package 目錄]    （預設 src/translation_eval）

  1. 只有 transport.py 可以 import 網絡相關的庫：這樣所有對外呼叫都必經那個可替換的 transport。
  2. package 的程式碼和檔案不可提及 .env 或 dotenv：key 只從環境變數讀，載入 .env 是 Pan 在終端機的事。

違反時列出「檔案:行號」，以非 0 結束。無法解析的檔案也算違反，不略過。
"""
import ast
import re
import sys
from pathlib import Path

TRANSPORT_MODULE = "transport.py"
NETWORK_MODULES = (
    "socket", "socketserver", "ssl", "http.client", "http.server", "urllib.request", "urllib.error", "urllib3",
    "requests", "httpx", "aiohttp", "websocket", "websockets", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc",
)
_DOTENV = re.compile(r"dotenv|(?<![\w])\.env(?![A-Za-z0-9_])", re.IGNORECASE)


def is_network_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in NETWORK_MODULES)


def import_violations(path: Path, source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"{path}:{e.lineno or 0}：無法解析，沒辦法檢查 import"]
    out = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]   # from urllib import request
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if called in ("__import__", "import_module"):
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names = [arg.value]
                else:
                    out.append(f"{path}:{node.lineno}：動態 import 的名稱不是字面字串，沒辦法檢查")
        for name in names:
            if is_network_module(name):
                out.append(f"{path}:{node.lineno}：只有 {TRANSPORT_MODULE} 可以 import 網絡相關的庫（{name}）")
    return out


def dotenv_violations(path: Path, text: str) -> list[str]:
    return [f"{path}:{n}：package 不可提及 .env 或 dotenv" for n, line in enumerate(text.splitlines(), 1) if _DOTENV.search(line)]


def violations(package: Path) -> tuple[list[str], int]:
    out, n_files = [], 0
    for path in sorted(p for p in package.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        n_files += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            out.append(f"{path}：不是 UTF-8 文字檔，沒辦法檢查")
            continue
        out += dotenv_violations(path, text)
        if path.suffix == ".py" and not (path.name == TRANSPORT_MODULE and path.parent == package):
            out += import_violations(path, text)
    return out, n_files


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    package = Path(argv[0]) if argv else Path(__file__).resolve().parent.parent / "src" / "translation_eval"
    if not package.is_dir():
        print(f"check_package_rules：找不到 package 目錄 {package}", file=sys.stderr)
        return 1
    found, n_files = violations(package)
    if found:
        print(f"check_package_rules：發現 {len(found)} 個問題：", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"check_package_rules：OK（{n_files} 個檔案）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
