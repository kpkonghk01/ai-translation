"""收集 HK01 公開文章作測試集候選。

子命令：
  sitemap  從 sitemap.xml 篩出候選網址（敏感題材關鍵字 / 對照樣本）
  fetch    抓網址清單裡的文章頁，轉成 Article JSON（可選：同時抓簡體版）
  report   彙總已抓文章，輸出 candidates.tsv

規則：只抓 hk01.com 文章頁、不碰 /api/、循序、固定延遲、遇到 403/429/503 即停。
抓回來的內容只當資料解析，不執行。

用法：python3 -I tools/hk01_collect.py <子命令> ...
"""
import argparse
import csv
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hk01_web as W  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; hk01-ai-translation-research/0.1)"
MAX_BYTES = 8_000_000
STOP_STATUS = {403, 429, 503}

SEXUAL = re.compile(r"性侵|強姦|姦|非禮|猥褻|迷姦|色情|偷拍|針孔|非法性交|肛交|雞姦|性罪行|性騷擾|裸照|性暴力")
VIOLENCE = re.compile(r"殺|斬|兇|凶|屍|虐|襲擊|毆|刺|槍|爆炸|縱火|死刑|屠|砍|綁架|擄|劫|傷亡")
SENSITIVE_SECTIONS = {"社會新聞", "突發", "即時中國", "即時國際", "大國小事", "熱爆話題", "人氣話題"}
CONTROL_SECTIONS = {"熱爆話題", "即時國際", "大國小事", "即時中國", "社會新聞", "地產樓市", "財經快訊", "旅遊", "飲食", "健康Easy", "數碼生活"}


class StopFetch(Exception):
    pass


# ───────────── sitemap ─────────────
_URL_BLOCK = re.compile(r"<url>(.*?)</url>", re.S)


def parse_sitemap(xml):
    """回傳 [{url, title, date, section}]。用 regex 解析，不用 XML 解析器（輸入不可信）。"""
    out = []
    for block in _URL_BLOCK.findall(xml):
        loc = re.search(r"<loc>([^<]+)</loc>", block)
        if not loc:
            continue
        url = html.unescape(loc.group(1)).strip()
        title = re.search(r"<news:title>([^<]*)</news:title>", block)
        date = re.search(r"<news:publication_date>([^<]*)<", block)
        try:
            W.check_url(url)
            W.article_id_from_url(url)
        except ValueError:
            continue
        section = unquote(urlsplit(url).path.split("/")[1])
        out.append({"url": url, "title": html.unescape(title.group(1)) if title else "", "date": date.group(1) if date else "", "section": section})
    return out


def classify(title):
    if SEXUAL.search(title):
        return "sexual"
    if VIOLENCE.search(title):
        return "violence"
    return None


def pick_candidates(entries, controls, seed):
    """敏感題材：頻道在允許名單且標題含關鍵字；對照：其餘標題不含任何關鍵字的樣本。"""
    sensitive, pool = [], []
    for e in entries:
        topic = classify(e["title"])
        if topic and e["section"] in SENSITIVE_SECTIONS:
            sensitive.append({**e, "topic": topic})
        elif not topic and e["section"] in CONTROL_SECTIONS:
            pool.append({**e, "topic": "control"})
    random.Random(seed).shuffle(pool)
    return sensitive + pool[:controls]


def write_tsv(path, rows, fields):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(fields)
        for r in rows:
            w.writerow([r.get(k, "") for k in fields])


def read_tsv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def cmd_sitemap(a):
    seen, entries = set(), []
    for path in a.sitemaps:
        with open(path, encoding="utf-8") as f:
            for e in parse_sitemap(f.read()):
                if e["url"] not in seen:
                    seen.add(e["url"])
                    entries.append(e)
    rows = pick_candidates(entries, a.controls, a.seed)
    write_tsv(a.out, rows, ["topic", "title", "url", "section", "date"])
    counts = {}
    for r in rows:
        counts[r["topic"]] = counts.get(r["topic"], 0) + 1
    print(f"sitemap 共 {len(entries)} 篇 → 候選 {len(rows)}：{counts}")


# ───────────── fetch ─────────────
class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        W.check_url(newurl)  # 轉址的每一跳都要在白名單內
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect())


def http_get(url):
    """回傳 (最終網址, 文字)。非 2xx 會丟 HTTPError。"""
    url = W.encode_url(url)  # 內含白名單檢查
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-HK,zh;q=0.9"})
    with _OPENER.open(req, timeout=30) as resp:
        raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("頁面超過大小上限")
        return resp.geturl(), raw.decode("utf-8", errors="replace")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def global_url(article_id):
    # global.hk01.com 只看 id，會 301 到正確的簡體網址
    return f"https://global.hk01.com/{quote('x')}/{article_id}/x"


def cmd_fetch(a):
    rows = []
    for path in a.urls:
        rows += read_tsv(path)
    os.makedirs(a.out, exist_ok=True)
    gdir = a.out.rstrip("/") + "-global"
    rdir = a.out.rstrip("/") + "-raw"  # 原始 article 物件：之後轉換器改了可以離線重轉，不必重抓
    os.makedirs(rdir, exist_ok=True)
    if a.with_global:
        os.makedirs(gdir, exist_ok=True)
    log = open(os.path.join(a.out, "fetch-log.tsv"), "a", encoding="utf-8")
    done = fetched = 0
    try:
        for r in rows:
            if a.limit and fetched >= a.limit:
                break
            url = r["url"]
            try:
                aid = W.article_id_from_url(W.check_url(url))
            except ValueError as e:
                log.write(f"{now_iso()}\t-\tskip\t{e}\n")
                continue
            path, gpath, rpath = os.path.join(a.out, f"{aid}.json"), os.path.join(gdir, f"{aid}.json"), os.path.join(rdir, f"{aid}.json")
            need_trad = not (os.path.exists(path) and os.path.exists(rpath))
            need_glob = a.with_global and not os.path.exists(gpath)
            if not (need_trad or need_glob):
                done += 1
                continue
            for kind, need, target, get_url in (("trad", need_trad, path, url), ("global", need_glob, gpath, global_url(aid))):
                if not need:
                    continue
                time.sleep(a.delay)
                try:
                    final, page = http_get(get_url)
                    if kind == "trad":
                        art = W.extract_article(page)
                        obj = W.convert_article(art, final, now_iso())
                        obj["meta"]["topic"] = r.get("topic", "")
                        save_json(rpath, art)
                    else:
                        obj = {"url": final, "fetchedAt": now_iso(), "items": W.parse_global_page(page)}
                    save_json(target, obj)
                    log.write(f"{now_iso()}\t{aid}\t{kind}\tok\n")
                except urllib.error.HTTPError as e:
                    log.write(f"{now_iso()}\t{aid}\t{kind}\tHTTP {e.code}\n")
                    if e.code in STOP_STATUS:
                        raise StopFetch(f"HTTP {e.code}，依規則停止")
                except (W.ParseError, ValueError, urllib.error.URLError, TimeoutError) as e:
                    log.write(f"{now_iso()}\t{aid}\t{kind}\terror {type(e).__name__}: {str(e)[:100]}\n")
            fetched += 1
            log.flush()
    except StopFetch as e:
        print(f"已停止：{e}")
    finally:
        log.close()
    print(f"本次處理 {fetched} 篇，原本已有 {done} 篇；輸出 {a.out}")


def cmd_reconvert(a):
    """用保存的原始 article 物件離線重轉（轉換器改版後用），保留原本的 topic / url / fetchedAt。"""
    os.makedirs(a.out, exist_ok=True)
    n = 0
    for name in sorted(os.listdir(a.raw)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(a.raw, name), encoding="utf-8") as f:
            art = json.load(f)
        target = os.path.join(a.out, name)
        old = {}
        if os.path.exists(target):
            with open(target, encoding="utf-8") as f:
                old = json.load(f).get("meta", {})
        url = old.get("url") or "https://www.hk01.com" + art.get("canonicalUrl", "")
        obj = W.convert_article(art, url, old.get("fetchedAt") or now_iso())
        if old.get("topic"):
            obj["meta"]["topic"] = old["topic"]
        save_json(target, obj)
        n += 1
    print(f"重轉 {n} 篇 → {a.out}")


def cmd_verify(a):
    """原始物件對轉換結果：缺少轉換檔、不認得的型別、原始文字沒有出現在結果，任何一項就以非 0 結束。
    訊息只列文章 id、區塊型別與字數，不印文字內容。"""
    problems, n = [], 0
    for name in sorted(os.listdir(a.raw)):
        if not (name.endswith(".json") and name[:-5].isdigit()):
            continue
        n += 1
        aid = name[:-5]
        with open(os.path.join(a.raw, name), encoding="utf-8") as f:
            raw = json.load(f)
        path = os.path.join(a.out, name)
        if not os.path.exists(path):
            problems.append(f"{aid}：沒有轉換結果")
            continue
        with open(path, encoding="utf-8") as f:
            art = json.load(f)
        problems += [f"{aid}：不認得的型別 {u}" for u in art["meta"].get("unknown", [])]
        problems += [f"{aid}：{u['block']} 區塊有 {len(u['text'])} 字沒有出現在轉換結果" for u in W.uncovered_texts(raw, art)]
    if problems:
        print(f"verify：{n} 篇中發現 {len(problems)} 個問題（{a.out}）", file=sys.stderr)
        for p in problems[:40]:
            print("  " + p, file=sys.stderr)
        if len(problems) > 40:
            print(f"  ……另有 {len(problems) - 40} 個", file=sys.stderr)
        print("先修 tools/hk01_web.py，再用 reconvert 重轉。", file=sys.stderr)
        return 1
    print(f"verify：OK（{n} 篇；沒有不認得的型別，原始文字全部出現在轉換結果）")
    return 0


# ───────────── report ─────────────
def cmd_report(a):
    rows, unknown_kinds, by_topic = [], {}, {}
    for name in sorted(os.listdir(a.dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(a.dir, name), encoding="utf-8") as f:
            art = json.load(f)
        m, chars = art["meta"], W.main_chars(art)
        for u in m["unknown"]:
            unknown_kinds[u] = unknown_kinds.get(u, 0) + 1
        topic, bucket = m.get("topic", ""), W.length_bucket(chars)
        by_topic.setdefault(topic, {}).setdefault(bucket, 0)
        by_topic[topic][bucket] += 1
        rows.append({
            "articleId": m["articleId"], "topic": topic, "section": art["mainChannel"], "chars": chars, "bucket": bucket,
            "components": len(art["components"]), "special": sum(c["type"] in ("table", "faq") for c in art["components"]),
            "unknown": len(m["unknown"]), "warnings": len(m["warnings"]), "title": art["title"], "url": m["url"],
        })
    rows.sort(key=lambda r: (r["topic"], -r["chars"]))
    write_tsv(a.out, rows, ["articleId", "topic", "section", "chars", "bucket", "components", "special", "unknown", "warnings", "title", "url"])
    print(f"{len(rows)} 篇 → {a.out}")
    for t, b in sorted(by_topic.items()):
        print(f"  {t or '(無)'}: {b}")
    print("不認得的型別（需要檢視）：", unknown_kinds or "無")
    return 1 if unknown_kinds else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sitemap")
    s.add_argument("sitemaps", nargs="+")
    s.add_argument("--out", required=True)
    s.add_argument("--controls", type=int, default=40)
    s.add_argument("--seed", type=int, default=1)
    s.set_defaults(fn=cmd_sitemap)
    f = sub.add_parser("fetch")
    f.add_argument("urls", nargs="+", help="含 url 欄的 TSV")
    f.add_argument("--out", required=True)
    f.add_argument("--delay", type=float, default=2.0)
    f.add_argument("--limit", type=int, default=0, help="最多處理幾篇（0 = 不限）")
    f.add_argument("--with-global", action="store_true", help="同時抓 global.hk01.com 簡體版")
    f.set_defaults(fn=cmd_fetch)
    rc = sub.add_parser("reconvert")
    rc.add_argument("--raw", required=True, help="fetch 保存的 -raw 目錄")
    rc.add_argument("--out", required=True)
    rc.set_defaults(fn=cmd_reconvert)
    v = sub.add_parser("verify", help="檢查轉換有沒有漏掉原文（不認得的型別也算失敗）")
    v.add_argument("--raw", required=True, help="fetch 保存的 -raw 目錄")
    v.add_argument("--out", required=True, help="對應的轉換結果目錄")
    v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("report")
    r.add_argument("--dir", required=True)
    r.add_argument("--out", required=True)
    r.set_defaults(fn=cmd_report)
    a = p.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
