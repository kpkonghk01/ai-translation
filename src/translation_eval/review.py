"""review：產生本機的審閱清單（Markdown，寫在 data/）。

用法：
  uv run --locked python -I -m translation_eval review --config <實驗設定> [--data-dir data]

必看的項目全部列出；其餘項目（通過的事實、一致的 Finding、Deterministic Finding）以固定種子抽出一部分供抽查，比例與種子在實驗設定的 review。
抽查用「種子加項目 key 的 hash」決定一項抽不抽：同一個輸入再跑，抽出的結果相同；之後有更多結果進來，已經抽到的不會變。
每個項目有 key 和輸入的 hash，審閱者把決定寫進同一個目錄的 review-decisions.json（格式見清單開頭）。已有決定的項目，清單裡標出決定。
決定指向不存在的項目或輸入已經改變的，指令失敗並列出。清單含原文和譯文，只寫在 data/；終端只印數量。

聚焦清單（review --approach <id>... --status <狀態>...）：只列這些模型、這些狀態的必看項目，編號，寫成另一個檔案 review-focus.md（完整的
review.md 不動）。每一項用白話寫出：要保留的事實、原文、譯文、Judge 為什麼判它被寫輕，審閱者用編號回報哪幾項不算。
"""
import hashlib
from collections import Counter

from .articles import load_article
from .check import require_checked
from .config import Experiment, load_experiment
from .errors import InputError
from .ground_truth import load_ground_truth
from .review_decisions import Decisions, check_decisions, load_decisions
from .report_text import DIRECTION_LABELS, WEAKENING_LABELS, group_label
from .review_items import ItemKind, ItemStatus, MUST_SEE, ReviewItem, build_items
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


def focus_item_lines(number: int, item: ReviewItem, names: dict[str, str], group: str, decisions: Decisions) -> list[str]:
    """聚焦清單的一項：用白話寫出什麼地方出了問題。第一行 lines 是「事實（種類）：…」，其餘是 Judge 兩次的判定、引用與理由。"""
    where = f"{names[item.run.approach]} ・ {DIRECTION_LABELS.get(item.run.direction, item.run.direction)} ・ {group_label(group)}"
    head, *judged = item.lines
    if item.kind is ItemKind.FACT:   # 第一行是「事實（種類）：…」，其餘是 Judge 兩次的判定、引用與理由
        body = [f"- 要保留的事實：{head}", f"- 原文：{_quote(item.source_text)}", f"- 譯文：{_quote(item.translation_text)}", *[f"- {line}" for line in judged],
                f"- 出了什麼問題：{problem_text(item)}"]
    else:   # 其他種類的項目沒有「要保留的事實」：照清單原本的寫法
        body = [f"- 原文：{_quote(item.source_text)}", f"- 譯文：{_quote(item.translation_text)}", *[f"- {line}" for line in item.lines]]
    return [f"### {number} ・ {where}", "", *body, f"- 位置：{item.run.label} ・ {item.component_id}",
            f"- key：`{item.key}`；輸入 hash：`{item.input_hash}`", f"- 決定：{decision_text(decisions.get(item.key))}", ""]


FACT_PROBLEMS = {ItemStatus.UNCERTAIN: "Judge 兩次的判定不一致，沒有最終判定", ItemStatus.INVALID: "Judge 的判定格式無效，沒有最終判定",
                 ItemStatus.UNJUDGED: "Judge 沒能評這一項（被擋或出錯）"}


def problem_text(item: ReviewItem) -> str:
    """一條事實出了什麼問題：被判淡化的寫淡化類型的白話說明；還沒有最終判定的寫為什麼沒有。"""
    if item.status in FACT_PROBLEMS:
        return FACT_PROBLEMS[item.status]
    if item.weakening is None:
        return "被判淡化，但兩次判定的類型不一致，沒有單一的類型（或譯文缺少這一段，事實直接判為不見）"
    return f"{item.weakening.value.upper()}：{WEAKENING_LABELS[item.weakening]}"


def render_focus(experiment_id: str, picked: list[ReviewItem], must_total: int, names: dict[str, str], group_of: dict[str, str],
                 conditions: str, decisions: Decisions) -> str:
    decided = sum(1 for i in picked if decisions.get(i.key))
    lines = [f"# 審閱清單（聚焦）：{experiment_id}", "", f"- 條件：{conditions}",
             f"- 符合的項目 {len(picked)} 項（必看共 {must_total} 項）；已有決定 {decided}",
             "- 怎麼看：每一項先看「要保留的事實」，再對照原文與譯文，看譯文有沒有把它寫輕或漏掉；Judge 的引用與理由在下面。",
             "  用編號回報哪幾項「不算淡化」（或判成別的），決定寫在同一個目錄的 `review-decisions.json`。", ""]
    if not picked:
        return "\n".join(lines + ["沒有符合的項目。"]) + "\n"
    for number, item in enumerate(sorted(picked, key=lambda i: (i.run.label, i.component_id, i.key)), 1):
        lines += focus_item_lines(number, item, names, group_of.get(item.run.article_id, "all"), decisions)
    return "\n".join(lines).rstrip("\n") + "\n"


def focus_items(experiment: Experiment, must: list[ReviewItem], approaches: list[str], statuses: list[str]) -> list[ReviewItem]:
    known = {p.id for p in experiment.approaches} | {b.id for b in experiment.config.baselines}
    if unknown := sorted(set(approaches) - known):
        raise InputError([f"不認識的 Approach：{a}（實驗設定裡有：{'、'.join(sorted(known))}）" for a in unknown])
    return [i for i in must if (not approaches or i.run.approach in approaches) and (not statuses or i.status.value in statuses)]


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
    if args.approach or args.status:   # 聚焦清單：另一個檔案，完整的清單不動
        picked = focus_items(experiment, must, args.approach, args.status)
        names = experiment.display_names()
        group_of = {i: truth.group_of(i) for i in truth.domains} if truth else {}
        conditions = "；".join(part for part in (f"模型 {'、'.join(names.get(a, a) for a in args.approach)}" if args.approach else "",
                                                 f"狀態 {'、'.join(args.status)}" if args.status else "") if part)
        out.write_review_focus(render_focus(cfg.id, picked, len(must), names, group_of, conditions, decisions))
        print(f"已寫入 {out.review_focus_path}：{len(picked)} 項（必看共 {len(must)} 項）；已有決定 {sum(1 for i in picked if decisions.get(i.key))} 條")
        return 0
    out.write_review(render(cfg.id, must, chosen, pool_size, cfg.review, decisions))
    statuses = Counter(i.status.value for i in must)
    print(f"已寫入 {out.review_path}：必看 {len(must)} 項（" + ("、".join(f"{k} {n}" for k, n in sorted(statuses.items())) or "沒有") +
          f"）、抽查 {len(chosen)} 項；已有決定 {len(decisions)} 條")
    return 0
