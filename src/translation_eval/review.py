"""review：產生本機的審閱清單（Markdown，寫在 data/）。

用法：
  uv run --locked python -I -m translation_eval review --config <實驗設定> [--data-dir data]

必看的項目全部列出；其餘項目（通過的事實、一致的 Finding、Deterministic Finding）以固定種子抽出一部分供抽查，比例與種子在實驗設定的 review。
抽查用「種子加項目 key 的 hash」決定一項抽不抽：同一個輸入再跑，抽出的結果相同；之後有更多結果進來，已經抽到的不會變。
每個項目有 key 和輸入的 hash，審閱者把決定寫進同一個目錄的 review-decisions.json（格式見清單開頭）。已有決定的項目，清單裡標出決定。
決定指向不存在的項目或輸入已經改變的，指令失敗並列出。清單含原文和譯文，只寫在 data/；終端只印數量。
"""
import hashlib
from collections import Counter

from .articles import load_article
from .check import require_checked
from .config import load_experiment
from .ground_truth import load_ground_truth
from .review_decisions import Decisions, check_decisions, load_decisions
from .review_items import ItemKind, ItemStatus, ReviewItem, build_items
from .schemas import Outcome, ReviewConfig, ReviewDecision
from .store import OutputDir

SECTIONS = {   # (種類, 狀態) → 標題；必看的狀態在前，抽查候選的在後
    (ItemKind.FACT, ItemStatus.FAILED): "Gate 失敗的事實",
    (ItemKind.FACT, ItemStatus.UNCERTAIN): "uncertain 的事實",
    (ItemKind.FACT, ItemStatus.INVALID): "無效的事實 verdict",
    (ItemKind.FACT, ItemStatus.UNJUDGED): "Unjudged 的事實",
    (ItemKind.FINDING, ItemStatus.UNCERTAIN): "uncertain 的 Finding",
    (ItemKind.INVALID, ItemStatus.INVALID): "無效的 Judge 輸出",
    (ItemKind.UNJUDGED, ItemStatus.UNJUDGED): "Unjudged 的 Judge 請求（MQM、全文篩查）",
    (ItemKind.SCREENING, ItemStatus.SCREENED): "全文篩查的結果",
    (ItemKind.FINDING, ItemStatus.SCREENED): "Fidelity-of-force 的 Finding（MQM）",
    (ItemKind.WARNING, ItemStatus.SUSPECTED_REFUSAL): "疑似 Refusal",
    (ItemKind.WARNING, ItemStatus.WARNING): "Deterministic 警示",
    (ItemKind.FACT, ItemStatus.PASSED): "通過的事實",
    (ItemKind.FINDING, ItemStatus.CONSISTENT): "一致的 Finding",
    (ItemKind.CHECK_FINDING, ItemStatus.CHECKED): "Deterministic Finding",
}
MAX_QUOTED = 2000   # 不翻譯的 Component（例如 code）整個顯示時可能很長
EXAMPLE = ('{"decisions": [{"key": "fact-…", "input_hash": "…", "action": "override", "verdict": "Weakened", "weakening_type": "e", '
           '"note": "…", "reviewer": "Pan", "reviewed_on": "2026-11-09"}]}')


def sampled(item: ReviewItem, config: ReviewConfig) -> bool:
    """這個不是必看的項目抽不抽：種子加 key 的 hash 落在比例之內。"""
    fraction = int(hashlib.sha256(f"{config.seed}:{item.key}".encode("utf-8")).hexdigest()[:8], 16) / 2 ** 32
    return fraction < config.sample_rate


def split_must_see_and_sample(items: list[ReviewItem], config: ReviewConfig) -> tuple[list[ReviewItem], list[ReviewItem], int]:
    """(必看, 抽出的, 候選的項目數)。"""
    must = [i for i in items if i.must_see]
    pool = [i for i in items if not i.must_see]
    return must, [i for i in pool if sampled(i, config)], len(pool)


def _quote(text: str | None) -> str:
    if text is None:
        return "（沒有）"
    shown = text if len(text) <= MAX_QUOTED else text[:MAX_QUOTED] + "…（截斷）"
    return "\n  > " + shown.replace("\n", "\n  > ")


def decision_text(decision: ReviewDecision | None) -> str:
    if decision is None:
        return "（未決定）"
    changed = (f" → {decision.verdict.value}" if decision.verdict else "") + (f" → {decision.severity.value}" if decision.severity else "")
    note = f"：{' '.join(decision.note.split())}" if decision.note else ""
    return f"{decision.action.value}{changed}（{decision.reviewer}，{decision.reviewed_on.isoformat()}）{note}"


def item_lines(item: ReviewItem, decisions: Decisions) -> list[str]:
    shown = decision_text(decisions.get(item.key))
    return [f"#### `{item.key}` ・ {item.run.label} ・ {item.component_id}" + (f" ・ Judge {item.judge}" if item.judge else ""), "",
            f"- 輸入 hash：`{item.input_hash}`", f"- 原文：{_quote(item.source_text)}", f"- 譯文：{_quote(item.translation_text)}",
            *[f"- {line}" for line in item.lines], f"- 決定：{shown}", ""]


def render(experiment_id: str, must: list[ReviewItem], chosen: list[ReviewItem], pool_size: int, config: ReviewConfig, decisions: Decisions) -> str:
    decided = sum(1 for i in must if decisions.get(i.key))
    lines = [f"# 審閱清單：{experiment_id}", "",
             f"- 必看 {len(must)} 項（已有決定 {decided}）；抽查 {len(chosen)} 項（從其餘 {pool_size} 項中以固定種子 {config.seed} 抽出，比例 {config.sample_rate:g}）",
             "- 決定寫在同一個目錄的 `review-decisions.json`，key 與 hash 照下面抄；action 是 accept、reject 或 override：",
             "  事實用 accept（確認 Gate 失敗或通過的）或 override 並寫 verdict；Finding 用 accept、reject 或 override 並寫 severity；其他項目 accept 或 reject 表示看過了。",
             f"  範例：`{EXAMPLE}`", ""]
    for heading, group in (("## 必看", must), ("## 抽查", chosen)):
        unlisted = {(i.kind, i.status) for i in group} - set(SECTIONS)
        assert not unlisted, f"審閱清單沒有這些項目的小節：{unlisted}"   # 新增項目種類或狀態時，SECTIONS 要跟著加，否則項目會從清單消失
        lines += [heading, ""]
        for (kind, status), title in SECTIONS.items():
            members = sorted((i for i in group if (i.kind, i.status) == (kind, status)), key=lambda i: (i.run.label, i.component_id, i.key))
            if members:
                lines += [f"### {title}（{len(members)}）", ""]
                for item in members:
                    lines += item_lines(item, decisions)
        if not group:
            lines += ["沒有項目。", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def completion_lines(items: list[ReviewItem], decisions: Decisions) -> list[str]:
    """報告開頭的審閱完成度。抽查的項目不計入。"""
    must = [i for i in items if i.must_see]
    decided = sum(1 for i in must if decisions.get(i.key))
    actions = decisions.action_counts()
    return ["## 審閱", "",
            f"- 必看 {len(must)} 項，已有決定 {decided} 項（未決定 {len(must) - decided}）；抽查的項目不計入",
            f"- 決定共 {len(decisions)} 條（" + ("、".join(f"{a} {n}" for a, n in sorted(actions.items())) or "沒有") + "），報告重算時套用；原始的 Judge 輸出沒有改動", ""]


def handle(args) -> int:
    experiment = load_experiment(args.config)
    cfg = experiment.config
    out = OutputDir(args.data_dir, cfg.id)
    records = out.require_runs()
    rows = out.read_components()
    require_checked(experiment, out, records, rows)
    ids = cfg.articles.ids if cfg.ground_truth else sorted({r.article_id for r in records if r.outcome is Outcome.OK})
    articles = {i: load_article(cfg.articles.dir, i) for i in ids}
    truth = load_ground_truth(cfg.ground_truth, articles) if cfg.ground_truth else None
    items = build_items(experiment, out, records, rows, articles, truth)
    decisions = load_decisions(out)
    check_decisions(decisions, items)
    must, chosen, pool_size = split_must_see_and_sample(items, cfg.review)
    out.write_review(render(cfg.id, must, chosen, pool_size, cfg.review, decisions))
    statuses = Counter(i.status.value for i in must)
    print(f"已寫入 {out.review_path}：必看 {len(must)} 項（" + ("、".join(f"{k} {n}" for k, n in sorted(statuses.items())) or "沒有") +
          f"）、抽查 {len(chosen)} 項；已有決定 {len(decisions)} 條")
    return 0
