"""Ground truth：讀入現有格式（articles → items → must_retain），驗證，並整理成 Sensitive sentence 與 Must-retain fact。

驗證規則與 tools/render_ground_truth.py 相同：文章要找得到、Component id 要在該篇內、anchor 要是該 Component 文字的逐字子字串
（不忽略空白）、原文瑕疵（source_defects）也一樣、kind 與 domain 在允許清單內、每個 item 至少一條事實、同一篇不能出現兩次；另外要求 Component 是要翻譯的類型，
而且同一個 Component 內沒有兩條一樣的事實（事實的 key 是文字的 hash，兩條一樣的就分不開）。
錯誤只列文章 id、Component id 和原因，不回顯 anchor 或事實的文字。不在實驗內的文章略過。

事實的 key 由文章 id、Component id 和事實文字的 SHA-256 組成：事實文字改了，舊的 verdict 就對不上，要重判。
"""
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from functools import cached_property

from .articles import text_of
from .errors import InputError
from .loading import read_model
from .schemas import Article, Domain, FactKind, GroundTruthRef, GtFile


def fact_key(text: str) -> str:
    """事實 key 的最後一段。完整的 key 是（文章 id、Component id、這一段）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Fact:
    key: str
    kind: FactKind
    text: str


@dataclass(frozen=True)
class Sentence:
    """一個 Sensitive sentence（ground truth 的 item），Gate 的計算單位。"""
    article_id: str
    component_id: str
    anchor: str
    facts: tuple[Fact, ...]

    @property
    def id(self) -> tuple[str, str, str]:
        return self.article_id, self.component_id, hashlib.sha256(self.anchor.encode("utf-8")).hexdigest()


def input_hash(sentences: list[Sentence]) -> str:
    """送給 Judge 的事實與 anchor 的 hash：ground truth 改了，已有的紀錄就過期。"""
    content = [[s.anchor, [[f.kind.value, f.text] for f in s.facts]] for s in sentences]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GroundTruth:
    ref: GroundTruthRef
    sentences: tuple[Sentence, ...]   # 只有實驗內的文章
    domains: dict[str, Domain]        # 實驗內有 ground truth 的文章 → domain
    hash: str                         # 所有檔案內容的 hash，報告開頭用
    outside_experiment: int           # 不在實驗內而略過的文章數

    def group_of(self, article_id: str) -> str:
        """Gate 的分組：設定裡的覆寫，否則是文章的 domain。"""
        return self.ref.group_overrides.get(article_id) or self.domains[article_id].value

    @cached_property
    def _by_component(self) -> dict[tuple[str, str], list[Sentence]]:
        grouped: dict[tuple[str, str], list[Sentence]] = {}
        for s in self.sentences:
            grouped.setdefault((s.article_id, s.component_id), []).append(s)
        return grouped

    def sentences_for(self, article_id: str) -> list[Sentence]:
        return [s for s in self.sentences if s.article_id == article_id]

    def sentences_in(self, article_id: str, component_id: str) -> list[Sentence]:
        return self._by_component.get((article_id, component_id), [])


def load_ground_truth(ref: GroundTruthRef, articles: dict[str, Article]) -> GroundTruth:
    """articles：這次要用的文章（id → 文章）。ground truth 裡不在其中的文章略過。"""
    problems, digest, entries = [], hashlib.sha256(), []
    for path in ref.paths:
        entries += read_model(path, GtFile).articles
        with open(path, "rb") as f:
            digest.update(f.read())
    for article_id, n in sorted(Counter(a.id for a in entries).items()):
        if n > 1:
            problems.append(f"ground truth：文章 {article_id} 出現了 {n} 次")
    sentences, domains, outside = [], {}, 0
    for entry in entries:
        article = articles.get(entry.id)
        if article is None:
            outside += 1
            continue
        domains[entry.id] = entry.domain
        texts = {c.id: text_of(c.type, c.model_dump()) for c in article.components}
        for item in entry.items:
            where = f"ground truth：文章 {entry.id} 的 {item.component_id}"
            if item.component_id not in texts:
                problems.append(f"{where}：component_id 不在該篇")
                continue
            if texts[item.component_id] is None:
                problems.append(f"{where}：這個 Component 不是要翻譯的類型")
                continue
            if item.anchor not in texts[item.component_id]:
                problems.append(f"{where}：anchor 不是逐字子字串")
            keys = [fact_key(f.fact) for f in item.must_retain]
            sentences.append(Sentence(entry.id, item.component_id, item.anchor,
                                      tuple(Fact(k, f.kind, f.fact) for k, f in zip(keys, item.must_retain))))
        for defect in entry.source_defects:
            where = f"ground truth：文章 {entry.id} 的 {defect.component_id}（原文瑕疵）"
            if defect.component_id not in texts:
                problems.append(f"{where}：component_id 不在該篇")
            elif defect.anchor not in (texts[defect.component_id] or ""):
                problems.append(f"{where}：anchor 不是逐字子字串")
        by_component = {}
        for sentence in (s for s in sentences if s.article_id == entry.id):
            by_component.setdefault(sentence.component_id, []).extend(f.key for f in sentence.facts)
        problems += [f"ground truth：文章 {entry.id} 的 {cid}：有事實重覆（同一個 Component 內兩條一樣的事實）"
                     for cid, keys in by_component.items() if len(set(keys)) != len(keys)]
    if problems:
        raise InputError(problems)
    return GroundTruth(ref, tuple(sentences), domains, digest.hexdigest(), outside)
