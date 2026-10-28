"""重現現行繁→簡服務（refs/opencc-service）的轉換流程，產生方案 E 的 baseline。

服務的做法（refs/opencc-service/src/app/services/ConversionService.ts、ConversionManager.ts）：
  整份 context 先 JSON.stringify，香港繁體→簡體分兩步：hk2t.json，再 t2s.json，最後 parse 回物件。
設定檔引用 npm opencc 的標準詞典；這裡把路徑改寫成本機 OpenCC wheel 內附的標準詞典，自訂詞典不變。

用法（需要裝了 OpenCC 的環境）：
  .venv/bin/python -I tools/service_baseline.py validate --trad data/web-articles --glob data/web-articles-global
  .venv/bin/python -I tools/service_baseline.py convert  --trad data/web-articles --out data/baseline-service

validate 用 global.hk01.com 的實際輸出核對重現得是否準確；核對通過前，不要把 convert 的結果當 baseline。
"""
import argparse
import collections
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STOCK_MARK = "node_modules/opencc/prebuilds/assets"
CONFIG_SUBDIR = "src/config/opencc/media-api-hk01-com"
STEPS = ("hk2t", "t2s")  # ConversionManager.convertHKtoS：先 convertHKtoT，再 convertTtoS


def rewrite_config(cfg, config_dir, stock_dir):
    """遞迴改寫設定內的 file 路徑：標準詞典指向 stock_dir，自訂詞典（相對路徑）改成絕對路徑。不改動傳入物件。"""
    def fix(path):
        if STOCK_MARK in path.replace("//", "/"):
            return os.path.join(stock_dir, os.path.basename(path))
        if not os.path.isabs(path):
            return os.path.normpath(os.path.join(config_dir, path))
        return path

    def walk(o):
        if isinstance(o, dict):
            return {k: (fix(v) if k == "file" and isinstance(v, str) else walk(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o

    return walk(cfg)


def context_string(article):
    """對應 JSON.stringify(context)：緊湊、不逸出非 ASCII。meta 是我們自己的欄位，不送進轉換。"""
    ctx = {k: v for k, v in article.items() if k != "meta"}
    return json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))


def apply_to_article(article, convert):
    """把 convert(str)->str 套用在整份 context 的 JSON 字串上，再 parse 回來；meta 原樣保留。"""
    out = json.loads(convert(context_string(article)))
    if "meta" in article:
        out["meta"] = article["meta"]
    return out


class ServiceConverter:
    def __init__(self, service_dir, stock_dir=None, work_dir=None):
        import opencc  # 只在實際轉換時需要

        config_dir = os.path.join(service_dir, CONFIG_SUBDIR)
        stock_dir = stock_dir or os.path.join(os.path.dirname(opencc.__file__), "clib", "share", "opencc")
        self._tmp = None if work_dir else tempfile.TemporaryDirectory()
        work_dir = work_dir or self._tmp.name
        self.steps = []
        for name in STEPS:
            with open(os.path.join(config_dir, f"{name}.json"), encoding="utf-8") as f:
                cfg = rewrite_config(json.load(f), config_dir, stock_dir)
            path = os.path.join(work_dir, f"{name}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            self.steps.append(opencc.OpenCC(path))

    def hk_to_s(self, s):
        for step in self.steps:
            s = step.convert(s)
        return s

    def convert_article(self, article):
        return apply_to_article(article, self.hk_to_s)


def load_articles(trad_dir):
    for name in sorted(os.listdir(trad_dir)):
        if name.endswith(".json") and name[:-5].isdigit():
            with open(os.path.join(trad_dir, name), encoding="utf-8") as f:
                yield name[:-5], json.load(f)


def cmd_validate(a):
    import compare_global as G

    conv = ServiceConverter(a.service, stock_dir=a.stock_dir)
    pairs = []
    for aid, art in load_articles(a.trad):
        gpath = os.path.join(a.glob, f"{aid}.json")
        if os.path.exists(gpath):
            with open(gpath, encoding="utf-8") as f:
                pairs.append((aid, art, json.load(f)))
    print(f"同時有繁體與 global 簡體版的文章：{len(pairs)} 篇")
    tot, diffs, perfect, bad = collections.Counter(), collections.Counter(), [], []
    for aid, art, g in pairs:
        r = G.compare(G.trad_texts(conv.convert_article(art)), G.glob_texts(g), lambda s: s)
        tot.update({k: r[k] for k in ("matched", "total", "no_counterpart", "extra_in_global")})
        diffs.update(r["pairs"])
        # global 每篇都多一個導語項目，不算差異；只看繁體版的每一段是否都有吻合的簡體段
        (perfect if r["matched"] == r["total"] else bad).append(aid)
    print(f"段落完全吻合 {tot['matched']}/{tot['total']}；無對應 {tot['no_counterpart']}；global 多出 {tot['extra_in_global']}（約每篇一個導語）；每段都吻合的文章 {len(perfect)}/{len(pairs)}")
    print("不吻合處的字元差異（服務重現 → global 實際）：")
    for (x, y), n in diffs.most_common(a.top):
        print(f"   {x} → {y}  ×{n}")
    if a.list_bad:
        print("未完全吻合的文章 id：", " ".join(bad))


def cmd_convert(a):
    conv = ServiceConverter(a.service, stock_dir=a.stock_dir)
    os.makedirs(a.out, exist_ok=True)
    n = 0
    for aid, art in load_articles(a.trad):
        out = conv.convert_article(art)
        out.setdefault("meta", {})["baseline"] = a.label
        with open(os.path.join(a.out, f"{aid}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        n += 1
    print(f"轉換 {n} 篇 → {a.out}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--service", default="refs/opencc-service", help="conversion service 原始碼目錄")
    p.add_argument("--stock-dir", default=None, help="標準詞典目錄；預設用本機 OpenCC wheel 內附的。要貼近生產環境，請指向 npm opencc 1.4.1 的 prebuilds/assets")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--trad", required=True)
    v.add_argument("--glob", required=True)
    v.add_argument("--top", type=int, default=15)
    v.add_argument("--list-bad", action="store_true")
    v.set_defaults(fn=cmd_validate)
    c = sub.add_parser("convert")
    c.add_argument("--trad", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--label", required=True, help="寫進每篇 meta.baseline，註明服務版本與 opencc 版本，例如 service-v1.12.0+opencc-1.3.1")
    c.set_defaults(fn=cmd_convert)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
