"""Term List：載入、hash，以及「原文出現哪些詞」「譯文有沒有用指定譯法」的比對。

prompt 注入和 Deterministic check 讀同一份 Term List，並用這裡同一套比對規則。
"""
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .loading import read_model
from .schemas import Direction, TermEntry, TermList


@dataclass(frozen=True)
class LoadedTermList:
    terms: TermList
    hash: str


def term_list_hash(terms: TermList) -> str:
    """以驗證後的內容算 hash：只是換排版、換行或縮排不會改變它。"""
    canonical = json.dumps(terms.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_term_list(path: str) -> LoadedTermList:
    terms = read_model(path, TermList)
    return LoadedTermList(terms=terms, hash=term_list_hash(terms))


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def entries_in(term_list: TermList | None, source_text: str, direction: Direction) -> list[TermEntry]:
    """原文出現的條目（依 Term List 的順序）。原文裡偶有多餘空白，比對時忽略所有空白；
    沒有這個 Direction 的譯法的條目不算。"""
    if term_list is None:
        return []
    squashed = _squash(source_text)
    return [e for e in term_list.terms if direction in e.translations and _squash(e.source) in squashed]


def has_allowed_rendering(translated_text: str, allowed: Iterable[str]) -> bool:
    """譯文含有任何一個允許的譯法（不分大小寫，連續空白視為一個）。用子字串比對：
    寧可漏報也不誤報，因為每個 Finding 都會扣分。"""
    haystack = _normalise(translated_text)
    return any(_normalise(a) in haystack for a in allowed)
