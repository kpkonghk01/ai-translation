"""篩選候選文章：剔除誤中的、按案件分組（每案最多 2 篇）、建議第一批。

決定（decision）：
  keep            建議收
  drop-same-case  同案名額已滿（含已有的 CMS 文章）
  exclude         標題命中已知的誤中規則，附原因
  review          需要人看（標題沒有敏感關鍵字）
  control-pool    對照類候選池；keep 的是挑出來的對照

用法：
  python3 -I tools/screen_candidates.py --web data/web-articles --glob data/web-articles-global \\
      --cms-csv data-prohibited.csv --controls 5 --batch1 25 --out data/candidates-screened.tsv

--cms-csv 只用來取出案件編號（例如 HCCC101/2030）以計算名額，不輸出任何內容。
輸出的是建議，最後由人決定。
"""
import argparse
import csv
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hk01_collect as C  # noqa: E402
import hk01_web as W  # noqa: E402

bucket = W.length_bucket
LONG_BUCKETS = {"長", "超長"}
TEXT_TYPES = ("highlight", "paragraph", "heading", "quote")

# 標題命中就剔除；每條都是看過實際標題後歸納的誤中類型
EXCLUDE_RULES = [
    (r"神槍手|槍王", "遊戲或電競，非暴力"),
    (r"刺鼻|辣椒", "氣味或食物，非暴力"),
    (r"焚化廠|三級火", "意外火警，非暴力犯罪"),
    (r"交通意外", "交通意外統計"),
    (r"隱形殺手|意外跌倒", "健康資訊"),
    (r"轟炸機|空對空", "軍備新聞，沒有傷亡描述"),
    (r"殺青", "影視用語"),
    (r"專用自習室|性別歧視", "性別議題，非性罪行或暴力"),
]
_EXCLUDE = [(re.compile(p), why) for p, why in EXCLUDE_RULES]

_CASE_NO = re.compile(r"(?<![A-Za-z])([A-Z]{2,6})\s?(\d{1,5})\s?/\s?(20\d{2})(?!\d)")
_SEPARATORS = "｜│|"
RECENT_DAYS = 90       # 建議第一批優先選近這麼多天發佈的
SIM_MERGE = 0.45       # 標題相似度達此值即視為同案
SIM_WITH_RELATED = 0.2  # 對方出現在相關文章列表時，只需較低的相似度


def exclusion_reason(title):
    for pattern, why in _EXCLUDE:
        if pattern.search(title):
            return why
    return None


# 比 hk01_collect 決定題材用的規則更寬：看過 review 清單後補上戰事、性行為用語、欺凌與家暴等。
# 只用來決定「要不要交人看」，寧可多看幾篇，所以允許誤中。
_BROADER = re.compile(r"空襲|導彈|開火|無人機|轟炸|炮擊|戰|加沙|恐怖|性交|口交|亂倫|倫常|裙底|坐大腿|命案|扼|鐵錘|刀|欺凌|掌摑|腳踢|家暴|禁錮|勒索|死|亡")


def has_sensitive_keyword(title):
    return C.classify(title) is not None or bool(_BROADER.search(title))


def case_numbers(text):
    return {f"{a}{b}/{c}" for a, b, c in _CASE_NO.findall(text)}


def series_prefix(title):
    """「某甲案｜…」這類以案名開頭的系列標題，回傳案名；否則 None。"""
    for sep in _SEPARATORS:
        if sep in title:
            prefix = re.split(r"[．·・]", title.split(sep, 1)[0])[0].strip()   # 「某甲案．開案」取「某甲案」
            return prefix if prefix.endswith("案") and 3 <= len(prefix) <= 12 else None
    return None


def _norm(title):
    return re.sub(r"[^\w]", "", title)


def _bigrams(title):
    s = _norm(title)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similarity(a, b):
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def group_cases(arts):
    """回傳 {group_id: [articleId, ...]}。同案號、同系列名、標題高度相似，或互相出現在相關文章且標題有重疊者歸為一組。"""
    parent = {a["id"]: a["id"] for a in arts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for key_of in (lambda a: case_numbers(" ".join(a["texts"])), lambda a: {series_prefix(a["title"])} - {None}):
        seen = {}
        for a in arts:
            for key in key_of(a):
                if key in seen:
                    union(a["id"], seen[key])
                else:
                    seen[key] = a["id"]
    related = {a["id"]: {_norm(t) for t in a["related"]} for a in arts}
    for i, a in enumerate(arts):
        for b in arts[i + 1:]:
            sim = similarity(a["title"], b["title"])
            linked = _norm(b["title"]) in related[a["id"]] or _norm(a["title"]) in related[b["id"]]
            if sim >= SIM_MERGE or (linked and sim >= SIM_WITH_RELATED):
                union(a["id"], b["id"])
    members = defaultdict(list)
    for a in arts:
        members[find(a["id"])].append(a["id"])
    ordered = sorted(members.values(), key=lambda ids: min(ids))
    return {f"G{n:03d}": sorted(ids) for n, ids in enumerate(ordered, 1)}


def maybe_same(arts, min_common=6):
    """{id: [可能同案的其他 id]}：兩個標題有至少 min_common 個連續相同的字。只是提示，不影響分組與決定。"""
    norm = {a["id"]: _norm(a["title"]) for a in arts}
    out = {a["id"]: [] for a in arts}
    for i, a in enumerate(arts):
        for b in arts[i + 1:]:
            x, y = norm[a["id"]], norm[b["id"]]
            if SequenceMatcher(None, x, y, autojunk=False).find_longest_match(0, len(x), 0, len(y)).size >= min_common:
                out[a["id"]].append(b["id"])
                out[b["id"]].append(a["id"])
    return out


def _pick_controls(pool, n):
    """先在每個頻道各取最長的一篇，再按字數補滿。"""
    ranked = sorted(pool, key=lambda a: (-a["chars"], a["id"]))
    chosen, sections = [], set()
    for a in ranked:
        if len(chosen) < n and a["section"] not in sections:
            chosen.append(a)
            sections.add(a["section"])
    for a in ranked:
        if len(chosen) < n and a not in chosen:
            chosen.append(a)
    return {a["id"] for a in chosen}


def macro_topic(a):
    """sexual / violence / conflict 三大類。題材標籤不準時（例如 longform）按標題判斷。"""
    if a["topic"] == "sexual" or C.classify(a["title"]) == "sexual":
        return "sexual"
    return "conflict" if a["topic"] == "conflict" else "violence"


def _pick_batch1(keeps, size, now):
    """3000 字以上的長文全收；其餘名額使 sexual 約佔一半、violence 與 conflict 平分
    （長文也計入各自題材）；各題材內先取近 RECENT_DAYS 天發佈的，再取字數較多的；某題材不足時由其他題材補上。"""
    recent_after = now - RECENT_DAYS * 86400

    def key(a):
        return (a["published"] < recent_after, -a["chars"], a["id"])

    chosen = sorted((a for a in keeps if a["bucket"] in LONG_BUCKETS), key=lambda a: (-a["chars"], a["id"]))[:size]
    sexual = math.ceil(size / 2)
    violence = math.ceil((size - sexual) / 2)
    targets = {"sexual": sexual, "violence": violence, "conflict": size - sexual - violence}
    for topic, goal in targets.items():
        taken = {a["id"] for a in chosen}
        have = sum(macro_topic(a) == topic for a in chosen)
        pool = sorted((a for a in keeps if macro_topic(a) == topic and a["id"] not in taken), key=key)
        for a in pool[:max(0, goal - have)]:
            if len(chosen) < size:
                chosen.append(a)
    if len(chosen) < size:
        taken = {a["id"] for a in chosen}
        chosen += sorted((a for a in keeps if a["id"] not in taken), key=key)[:size - len(chosen)]
    return {a["id"] for a in chosen}


def screen(arts, cms_case_numbers, controls=5, batch1_size=25, max_per_case=2, review_min_chars=None, now=None):
    """arts: 每篇含 id,title,chars,bucket,topic,section,texts,related,has_global,published。回傳每篇一列的 dict 清單。

    review_min_chars：給定時，標題沒有敏感關鍵字（原本要 review）的文章，字數達標者直接當候選保留（仍受每案上限），
    不足者 drop-short-review。"""
    now = time.time() if now is None else now
    decision, reason, keep_note = {}, {}, {}
    candidates, control_pool, grouped = [], [], []
    for a in arts:
        why = exclusion_reason(a["title"])
        too_short = review_min_chars is not None and a["chars"] < review_min_chars
        if why:
            decision[a["id"]], reason[a["id"]] = "exclude", why
        elif a["topic"] == "control":
            if not has_sensitive_keyword(a["title"]):
                control_pool.append(a)
            elif too_short:
                decision[a["id"]], reason[a["id"]] = "drop-short-review", f"對照類標題含敏感關鍵字，且不足 {review_min_chars} 字"
            else:
                decision[a["id"]], reason[a["id"]] = "review", "對照類標題含敏感關鍵字"
        elif has_sensitive_keyword(a["title"]):
            candidates.append(a)
            grouped.append(a)
        elif too_short:
            decision[a["id"]], reason[a["id"]] = "drop-short-review", f"原為 review（標題沒有敏感關鍵字），且不足 {review_min_chars} 字"
        elif review_min_chars is not None:
            candidates.append(a)
            grouped.append(a)
            keep_note[a["id"]] = f"原為 review（標題沒有敏感關鍵字），依指示保留（{review_min_chars} 字以上）"
        else:
            decision[a["id"]], reason[a["id"]] = "review", "標題沒有敏感關鍵字，請人看"
            grouped.append(a)

    groups = group_cases(candidates + [a for a in grouped if a not in candidates])
    group_of = {i: g for g, ids in groups.items() for i in ids}
    by_id = {a["id"]: a for a in arts}
    keeps = []
    for g, ids in groups.items():
        members = [by_id[i] for i in ids]
        cms = max([cms_case_numbers.get(n, 0) for m in members for n in case_numbers(" ".join(m["texts"]))] or [0])
        slots = max(0, max_per_case - cms)
        contenders = sorted((m for m in members if m in candidates), key=lambda m: (-m["chars"], m["id"]))
        for rank, m in enumerate(contenders):
            if rank < slots:
                decision[m["id"]], reason[m["id"]] = "keep", keep_note.get(m["id"], "")
                keeps.append(m)
            else:
                decision[m["id"]] = "drop-same-case"
                reason[m["id"]] = f"同案名額已滿（上限 {max_per_case}" + (f"，其中 CMS 文章 {cms} 篇）" if cms else "）")

    picked_controls = _pick_controls(control_pool, controls)
    for a in control_pool:
        decision[a["id"]] = "keep" if a["id"] in picked_controls else "control-pool"
        reason[a["id"]] = ""
    batch1 = _pick_batch1(keeps, batch1_size, now) | picked_controls
    maybe = maybe_same(grouped)

    order = {"keep": 0, "review": 1, "drop-same-case": 2, "drop-short-review": 3, "exclude": 4, "control-pool": 5}
    rows = [{
        "articleId": a["id"], "topic": a["topic"], "section": a["section"], "chars": a["chars"], "bucket": a["bucket"],
        "hasGlobal": a["has_global"], "group": group_of.get(a["id"], ""), "groupSize": len(groups.get(group_of.get(a["id"], ""), [])),
        "decision": decision[a["id"]], "reason": reason[a["id"]], "batch1": a["id"] in batch1, "title": a["title"],
        "maybeSame": " ".join(i for i in maybe.get(a["id"], []) if group_of.get(i) != group_of.get(a["id"])),
    } for a in arts]
    rows.sort(key=lambda r: (order[r["decision"]], r["topic"], -r["chars"], r["articleId"]))
    return rows


# ───────────── 載入 ─────────────
def load_articles(web_dir, glob_dir):
    arts = []
    for name in sorted(os.listdir(web_dir)):
        if not (name.endswith(".json") and name[:-5].isdigit()):
            continue
        with open(os.path.join(web_dir, name), encoding="utf-8") as f:
            art = json.load(f)
        comps = art["components"]
        arts.append({
            "id": name[:-5], "title": art["title"], "chars": W.main_chars(art), "bucket": bucket(W.main_chars(art)),
            "topic": art["meta"].get("topic", ""), "section": art["mainChannel"], "published": art["meta"].get("publishTime") or 0,
            "texts": [c.get("text", "") for c in comps if c["type"] in TEXT_TYPES],
            "related": [t for c in comps if c["type"] == "related-article" for t in c.get("titles", [])],
            "has_global": os.path.exists(os.path.join(glob_dir, name)),
        })
    return arts


def cms_cases(csv_path):
    """CMS 匯出的每篇文章用了哪些案件編號 → {案件編號: 文章數}。只回傳編號與數量，不回傳內容。"""
    csv.field_size_limit(sys.maxsize)
    counts = Counter()
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            turn = next((t for t in json.loads(row[1]) if t.get("role") == "user"), None)
            if turn:
                counts.update(case_numbers("".join(p.get("text", "") for p in turn["parts"])))
    return dict(counts)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--web", required=True)
    p.add_argument("--glob", required=True)
    p.add_argument("--cms-csv")
    p.add_argument("--controls", type=int, default=5)
    p.add_argument("--batch1", type=int, default=25)
    p.add_argument("--review-min-chars", type=int, default=None, help="原本要 review 的文章，字數達此值才保留，不足的 drop-short-review")
    p.add_argument("--out", required=True)
    a = p.parse_args()

    cms = cms_cases(a.cms_csv) if a.cms_csv else {}
    rows = screen(load_articles(a.web, a.glob), cms, controls=a.controls, batch1_size=a.batch1, review_min_chars=a.review_min_chars)
    C.write_tsv(a.out, rows, ["articleId", "decision", "batch1", "topic", "section", "chars", "bucket", "hasGlobal", "group", "groupSize", "maybeSame", "reason", "title"])
    print(f"CMS 文章用到 {len(cms)} 個案件編號：{sorted(cms)}")
    print(f"{len(rows)} 篇 → {a.out}\n")
    table = Counter((r["decision"], r["topic"]) for r in rows)
    topics = sorted({r["topic"] for r in rows})
    print(f"{'decision':16}" + "".join(f"{t:>12}" for t in topics))
    for d in ("keep", "review", "drop-same-case", "drop-short-review", "exclude", "control-pool"):
        print(f"{d:16}" + "".join(f"{table.get((d, t), 0):>12}" for t in topics))
    keeps = [r for r in rows if r["decision"] == "keep" and r["topic"] != "control"]
    print("\nkeep（非對照）長度分佈:", dict(Counter(r["bucket"] for r in keeps)))
    b1 = [r for r in rows if r["batch1"]]
    sens = [r for r in b1 if r["topic"] != "control"]
    print(f"建議第一批 {len(b1)} 篇（敏感 {len(sens)} + 對照 {len(b1) - len(sens)}）:", dict(Counter(macro_topic(r) for r in sens)), "長度:", dict(Counter(r["bucket"] for r in b1)))


if __name__ == "__main__":
    main()
