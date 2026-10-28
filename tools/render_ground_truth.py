"""驗證 ground truth 草稿，並產生給人手審閱的 Markdown。

用法：
  python3 -I tools/render_ground_truth.py --out out.md gt.json [gt2.json ...] \\
      [--csv data-prohibited.csv] [--web data/web-articles] [--web data/web-articles-china]

文章來源：--csv 是 CMS 匯出（以 24 位 hex 的 session id 識別）；--web 是 hk01_collect 抓下來的目錄（以數字 articleId 識別）。
每篇用 "article_id"（沒有的話用 "session_id"）指向來源。

驗證（deterministic）：每篇文章要找得到；每個 component_id 要在該篇內；每個 anchor 要是該 component 文字的逐字子字串；
kind、domain 要在允許清單內。任何一項失敗就以非 0 結束，不產生輸出。完整原句由程式從來源帶入，避免人手或 LLM 抄錯。
"""
import argparse
import csv
import json
import os
import re
import sys

# 文章層級的題材標記（可選，缺省視為 general）。china-political 文章的所有事實都算 political fact，
# 報告以此比較模型在政治題材與其他題材上的淡化率。
DOMAINS = {"general", "china-political"}

KIND_LABEL = {
    "actor": "行為者", "victim": "受害人", "act": "行為", "detail": "細節", "quantity": "數字／清單",
    "legal_status": "法律狀態", "attribution": "歸因", "quote": "直接引語", "term": "用語／定義",
}


def load_csv_articles(csv_path):
    csv.field_size_limit(sys.maxsize)
    articles = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            history = json.loads(row[1])
            turn = next(t for t in history if t.get("role") == "user")
            article = json.loads("".join(p.get("text", "") for p in turn["parts"]))
            articles[row[0]] = {
                "title": article.get("title", ""),
                "components": {c["id"]: c for c in article["components"] if "id" in c},
            }
    return articles


def load_web_articles(web_dir):
    articles = {}
    for name in sorted(os.listdir(web_dir)):
        if name.endswith(".json") and name[:-5].isdigit():
            with open(os.path.join(web_dir, name), encoding="utf-8") as f:
                article = json.load(f)
            articles[name[:-5]] = {
                "title": article.get("title", ""),
                "components": {c["id"]: c for c in article["components"] if "id" in c},
            }
    return articles


def load_sources(csv_path=None, web_dirs=()):
    articles = load_csv_articles(csv_path) if csv_path else {}
    for d in web_dirs:
        articles.update(load_web_articles(d))
    return articles


def render(articles, gts):
    """回傳 (markdown 或 None, 錯誤清單, 統計)。gts 是讀入的 ground truth 物件清單。"""
    errors, n_articles, n_items, n_facts = [], 0, 0, 0

    def check(sid, cid, anchor, label):
        art = articles.get(sid)
        if art is None:
            errors.append(f"{label}: 文章不在來源：{sid}")
            return None
        comp = art["components"].get(cid)
        if comp is None:
            errors.append(f"{label}: component_id 不在該篇：{sid} / {cid}")
            return None
        text = comp.get("text", "")
        if anchor not in text:
            errors.append(f"{label}: anchor 不是逐字子字串：{cid} / {anchor!r}")
        return text

    out = [
        "# Ground truth 草稿（待確認）",
        "",
        "每個「Sensitive sentence」列出譯文必須保留的事實（Must-retain fact）。Judge 會逐項判 Present / Weakened / Absent。",
        "",
        "**怎樣審閱**：只需看三件事 —— (1) 這句是否真的敏感、有沒有漏掉更該核對的句子；(2) 事實有沒有寫錯或漏了關鍵（數字、法律狀態、歸因、直接引語）；(3) 文末「原文瑕疵」的處理方式。全部沒問題，回覆「ok」即可；有問題請引用 component id。",
        "",
        "類型：" + "、".join(f"{v}（{k}）" for k, v in KIND_LABEL.items()),
        "",
    ]
    seen = set()
    for gt in gts:
        for a in gt["articles"]:
            sid = a.get("article_id") or a["session_id"]
            if sid in seen:
                errors.append(f"同一篇出現多次：{sid}")
            seen.add(sid)
            art = articles.get(sid)
            if art is None:
                errors.append(f"文章不在來源：{sid}")
                continue
            n_articles += 1
            domain = a.get("domain", "general")
            if domain not in DOMAINS:
                errors.append(f"未知 domain：{domain}（{sid}）")
            tag = "　【political】" if domain == "china-political" else ""
            role = "敏感文章" if a["role"] == "sensitive" else "對照（表面無害）"
            out += [f"## {art['title']}{tag}", f"`{sid}` ・ {role} ・ {a['note']}", ""]
            if not a["items"]:
                out += ["_沒有需要核對力度的句子，只用來測 block。_", ""]
            for it in a["items"]:
                text = check(sid, it["component_id"], it["anchor"], "item")
                n_items += 1
                out += [f"### `{it['component_id']}` ・ {it['why']}", "", "> " + (text or "").replace("\n", "\n> "), ""]
                if not it["must_retain"]:
                    errors.append(f"item 沒有事實：{it['component_id']}")
                for f in it["must_retain"]:
                    if f["kind"] not in KIND_LABEL:
                        errors.append(f"未知 kind：{f['kind']}（{it['component_id']}）")
                    n_facts += 1
                    out.append(f"- [ ] **{KIND_LABEL.get(f['kind'], f['kind'])}**：{f['fact']}")
                out.append("")
            for d in a.get("source_defects", []):
                check(sid, d["component_id"], d["anchor"], "defect")
                out.append(f"- ⚠ **原文瑕疵** `{d['component_id']}`：{d['issue']}")
            if a.get("source_defects"):
                out.append("")

    stats = {"articles": n_articles, "items": n_items, "facts": n_facts}
    return (None if errors else "\n".join(out) + "\n"), errors, stats


_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_QUOTE = re.compile(r"[「『“]([^」』”]{2,})[」』”]")


def _squash(s):
    return re.sub(r"\s+", "", s)


def grounding_warnings(articles, gts):
    """事實裡的數字與引號內文字，必須出現在對應的原文句子裡。回傳 [(文章, component, 問題, 事實)]。

    只是警告，不算驗證失敗：原文可能用不同寫法（十年／10年），要人看。
    不能抓到「數字存在但配錯對象」這類錯誤。
    """
    out = []
    for gt in gts:
        for a in gt["articles"]:
            sid = a.get("article_id") or a["session_id"]
            art = articles.get(sid)
            for it in a["items"]:
                comp = (art or {"components": {}})["components"].get(it["component_id"])
                if comp is None:
                    continue
                text = _squash(comp.get("text", ""))   # 原文偶有多餘空格，比對時忽略所有空白
                plain = text.replace(",", "")
                for f in it["must_retain"]:
                    for n in _NUM.findall(f["fact"]):
                        if n.replace(",", "") not in plain:
                            out.append((sid, it["component_id"], f"數字 {n} 不在原句", f["fact"]))
                    for q in _QUOTE.findall(f["fact"]):
                        if _squash(q) not in text:
                            out.append((sid, it["component_id"], f"引語「{q}」不在原句", f["fact"]))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("gt", nargs="+", help="ground truth JSON（可多個）")
    p.add_argument("--out", required=True)
    p.add_argument("--csv")
    p.add_argument("--web", action="append", default=[])
    a = p.parse_args(argv)
    gts = []
    for path in a.gt:
        with open(path, encoding="utf-8") as f:
            gts.append(json.load(f))
    markdown, errors, stats = render(load_sources(a.csv, a.web), gts)
    if errors:
        print("驗證失敗：", *errors, sep="\n- ")
        return 1
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"OK：{stats['articles']} 篇、{stats['items']} 個敏感句、{stats['facts']} 條事實；已寫入 {a.out}")
    warnings = grounding_warnings(load_sources(a.csv, a.web), gts)
    warn_path = a.out + ".warnings.tsv"
    with open(warn_path, "w", encoding="utf-8") as f:
        f.write("article\tcomponent\tissue\tfact\n")
        for row in warnings:
            f.write("\t".join(row) + "\n")
    print(f"自動檢查警告：{len(warnings)} 條（數字或引語不在原句；已寫入 {warn_path}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
