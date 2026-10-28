"""驗證 ground truth 草稿，並產生給人手審閱的 Markdown。

用法：python3 -I tools/render_ground_truth.py <data.csv> <ground-truth.json> <out.md>

驗證（deterministic）：每個 session_id 要在 CSV 內；每個 component_id 要在該篇文章內；
每個 anchor 要是該 component 文字的逐字子字串。任何一項失敗就以非 0 結束，不產生輸出。
完整原句由程式從 CSV 帶入，避免人手或 LLM 抄錯。
"""
import csv
import json
import sys

KIND_LABEL = {
    "actor": "行為者", "victim": "受害人", "act": "行為", "detail": "細節", "quantity": "數字／清單",
    "legal_status": "法律狀態", "attribution": "歸因", "quote": "直接引語", "term": "用語／定義",
}


def load_articles(csv_path):
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


def main(csv_path, gt_path, out_path):
    articles = load_articles(csv_path)
    gt = json.load(open(gt_path, encoding="utf-8"))
    errors, n_items, n_facts = [], 0, 0

    def check(sid, cid, anchor, label):
        art = articles.get(sid)
        if art is None:
            errors.append(f"{label}: session_id 不在 CSV：{sid}")
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
    for a in gt["articles"]:
        sid = a["session_id"]
        art = articles.get(sid)
        if art is None:
            errors.append(f"session_id 不在 CSV：{sid}")
            continue
        out += [f"## {art['title']}", f"`{sid}` ・ {'敏感文章' if a['role'] == 'sensitive' else '對照（表面無害）'} ・ {a['note']}", ""]
        if not a["items"]:
            out += ["_沒有需要核對力度的句子，只用來測 block。_", ""]
        for it in a["items"]:
            text = check(sid, it["component_id"], it["anchor"], "item")
            n_items += 1
            out += [f"### `{it['component_id']}` ・ {it['why']}", "", "> " + (text or "").replace("\n", "\n> "), ""]
            for f in it["must_retain"]:
                if f["kind"] not in KIND_LABEL:
                    errors.append(f"未知 kind：{f['kind']}")
                n_facts += 1
                out.append(f"- [ ] **{KIND_LABEL.get(f['kind'], f['kind'])}**：{f['fact']}")
            out.append("")
        for d in a.get("source_defects", []):
            text = check(sid, d["component_id"], d["anchor"], "defect")
            out.append(f"- ⚠ **原文瑕疵** `{d['component_id']}`：{d['issue']}")
        if a.get("source_defects"):
            out.append("")

    if errors:
        print("驗證失敗：", *errors, sep="\n- ")
        return 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"OK：{len(gt['articles'])} 篇、{n_items} 個敏感句、{n_facts} 條事實；已寫入 {out_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
