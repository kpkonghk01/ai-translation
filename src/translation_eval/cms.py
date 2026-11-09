"""import-cms：把 CMS 匯出轉成與網上文章相同結構的文章檔，可以放進實驗設定翻譯，也可以產生方案 E 的 baseline。

用法：
  uv run --locked python -I -m translation_eval import-cms --csv <CMS 匯出> [--out data/cms-articles]

CMS 匯出是 CSV：每列是 session id 和對話紀錄（JSON），取紀錄中第一個 user turn 的文字（可能分成幾個 part）當文章。
session id 就是文章 id；meta.source 記為 cms。轉換只保留已知的欄位。原文中看得見的文字必須都出現在轉換結果裡
（與網上文章的 verify 同一個規則）：不認識的欄位若有文字，指令失敗並列出欄位名，不會靜默丟掉；沒有文字的（數字等）才略過。
不認識的 Component type 同樣失敗並列出。全部驗證通過才寫出，所以失敗時不會留下一半的檔案。
"""
import csv
import json
import os
import sys

from pydantic import TypeAdapter, ValidationError

from .articles import KNOWN_TYPES, check_article, kept_fields
from .errors import InputError
from .schemas import Article, Slug

TOP_LEVEL_KEPT = ("title", "mainChannel", "tags", "components")
_SESSION_ID = TypeAdapter(Slug)


def _has_text(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_text(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_text(v) for v in value)
    return False


def _read_rows(csv_path: str) -> list[list[str]]:
    csv.field_size_limit(sys.maxsize)
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            return list(csv.reader(f))
    except OSError as e:
        raise InputError(f"{csv_path}：讀不到檔案（{type(e).__name__}）") from e
    except csv.Error as e:
        raise InputError(f"{csv_path}：不是合法的 CSV（{e}）") from e


def _first_user_article(row_number: int, history_text: str) -> dict:
    """對話紀錄裡第一個 user turn 的文字（part 接起來）解析成的 JSON 物件。"""
    where = f"第 {row_number} 列"
    try:
        history = json.loads(history_text)
    except json.JSONDecodeError as e:
        raise InputError(f"{where}：對話紀錄不是合法的 JSON（第 {e.lineno} 行）") from e
    turn = next((t for t in history if isinstance(t, dict) and t.get("role") == "user"), None) if isinstance(history, list) else None
    parts = turn.get("parts") if turn else None
    if not isinstance(parts, list):
        raise InputError(f"{where}：對話紀錄裡沒有 user turn")
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and isinstance(p.get("text", ""), str))
    try:
        article = json.loads(text)
    except json.JSONDecodeError as e:
        raise InputError(f"{where}：user turn 的文字不是合法的 JSON（第 {e.lineno} 行）") from e
    if not isinstance(article, dict):
        raise InputError(f"{where}：user turn 的 JSON 不是物件")
    return article


def convert(session_id: str, raw: dict) -> dict:
    """只保留已知的欄位。被丟掉的欄位若有文字就失敗：看得見的文字都必須出現在轉換結果裡。"""
    problems = [f"文章 {session_id}：頂層欄位 {key} 的文字沒有出現在轉換結果，不認識這個欄位"
                for key in raw if key not in TOP_LEVEL_KEPT and _has_text(raw[key])]
    components = raw.get("components")
    if not isinstance(components, list):
        raise InputError(f"文章 {session_id}：沒有 components 清單")
    kept_components = []
    for index, component in enumerate(components, 1):
        if not isinstance(component, dict) or not isinstance(component.get("id"), str) or not isinstance(component.get("type"), str):
            raise InputError(f"文章 {session_id}：第 {index} 個 Component 沒有 id 或 type")
        if component["type"] not in KNOWN_TYPES:   # 不認識的 type 由 check_article 列出；沒辦法判斷它的欄位，不另外報
            kept_components.append({"id": component["id"], "type": component["type"]})
            continue
        names = {"id", "type", *kept_fields(component["type"])}
        kept_components.append({k: v for k, v in component.items() if k in names})
        problems += [f"文章 {session_id}：{component['id']} 的欄位 {k} 的文字沒有出現在轉換結果，不認識這個欄位"
                     for k, v in component.items() if k not in names and _has_text(v)]
    if problems:
        raise InputError(problems)
    converted = {k: raw[k] for k in TOP_LEVEL_KEPT if k in raw}
    converted["components"] = kept_components
    try:
        article = Article.model_validate(converted)
    except ValidationError as e:
        raise InputError([f"文章 {session_id}：{'.'.join(str(p) for p in err['loc'])}：{err['msg']}" for err in e.errors()]) from e
    check_article(article, session_id)
    converted["meta"] = {"source": "cms", "articleId": session_id, "unknown": [], "warnings": []}
    return converted


def handle(args) -> int:
    rows = _read_rows(args.csv)
    problems, converted = [], {}
    for row_number, row in enumerate(rows, 1):
        try:
            if len(row) < 2:
                raise InputError(f"第 {row_number} 列：欄位不足（要有 session id 和對話紀錄）")
            session_id = row[0]
            try:
                _SESSION_ID.validate_python(session_id)
            except ValidationError as e:
                raise InputError(f"第 {row_number} 列：session id 不能當檔名用") from e
            if session_id in converted:
                raise InputError(f"第 {row_number} 列：session id 重覆：{session_id}")
            converted[session_id] = convert(session_id, _first_user_article(row_number, row[1]))
        except InputError as e:
            problems += e.problems
    if not rows:
        problems.append(f"{args.csv}：沒有任何一列")
    if problems:
        raise InputError(problems)
    os.makedirs(args.out, exist_ok=True)
    for session_id, article in converted.items():
        with open(os.path.join(args.out, f"{session_id}.json"), "w", encoding="utf-8") as f:
            json.dump(article, f, ensure_ascii=False, indent=1)
    print(f"轉換 {len(converted)} 篇 → {args.out}")
    return 0
