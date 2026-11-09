# data/ 的產生與重做

`data/` 與根目錄的 `data-prohibited.csv` 都被 git 排除，只存在 Pan 的本機。本文記錄每份資料怎樣產生、怎樣重做；決定與理由見 [consensus-summary.md](consensus-summary.md)。指令都在 repo 根目錄執行。

## 目錄

| 路徑 | 內容 |
|---|---|
| `data-prohibited.csv` | CMS 匯出，15 篇曾被 Gemini block 的文章（無標題列；session id、history JSON） |
| `data/ground-truth-draft.{json,md}` | 上述 15 篇的 Must-retain fact，Pan 已同意 |
| `data/sitemap-urls.tsv`、`search-urls.tsv`、`search-urls-china.tsv` | 候選網址 |
| `data/web-articles/`、`web-articles-china/` | 轉換後的文章（與 CMS 匯出同一結構，另有 `meta`） |
| `data/web-articles*-raw/` | 頁面 `__NEXT_DATA__` 裡的原始 article 物件，用來離線重轉與驗證 |
| `data/web-articles*-global/` | `global.hk01.com` 的簡體版文字，只用來驗證 baseline 重現 |
| `data/candidates*.tsv`、`candidates-screened.tsv`、`china-screened.tsv` | 篩選結果 |
| `data/baseline-service/`、`baseline-service-china/` | 主簡體 baseline（服務 HEAD 290bf22 + opencc 1.4.2） |
| `data/baseline-service-prod-v1.12.0/` | prod 現行版本的輸出，對照用 |
| `data/baseline-env/` | 重現服務用的固定環境（見下文） |
| `data/experiments/<實驗 id>/` | 評核框架的輸出：`runs.jsonl`（每行一個 Run）、`translations/`、`raw/`（原始回應）、`components.jsonl`（check 結果）、`report.md` |
| `data/gt-slices/`、`data/gt-drafts/` | 60 篇 Must-retain fact 的分片、草稿與審閱樣本 |
| `refs/`（git 排除） | `media-cms`、`media-cms-api`、`opencc-service` 的淺層 clone；opencc-service 已補全歷史；副本中非 `.example` 的 `.env*` 已刪除 |
| `.venv/`（git 排除） | Python 的 OpenCC，用於重現服務 |

## 抓取與轉換

```bash
# 抓文章（循序、間隔 2 秒、遇到 403/429/503 即停；已抓的會略過，所以中斷後重跑即可續跑）
python3 -I tools/hk01_collect.py fetch data/sitemap-urls.tsv data/search-urls.tsv --out data/web-articles --delay 2 --with-global
python3 -I tools/hk01_collect.py fetch data/search-urls-china.tsv --out data/web-articles-china --delay 2 --with-global

# 改了 tools/hk01_web.py 之後，用保存的原始物件離線重轉，再驗證（tools/check.sh 每次都會跑 verify）
python3 -I tools/hk01_collect.py reconvert --raw data/web-articles-raw --out data/web-articles
python3 -I tools/hk01_collect.py verify --raw data/web-articles-raw --out data/web-articles

# 彙總（題材、字數、長度分組）
python3 -I tools/hk01_collect.py report --dir data/web-articles --out data/candidates.tsv
```

轉換器的已知限制：table 與 faq 以結構化 component 保存，v1 不翻譯、不計入 Main component 字數；table 的合併儲存格不保留。

## 篩選

```bash
python3 -I tools/screen_candidates.py --web data/web-articles --glob data/web-articles-global \
  --cms-csv data-prohibited.csv --controls 5 --batch1 25 --review-min-chars 1000 --out data/candidates-screened.tsv
python3 -I tools/screen_china.py --web data/web-articles-china --glob data/web-articles-china-global --out data/china-screened.tsv
```

`--cms-csv` 只用來取出案件編號以計算每案名額。同案判斷只靠案件編號、系列名、標題相似度與相關文章連結；標題寫法差很多的同一事件抓不到，所以另有 `maybeSame` 欄提示，不自動剔除。

## 簡體 baseline

服務的做法（`refs/opencc-service` 的 `ConversionService`、`ConversionManager`）：整份 context 先 `JSON.stringify`，香港繁體→簡體分兩步，`hk2t.json` 再 `t2s.json`，最後 parse 回物件。`tools/service_baseline.py` 照做，並把設定檔裡 npm opencc 的標準詞典路徑換成固定版本的詞典。

固定環境 `data/baseline-env/`：

- `svc-v1.12.0/`：服務在 tag v1.12.0 的 `src/config`（`git -C refs/opencc-service archive v1.12.0 src/config`）。
- `opencc-{1.3.1,1.4.1,1.4.2}-assets/`：`npm pack opencc@<版本>` 後只解出 `package/prebuilds/assets/*`，不執行套件內任何程式。壓縮檔的 sha512 已核對：1.3.1 對 v1.12.0 的 yarn.lock，1.4.1 對 HEAD 的 yarn.lock，1.4.2 對 npm registry。1.4.1 與 1.4.2 在 248 篇上的輸出完全相同。

```bash
# 主 baseline（一定要傳 --label，它會寫進每篇的 meta.baseline）
.venv/bin/python -I tools/service_baseline.py --service refs/opencc-service --stock-dir data/baseline-env/opencc-1.4.2-assets \
  convert --trad data/web-articles --out data/baseline-service --label "service-HEAD-290bf22+opencc-1.4.2"
.venv/bin/python -I tools/service_baseline.py --service refs/opencc-service --stock-dir data/baseline-env/opencc-1.4.2-assets \
  convert --trad data/web-articles-china --out data/baseline-service-china --label "service-HEAD-290bf22+opencc-1.4.2"

# 驗證重現：prod 現行版本對 global 簡體版
.venv/bin/python -I tools/service_baseline.py --service data/baseline-env/svc-v1.12.0 --stock-dir data/baseline-env/opencc-1.3.1-assets \
  validate --trad data/web-articles --glob data/web-articles-global
```

驗證結果（2026-10-28）：global 簡體版對應服務 v1.12.0 + opencc 1.3.1，161 篇吻合 1893/1943 段；唯一殘留的字元差異是 1 篇的 `雪糕`（疑似人工校正）。其餘不吻合的段落來自 quote 與部分 highlight 沒有被簡體頁解析到，不是轉換差異。服務 HEAD 與 v1.12.0 的差異集中在 `舖`、`擡` 與 `雪櫃` → `冰箱`。更早的 v1.7.0–v1.10.1 用 `hk01-digital/media-opencc` 的 fork（1.1.9），沒有重現。

現有 15 篇 CMS 文章還沒有 baseline：CMS 匯出是 CSV，需要先寫載入。

## 翻譯評測（評核框架）

評核框架是 uv 管理的 Python package（`src/translation_eval/`），指令一律在 repo 根目錄、以 `uv run --locked python -I -m translation_eval <子命令>` 執行。設定在 `configs/`（實驗設定與 Approach 設定，路徑都相對於 repo 根目錄），輸出預設寫在 `data/experiments/<實驗 id>/`。設計見 [spec](../.scratch/evaluation-framework/spec.md)。

```bash
# 預覽：不送出任何請求，也不需要 key；印出請求數、估計輸入字數和 endpoint
uv run --locked python -I -m translation_eval translate --config configs/experiments/tracer-bullet.json --dry-run

# 真跑（會呼叫 API）：只由 Pan 在自己的終端機執行，不經過 agent。
# uv 的 --env-file 把 .env 載入這個行程的環境變數；程式只讀 GEMINI_API_KEY，不讀 .env 檔。
# agent 的 permission 設定已禁止 --env-file 這個參數。
uv run --locked --env-file .env python -I -m translation_eval translate --config configs/experiments/tracer-bullet.json --article 60403903 --limit 1

# 以下兩步不用 key，也不連網
uv run --locked python -I -m translation_eval check --config configs/experiments/tracer-bullet.json
uv run --locked python -I -m translation_eval report --config configs/experiments/tracer-bullet.json
```

- `translate` 可以中斷後重跑同一條指令：已有紀錄的 Run 會略過；`--redo-errors` 重做結果為 `error` 的 Run（`block`、`refusal`、`malformed`、`truncated` 是結果，不會重做）。`--article <id>`（可重覆）只跑指定文章，`--limit <N>` 最多執行 N 個 Run。
- `check` 與 `report` 每次都從 `runs.jsonl` 整份重算。`report` 發現有 `ok` 的 Run 還沒有 `check` 結果，或 `check` 之後規則、Term List、長度比例範圍改了，會停止，不產生報告；先重新執行 `check`。`check` 另寫 `components.jsonl`（每個 Component 的 Finding 與警示）和 `check-meta.json`（check 當時用的規則版本與輸入，report 靠它判斷結果有沒有過期）。規則版本由實作規則的原始碼的 hash 決定：程式的規則有任何改動，舊的 check 結果就視為過期，重跑 `check` 即可（只要幾秒，不呼叫 API）。所有 Run 都沒有譯文（例如全被 Block）時不需要 `check`。
- **Term List**：實驗設定的 `term_list` 指向 Term List 檔（放在 `data/`，格式由程式驗證，重覆條目會令指令失敗）。翻譯 prompt 只注入原文出現過的條目；`check` 在原文出現條目、譯文卻找不到任何允許的譯法時，記一個 Terminology Finding。沒有設 `term_list` 就不注入，也不做 Terminology 檢查。每個 Run 記下模型看到的那部分 Term List（原文詞與譯法）的 hash，只換排版不會變。`check` 用的 Term List 必須與翻譯時的同一份：改了原文詞或譯法，`check` 會拒絕執行（模型沒看過新的譯法），要用新的實驗 id 重新翻譯；只改嚴重度、類別或備註不影響 prompt，只需重跑 `check`（嚴重度、類別會令舊的 check 結果過期，備註不會）。格式（以虛構條目為例）：

  ```json
  {"terms": [{"source": "虛構市政廳", "category": "organization",
              "translations": {"trad-to-en": ["Fictional City Hall"]},
              "severity": "minor", "simplified_exceptions": [], "note": ""}]}
  ```

  `category` 是 `person`、`place`、`organization`、`legal`、`news-idiom` 之一；`severity` 預設 `minor`；允許的譯法可以多於一個，比對時不分大小寫。條目可以先寫 `trad-to-simplified` 的譯法（繁→簡還不能跑實驗，所以現在不會被用到）。
- **警示**（不計分，交人手看）：長度比例超出範圍、原文的數字在譯文找不到、直接引語數量不同、疑似 Refusal（譯文像拒絕語，而且長度比低於該 Direction 範圍的下限）、要翻譯的 Component 譯文與原文相同、不翻譯的 Component 被改動。數字的比對接受英文的寫法（`three`、`third`、`May`、`15 million`）。長度比例的範圍預設是繁→英 1.5–8.0（暫定；一篇真實譯文實測約 2.8–5.2），可以在實驗設定用 `length_ratio` 覆寫：`"length_ratio": {"trad-to-en": {"min": 2, "max": 6.5}}`。
- 終端只印 id、結果類別和數量；原始回應只存 response body，不存 request headers，也不含 key。
- 測試與離線：`tools/check.sh` 設了 `AI_TRANSLATION_OFFLINE` 並清掉所有 key，真 transport 見到這個變數就拒絕送出任何請求；測試一律注入假 transport。
- 煙霧測試（Pan，2026-11-09 已通過）：對 1 篇文章跑一次後，回報結果類別和數量，不貼內容。若每個 Run 都是 `error` 且原因是 `HTTP 400 INVALID_ARGUMENT`，先看 `raw/` 裡的回應：最可能是 safety settings 的 category 或 key 的 header 名稱與 Google 現行的不符（見 `src/translation_eval/providers/gemini.py`）。

## Ground truth

```bash
# 驗證並渲染（任何 id 或 anchor 對不上就失敗；另寫一份「數字或引語不在原句」的警告 .warnings.tsv）
python3 -I tools/render_ground_truth.py data/gt-drafts/draft-{a,b,c,d}.json --web data/web-articles --web data/web-articles-china --out data/gt-drafts/all.md
python3 -I tools/render_ground_truth.py data/ground-truth-draft.json --csv data-prohibited.csv --out /tmp/gt.md

# 審閱樣本（固定種子，可重現；包含全部 NOT-NEGATIVE? 的文章與有 item 的對照文章）
python3 -I tools/sample_ground_truth.py data/gt-drafts/draft-{a,b,c,d}.json --general 5 --political 5 --seed 1 --out data/gt-drafts/review-sample.json

# 精簡列印一篇文章（id｜type｜文字）
python3 -I tools/dump_article.py data/web-articles/<id>.json
```
