"""展示報告：給非技術讀者看的一頁 HTML。只放計數（模型名、組別、數量），不含文章的任何內容，也沒有文章編號。

用法：
  uv run --locked python -I -m translation_eval showcase --config <實驗設定> [--data-dir data]

寫到 <data-dir>/experiments/<實驗 id>/showcase.html。隨時可以執行：還沒跑完時頁面頂部會寫「仍在進行」，數字是中途的。
讀的是 runs.jsonl，不需要先 check；Judge 的結果不放在這裡（只說有沒有評核紀錄），完整的數字在 report.md。
模型名用 Approach 設定的 display_name（Experiment.display_names）；沒有就用 model（不用代號 a、f：讀的人看不懂）。baseline 沒有顯示名稱時退回它的 id，所以要寫。

主表只看完整文章、每篇每個方向的第 1 次：每個模型的分母一樣，才比得出來。重複跑的次數和只送標題與正文的版本，另放一張表。
沒有任何外部資源（字型、script、網址）：離線也能開，也不會把頁面內容送出去。頁面用「模型」稱 Approach：讀的人不認識 Approach。
"""
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape

from .articles import load_article
from .config import Experiment, load_experiment
from .gate_report import planned_runs
from .ground_truth import GroundTruth, load_ground_truth
from .report_text import DIRECTION_LABELS, GROUP_LABELS, group_label, shown
from .schemas import Direction, Domain, InputVariant, Outcome, REDO_OUTCOMES, RunRecord, SampleNote
from .store import OutputDir

GROUP_NOTES = {Domain.GENERAL.value: "性罪行、暴力、衝突等社會新聞", Domain.CHINA_POLITICAL.value: "對中國政府不利的新聞"}
# 這兩個組名同時就是顯示的文字：它們不是合法的 Slug，所以不會跟 group_overrides 裡使用者取的組名撞在一起
ALL_ARTICLES, UNGROUPED = "全部文章", "沒有 ground truth 的文章"
VARIANT_LABELS = {InputVariant.FULL: "完整文章", InputVariant.MAIN_ONLY: "只送標題與正文"}
OUTCOME_LABELS = {Outcome.OK: "完成翻譯", Outcome.BLOCK: "被擋", Outcome.REFUSAL: "拒絕", Outcome.MALFORMED: "輸出格式壞",
                  Outcome.TRUNCATED: "截斷", Outcome.EMPTY: "空內容", Outcome.ERROR: "出錯"}
# 沒有拿到可用譯文、又不是 Block 或 Refusal 的結果：從 Outcome 推導，新增的結果類別不會被靜默漏掉
NO_TRANSLATION = tuple(o for o in Outcome if o not in (Outcome.OK, Outcome.BLOCK, Outcome.REFUSAL))
HONG_KONG = timezone(timedelta(hours=8))


def outcome_label(outcome: Outcome) -> str:
    return OUTCOME_LABELS.get(outcome, outcome.value)


@dataclass
class Tally:
    runs: int = 0
    outcomes: Counter = field(default_factory=Counter)

    def add(self, record: RunRecord) -> None:
        self.runs += 1
        self.outcomes[record.outcome] += 1

    def count(self, *outcomes: Outcome) -> int:
        return sum(self.outcomes[o] for o in outcomes)


def tally(records) -> Tally:
    result = Tally()
    for record in records:
        result.add(record)
    return result


def share(part: int, whole: int) -> tuple[str, str]:
    """(百分比, 分數)：分母是 0 就沒有百分比。"""
    return ("—" if whole == 0 else f"{int(100 * part / whole + 0.5)}%"), f"{part}/{whole}"


def sentence_share(part: int, whole: int) -> str:
    pct, fraction = share(part, whole)
    return f"{pct}（{fraction}）"


def cell(part: int, whole: int, kind: str, detail: str | None = None) -> str:
    """一格：百分比、分數、色條。detail（例如各項的數量）直接寫在數字下面：不靠滑鼠提示，印出來或用沒有滑鼠的環境也看得到。"""
    pct, fraction = share(part, whole)
    width = 100 * part / whole if whole else 0
    under = f'<span class="detail">{escape(detail)}</span>' if detail else ""
    return (f'<td><b>{pct}</b> <small>{fraction}</small>{under}'
            f'<span class="track"><span class="bar {kind}" style="width:{width:.0f}%"></span></span></td>')


def group_of_article(experiment: Experiment, truth: GroundTruth | None) -> dict[str, str]:
    ids = experiment.config.articles.ids
    if truth is None:
        return {i: ALL_ARTICLES for i in ids}
    return {i: truth.group_of(i) if i in truth.domains else UNGROUPED for i in ids}


def ordered_groups(groups) -> list[str]:
    known = list(GROUP_LABELS)
    return sorted(set(groups), key=lambda g: (known.index(g) if g in known else len(known), g))


def planned_for(experiment: Experiment, approach_id: str) -> int:
    """這個 Approach 該有的 Run 數：文章 × 設定裡每個方向與輸入版本的次數。"""
    cfg = experiment.config
    planned = next(p for p in experiment.approaches if p.id == approach_id)
    return len(cfg.articles.ids) * sum(planned_runs(experiment, approach_id, d, v) or 0 for d in cfg.directions for v in planned.input_variants)


def first_full_runs(records: list[RunRecord]) -> list[RunRecord]:
    return [r for r in records if r.input_variant is InputVariant.FULL and r.run_index == 1]


# ───────────── 各節 ─────────────

def status_html(experiment: Experiment, records: list[RunRecord]) -> str:
    planned = sum(planned_for(experiment, p.id) for p in experiment.approaches)
    failed = [r for r in records if r.outcome in REDO_OUTCOMES]
    redo = sum(1 for r in failed if r.redone_from is None)   # 還沒重做過的：重做之後數字會變
    stuck = len(failed) - redo   # 重做過仍然沒有結果（例如 provider 對這個輸入每次都回空內容）：再做也不會變，算在「其他未完成」
    done = len(records) - redo   # 還沒重做的出錯或空內容 Run 還沒有結果：不算完成
    redo_note = f"另有 {redo} 個 Run 出錯或空內容，尚待重做；重做後數字會再變。"
    if len(records) < planned:
        text, kind = f"仍在進行：已完成 {done}／{planned} 個 Run（{int(100 * done / planned)}%，所有模型合計；各模型的數量見「測了什麼」）。以下是中途的數字，之後還會變。" + (redo_note if redo else ""), "wait"
    elif redo:
        text, kind = f"已全部跑過一遍，但有 {redo} 個 Run 出錯或空內容，尚待重做；重做後數字會再變。", "wait"
    else:
        text, kind = f"已全部跑完：{len(records)} 個 Run。", "ok"
    if stuck:
        text += f"其中 {stuck} 個 Run 重做過仍然沒有結果（出錯或空內容），算在「其他未完成」。"
    return f'<p class="status {kind}">{escape(text)}</p>'


def summary_items(rows: list[tuple[str, Tally]]) -> list[str]:
    """「先看結論」：只陳述表裡的數字，不下判斷。rows：(名稱, 完整文章第 1 次的結果)。回傳純文字，逸出在輸出時才做。"""
    rows = [(name, t) for name, t in rows if t.runs]
    if not rows:
        return []

    def highest(count) -> list[str]:
        top = max(count(t) / t.runs for _, t in rows)
        return [f"{name}：{sentence_share(count(t), t.runs)}" for name, t in rows if top and count(t) / t.runs == top]

    blocked = lambda t: t.count(Outcome.BLOCK)  # noqa: E731
    refused = lambda t: t.count(Outcome.REFUSAL)  # noqa: E731
    unfinished = lambda t: t.count(*NO_TRANSLATION)  # noqa: E731
    items = []
    if any(blocked(t) for _, t in rows):
        items.append("被擋最多的是" + "、".join(highest(blocked)))
        if unblocked := [name for name, t in rows if not blocked(t)]:
            items.append("完全沒被擋：" + "、".join(unblocked))
    else:
        items.append("沒有任何模型被擋")
    items.append("拒絕翻譯（Refusal）最多的是" + "、".join(highest(refused)) if any(refused(t) for _, t in rows) else "沒有任何模型拒絕翻譯（Refusal）")
    if any(unfinished(t) for _, t in rows):
        items.append("其他未完成最多的是" + "、".join(highest(unfinished)))
    return items


def what_was_tested_html(experiment: Experiment, names: dict[str, str], group_sizes: Counter) -> str:
    cfg = experiment.config
    models = []
    for p in experiment.approaches:
        formula = f"{len(cfg.articles.ids)} 篇 × {len(cfg.directions)} 個方向 × {p.runs} 次" + (f" × {len(p.input_variants)} 個輸入版本" if len(p.input_variants) > 1 else "")
        models.append(f"<li>{escape(names[p.id])}<small>共 {planned_for(experiment, p.id)} 個 Run（{escape(formula)}）</small></li>")
    groups = "".join(f"<li>{escape(group_label(g))} {group_sizes[g]} 篇" + (f"<small>（{escape(GROUP_NOTES[g])}）</small>" if g in GROUP_NOTES else "") + "</li>"
                     for g in ordered_groups(group_sizes))
    truth_note = f"<small>組別依 ground truth 的題材標記（ground truth 目前是 {escape(cfg.ground_truth.status.value)}）</small>" if cfg.ground_truth else ""
    directions = "、".join(DIRECTION_LABELS.get(d, str(d)) for d in cfg.directions)
    baselines = "".join(f"<li>對照（不經 AI）：{escape(names[b.id])}，只做{DIRECTION_LABELS.get(b.direction, str(b.direction))}</li>" for b in cfg.baselines)
    return ("<section><h2>測了什麼</h2><dl class=\"facts\">"
            f"<dt>模型</dt><dd><ul>{''.join(models)}{baselines}</ul></dd>"
            f"<dt>文章</dt><dd><ul>{groups}</ul>{truth_note}</dd>"
            f"<dt>翻譯方向</dt><dd>{escape(directions)}</dd></dl></section>")


def results_html(experiment: Experiment, names: dict[str, str], records: list[RunRecord], group_of: dict[str, str]) -> str:
    groups = ordered_groups(group_of.values())
    head = "".join(f"<th>被擋（{escape(group_label(g))}）</th>" for g in groups)
    body, summary_rows = [], []
    for p in experiment.approaches:
        mine = [r for r in first_full_runs(records) if r.approach == p.id]
        overall = tally(mine)
        by_group = {g: tally(r for r in mine if group_of[r.article_id] == g) for g in groups}
        breakdown = "、".join(f"{outcome_label(o)} {overall.count(o)}" for o in NO_TRANSLATION if overall.count(o)) or None
        summary_rows.append((names[p.id], overall))
        body.append("<tr>" + f"<th scope=\"row\">{escape(names[p.id])}</th>"
                    + cell(overall.count(Outcome.OK), overall.runs, "ok")
                    + "".join(cell(by_group[g].count(Outcome.BLOCK), by_group[g].runs, "bad") for g in groups)
                    + cell(overall.count(Outcome.REFUSAL), overall.runs, "bad")
                    + cell(overall.count(*NO_TRANSLATION), overall.runs, "muted", breakdown) + "</tr>")
    items = summary_items(summary_rows)
    lines = f'<ul class="takeaways">{"".join(f"<li>{escape(i)}</li>" for i in items)}</ul>' if items else ""
    table = ('<table id="results"><caption>完整文章、每篇每個方向的第 1 次結果。分母是 Run 數（一篇文章翻成一個方向是一個 Run）。</caption>'
             f"<thead><tr><th>模型</th><th>{outcome_label(Outcome.OK)}</th>{head}<th>{outcome_label(Outcome.REFUSAL)}</th><th>其他未完成</th></tr></thead>"
             f"<tbody>{''.join(body)}</tbody></table>")
    legend = ('<dl class="legend"><dt>被擋（Block）</dt><dd>模型供應商在系統層拒絕處理這篇文章，沒有任何譯文。</dd>'
              '<dt>拒絕（Refusal）</dt><dd>模型回覆「無法翻譯」之類，而不是譯文。</dd>'
              '<dt>其他未完成</dt><dd>沒有拿到可用的譯文：空內容（供應商有回應但沒有輸出）、輸出格式壞、被截斷，或出錯（網路或供應商後端的錯誤）。'
              '各項的數量寫在數字下面。</dd></dl>')
    return f"<section><h2>先看結論</h2>{lines}</section><section><h2>結果</h2>{table}{legend}</section>"


# 例子卡的順序與說明。說明是這一種結果的意思，由程式按結果類別寫；不用 provider 回應裡的字（它可能夾著不該外傳的東西）。
EXAMPLE_ORDER = (Outcome.OK, Outcome.BLOCK, Outcome.REFUSAL, Outcome.EMPTY, Outcome.MALFORMED, Outcome.TRUNCATED, Outcome.ERROR)
EXPLANATIONS = {
    Outcome.OK: "供應商回應正常，拿到可用的譯文。",
    Outcome.REFUSAL: "模型有回應，但內容是「無法翻譯」之類，不是譯文。",
    Outcome.EMPTY: "供應商回應正常（沒有報錯），但回應裡沒有任何輸出。",
    Outcome.MALFORMED: "模型有輸出，但不是要求的格式，程式讀不成譯文。",
    Outcome.TRUNCATED: "模型輸出到長度上限就停了，譯文不完整。",
    Outcome.ERROR: "供應商或網路回傳了錯誤，不是文章內容造成的；重做可能就有結果。",
}
BLOCK_EXPLANATIONS = (("prompt:", "輸入端", "供應商在送進模型之前，就判定這篇文章屬於它不處理的內容類別，模型沒有開始翻譯。"),
                      ("output:", "翻到一半", "模型已經開始輸出，但供應商判定輸出屬於它不處理的內容類別，整份譯文作廢。"))
OTHER_BLOCK = ("", "其他", "供應商的內容審查擋下了請求，沒有任何譯文。")


def block_code(record: RunRecord) -> str | None:
    """被擋的原因代碼（例如 prompt:PROHIBITED_CONTENT）。只有單純的代碼才放出來；其他寫法不外傳。"""
    code = shown(record.reason, True)
    return None if code is None or code == shown("x y", True) else code


def example_kind(record: RunRecord) -> tuple[Outcome, str | None]:
    return record.outcome, block_code(record) if record.outcome is Outcome.BLOCK else None


def kind_text(kind: tuple[Outcome, str | None]) -> tuple[str, str]:
    """這一種結果的標題與說明（由程式按結果類別寫）。"""
    outcome, code = kind
    if outcome is Outcome.BLOCK:
        _, kind_label, explanation = next((b for b in BLOCK_EXPLANATIONS if code and code.startswith(b[0])), OTHER_BLOCK)
        return f"被擋（{kind_label}）", explanation
    return outcome_label(outcome), EXPLANATIONS.get(outcome, "")


def runs_by_kind(records: list[RunRecord]) -> dict[tuple[Outcome, str | None], list[RunRecord]]:
    """每種結果有哪些 Run：與主表同一批（完整文章、第 1 次），照 Run 紀錄的順序。每種結果的第一個就是例子。"""
    by_kind = defaultdict(list)
    for r in first_full_runs(records):
        by_kind[example_kind(r)].append(r)
    return by_kind


def ordered_kinds(by_kind: dict) -> list[tuple[Outcome, str | None]]:
    return sorted(by_kind, key=lambda k: (EXAMPLE_ORDER.index(k[0]), -len(by_kind[k]), k[1] or ""))


def example_html(kind: tuple[Outcome, str | None], runs: list[RunRecord], names: dict[str, str], approach_order: dict[str, int],
                 group_of: dict[str, str], segments_of: Callable[[RunRecord], int], extra: str = "") -> str:
    """一張例子卡：取這種結果在 Run 紀錄裡的第一個 Run，加上這一批共幾個、各模型各幾個。只有模型名、方向、組別和數量；
    extra 是呼叫者另外加的內容（含文章內容的那一頁用它放原文與譯文，這一頁不用）。"""
    outcome, code = kind
    first = runs[0]
    heading, explanation = kind_text(kind)
    facts = [f"發生在：{names[first.approach]}、{DIRECTION_LABELS.get(first.direction, first.direction)}、{group_label(group_of[first.article_id])}"]
    if code:
        facts.append(f"原因代碼 {code}")
    if outcome is Outcome.OK:
        facts.append(f"譯文有 {segments_of(first)} 個段落")
    by_model = Counter(r.approach for r in runs)
    breakdown = "、".join(f"{names[a]} {by_model[a]}" for a in sorted(by_model, key=approach_order.get))
    facts.append(f"這一批共 {len(runs)} 個 Run（{breakdown}）")
    return (f'<li class="example"><h3>{escape(heading)}</h3><p>{escape(explanation)}</p>'
            + "".join(f'<p class="meta">{escape(f)}</p>' for f in facts) + extra + "</li>")


def examples_html(experiment: Experiment, names: dict[str, str], records: list[RunRecord], group_of: dict[str, str],
                  segments_of: Callable[[RunRecord], int]) -> str:
    """每種出現過的結果各一張例子卡（與主表同一批 Run：完整文章、第 1 次），讓讀的人知道每個數字背後是什麼樣。"""
    by_kind = runs_by_kind(records)
    approach_order = {p.id: n for n, p in enumerate(experiment.approaches)}
    kinds = ordered_kinds(by_kind)
    absent = [outcome_label(o) for o in EXAMPLE_ORDER if o is not Outcome.OK and not any(k[0] is o for k in by_kind)]
    cards = "".join(example_html(k, by_kind[k], names, approach_order, group_of, segments_of) for k in kinds)
    missing = f'<p class="note">這一批沒有出現：{escape("、".join(absent))}。</p>' if absent else ""
    return ("<section><h2>例子</h2><p class=\"note\">上表每個數字背後是什麼樣：每種結果各舉一個真實的 Run，取這種結果在紀錄裡的第一個，不是挑過的。"
            f"只寫模型、方向、題材和數量，沒有文章內容。</p><ul class=\"examples\">{cards}</ul>{missing}</section>")


def stability_html(experiment: Experiment, names: dict[str, str], records: list[RunRecord]) -> str:
    rows = []
    for p in experiment.approaches:
        if p.runs == 1 and InputVariant.MAIN_ONLY not in p.input_variants:
            continue
        mine = [r for r in records if r.approach == p.id]
        full = [r for r in mine if r.input_variant is InputVariant.FULL]
        main_only = tally(r for r in mine if r.input_variant is InputVariant.MAIN_ONLY)
        articles = {r.article_id for r in full}
        blocked_articles = {r.article_id for r in full if r.outcome is Outcome.BLOCK}
        only = cell(main_only.count(Outcome.BLOCK), main_only.runs, "bad") if InputVariant.MAIN_ONLY in p.input_variants else "<td>—</td>"
        rows.append(f"<tr><th scope=\"row\">{escape(names[p.id])}</th><td>{p.runs}</td>"
                    + cell(tally(full).count(Outcome.BLOCK), len(full), "bad") + cell(len(blocked_articles), len(articles), "bad") + only + "</tr>")
    if not rows:
        return ""
    return ("<section><h2>重複跑與只送標題與正文</h2>"
            "<p class=\"note\">重複跑是看同一篇文章會不會有時被擋、有時不被擋。只送標題與正文的版本不含圖說、標籤等周邊內容，用來看這些內容會不會誘發被擋。"
            "這張表算全部次數和兩個方向。</p>"
            '<table id="stability"><thead><tr><th>模型</th><th>每篇每個方向跑幾次</th><th>完整文章：被擋的 Run</th>'
            f"<th>完整文章：至少被擋一次的文章</th><th>只送標題與正文：被擋的 Run</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>")


def details_html(experiment: Experiment, names: dict[str, str], records: list[RunRecord]) -> str:
    order = {p.id: n for n, p in enumerate(experiment.approaches)}
    directions = {d: n for n, d in enumerate(experiment.config.directions)}
    variants = {v: n for n, v in enumerate(InputVariant)}
    tallies = defaultdict(Tally)
    for r in records:
        tallies[(r.approach, r.direction, r.input_variant)].add(r)
    rows = []
    for key in sorted(tallies, key=lambda k: (order[k[0]], directions.get(k[1], len(directions)), variants[k[2]])):
        t, (approach, direction, variant) = tallies[key], key
        rows.append(f"<tr><th scope=\"row\">{escape(names[approach])}</th><td>{DIRECTION_LABELS.get(direction, escape(str(direction)))}</td>"
                    f"<td>{VARIANT_LABELS[variant]}</td><td>{t.runs}</td>" + "".join(f"<td>{t.count(o)}</td>" for o in Outcome) + "</tr>")
    heads = "".join(f"<th>{h}</th>" for h in ("模型", "方向", "輸入版本", "Run 數", *(outcome_label(o) for o in Outcome)))
    return (f'<section><details><summary>各模型、各方向、各輸入版本的完整數字</summary>'
            f'<table id="details"><thead><tr>{heads}</tr></thead><tbody>{"".join(rows)}</tbody></table></details></section>')


def not_measured_html(experiment: Experiment, judged: bool, samples: list[SampleNote]) -> str:
    if not experiment.judges and samples:
        sizes = "、".join(f"{len(s.articles)} 篇文章" for s in samples)
        text = f"這一頁的實驗沒有直接設定 Judge。淡化核對在抽樣的實驗上做（{sizes}），結果出來後另行報告；這一頁只說明翻譯有沒有成功。"
    elif not experiment.judges:
        text = "這一批沒有設定 Judge，所以還沒有淡化率（譯文有沒有把敏感內容寫輕）和譯文質素的數字。這一頁只說明翻譯有沒有成功。"
    elif not judged:
        text = "已設定 Judge，但還沒有評核結果，所以還沒有淡化率（譯文有沒有把敏感內容寫輕）和譯文質素的數字。這一頁只說明翻譯有沒有成功。"
    else:
        text = "淡化率與譯文質素由 Judge 評核。評核結果沒有放在這一頁，完整數字在 report.md（請向負責人索取）。"
    return f"<section><h2>還沒有的結果</h2><p>{escape(text)}</p></section>"


def shown_records(experiment: Experiment, records: list[RunRecord], group_of: dict[str, str]) -> list[RunRecord]:
    """展示用的 Run：目前設定裡的模型、目前設定裡的文章；baseline 不算。（展示報告的兩頁共用）"""
    approaches = {p.id for p in experiment.approaches}
    return [r for r in records if not r.is_baseline and r.approach in approaches and r.article_id in group_of]


def render(experiment: Experiment, names: dict[str, str], records: list[RunRecord], group_of: dict[str, str], judged: bool,
           samples: list[SampleNote], segments_of: Callable[[RunRecord], int]) -> str:
    hashes = {p.id: p.config_hash for p in experiment.approaches}
    shown = shown_records(experiment, records, group_of)
    left_out = sum(1 for r in records if not r.is_baseline) - len(shown)
    other_config = sum(1 for r in shown if r.config_hash != hashes[r.approach])
    warnings = []
    if other_config:
        warnings.append(f"有 {other_config} 個 Run 是用跟現在不同的設定跑的（設定在跑的途中改過）；這一頁照樣算進去。")
    if left_out:
        warnings.append(f"另有 {left_out} 個 Run 屬於目前設定裡已沒有的模型或文章，沒有列在這一頁。")
    notes = "".join(f"<p class=\"note\">{escape(w)}</p>" for w in warnings)
    updated = max(datetime.fromisoformat(r.created_at) for r in records).astimezone(HONG_KONG).strftime("%Y-%m-%d %H:%M")
    body = (f"<header><h1>AI 翻譯會不會被擋、會不會拒絕</h1>"
            "<p class=\"lede\">用同一批新聞，測幾個翻譯模型能不能把文章翻完。這一頁只看「有沒有翻出來」。</p>"
            f"{status_html(experiment, shown)}{notes}</header>"
            f"{results_html(experiment, names, shown, group_of)}"
            f"{examples_html(experiment, names, shown, group_of, segments_of)}"
            f"{what_was_tested_html(experiment, names, Counter(group_of.values()))}"
            f"{stability_html(experiment, names, shown)}"
            f"{details_html(experiment, names, shown)}"
            f"{not_measured_html(experiment, judged, samples)}"
            f"<footer><p>資料截至 {updated}（香港時間）。這一頁只有計數，不含文章內容，也沒有文章編號。實驗：{escape(experiment.config.id)}</p></footer>")
    return f'<!doctype html>\n<html lang="zh-Hant"><meta charset="utf-8"><title>AI 翻譯評測結果</title><style>{STYLE}</style><body><main>{body}</main></body></html>\n'


@dataclass(frozen=True)
class Loaded:
    """展示報告的兩個指令（showcase、showcase-text）都要的東西。"""
    experiment: Experiment
    out: OutputDir
    records: list[RunRecord]
    names: dict[str, str]
    group_of: dict[str, str]
    articles: dict


def load(args, *, with_articles: bool = False) -> Loaded:
    """with_articles：要原文時（showcase-text）一定載入文章；否則只有 ground truth 要驗證時才載入。"""
    experiment = load_experiment(args.config)
    cfg = experiment.config
    out = OutputDir(args.data_dir, cfg.id)
    records = out.require_runs()
    names = experiment.display_names()
    truth, articles = None, {}
    if cfg.ground_truth or with_articles:
        articles = {i: load_article(cfg.articles.dir, i) for i in cfg.articles.ids}
    if cfg.ground_truth:   # 組別來自 ground truth（與 Gate 的分組一樣）；它對文章驗證
        truth = load_ground_truth(cfg.ground_truth, articles)
    return Loaded(experiment, out, records, names, group_of_article(experiment, truth), articles)


def handle(args) -> int:
    loaded = load(args)
    experiment, out, records = loaded.experiment, loaded.out, loaded.records
    judged = bool(experiment.judges) and bool(out.read_judge_calls(None))
    html = render(experiment, loaded.names, records, loaded.group_of, judged, out.samples_made_from_this(),
                  lambda record: len(out.read_translation(record).components))
    out.write_showcase(html)   # 都算好才寫：中途失敗不會留下半份頁面
    print(f"已寫入 {out.showcase_path}（{len(records)} 個 Run）")
    return 0


STYLE = """
:root { --paper:#f5f7f9; --panel:#fff; --ink:#1b2430; --muted:#5a6672; --rule:#d5dce3; --ok:#2c7a4b; --bad:#b3261e; --wait:#9a6200;
  --ok-soft:#e1f1e7; --wait-soft:#f8edd8; --track:#e6ebf0; }
body { margin:0; background:var(--paper); color:var(--ink); font:15px/1.75 "PingFang TC","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif; }
main { max-width:940px; margin:0 auto; padding:48px 16px 72px; display:grid; gap:40px; }
h1 { margin:0; font-size:2rem; line-height:1.3; } h2 { margin:0 0 12px; font-size:1.25rem; }
section, header { display:grid; gap:12px; min-width:0; } p, ul, dl { margin:0; } ul { padding:0; list-style:none; }
.lede { font-size:1.05rem; } .note, small, caption, footer { color:var(--muted); font-size:.875rem; }
li small { margin-left:8px; }
.status { padding:10px 14px; border-radius:6px; font-weight:500; }
.status.wait { background:var(--wait-soft); color:var(--wait); } .status.ok { background:var(--ok-soft); color:var(--ok); }
.takeaways { display:grid; gap:6px; padding:16px 20px; background:var(--panel); border:1px solid var(--rule); border-radius:8px; }
.takeaways li::before { content:"・"; color:var(--muted); }
table { width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--rule); }
caption { text-align:left; padding:0 0 8px; } th, td { padding:10px 12px; text-align:left; border-bottom:1px solid var(--rule); vertical-align:top; }
thead th { font-size:.8125rem; color:var(--muted); font-weight:500; } tbody th { font-weight:700; }
td b { font-size:1.05rem; font-variant-numeric:tabular-nums; } td small { margin-left:4px; font-variant-numeric:tabular-nums; }
.track { display:block; height:6px; margin-top:6px; background:var(--track); border-radius:3px; overflow:hidden; }
.detail { display:block; margin-top:2px; color:var(--muted); font-size:.8125rem; }
.examples { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:14px; }
.example { background:var(--panel); border:1px solid var(--rule); border-radius:8px; padding:14px 16px; display:grid; gap:6px; align-content:start; }
.example h3 { margin:0; font-size:1rem; } .meta { color:var(--muted); font-size:.8125rem; }
.bar { display:block; height:100%; } .bar.ok { background:var(--ok); } .bar.bad { background:var(--bad); } .bar.muted { background:var(--muted); }
.facts { display:grid; grid-template-columns:7em 1fr; gap:10px 16px; } .facts dt, .legend dt { font-weight:700; } .facts dd, .legend dd { margin:0; }
.legend { display:grid; gap:4px; font-size:.9rem; } summary { cursor:pointer; font-weight:700; margin-bottom:12px; }
footer { border-top:1px solid var(--rule); padding-top:16px; display:grid; gap:6px; }
"""
