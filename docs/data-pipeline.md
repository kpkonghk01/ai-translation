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
| `data/baseline-service/`、`baseline-service-china/`、`baseline-service-cms/` | 主簡體 baseline（服務 HEAD 290bf22 + opencc 1.4.2） |
| `data/cms-articles/` | CMS 匯出的 15 篇轉成的文章檔（`import-cms` 產生，與網上文章同一結構，`meta.source` 是 `cms`） |
| `data/baseline-service-prod-v1.12.0/` | prod 現行版本的輸出，對照用 |
| `data/baseline-env/` | 重現服務用的固定環境（見下文） |
| `data/experiments/<實驗 id>/` | 評核框架的輸出：`runs.jsonl`（每行一個 Run）、`translations/`、`raw/`（原始回應）、`components.jsonl`（check 結果）、`report.md` |
| `data/gt-slices/`、`data/gt-drafts/` | 60 篇 Must-retain fact 的分片、草稿與審閱樣本 |
| `data/articles-first-batch/`、`data/baseline-first-batch/` | 第一批實驗（`first-batch`）的 60 篇文章與對應的簡體 baseline，從上面各目錄複製而來（做法見「翻譯評測」） |
| `data/gold-set.json`、`data/gold-parts/` | Gold set（Judge 校準用的已知答案項目；`gold-set.json` 是唯一來源）；`gold-parts/` 只是撰寫時的分檔、文章分配與審查筆記，不再維護 |
| `data/term-list.json`、`data/term-list-review.md` | Term List（原文詞 → 指定譯法）與給 Pan 抽查的清單（含核對來源） |
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

CMS 的 15 篇：先轉成文章檔，再用同一個工具產生 baseline（標籤與其他 baseline 一致）：

```bash
uv run --locked python -I -m translation_eval import-cms --csv data-prohibited.csv --out data/cms-articles
.venv/bin/python -I tools/service_baseline.py --service refs/opencc-service --stock-dir data/baseline-env/opencc-1.4.2-assets \
  convert --trad data/cms-articles --out data/baseline-service-cms --label "service-HEAD-290bf22+opencc-1.4.2"
```

`import-cms` 取每列對話紀錄裡第一個 user turn 的 JSON，session id 當文章 id。只保留已知的欄位：不認識的欄位若有文字、或有不認識的 Component type，指令失敗並列出，不會靜默丟掉；全部通過才寫出。`tools/check.sh` 在本機有 `data-prohibited.csv` 時，會把它轉到暫存目錄檢查覆蓋，並與 `data/cms-articles` 比對。baseline 工具遇到檔名不是文章 id（數字或 24 位 hex）的 `.json` 會失敗，不會略過。匯入成 Run 見「翻譯評測」的 `import-baseline`。

## 翻譯評測（評核框架）

評核框架是 uv 管理的 Python package（`src/translation_eval/`），指令一律在 repo 根目錄、以 `uv run --locked python -I -m translation_eval <子命令>` 執行。設定在 `configs/`（實驗設定與 Approach 設定，路徑都相對於 repo 根目錄），輸出預設寫在 `data/experiments/<實驗 id>/`。設計見 [spec](../.scratch/evaluation-framework/spec.md)。

```bash
# 預覽：不送出任何請求，也不需要 key；印出請求數、估計輸入字數和 endpoint
uv run --locked python -I -m translation_eval translate --config configs/experiments/tracer-bullet.json --dry-run

# 真跑（會呼叫 API、要 key）：一律經 tools/run-real.sh，Pan 或 agent 都用這個入口。
# 入口只放行 translate、judge、calibrate、crosscheck、providers，並用 uv 的 --env-file 把 .env 載入這個行程的環境變數；
# 程式只從環境變數讀 key，不讀 .env 檔。agent 的 permission 設定禁止它讀 .env、也禁止它的指令帶 --env-file，所以它只能經這個入口用 key。
tools/run-real.sh translate --config configs/experiments/tracer-bullet.json --article 60403903 --limit 1

# 以下兩步不用 key，也不連網
uv run --locked python -I -m translation_eval check --config configs/experiments/tracer-bullet.json
uv run --locked python -I -m translation_eval report --config configs/experiments/tracer-bullet.json
```

- **第一批實驗**（`configs/experiments/first-batch.json`）：60 篇（general 30 篇，含 5 篇對照；china-political 30 篇）× 繁→英與繁→簡 × 方案 A（`gemini-3.5-flash-lite`，每篇 3 次，另加 `main-only` 版本）、F（Hy-MT2-30B-A3B，逐段翻譯；Tencent 對部分請求回空內容，Run 大多拿不到完整譯文，結果會是 `empty`）、F7（Hy-MT2-7B，同樣逐段翻譯）、G（Qwen3.6-35B-A3B，經 OpenRouter）；繁→簡另有 baseline E。這批沒有 Judge，只做翻譯、`check` 與 Block／Refusal 報告。F、F7、G 要先用 `providers` 確認 provider 清單、寫進各自 Approach 設定的 `routing` 才能真跑。一個實驗只有一個文章目錄，而兩組文章原本在不同目錄，所以複製成同一個（這樣同一份報告才能比較 political 與 general 的淡化率差距）：

  ```bash
  mkdir -p data/articles-first-batch data/baseline-first-batch
  python3 -I -c '
  import json, os, shutil
  for article_id in json.load(open("configs/experiments/first-batch.json"))["articles"]["ids"]:
      for src, dst in (("data/web-articles", "data/articles-first-batch"), ("data/web-articles-china", "data/articles-first-batch"),
                       ("data/baseline-service", "data/baseline-first-batch"), ("data/baseline-service-china", "data/baseline-first-batch")):
          if os.path.exists(f"{src}/{article_id}.json"):
              shutil.copyfile(f"{src}/{article_id}.json", f"{dst}/{article_id}.json")
  '
  ```

- `translate` 可以中斷後重跑同一條指令：已有紀錄的 Run 會略過；`--redo-errors` 重做結果為 `error` 或 `empty`（空內容：provider 正常結束，但沒有任何輸出，見 GLOSSARY）的 Run（`block`、`refusal`、`malformed`、`truncated` 是結果，不會重做）。重做的 Run 取代舊紀錄，並在 `redone_from` 記下舊紀錄的結果（`error` 或 `empty`）；報告每個 Approach × Direction 一節有「重做過的 Run」一行，列出重做之前與之後的結果。空內容不在 Run 內自動重試。`--article <id>`（可重覆）只跑指定文章，`--limit <N>` 最多執行 N 個 Run。
- **展示報告**（給非技術讀者，例如上司）：`showcase` 讀 `runs.jsonl`，寫出一頁 HTML（`showcase.html`，在實驗的輸出目錄；在 `data/` 底下，不進 git）。頁面只有計數，不含文章內容，可以直接給上司看。內容是結論、各模型的結果表、測了什麼、重複跑與只送標題與正文的結果、完整數字；沒有文章內容、沒有文章編號，也沒有任何外部資源（離線也能開）。模型名用 Approach 設定的 `display_name`（baseline 在實驗設定的 `baselines` 條目也可以寫），沒有就用 `model`，不用代號；`display_name` 不進設定 hash，改名不會讓已有的 Run 被標成「與目前設定不同」，兩個模型的名稱相同會停止（明寫的重名在載入設定時就停止，沒寫而 model 相同的在 `showcase` 時停止）。主表只看完整文章、每篇每個方向的第 1 次，每個模型的分母才一樣；組別（一般題材、中國政治題材）來自 ground truth，沒有就是全部文章一組。不需要先 `check`，隨時可以執行；還沒跑完時頁面頂部寫「仍在進行」。Judge 的結果不放在這一頁。

  ```bash
  uv run --locked python -I -m translation_eval showcase --config configs/experiments/first-batch.json
  ```
- **抽樣實驗**（縮小 Judge 的範圍）：Judge 的請求數跟要評的 Run 數成正比（Judge 逐 Component 評，每個 Component 兩次，所以一個 Run 約幾十個請求），全部評太貴太慢。`sample` 從一個實驗抽出幾篇文章，把它們的第 1 次、完整文章的 Run 紀錄與譯文**原樣複製**成新的實驗，之後 `check`、`judge`、`report` 都對新實驗執行，Gate 與審閱清單自成一套：

  ```bash
  uv run --locked python -I -m translation_eval sample --config configs/experiments/first-batch.json --id first-batch-sample --per-group 10 --controls 3 --seed 1
  ```

  每組（ground truth 的題材，或 `group_overrides` 的分組）抽 `--per-group` 篇，對照文章另外抽 `--controls` 篇；組裡的文章比要抽的少就全取，沒有 ground truth 的文章不抽。依據只有種子（種子加文章 id 的 hash）與 ground truth，不看 Run 的結果，所以不能看結果挑。新實驗的設定檔寫在來源設定檔旁邊（`<新實驗 id>.json`）：與來源一樣，只是文章換成抽中的、每個 Approach 只有 1 次與完整文章；baseline 的 Run 一起帶過去。`sample.json` 記下從哪裡抽、種子與抽中的文章。新實驗或設定檔已存在、缺少要複製的檔案時，什麼都不寫。要等來源的翻譯跑完、`--redo-errors` 補完之後再抽，才有最終的譯文；來源還沒跑的 Run 不會被複製，新實驗的 `translate` 會補跑。
- **含原文與譯文的展示頁**：`showcase-text` 另寫一頁 `showcase-text.html`（同一個輸出目錄），讓人看到每個數字背後是什麼樣：成功的例子（同一段原文，各模型的譯文並排；優先用對照文章，要所有模型都成功翻譯才能比）和沒有成功的例子（每種結果各一張卡，附該 Run 的文章的原文片段，並說明回來的是什麼，不放 provider 回應裡的字）。片段取第一個 40 至 160 字的段落，沒有就取第一個有字的、超過 160 字的切掉並標「…」，原文被切時譯文也只取開頭 160 字。**這一頁含真實文章的原文與譯文，只在本機看，不要轉發，也不要上傳；頁面頂部有警語。** 要給別人看的是只有計數的 `showcase.html`：它不會因為這一頁而有任何文章內容，`showcase` 也不會寫這一頁。終端只印路徑。

  ```bash
  uv run --locked python -I -m translation_eval showcase-text --config configs/experiments/first-batch.json
  ```
- `check` 與 `report` 每次都從 `runs.jsonl` 整份重算。`report` 發現有 `ok` 的 Run 還沒有 `check` 結果，或 `check` 之後規則、Term List、長度比例範圍改了，會停止，不產生報告；先重新執行 `check`。`check` 另寫 `components.jsonl`（每個 Component 的 Finding 與警示）和 `check-meta.json`（check 當時用的規則版本與輸入，report 靠它判斷結果有沒有過期）。規則版本由實作規則的原始碼的 hash 決定：程式的規則有任何改動，舊的 check 結果就視為過期，重跑 `check` 即可（只要幾秒，不呼叫 API）。所有 Run 都沒有譯文（例如全被 Block）時不需要 `check`。
- **Judge（MQM）**：實驗設定的 `judging` 列出 Judge 設定檔（欄位與 Approach 設定相同，另加 `role`：`primary` 決定 Gate，`experiment` 只作實驗；`temperature` 固定 0，只用 `article-json`），並可以寫 `same_family_exceptions`、`mqm_run_limit`；頂層的 `weights` 是每個 Severity 的罰分（預設 minor 1、major 5、critical 25）：

  ```json
  "judging": {"judges": [{"config": "configs/judges/j1.json"}],
              "same_family_exceptions": [{"approach": "a", "judge": "j1", "reason": "中國組以 Gemini 為主 Judge（Q31）"}],
              "mqm_run_limit": {"a": 1}},
  "weights": {"minor": 1, "major": 5, "critical": 25}
  ```

  ```bash
  # 會呼叫 API、需要 key：經 tools/run-real.sh。--dry-run 先看請求數
  tools/run-real.sh judge --config <實驗設定> [--task mqm|fact|screen] [--dry-run] [--article <id>] [--limit <N>] [--redo-errors]
  ```

  每個 ok 的 Run（包括 baseline）的每個要翻譯的 Component（含 title 和 tags），由每個 Judge 評兩次，每次一個獨立的請求，附前後各一個要翻譯的 Component 當前後文、Direction 的語域要求，和原文出現的 Term List 條目。Judge 只輸出 Finding 和 Source defect，不輸出分數。輸出裡的片段必須是原文或譯文的逐字子字串（連續空白忽略）；不合格的那一條丟掉，記為無效並保留原文，Dimension 或 Severity 不在清單內的也一樣；整個輸出的結構不符則該請求是 `malformed`。請求的結果寫在 `judge-calls.jsonl`（一個請求一行），原始回應在 `judge-raw/`；已有紀錄的請求略過，可以續跑，`--redo-errors` 只重做 `error` 與 `empty`（`block`、`refusal`、`malformed` 不重做）；重做的請求記下 `redone_from`（舊紀錄是 error 還是 empty）。Judge 與被評的 Approach 同家族時，指令拒絕執行，除非 `same_family_exceptions` 針對這個組合寫了理由。`check` 不必先跑。
  `report` 計算一致性和分數：兩次的 Finding 若同一 Dimension、譯文片段重疊（譯文片段都是空的就看原文片段），就是同一個 Finding，Severity 不同取較高者；配對不到的是 uncertain；只有一次成功時全部是 uncertain；兩次都失敗是 Unjudged，並記下原因。罰分 = Severity 的權重，Fidelity-of-force 不計分；Deterministic 的 Finding 一律計入（包括漏段和多出的 Component）；Main 與 Peripheral 分開，列出「只計一致的」與「連 uncertain 一起計」兩個數字，以及每千字原文的罰分（分母是這些 Run 的原文總字數）。改了權重只要重跑 `report`。報告另有 Source defect 清單，以「文章、Component、原文片段」去重。
- **Must-retain fact 與 Gate**：實驗設定寫 `ground_truth`（現有格式的 ground truth 檔，可以多個；`status` 是 `draft` 或 `approved`，寫進報告開頭）後，`judge` 預設連 fact 任務一起跑（`--task mqm` 或 `--task fact` 只跑其中一個）。`judging.primary_judges` 為每個分組指定決定 Gate 的主 Judge（Judge 的 `role` 必須是 `primary`）；沒有指定的分組用第一個 `primary` 的 Judge，報告標示「主 Judge 暫定」。`ground_truth.group_overrides` 把文章從它的 domain 另列成一組（Q34）：

  ```json
  "ground_truth": {"paths": ["data/ground-truth-draft.json"], "status": "draft", "group_overrides": {"<文章 id>": "q34"}},
  "judging": {"judges": [...], "primary_judges": {"general": "j1", "china-political": "j1"}}
  ```

  ground truth 的驗證與 `tools/render_ground_truth.py` 相同（文章存在、Component id 存在、anchor 是逐字子字串、kind 與 domain 在清單內），另外要求 Component 是要翻譯的類型、同一個 Component 內沒有兩條一樣的事實；不合格就停止，錯誤只列文章 id 與 Component id。ground truth 內不在實驗裡的文章略過（報告開頭列出篇數，也列出實驗內沒有 ground truth 的文章，id 打錯時看得到）；原文瑕疵（`source_defects`）的 Component 與 anchor 也要驗證。每個含 Sensitive sentence 的 Component，由每個 Judge 評兩次，一個請求帶這個 Component 內所有句子（anchor）和它們的事實，Judge 對每條事實回 verdict（Present／Weakened／Absent）、類型（(a)–(f) 或 `amplification`）、譯文引用和理由；複合事實只要任一要點被淡化就是 Weakened。Present 與 Weakened 的引用必須是譯文的逐字子字串，否則那一條 verdict 作廢（列為無效，原文保留）。事實的 key 是文章 id、Component id 和事實文字的 SHA-256：事實文字或 anchor 改了，舊的 fact 紀錄過期，下次 `judge` 會重送，`report` 在重判之前把它們列為「過期」。譯文缺少含 Sensitive sentence 的 Component 時，不送給 Judge，它的所有事實直接是 Absent。
  `report` 的 Gate 一節按「Approach × Direction × 分組」列出：淡化率 = 在任一 Run 失敗過的不重覆句子 ÷ 有結果的不重覆句子（一句只要有一條事實最終是 Weakened 或 Absent 就失敗；有事實仍待審而還沒有結果的句子不在分母），附單尾 Clopper–Pearson 95% 上限、涉及的文章數、每個 Run 的淡化率、失敗事實按類型和事實種類的分佈，Amplification 另計、不計入淡化率；block、refusal 和其他失敗的 Run 沒有譯文，不在分母，列在「未計入的 Run」。最終 verdict 只有「一致」的結果（兩次都有效而且相同）；uncertain（不同，或只有一個有效的 verdict）、無效、Unjudged、尚未評完和過期的事實都要等審閱，有任何一條時 Gate 標為「未完成」並列出數量（附分母：核對過的事實 × Run 數）。沒有任何句子有結果、計畫的 Run 還有沒有紀錄的、有 `error` 或 `empty` 的 Run（要先 `--redo-errors`）時也是「未完成」；block 與 refusal 是結果，不算缺漏。同一個 Component 內 anchor 一樣的兩個 item 視為同一句。「political 與 general 的差距」一節把每個 Approach 的兩組淡化率並列。校準（ticket 14）做完之前，每個分組旁寫「Judge recall 未量度」。
- **與 baseline 比較與摘要版**：實驗設定的 `comparison` 指定質素的 baseline（方案 A 的 id），以及某個 Direction 另外要比的 baseline：

  ```json
  "comparison": {"baseline": "a", "also_against": {"trad-to-simplified": ["e"]}}
  ```

  `report` 的「與 baseline 比較」一節，把每個 Approach 和 baseline（`import-baseline` 匯入的也算）的 MQM 分數對質素 baseline 比，繁→簡另外對 `also_against` 的 baseline 比（自己不與自己比；A 對 E 與 E 對 A 都會列，各自以被當作 baseline 的那一邊篩選文章；某個 Direction 沒有 Run 的組合，例如 E 的繁→英，不列）。每個 `primary` 的 Judge 各比一次。只比 `full` 的 Run，而且只在三種文章上比：baseline 這個 Direction 的所有 Run 都有紀錄而且都是 `ok`（被 Block 或有失敗的文章不比；紀錄比計畫少也不比）、被比的一邊至少有一個在 MQM 評核範圍內的 `ok` 的 Run（沒有譯文的文章沒有分數，不當零分；它的 Block 在 Block 率呈報）、兩邊都評完。多個 Run 時每篇文章取 `ok` 的 Run 的平均，並列出平均用了幾個 Run、計畫幾個（不夠就標示）。每個比較列出比較了幾篇文章、沒比的文章 id（全部列出）和原因、Main 與 Peripheral 的每千字罰分（只計一致的／連 uncertain 一起計）與差（被比的 − baseline，正數是罰分較多）、Unjudged 的 Component 數。`comparison` 指向不存在的 Approach 或 baseline、不在實驗內的 Direction、在要比的 Direction 沒有 `full` 的 Run 的對象，或質素 baseline 不是 API 跑的 Approach，會在載入設定時失敗，不會悄悄什麼也不列。
  `report` 同時寫 `summary.md`（摘要版）：內容與完整版一樣的數字，但沒有原文、譯文、事實文字、Judge 寫的理由、Source defect 的片段與說明、同家族例外的理由和審閱備註；Source defect 只列文章 id 與 Component id，provider 或檔案裡的字串（Block 原因、模型版本、baseline 標籤等）只放得出是代碼的（字母、數字和 `_:.-`，不含空白，60 字以內），其他寫「（其他）」；Source defect 在摘要版每個 Component 一行（有幾處、哪些 Judge）。摘要版可以交給 Ivan／Stephen，是否外傳由 Pan 決定。報告開頭有實驗設定、各 Approach 的模型版本與 provider、各任務的 prompt hash、Judge 設定、權重、正規化方式、同家族的例外、ground truth 的狀態與 hash、baseline 標籤和審閱完成度。
- **Gold set 與 Judge 校準**：實驗設定寫 `"calibration": {"gold_set": "data/gold-set.json"}`（Gold set 是 Pan 手寫的，放在 `data/`）。格式：

  ```json
  {"items": [
    {"id": "g01", "category": "annotated", "direction": "trad-to-en", "domain": "general", "source": "…", "translation": "…",
     "context_before": {"source": "…", "translation": "…"}, "context_after": null,
     "findings": [{"dimension": "Accuracy", "severity": "major", "source_span": "…", "target_span": "…"}]},
    {"id": "g02", "category": "variant", "direction": "trad-to-en", "domain": "china-political", "source": "…", "translation": "…（人手寫的 Softening 變體）",
     "ground_truth": {"article_id": "<文章 id>", "component_id": "<Component id>"},
     "facts": [{"fact": "<ground truth 那條事實的文字>", "verdict": "Weakened", "weakening_type": "c"}]},
    {"id": "g03", "category": "clean", "direction": "trad-to-en", "domain": "general", "source": "…", "translation": "…"}
  ]}
  ```

  類別：`annotated`（真實候選譯文的人手標註，標註要完整：沒標到的 Finding 算 Judge 多報）、`variant`（人手寫的 Softening 變體：指向 ground truth 的 Component，列出預期 verdict 與類型；至少一條預期是 Weakened 或 Absent）、`clean`（無錯樣本：沒有預期的 Finding，事實預期都是 Present）。目標約 50／25／25（Q14，只用來提醒）。`gold check` 驗證並列出各類別、Direction、domain 的數量；驗證規則：預期 Finding 的片段是原文或譯文的逐字子字串（連續空白忽略）；`facts` 指向的 Component 在 ground truth 有 Sensitive sentence、事實文字是那個 Component 的事實、項目的原文與那個 Component 的原文相同（連續空白忽略）、項目的 `domain` 與 ground truth 那篇文章的 domain 相同（`domain` 決定 Gate 旁邊的 recall 與 Q31 的比較看哪一格，標錯就放進錯的格）；項目 id 不重覆。錯誤只列項目 id 與原因。`gold sample --count 30 --seed 1` 以固定種子抽項目，寫成 `gold-sample.md`（含原文與譯文，只在本機看）給 Pan 審閱。

  ```bash
  # 會呼叫 API、需要 key：經 tools/run-real.sh。--dry-run 先看請求數
  tools/run-real.sh calibrate --config <實驗設定> --judge <id> [--dry-run] [--limit <N>] [--redo-errors]
  # 不寫 --judge：不送請求，只重算結果
  uv run --locked python -I -m translation_eval calibrate --config <實驗設定>
  ```

  `calibrate --dry-run` 另印估計的輸入字數。`calibrate --judge` 對每個項目的 MQM 任務、每個有 `facts` 的項目的 fact 任務，各送兩個獨立的請求，格式與正式評核完全相同（項目自己的前後文、ground truth 那句的全部事實）。已有新紀錄的請求略過；prompt、Judge 設定或項目送給 Judge 的內容（Direction、原文、譯文、前後文，和它指向的 ground truth 句子與事實）改了，舊紀錄過期，會重送；預期答案與備註沒有送給 Judge，改了不用重送，指標由程式重算。結果寫在 `gold-calls.jsonl`，並重寫 `calibration.md`（各 Judge 的指標，只有數字）和 `calibration-disagreements.md`（Judge 對標註、Judge 對 Judge 的分歧，含原文與譯文）。每個 Judge（不分 primary 或 experiment）都可以校準；選主 Judge 要先把候選的都跑過。指標（全部、按 Direction、按 domain、Direction × domain，另有 variant 按 domain，各一組，分子分母都列）：預期 Finding 的 recall（只計一致的／連 uncertain 一起計）與 Severity 一致率；驗證後 precision（只看 annotated 項目）；clean 項目上的誤報；無效 Finding 率；兩次一致率（Finding、事實 verdict）；uncertain 與一致兩組 Finding 的錯誤率（檢驗「跑兩次」：uncertain 的錯誤率若沒有比較高，不一致就沒有提供資訊）；事實 verdict 的準確率、Weakened 與 Absent 各自的 recall、失敗的事實（Weakened 或 Absent）的 recall（uncertain 與 Unjudged 算沒有抓到：Judge 被擋的項目，預期的 Finding 與事實留在分母，不能因為被擋讓 recall 好看）、預期 Present 被判成失敗的誤報率；Unjudged 率與「尚未評完」（還沒有兩個請求，不在分母）分開列。合格線（recall、precision）要等第一批數字出來才訂，程式不判合格與否。Q31 比較 1（Gemini 與 Qwen 在 political 與 general 變體上的 recall）就是 `calibration.md` 裡兩個 Judge 的「variant × domain」分組。分歧清單另列 Severity 不同的 Finding 與類型不同的事實 verdict。
  `report` 讀最新的校準結果：Gate 每個分組旁邊列主 Judge 的 recall（Gold set 上預期失敗的事實，按該 Gate 的 Direction 與 domain 取，在 `calibration.md` 找「Direction × domain」那一組；Q34 這類覆寫分組不分 domain）；沒有校準、校準的請求都過期、Gold set 在這一格沒有預期失敗的事實、或這一格的項目還沒有請求結果，就明確寫「Judge recall 未量度」並寫明是哪一種原因。報告另有「Judge 校準摘要」一節（只有數字，摘要版也有）。Gold set 驗證不合時 `report` 也停止。
- **Ground truth 交叉檢驗**：模型在沒看過 ground truth 的情況下，盲選每篇最敏感的句子、盲抽 Must-retain fact，再與 ground truth 對照。實驗設定寫 `crosscheck`（兩個都是 `judging.judges` 裡的 Judge 設定，所以 OpenRouter 的允許清單、資料保護欄位和 key 的讀法都沿用；要有 `ground_truth`）：

  ```json
  "crosscheck": {"judge": "<Qwen 的 Judge id>", "reference_judge": "<Gemini 的 Judge id>"}
  ```

  ```bash
  # 會呼叫 API、需要 key：經 tools/run-real.sh。--dry-run 先看請求數
  tools/run-real.sh crosscheck --config <實驗設定> [--dry-run] [--article <id>]... [--limit <N>] [--redo-errors]
  ```

  有 ground truth 的文章（含沒有敏感句的對照文章）各用 `judge` 跑一次；`domain` 是 `china-political` 的文章另外用 `reference_judge` 跑一次（沒設就不跑）。每篇每個模型一個請求，不跑兩次（這是找分歧給 Pan 看，不是判定）。模型收到的只有標題和各 Component 的原文（`code` 等不翻譯的 Component 不給），沒有 ground truth 的任何東西。模型選的句子必須逐字取自它指的那個 Component 的原文（忽略空白；同一個欄位內，不可以跨過 quote 的出處、gallery 的多個說明這類欄位的邊界），否則那一條丟掉並計數、保留原文供審閱；沒有任何有效事實的句子、kind 不在清單內的事實也一樣（清單分開列「丟掉的句子」與「丟掉的事實」，盲抽與對照的模型各列各的）；同一句選兩次只留一個、事實合併；輸出結構不符則該請求是 `malformed`。結果記在 `crosscheck-calls.jsonl`（原始回應在 `crosscheck-raw/`），已有新紀錄的請求略過，文章、prompt 或 Judge 設定改了才重送，`--redo-errors` 重做 `error` 與 `empty`（記 `redone_from`）；報告對 error、empty 的請求寫「沒有拿到結果，先用 --redo-errors 重做」，被擋或拒絕才說「這本身就是結果」。分歧清單 `crosscheck.md`（含原文與事實文字，只在本機看；終端只印數量）開頭寫明 ground truth 的 hash、用的模型、每篇最多選 10 句、實驗內沒有 ground truth 的文章（不送給模型），接著按 domain 列數量，再按文章列：兩邊都有的句子（同一個 Component、位置重疊，只是相鄰不算；ground truth 的 anchor 可以只是模型選的整句的一段；一個模型句子蓋住幾個 anchor、或幾個模型句子蓋住同一個 anchor，連在一起的放同一組，事實與差異按整組算）把兩邊的事實並排，阿拉伯數字、接量詞的中文數字（三名、十年；統一、唯一的「一」不算）和引號內文字（巢狀的引號算在最外層那一段）的差異由程式標出（「30 萬」與「300,000」寫法不同也會標出，要人看）；只有模型選了的句子；只有 ground truth 有的句子。模型選滿 10 句的文章會註明：ground truth 的句子更多時，「只有 ground truth 有」可能是上限造成的。每個請求的結果都寫出來：**被擋（block）或失敗本身就是結果，與「評了但漏看」不同**，清單分開計；模型被擋時，對照模型的結果照樣列出（全部事實都列出）；對照模型沒有結果也明講。`china-political` 的文章整篇標明「只作參考，不用來改 ground truth」（Qwen 是被測方，單看它的結果分不出「ground truth 寫多了」和「Qwen 自己漏看」），並另列「Gemini 抽到、Qwen 沒抽到的事實」：整句沒選到的列出那一句的全部事實；兩邊都選了的句子，列出沒有被涵蓋的事實（數字、中文數字、引語都涵蓋，而且字元二連字重疊到一半以上；不看 kind，因為同一件事兩個模型可能標成不同的種類。這是粗略的啟發式，只作參考）。**`crosscheck` 從不寫 ground truth 檔**：要不要改由 Pan 決定（有測試）。
- **Judge 實驗（Q31）**：實驗設定的 `judging.judges` 裡角色是 `experiment` 的 Judge（Qwen），在報告有獨立一節「Judge 實驗（Q31）」，對這一組的主 Judge（`primary` 角色，Gemini；按 Gate 的分組取，所以 `primary_judges` 與 `ground_truth.group_overrides` 怎樣改 Gate，這一節就怎樣跟，每一列都寫出對的是哪個主 Judge）比較。**這一節只作實驗，不影響 Gate、分數和審閱清單**（它們只用 `primary` 角色的 Judge；有測試：換成結果天差地遠的 experiment Judge，這三樣一模一樣）。判斷門檻（recall 差多少算有問題）不寫死，只列數字。experiment 的 Judge 要和被評的 Approach 同家族時，仍要在 `same_family_exceptions` 寫明理由（Qwen 判自己的譯文就是這種有理由的例外）。三個比較加一個單獨的項目：
  - **比較 1（合成變體）**：從校準結果取兩個 Judge 在 political 與 general 的變體（Gold set 的 `variant`）上，失敗的事實的 recall，列出兩者的差距，和各自的 political − general；Judge 被擋、uncertain、尚未評完、請求過期的數量寫在各自的數字後面，免得兩邊的項目不一樣卻看不出來。變體與被測模型無關，沒有「Qwen 判自己」的問題。要先對兩個 Judge 都跑 `calibrate --judge`。
  - **比較 2（真實譯文）**：同一批 `ok` 的 Run 的譯文，兩個 Judge 的 fact 任務各判一次（`judge` 預設兩個都跑）。主 Judge 判 Weakened 或 Absent、experiment 的 Judge 判 Present 的事實是「疑似漏看」；按分組和譯文來源（與這個 Judge 同家族的 Approach 的譯文＝自己的譯文，其他 Approach 與 baseline 的譯文＝其他；Approach 已不在實驗設定內的另列）分開統計；單位是「事實 × Run」，同一條事實在幾個 Run 就算幾次（包括 main-only 的 Run）。分母是主 Judge 判失敗、而且 experiment 的 Judge 有最終 verdict（兩次一致）的事實；experiment 的 Judge uncertain、無效、Unjudged 或還沒評完的另列，不混進「漏看」；主 Judge 沒有最終 verdict 的事實不比，但數出來；反方向（experiment 判失敗而主 Judge 判 Present）和兩邊一致的也列。疑似漏看的事實列出位置（文章、Component、事實的種類）；原文、譯文和兩個 Judge 的引用與理由寫在 `experiment-judge-misses.md`（含原文，只在本機看），供人手抽查；沒有疑似漏看、或沒有 experiment 的 Judge 時，`report` 會刪掉舊的這個檔案（舊的含原文，不能留著讓報告指向一份過期的清單）。
  - **比較 3（盲抽）**：有交叉檢驗的結果時，列出 china-political 文章裡兩個模型都有結果的篇數、對照模型抽到而盲抽的模型沒抽到的句子數（內容在 `crosscheck.md`）；盲抽的模型被擋或失敗、還沒有結果、以及對照模型沒有結果的篇數分開列，不算進句數。
  - **Unjudged 率與無效 Finding 率**：experiment 的 Judge（並列主 Judge 作對照）按分組列：MQM、事實、全文篩查三個任務的 Unjudged（兩次請求都失敗，包括輸入端被擋；MQM 與事實的單位是 Component，篩查是 Run）和原因、block 的原因碼（輸入端與輸出端的前綴不同）、無效 Finding 率、無效的 verdict 率。「根本不給評」本身就是結果，與「評了但漏看」分開。
  摘要版（`summary.md`）也有這一節，但 block 的原因碼只放得出是代碼的，其他寫「（其他）」；疑似漏看只列位置，不含任何文字。
- **全文篩查**：`judge` 預設連全文篩查一起跑（`--task screen` 只跑它；`judging.screening: false` 可以關掉預設，`--task screen` 仍可明講要跑）。每個 ok 的 Run、每個 Judge 發兩個獨立請求，輸入是整篇的原文與譯文（逐 Component，譯文缺少的 Component 是 null），Judge 先列出關鍵事實再逐條核對，只輸出 Weakened、Absent 或 Amplification 的條目（附類型）。原文片段必須是某一個 Component 原文的逐字子字串（也決定條目屬於哪個 Component），譯文片段必須是譯文某處的逐字子字串；Weakened 與 Amplification 要有譯文片段，Absent 可以沒有。不合格的條目丟掉，記為無效並保留原文。兩次的條目若 verdict 相同而且譯文片段重疊（Absent 看原文片段），就是同一處、一致，否則 uncertain；只有一次成功時全部是 uncertain；兩次都失敗是 Unjudged。結果寫在 `judge-calls.jsonl`（Component 欄是 `*`）。篩查的結果**不決定 Gate，也不計分**，全部進審閱清單的必看（和 MQM 產生的 Fidelity-of-force Finding 是同一組必看，分兩個小節）。報告的 Judge 評核一節列出篩查評完幾個 Run、Unjudged 率和條目數；Judge 設定一節列出各任務的 prompt hash。Fidelity-of-force Finding 不計分，審閱決定只表示看過了，不改變報告裡的數量。
- **人手審閱**：`review` 產生審閱清單 `data/experiments/<實驗 id>/review.md`（含原文和譯文，只在本機；終端只印數量），要先有最新的 `check` 結果：

  ```bash
  uv run --locked python -I -m translation_eval review --config <實驗設定>
  ```

  清單分「必看」與「抽查」。必看：Gate 失敗的事實（包括漏段）、uncertain 或無效的事實 verdict、Unjudged 的事實、uncertain 的 Finding、無效的 Judge 輸出、Judge 沒評成的請求（MQM 或全文篩查）、全文篩查的結果、MQM 的 Fidelity-of-force Finding、疑似 Refusal 和其他 Deterministic 警示。抽查：從其餘項目（通過的事實、一致的 Finding、Deterministic Finding）以固定種子抽出；比例與種子寫在實驗設定的 `"review": {"sample_rate": 0.1, "seed": 1}`（這是預設）。「種子加項目 key」的 hash 決定一項抽不抽：同一個輸入再跑，抽出的相同；之後有更多結果進來，已經抽到的不會變。只審 `role` 是 `primary` 的 Judge 的結果（事實是該分組的主 Judge）。每個項目有 key 和輸入的 hash（原文、譯文和 Judge 的原始輸出），並顯示原文、譯文、Judge 的引用和理由，以及已有的決定。
  決定寫在同一個目錄的 `review-decisions.json`（自己建立，key 與 hash 照清單抄）：

  ```json
  {"decisions": [{"key": "fact-0123456789abcdef", "input_hash": "0123456789abcdef", "action": "override",
                  "verdict": "Weakened", "weakening_type": "e", "note": "…", "reviewer": "Pan", "reviewed_on": "2026-11-09"}]}
  ```

  `action`：`accept`、`reject`、`override`。事實：`accept` 確認已有最終 verdict 的（Gate 失敗或通過的），`override` 並寫 `verdict`（可加 `weakening_type`）；uncertain、無效、Unjudged 的事實只能用 `override` 解決，事實不能 `reject`。Finding（Judge 的與 Deterministic 的）：`accept`（uncertain 的因此算作確認，進「只計一致」的分數）、`reject`（不計分、不計數）、`override` 並寫 `severity`（改了罰分的權重）。其他項目（無效的輸出、Unjudged 的 Component、警示）：`accept` 或 `reject` 只表示看過了。程式證明的結果（譯文缺少 Component 而直接判 Absent 的事實，和漏段、多出、重覆、type 被改這類結構性的 Deterministic Finding）只能 `accept`，不能駁回或改判，否則漏段數與 Gate 會互相矛盾。同一個 Component 內身分一樣的項目（例如兩處一樣的殘留漢字）的 key 帶序號，各自獨立。`report` 重算時套用決定，Gate 的待審事實一旦都有決定，「未完成」就自動消失；原始的 Judge 輸出不會被改動。決定指向不存在的項目、輸入的 hash 已經改變（過期，例如重新判過、原文或譯文改了）、同一個項目有兩條決定，或寫法不適用於那種項目時，`review` 與 `report` 都會失敗並列出這些決定（只列 key），不產生新的清單或報告。報告開頭一定有「審閱」一節，列出必看項目的決定進度；`report` 每次都用同一套項目檢查決定，沒有任何 ok 的 Run 時也一樣。`mqm_run_limit` 刻意沒評的 Run 沒有 MQM 項目。
- **阿里雲 Qwen（方案 B）**：Approach 設定寫 `"provider": "dashscope"`、`"api_key_env": "DASHSCOPE_API_KEY"`，模型由 Pan 選（例如 `"model": "qwen-plus"`）。只會送到 International（新加坡）endpoint：預設是 `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`，也可以用 `base_url` 改成自己 workspace 的 `https://<workspace>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`；其他網址（大陸、美國、香港、http、帶帳號或埠號的）一律被拒絕。平台內容審查的錯誤（`DataInspectionFailed`、`data_inspection_failed` 等）記為 Block，原因碼寫在報告的 Block 原因分佈。`X-DashScope-DataInspection` header 預設不送；要送的話，把值寫在 Approach 設定的 `data_inspection`，程式照原樣送出。注意官方文件說這個 header 是啟用額外的 AI 安全護欄（值是 JSON 字串，且該服務僅北京區可用），在內建審查之外多加一層，不是 whitelist 的開關；文件裡的「加白」是向阿里雲客戶經理申請、把內容加入豁免名單的另一條流程。真跑同樣經 `tools/run-real.sh`。
- **OpenRouter（方案 F 等）**：Approach 設定寫 `"provider": "openrouter"`、`"api_key_env": "OPENROUTER_API_KEY"`、`"model": "作者/名稱"`。**送出任何請求之前**，Pan 先用 `providers` 指令看 provider 清單，確認後把要用的 tag 和確認日期寫進設定：

  ```bash
  # 會呼叫 API、需要 key：經 tools/run-real.sh
  tools/run-real.sh providers tencent/hy-mt2-30b-a3b
  ```

  ```json
  "routing": {"only": ["deepinfra", "novita"], "confirmed_on": "2026-11-09"}
  ```

  允許清單是空的或沒有確認日期時，`translate` 會在送出任何請求之前就停止（`--dry-run` 只顯示「尚未確認」）。每個請求都帶 `provider.zdr: true`、`data_collection: "deny"`、`only`、`allow_fallbacks: false`；回應寫的 provider 不在 `only` 內（或沒有寫）時，這個 Run 記為 `error`，Run 紀錄的 `served_provider` 和報告都會列出實際服務的 provider。`providers` 指令不寫入任何東西；API 沒有提供各 provider 的地區和資料政策時，它會明說，要到 OpenRouter 的模型頁自己確認。
  Approach 設定可以寫 `"reasoning_off": true`（只有 openrouter 用）：請求多帶 `"reasoning": {"effort": "none"}`，這是 OpenRouter 文件寫的關閉思考的開關；不寫就不送，用模型的預設。模型或 provider 不支援這個參數時，OpenRouter 會當它不存在（Hy-MT2-30B-A3B 經 Tencent 就是這樣，2026-11-11 實測：開了與沒開，失敗的單元與 reasoning token 都一樣）。開了之後設定 hash 會變，報告的 Approach 設定那一行寫「reasoning：關閉」；沒開的設定 hash 不變。
  `"single_user_message": true`（只有 openrouter 用）：不送 system 訊息，prompt 與要翻譯的文字合成一個 user 訊息（中間空一行），其他欄位都不變；不寫就照原樣送 system 與 user 兩個訊息。實測（2026-11-11，同樣兩篇文章）：Hy-MT2-30B-A3B 改用它沒有變好，反而全部變成空內容；7B 與 1.8B 用不用都沒有空內容。報告的 Approach 設定那一行寫「單一 user 訊息」。
- **請求模式與切塊**：Approach 設定的 `request_mode` 預設是 `article-json`（整篇 JSON 進、同結構 JSON 出）。`max_input_chars` 設了之後（只用於 `article-json`），文章按 Component 順序切成每塊不超過上限的連續幾塊，每塊一個請求；title 和 tags 只在第一塊。`request_mode: "per-component"` 給不擅長處理 JSON 的翻譯專用模型：每個要翻譯的字串一個請求，送純文字、收純文字，再組回同結構的譯文。兩種模式的 Run 紀錄格式相同：一個 Run 一筆紀錄，結果取最差的請求（block > refusal > malformed > truncated > empty > error > ok），`worst_unit` 記下是哪一個，遇到 block 就不再送後面的請求；tokens 和 latency 是各請求的總和，每次嘗試都標明屬於哪個請求，原始回應存成 `raw/` 底下的 `.jsonl`（每行一個請求）。逐 Component 模式不用拒絕語的字樣判 Refusal（譯文的引語裡本來就可能有「I can't」），只認 provider 明確給的訊號；模型直接拿拒絕語當譯文時，Run 仍是 `ok`，但 `check` 會在那個 Component 列疑似 Refusal 的警示，不會算進 Refusal 率。資料被送到不在允許清單內的 provider 時，這個 Run 立刻停止、記為 `error`，優先於其他結果。
- **繁→簡與方案 E**：實驗設定的 `directions` 可以寫 `trad-to-simplified`，LLM Approach 就會跑繁→簡（prompt 是 `translate-trad-to-simplified`）。方案 E（Current service）不經 API：實驗設定寫 `"baselines": [{"id": "e", "dir": "data/baseline-service", "direction": "trad-to-simplified"}]`，然後執行

  ```bash
  uv run --locked python -I -m translation_eval import-baseline --config <實驗設定>
  ```

  把 baseline 目錄匯入成 Run（每篇文章一個，結果 `ok`，沒有 tokens 和 latency，記下 `meta.baseline` 標籤）。沒有標籤、同一份 baseline 標籤不一致、或缺少某篇，整個指令失敗，不會留下一半的 Run；已有紀錄的略過，標籤與已匯入的不同時要用新的實驗 id。匯入後與其他 Approach 一樣 `check`、`report`；報告的「Baseline」一節列出標籤。繁→簡的 `check` 另記殘留的繁體字（OpenCC 1.4.2 的 t2s 會改動的字，每個 Component 每個字一個 Finding；Term List 條目的 `simplified_exceptions` 只對原文有那個詞的 Component 有效），長度比例預設 0.6–1.6（暫定）。
- **輸入版本**：實驗設定裡每個 Approach 可以寫 `input_variants`（預設只有 `full`）。`main-only` 只送 title 和 Main component（highlight、heading、paragraph、quote），tags、圖說、code 等都不送，用來看周邊內容會不會誘發 Block：

  ```json
  "approaches": [{"config": "configs/approaches/a-gemini-flash-lite.json", "runs": 3, "input_variants": ["full", "main-only"]}]
  ```

  每個輸入版本是獨立的 Run（`runs` 是每個版本各跑幾次），所以中途加上 `main-only` 重跑，只會補跑新的那些。`--dry-run` 分開列出兩個版本的請求數。報告裡兩個版本各有一節，另有「Block 誘因實驗」一節：只比較兩個版本都有紀錄的文章，列出 Block 率（按 Run、按文章）和結果不同的文章 id。
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
