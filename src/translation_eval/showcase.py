"""展示報告：給非技術讀者看的一頁 HTML。只放計數（模型名、組別、數量），不含文章的任何內容，也沒有文章編號。

用法：
  uv run --locked python -I -m translation_eval showcase --config <實驗設定> [--data-dir data]

寫到 <data-dir>/experiments/<實驗 id>/showcase.html。隨時可以執行：還沒跑完時頁面頂部會寫「仍在進行」，數字是中途的。
讀的是 runs.jsonl，不需要先 check；Judge 的結果不放在這裡（只說有沒有 Judge），完整的數字在 report.md。
模型名用 Approach 設定的 display_name；沒有就用 model（不用代號 a、f：讀的人看不懂）。

主表只看完整文章、每篇每個方向的第 1 次：每個模型的分母一樣，才比得出來。重複跑的次數和只送標題與正文的版本，另放一張表。
沒有任何外部資源（字型、script、網址）：離線也能開，也不會把頁面內容送出去。
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape

from .articles import load_article
from .config import Experiment, load_experiment
from .errors import InputError
from .ground_truth import GroundTruth, load_ground_truth
from .schemas import Direction, Domain, InputVariant, Outcome, REDO_OUTCOMES, RunRecord
from .store import OutputDir

# 組別：沒有 ground truth 時全部文章一組；其他名稱（group_overrides）照原樣
GROUP_LABELS = {Domain.GENERAL.value: "一般題材", Domain.CHINA_POLITICAL.value: "中國政治題材"}
GROUP_NOTES = {Domain.GENERAL.value: "性罪行、暴力、衝突等社會新聞，含少量不敏感的對照文章",
               Domain.CHINA_POLITICAL.value: "對中國政府不利的新聞"}
ALL_ARTICLES, UNGROUPED = "all", "ungrouped"
DIRECTION_LABELS = {Direction.TRAD_TO_EN: "繁→英", Direction.TRAD_TO_SIMPLIFIED: "繁→簡"}
VARIANT_LABELS = {InputVariant.FULL: "完整文章", InputVariant.MAIN_ONLY: "只送標題與正文"}
# 沒有拿到可用譯文、又不是 Block 或 Refusal 的結果（顯示用的名稱）
NO_TRANSLATION = {Outcome.EMPTY: "空內容", Outcome.MALFORMED: "輸出格式壞", Outcome.TRUNCATED: "截斷", Outcome.ERROR: "出錯"}
HONG_KONG = timezone(timedelta(hours=8))


@dataclass
class Tally:
    runs: int = 0
    outcomes: Counter = field(default_factory=Counter)

    def add(self, record: RunRecord) -> None:
        self.runs += 1
        self.outcomes[record.outcome] += 1

    def count(self, *outcomes: Outcome) -> int:
        return sum(self.outcomes[o] for o in outcomes)


def share(part: int, whole: int) -> tuple[str, str]:
    """(百分比, 分數)：分母是 0 就沒有百分比。"""
    return ("—" if whole == 0 else f"{int(100 * part / whole + 0.5)}%"), f"{part}/{whole}"


def sentence_share(part: int, whole: int) -> str:
    pct, fraction = share(part, whole)
    return f"{pct}（{fraction}）"


def cell(part: int, whole: int, kind: str, title: str | None = None) -> str:
    pct, fraction = share(part, whole)
    width = 100 * part / whole if whole else 0
    hint = f' title="{escape(title)}"' if title else ""
    return (f'<td{hint}><b>{pct}</b> <small>{fraction}</small>'
            f'<span class="track"><span class="bar {kind}" style="width:{width:.0f}%"></span></span></td>')


def labels(experiment: Experiment) -> dict[str, str]:
    """代號 → 給人看的名稱。兩列同名（沒有顯示名稱而 model 相同，或名稱剛好等於別人的 model）會分不出來，所以停止。"""
    names = {p.id: p.config.display_name or p.config.model for p in experiment.approaches}
    names.update({b.id: b.display_name or b.id for b in experiment.config.baselines})
    clashes = sorted(name for name, n in Counter(names.values()).items() if n > 1)
    if clashes:
        raise InputError([f"顯示名稱重覆：{name}（在 Approach 設定加上不同的 display_name）" for name in clashes])
    return names


def group_of_article(experiment: Experiment, truth: GroundTruth | None) -> dict[str, str]:
    ids = experiment.config.articles.ids
    if truth is None:
        return {i: ALL_ARTICLES for i in ids}
    return {i: truth.group_of(i) if i in truth.domains else UNGROUPED for i in ids}


def group_label(group: str) -> str:
    return {ALL_ARTICLES: "全部文章", UNGROUPED: "沒有 ground truth 的文章"}.get(group) or GROUP_LABELS.get(group) or group


def ordered_groups(groups) -> list[str]:
    known = list(GROUP_LABELS)
    return sorted(set(groups), key=lambda g: (known.index(g) if g in known else len(known), g))


def planned_total(experiment: Experiment) -> int:
    """每個 Approach 該有的 Run 數：文章 × 方向 × 每篇的次數 × 輸入版本。baseline 不經 API，不算。"""
    cfg = experiment.config
    return sum(len(cfg.articles.ids) * len(cfg.directions) * p.runs * len(p.input_variants) for p in experiment.approaches)


def first_full_runs(records: list[RunRecord]) -> list[RunRecord]:
    return [r for r in records if r.input_variant is InputVariant.FULL and r.run_index == 1]


# ───────────── 各節 ─────────────

def status_html(experiment: Experiment, records: list[RunRecord]) -> str:
    planned, recorded = planned_total(experiment), len(records)
    if recorded < planned:
        text = f"仍在進行：已完成 {recorded}／{planned} 個 Run（{int(100 * recorded / planned)}%）。以下是中途的數字，之後還會變。"
        kind = "wait"
    else:
        text = f"已全部跑完：{recorded} 個 Run。"
        kind = "ok"
    redo = sum(1 for r in records if r.outcome in REDO_OUTCOMES)
    if redo:
        text += f"另有 {redo} 個 Run 出錯或空內容，尚待重做；重做後數字會再變。"
    return f'<p class="status {kind}">{escape(text)}</p>'


def summary_items(rows: list[tuple[str, Tally]]) -> list[str]:
    """「先看結論」：只陳述表裡的數字，不下判斷。rows：(名稱, 完整文章第 1 次的結果)。"""
    rows = [(name, t) for name, t in rows if t.runs]
    if not rows:
        return []

    def best(counter) -> list[str]:
        top = max(counter(t) / t.runs for _, t in rows)
        return [f"{escape(name)}：{sentence_share(counter(t), t.runs)}" for name, t in rows if top and counter(t) / t.runs == top]

    items = []
    blocked = lambda t: t.count(Outcome.BLOCK)  # noqa: E731
    if any(blocked(t) for _, t in rows):
        items.append("被擋最多的是" + "、".join(best(blocked)))
        unblocked = [escape(name) for name, t in rows if not blocked(t)]
        if unblocked:
            items.append("完全沒被擋：" + "、".join(unblocked))
    else:
        items.append("沒有任何模型被擋")
    no_translation = lambda t: t.count(*NO_TRANSLATION)  # noqa: E731
    if any(no_translation(t) for _, t in rows):
        items.append("其他原因沒有拿到譯文最多的是" + "、".join(best(no_translation)))
    return items


def what_was_tested_html(experiment: Experiment, names: dict[str, str], group_sizes: Counter) -> str:
    cfg = experiment.config
    models = []
    for p in experiment.approaches:
        extra = [f"每篇每個方向跑 {p.runs} 次"] if p.runs > 1 else []
        if InputVariant.MAIN_ONLY in p.input_variants:
            extra.append("另有只送標題與正文的版本")
        models.append(f"<li>{escape(names[p.id])}" + (f"<small>（{escape('；'.join(extra))}）</small>" if extra else "") + "</li>")
    groups = "".join(f"<li>{escape(group_label(g))} {group_sizes[g]} 篇" + (f"<small>（{escape(GROUP_NOTES[g])}）</small>" if g in GROUP_NOTES else "") + "</li>"
                     for g in ordered_groups(group_sizes))
    directions = "、".join(DIRECTION_LABELS.get(d, str(d)) for d in cfg.directions)
    baselines = "".join(f"<li>對照（不經 AI）：{escape(names[b.id])}，只做{DIRECTION_LABELS.get(b.direction, str(b.direction))}</li>" for b in cfg.baselines)
    return ("<section><h2>測了什麼</h2><dl class=\"facts\">"
            f"<dt>模型</dt><dd><ul>{''.join(models)}{baselines}</ul></dd>"
            f"<dt>文章</dt><dd><ul>{groups}</ul></dd>"
            f"<dt>翻譯方向</dt><dd>{escape(directions)}</dd></dl></section>")


def results_html(experiment: Experiment, names: dict[str, str], records: list[RunRecord], group_of: dict[str, str]) -> str:
    groups = ordered_groups(group_of.values())
    head = "".join(f"<th>被擋（{escape(group_label(g))}）</th>" for g in groups)
    body, summary_rows = [], []
    for p in experiment.approaches:
        mine = [r for r in first_full_runs(records) if r.approach == p.id]
        overall, by_group = Tally(), defaultdict(Tally)
        for r in mine:
            overall.add(r)
            by_group[group_of[r.article_id]].add(r)
        other = overall.count(*NO_TRANSLATION)
        breakdown = "、".join(f"{label} {overall.count(o)}" for o, label in NO_TRANSLATION.items() if overall.count(o)) or None
        summary_rows.append((names[p.id], overall))
        body.append("<tr>" + f"<th scope=\"row\">{escape(names[p.id])}</th>"
                    + cell(overall.count(Outcome.OK), overall.runs, "ok")
                    + "".join(cell(by_group[g].count(Outcome.BLOCK), by_group[g].runs, "bad") for g in groups)
                    + cell(overall.count(Outcome.REFUSAL), overall.runs, "bad")
                    + cell(other, overall.runs, "muted", breakdown) + "</tr>")
    items = summary_items(summary_rows)
    lines = f'<ul class="takeaways">{"".join(f"<li>{i}</li>" for i in items)}</ul>' if items else ""
    table = ('<table id="results"><caption>完整文章、每篇每個方向的第 1 次結果。分母是 Run 數（一篇文章翻成一個方向是一個 Run）。</caption>'
             f"<thead><tr><th>模型</th><th>完成翻譯</th>{head}<th>拒絕</th><th>其他未完成</th></tr></thead><tbody>{''.join(body)}</tbody></table>")
    legend = ('<dl class="legend"><dt>被擋（Block）</dt><dd>模型供應商在系統層拒絕處理這篇文章，沒有任何譯文。</dd>'
              '<dt>拒絕（Refusal）</dt><dd>模型回覆「無法翻譯」之類，而不是譯文。</dd>'
              '<dt>其他未完成</dt><dd>沒有拿到可用的譯文：空內容（供應商有回應但沒有輸出）、輸出格式壞、被截斷，或出錯（網路或供應商後端的錯誤）。'
              '把滑鼠移到數字上可以看各項的數量。</dd></dl>')
    return f"<section><h2>先看結論</h2>{lines}</section><section><h2>結果</h2>{table}{legend}</section>"


def stability_html(experiment: Experiment, names: dict[str, str], records: list[RunRecord]) -> str:
    rows = []
    for p in experiment.approaches:
        if p.runs == 1 and InputVariant.MAIN_ONLY not in p.input_variants:
            continue
        mine = [r for r in records if r.approach == p.id]
        full = [r for r in mine if r.input_variant is InputVariant.FULL]
        main_only = [r for r in mine if r.input_variant is InputVariant.MAIN_ONLY]
        blocked_runs = sum(1 for r in full if r.outcome is Outcome.BLOCK)
        articles = {r.article_id for r in full}
        blocked_articles = {r.article_id for r in full if r.outcome is Outcome.BLOCK}
        only = (cell(sum(1 for r in main_only if r.outcome is Outcome.BLOCK), len(main_only), "bad")
                if InputVariant.MAIN_ONLY in p.input_variants else "<td>—</td>")
        rows.append(f"<tr><th scope=\"row\">{escape(names[p.id])}</th><td>{p.runs}</td>"
                    + cell(blocked_runs, len(full), "bad") + cell(len(blocked_articles), len(articles), "bad") + only + "</tr>")
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
        t, approach, direction, variant = tallies[key], *key
        counts = [t.count(Outcome.OK), t.count(Outcome.BLOCK), t.count(Outcome.REFUSAL)] + [t.count(o) for o in NO_TRANSLATION]
        rows.append(f"<tr><th scope=\"row\">{escape(names[approach])}</th><td>{DIRECTION_LABELS.get(direction, escape(str(direction)))}</td>"
                    f"<td>{VARIANT_LABELS[variant]}</td><td>{t.runs}</td>" + "".join(f"<td>{n}</td>" for n in counts) + "</tr>")
    heads = "".join(f"<th>{h}</th>" for h in ("模型", "方向", "輸入版本", "Run 數", "成功", "被擋", "拒絕", *NO_TRANSLATION.values()))
    return (f'<section><details><summary>各模型、各方向、各輸入版本的完整數字</summary>'
            f'<table id="details"><thead><tr>{heads}</tr></thead><tbody>{"".join(rows)}</tbody></table></details></section>')


def not_measured_html(experiment: Experiment) -> str:
    if experiment.judges:
        text = "譯文有沒有把敏感內容寫輕（淡化率），以及譯文的質素，由 Judge 評核。結果在 report.md 的 Gate 一節，這一頁不重複。"
    else:
        text = "這一批沒有設定 Judge，所以還沒有淡化率（譯文有沒有把敏感內容寫輕）和譯文質素的數字。這一頁只說明翻譯有沒有成功。"
    return f"<section><h2>還沒有的結果</h2><p>{escape(text)}</p></section>"


def render(experiment: Experiment, names: dict[str, str], records: list[RunRecord], group_of: dict[str, str]) -> str:
    in_experiment = {p.id for p in experiment.approaches}
    shown_records = [r for r in records if not r.is_baseline and r.approach in in_experiment]
    left_out = sum(1 for r in records if not r.is_baseline and r.approach not in in_experiment)
    results = results_html(experiment, names, shown_records, group_of)
    updated = max(datetime.fromisoformat(r.created_at) for r in records).astimezone(HONG_KONG).strftime("%Y-%m-%d %H:%M")
    notes = f"<p class=\"note\">另有 {left_out} 個 Run 屬於目前設定裡已沒有的模型，沒有列在這一頁。</p>" if left_out else ""
    body = (f"<header><h1>AI 翻譯會不會被擋、會不會拒絕</h1>"
            "<p class=\"lede\">用同一批新聞，測幾個翻譯模型能不能把文章翻完。這一頁只看「有沒有翻出來」。</p>"
            f"{status_html(experiment, shown_records)}</header>"
            f"{results}"
            f"{what_was_tested_html(experiment, names, Counter(group_of.values()))}"
            f"{stability_html(experiment, names, shown_records)}"
            f"{details_html(experiment, names, shown_records)}"
            f"{not_measured_html(experiment)}"
            f"<footer>{notes}<p>資料截至 {updated}（香港時間）。這一頁只有計數，不含文章內容，也沒有文章編號。實驗：{escape(experiment.config.id)}</p></footer>")
    return f'<!doctype html>\n<html lang="zh-Hant"><meta charset="utf-8"><title>AI 翻譯評測結果</title><style>{STYLE}</style><body><main>{body}</main></body></html>\n'


def handle(args) -> int:
    experiment = load_experiment(args.config)
    cfg = experiment.config
    out = OutputDir(args.data_dir, cfg.id)
    records = out.require_runs()
    names = labels(experiment)
    truth = None
    if cfg.ground_truth:   # 組別來自 ground truth（與 Gate 的分組一樣）；它對文章驗證，所以要載入文章
        truth = load_ground_truth(cfg.ground_truth, {i: load_article(cfg.articles.dir, i) for i in cfg.articles.ids})
    html = render(experiment, names, records, group_of_article(experiment, truth))
    out.write_showcase(html)   # 都算好才寫：中途失敗不會留下半份頁面
    print(f"已寫入 {out.showcase_path}（{len(records)} 個 Run）")
    return 0


STYLE = """
:root { --paper:#f5f7f9; --panel:#fff; --ink:#1b2430; --muted:#5a6672; --rule:#d5dce3; --ok:#2c7a4b; --bad:#b3261e; --wait:#9a6200;
  --ok-soft:#e1f1e7; --wait-soft:#f8edd8; --track:#e6ebf0; color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root { --paper:#12161b; --panel:#1a2027; --ink:#e2e7ec; --muted:#9ba7b3; --rule:#2c353f;
  --ok:#74c393; --bad:#f0928a; --wait:#e2ad55; --ok-soft:#1a2e22; --wait-soft:#33281a; --track:#2c353f; } }
body { margin:0; background:var(--paper); color:var(--ink); font:15px/1.75 "PingFang TC","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif; }
main { max-width:940px; margin:0 auto; padding:48px 16px 72px; display:grid; gap:40px; }
h1 { margin:0; font-size:2rem; line-height:1.3; } h2 { margin:0 0 12px; font-size:1.25rem; }
section, header { display:grid; gap:12px; min-width:0; } p, ul, dl { margin:0; } ul { padding:0; list-style:none; }
.lede { font-size:1.05rem; } .note, small, caption, footer { color:var(--muted); font-size:.875rem; }
.status { padding:10px 14px; border-radius:6px; font-weight:500; }
.status.wait { background:var(--wait-soft); color:var(--wait); } .status.ok { background:var(--ok-soft); color:var(--ok); }
.takeaways { display:grid; gap:6px; padding:16px 20px; background:var(--panel); border:1px solid var(--rule); border-radius:8px; }
.takeaways li::before { content:"・"; color:var(--muted); }
table { width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--rule); }
caption { text-align:left; padding:0 0 8px; } th, td { padding:10px 12px; text-align:left; border-bottom:1px solid var(--rule); vertical-align:top; }
thead th { font-size:.8125rem; color:var(--muted); font-weight:500; } tbody th { font-weight:700; }
td b { font-size:1.05rem; font-variant-numeric:tabular-nums; } td small { margin-left:4px; font-variant-numeric:tabular-nums; }
.track { display:block; height:6px; margin-top:6px; background:var(--track); border-radius:3px; overflow:hidden; }
.bar { display:block; height:100%; } .bar.ok { background:var(--ok); } .bar.bad { background:var(--bad); } .bar.muted { background:var(--muted); }
.facts { display:grid; grid-template-columns:7em 1fr; gap:10px 16px; } .facts dt, .legend dt { font-weight:700; } .facts dd, .legend dd { margin:0; }
.legend { display:grid; gap:4px; font-size:.9rem; } summary { cursor:pointer; font-weight:700; margin-bottom:12px; }
footer { border-top:1px solid var(--rule); padding-top:16px; display:grid; gap:6px; }
@media print { body { background:#fff; } main { padding:0; } }
"""
