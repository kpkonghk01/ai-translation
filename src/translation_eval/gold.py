"""Gold set：已知答案的項目，用來量度 Judge 的準確度。讀入、驗證、列出數量、抽樣給 Pan 審閱。

用法：
  uv run --locked python -I -m translation_eval gold check  --config <實驗設定>             驗證，並列出各類別、Direction、domain 的數量
  uv run --locked python -I -m translation_eval gold sample --config <實驗設定> [--count 30] [--seed 1] [--data-dir data]
                                                                                           以固定種子抽項目，渲染成 Markdown 給 Pan 審閱

驗證（任何一項不合就以非 0 結束，不寫出任何東西；錯誤只列項目 id 與原因，不回顯文字）：預期 Finding 的片段是原文或譯文的逐字子字串
（連續空白忽略，與 Judge 輸出的驗證同一把尺）；facts 指向的 ground truth 的 Sensitive sentence 和事實都要存在，原文要含那句的 anchor；
各類別的規則在 schema（clean 沒有預期的 Finding、variant 至少一條預期失敗的事實等）。
"""
import hashlib
from collections import Counter
from dataclasses import dataclass

from .articles import load_article
from .config import Experiment, load_experiment
from .errors import InputError
from .ground_truth import GroundTruth, Sentence, fact_key, input_hash, load_ground_truth
from .loading import read_model
from .schemas import GoldCategory, GoldFile, GoldItem
from .store import OutputDir
from .textrules import squash

TARGET_MIX = "約 50／25／25（annotated／variant／clean）"   # Q14：只用來提醒，不當作合格條件


@dataclass(frozen=True)
class GoldSet:
    items: tuple[GoldItem, ...]
    hash: str   # 檔案內容的 hash：報告開頭用


def item_sentences(item: GoldItem, truth: GroundTruth | None) -> list[Sentence]:
    """項目指向的 Sensitive sentence（沒有 facts 的項目沒有）。"""
    if item.ground_truth is None or truth is None:
        return []
    return truth.sentences_in(item.ground_truth.article_id, item.ground_truth.component_id)


def item_hash(item: GoldItem, truth: GroundTruth | None) -> str:
    """項目內容（含它指向的事實與 anchor）的 hash：項目或 ground truth 改了，已有的校準請求就過期。"""
    return hashlib.sha256(f"{item.model_dump_json()}|{input_hash(item_sentences(item, truth))}".encode("utf-8")).hexdigest()


def _span_problems(item: GoldItem) -> list[str]:
    problems = []
    for n, finding in enumerate(item.findings, 1):
        if not finding.source_span.strip() and not finding.target_span.strip():
            problems.append(f"預期的 Finding {n}：兩個片段都是空的")
        if finding.source_span.strip() and squash(finding.source_span) not in squash(item.source):
            problems.append(f"預期的 Finding {n}：原文片段不是逐字子字串")
        if finding.target_span.strip() and squash(finding.target_span) not in squash(item.translation):
            problems.append(f"預期的 Finding {n}：譯文片段不是逐字子字串")
    return problems


def _fact_problems(item: GoldItem, truth: GroundTruth | None) -> list[str]:
    if not item.facts:
        return []
    if truth is None:
        return ["facts 要實驗設定有 ground_truth"]
    sentences = item_sentences(item, truth)
    if not sentences:
        return ["ground_truth 指向的 Component 在 ground truth 裡沒有 Sensitive sentence（或不在實驗內）"]
    known = {f.key for s in sentences for f in s.facts}
    problems = [f"預期的事實 {n}：不是 ground truth 這個 Component 的事實" for n, fact in enumerate(item.facts, 1) if fact_key(fact.fact) not in known]
    keys = [fact_key(f.fact) for f in item.facts]
    if len(set(keys)) != len(keys):
        problems.append("預期的事實有重覆")
    if not any(squash(s.anchor) in squash(item.source) for s in sentences):
        problems.append("原文不含 ground truth 那句的 anchor")
    return problems


def load_gold_set(path: str, truth: GroundTruth | None) -> GoldSet:
    gold = read_model(path, GoldFile)
    problems, ids = [], Counter(i.id for i in gold.items)
    problems += [f"Gold set：項目 id 重覆：{i}" for i, n in ids.items() if n > 1]
    for item in gold.items:
        problems += [f"Gold set 項目 {item.id}：{p}" for p in [*_span_problems(item), *_fact_problems(item, truth)]]
    if problems:
        raise InputError(problems)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return GoldSet(tuple(gold.items), digest)


def count_lines(gold: GoldSet) -> list[str]:
    """各類別、Direction、domain 的數量；只有數字。"""
    def counted(values) -> str:
        return "、".join(f"{k} {n}" for k, n in sorted(Counter(values).items())) or "無"
    by_category = Counter(i.category.value for i in gold.items)
    return [f"Gold set：{len(gold.items)} 條（hash {gold.hash}）",
            f"- 類別：{'、'.join(f'{c.value} {by_category[c.value]}' for c in GoldCategory)}（目標{TARGET_MIX}）",
            f"- Direction：{counted(i.direction.value for i in gold.items)}",
            f"- domain：{counted(i.domain.value for i in gold.items)}",
            f"- 類別 × Direction × domain：{counted(f'{i.category.value}/{i.direction.value}/{i.domain.value}' for i in gold.items)}"]


def sample_items(items: tuple[GoldItem, ...], count: int, seed: int) -> list[GoldItem]:
    """固定種子抽 count 條：依「種子加項目 id」的 hash 排序取前面的。同一個 Gold set 再跑結果相同；加了新項目，已經抽到的多半還在。"""
    ranked = sorted(items, key=lambda i: hashlib.sha256(f"{seed}:{i.id}".encode("utf-8")).hexdigest())
    return sorted(ranked[:count], key=lambda i: i.id)


def _quote(text: str) -> str:
    return "\n  > " + text.replace("\n", "\n  > ")


def render_sample(sample: list[GoldItem], total: int, seed: int) -> str:
    """給 Pan 審閱的 Markdown：每條有原文、譯文、前後文和預期答案。含原文與譯文，只寫在 data/。"""
    lines = [f"# Gold set 抽樣（{len(sample)}／{total} 條，種子 {seed}）", "",
             "怎樣審閱：每一條看預期答案有沒有錯（Finding 的 Dimension、Severity 和片段；事實的 verdict 與淡化類型）。有問題請引用項目 id。", ""]
    for item in sample:
        lines += [f"## `{item.id}` ・ {item.category.value} ・ {item.direction.value} ・ {item.domain.value}", "", f"- 原文（{item.component_type}）：{_quote(item.source)}",
                  f"- 譯文：{_quote(item.translation)}"]
        for name, context in (("前文", item.context_before), ("後文", item.context_after)):
            if context:
                lines.append(f"- {name}（原文）：{_quote(context.source)}")
                lines.append(f"- {name}（譯文）：{_quote(context.translation)}")
        for finding in item.findings:
            lines.append(f"- [ ] 預期的 Finding：{finding.dimension.value}／{finding.severity.value}；原文片段「{finding.source_span}」；譯文片段「{finding.target_span}」"
                         + (f"；{' '.join(finding.note.split())}" if finding.note else ""))
        for fact in item.facts:
            kind = f"（{fact.weakening_type.value}）" if fact.weakening_type else ""
            lines.append(f"- [ ] 預期的事實：{' '.join(fact.fact.split())} → {fact.verdict.value}{kind}")
        if not item.findings and not item.facts:
            lines.append("- 預期：沒有任何問題")
        if item.note:
            lines.append(f"- 備註：{' '.join(item.note.split())}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def load_gold_for(config_path: str) -> tuple[GoldSet, Experiment, GroundTruth | None]:
    """實驗設定的 Gold set，連同驗證它需要的 ground truth（設定有的話）。沒有 calibration 設定就失敗。"""
    experiment = load_experiment(config_path)
    cfg = experiment.config
    if cfg.calibration is None:
        raise InputError("實驗設定沒有 calibration（Gold set 的位置）")
    truth = None
    if cfg.ground_truth:
        truth = load_ground_truth(cfg.ground_truth, {i: load_article(cfg.articles.dir, i) for i in cfg.articles.ids})
    return load_gold_set(cfg.calibration.gold_set, truth), experiment, truth


def handle_check(args) -> int:
    gold, _, _ = load_gold_for(args.config)
    print("\n".join(count_lines(gold)))
    return 0


def handle_sample(args) -> int:
    gold, experiment, _ = load_gold_for(args.config)
    sample = sample_items(gold.items, args.count, args.seed)
    out = OutputDir(args.data_dir, experiment.config.id)
    out.write_gold_sample(render_sample(sample, len(gold.items), args.seed))
    print(f"已寫入 {out.gold_sample_path}：{len(sample)}／{len(gold.items)} 條（種子 {args.seed}）")
    return 0
