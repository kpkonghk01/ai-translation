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
from .textrules import squash


def _hash(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoadedTermList:
    """載入並驗證過的 Term List。沒有設 Term List 時用 unused()：沒有條目、hash 是 None，不需要到處判斷有沒有。

    兩個 hash 管不同的事，都以驗證後的內容計算，只換排版不會變：
    prompt_hash 只含模型看到的部分（原文詞與各 Direction 的譯法），記入每個 Run；
    check_hash 含 check 用到的全部（嚴重度、類別等；只有備註不算），記入 check 的紀錄。
    所以只改嚴重度，只要重跑 check，不必重新翻譯；改了原文詞或譯法，就是另一份 prompt。"""
    entries: tuple[TermEntry, ...]
    prompt_hash: str | None
    check_hash: str | None

    @classmethod
    def unused(cls) -> "LoadedTermList":
        return cls((), None, None)

    @classmethod
    def from_model(cls, terms: TermList) -> "LoadedTermList":
        seen_by_the_model = [[e.source, {d.value: e.translations[d] for d in sorted(e.translations)}] for e in terms.terms]
        used_by_check = terms.model_dump(mode="json", exclude={"terms": {"__all__": {"note"}}})
        return cls(tuple(terms.terms), _hash(seen_by_the_model), _hash(used_by_check))

    @property
    def is_used(self) -> bool:
        return self.prompt_hash is not None


def load_term_list(path: str) -> LoadedTermList:
    return LoadedTermList.from_model(read_model(path, TermList))


def entries_in(entries: Iterable[TermEntry], source_text: str, direction: Direction) -> list[TermEntry]:
    """原文出現的條目（依 Term List 的順序）。原文偶有多餘的空白，比對時忽略；但逐行比對：
    多個 tag、圖說或相關文章標題是以換行連成一段的，不能讓兩個項目的頭尾接起來湊成一個詞。
    沒有這個 Direction 的譯法的條目不算。"""
    lines = [squash(line) for line in source_text.split("\n")]
    return [e for e in entries if e.applies_to(direction) and any(squash(e.source) in line for line in lines)]


def matched_text(source_text: str, term: str) -> str:
    """這個詞在原文裡實際的寫法（可能夾著多餘的空白）：Finding 的原文片段要是原文的逐字子字串。"""
    pattern = re.compile(r"\s*".join(re.escape(c) for c in squash(term)))
    for line in source_text.split("\n"):
        found = pattern.search(line)
        if found:
            return found.group(0)
    return term


def has_allowed_rendering(translated_text: str, allowed: Iterable[str]) -> bool:
    """譯文含有任何一個允許的譯法（不分大小寫，連續空白視為一個）。用子字串比對：
    寧可漏報也不誤報，因為每個 Finding 都會扣分。"""
    def normalise(text: str) -> str:
        return re.sub(r"\s+", " ", text).casefold()
    haystack = normalise(translated_text)
    return any(normalise(a) in haystack for a in allowed)
