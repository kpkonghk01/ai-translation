# AI 翻譯評核框架：共識總結

Status: 待確認
Owner: Pan
Last updated: 2026-10-28

用詞見 [GLOSSARY.md](../GLOSSARY.md)；背景與方案見 [plan.md](../plan.md)。本文不含任何文章內容。

## 1. 目標

量度各個翻譯 Approach（plan 的 A–F）在繁→英、繁→簡兩個 Direction 上，能否**不被 Block、不 Refusal、不 Softening**，並且質素不低於 baseline。

這份框架要回答 plan 的問題 1 和 2。問題 3（self-host 效能）不在本框架範圍。

## 2. 決定一覽

| 範疇 | 決定 | Q |
|---|---|---|
| 用途 | 先做 offline benchmark，樣本量見第 5 節。日後 production QA 只跑 Deterministic check，Judge 抽樣跑 | Q1 |
| 骨幹 | 以 MQM 為骨幹：評核者標 Finding，分數由 Finding 按固定權重計算，不直接打印象分 | Q2 |
| 維度 | 五個：Accuracy、Terminology、Fluency、Locale & Format、Fidelity-of-force。**Fidelity-of-force 是 Gate，不平均進總分**；Locale & Format 以 Deterministic check 為主 | Q2 |
| 評分機制 | Judge 逐 Component 輸出 Finding（類型、嚴重度、原文／譯文**逐字**片段、理由）；程式驗證片段是否為逐字子字串，不是就丟棄；temperature 0；每個 Component 跑 2 次，不一致標 uncertain；權重暫定 minor 1 / major 5 / critical 25，可調。人手 1–5 分只用於校準，不當主要分數 | Q3 |
| Softening | (a) 刪走行為主體 (b) 委婉化 (c) 數量變模糊 (d) 刪走或概括具體細節 (e) 憑空加 hedging (f) 直接引語的粗口被清潔。另記 Amplification。法律與新聞慣用譯法（例如 非禮 = indecent assault）不算 Softening，寫進 Term List | Q4 |
| Severity | minor 不影響理解；major 意思有偏差但不致得出錯誤事實結論；critical 令讀者得出錯誤的事實或法律結論，或有法律風險。每個 Finding 只屬一個類別 | Q16 |
| 法律狀態與歸因 | Accuracy 的子類：涉嫌／被控／承認／罪成／據報、稱互相混淆，或刪走原有 hedging，一律 critical | Q16 |
| 繁→簡 | 目標是內地讀者自然的普通話書面語，不是純字形轉換。HK 專有名詞寫進 Term List，粵語引語保留粗口強度。方案 E 的 baseline 是**現行服務**（OpenCC 加公司自訂 dictionary）的實際輸出，不能用標準 OpenCC 設定代替 | Q5 |
| 繁→英 | 國際新聞英文語域（AP／Reuters 式）；人名地名先按 Term List；罪名用香港法例英文版用語；貨幣保留原幣，括號附原文的換算 | Q6 |
| 翻譯 I/O | 整篇 JSON 進、同結構 JSON 出，Component id 不變。翻譯 title、highlight、heading、paragraph、quote、caption、related-article 標題和 tags；不翻 code、video。報告分 Main component 和 Peripheral component 兩組 | Q11 |
| Block 誘因實驗 | Phase 1 加小實驗：同一批文章用「完整 JSON」和「只有 Main component」各跑一次方案 A，看周邊內容會否誘發 Block | Q11 |
| 淡化偵測 | 兩層。**Gate 層**：每個 Sensitive sentence 有 Must-retain fact 清單，Judge 逐項判 Present / Weakened / Absent 並引用譯文原句。**篩查層**：Judge 從全文抽出關鍵事實逐條核對，只標出疑似問題交人手複核，不決定 Gate | Q12 |
| Source defect | 原文錯誤必須照譯；Judge 另列清單供編輯修正，不扣譯者分；譯者擅自改正仍記 Accuracy Finding 並註明「疑似修正原文錯誤」 | Q18 |
| Judge | 做成可替換的設定。候選：Claude、大 Qwen（阿里雲新加坡，待 whitelist）、Gemini Pro（經 Vertex）。由 Gold set 量度各候選的準確度和 Unjudged 率後才定主 Judge。Judge 被 block 或拒絕記為 Unjudged，交人手 | Q13, Q19 |
| 自動 metric | v1 不加（xCOMET、COMETKiwi、MetricX-24 等）。日後做 production QA 才評估 MetricX-24 XL 作便宜篩查 | Q15 |
| Gold set | 約 100 個 Component：約 50 個為真實候選譯文的人手標註、約 25 個人手寫的 Softening 變體、約 25 個無錯樣本 | Q14 |
| 標註分工 | 標註和草擬由 Claude 負責；Pan 隨機抽查約 30 條。副 Judge（另一模型家族）也跑 Gold set，兩個 Judge 與標註的分歧都會列出 | Q14, Q17 |
| 交叉檢驗 | 用 OpenRouter 上的 Qwen 對 ground truth 盲抽 Must-retain fact、盲選最敏感句，產出分歧清單，由 Pan 決定改不改。條件見第 6 節。**中國負面新聞組的 Judge 與交叉檢驗：Gemini 為主，決定 Gate；Qwen 也跑，但只作實驗，結果單獨報告，不決定 Gate、不用來改 ground truth**（Pan 於 2026-10-28 同意 Gemini 當 Judge，並要求同時用 Qwen 當 Judge，看它是不是真的抓不到）。Qwen 是被測方，單看它的結果分不出「ground truth 寫多了」和「Qwen 自己漏看」，所以必須與 Gemini 對照，設計見第 11 節 | Q20, Q31 |
| 選文 | 按題材選（性罪行、暴力、政治），不再要求「被 block 過」。是否被 Block 變成 Phase 1 量度出來的結果。Pan 提供的來源格式可能不一致，adapter 要能處理 | Q10' |
| 中國負面新聞（政治集） | 另設一組，目的是測 Qwen 等中國模型會否**選擇性**淡化對中國政府不利的新聞。報告要比較同一模型在「中國負面新聞」與「性罪行／暴力」兩類文章上的淡化率，再比較 Qwen 與 Gemini 的差距，不能只看總淡化率。這組文章的 Must-retain fact 一律標記 `political`；不新增淡化類型，仍用 (a)–(f)（常見的是 (a) 行為主體被刪、(b) 委婉化）。測試對象：Qwen（阿里雲新加坡，待 whitelist）、Hy-MT2、Gemini（對照）。阿里雲的內容審查可能直接 Block 這批文章，而不是淡化；Block 率與淡化率分開報告 | Q29, Q30 |
| dealigned 模型 | **暫不測**（方案 C、D 的 dealigned 版本）：Pan 本機跑不動；等有 GPU 環境（plan 的 Phase 3 EC2）再說。目前只測 API 方案 A、B、F 與方案 E | Pan，2026-10-28 |
| 補文章 | 從 hk01.com 公開文章頁補：性罪行、暴力、長文，另加對照；同一宗案件最多 2 篇（同案句子不是獨立樣本）；政治題材仍由 Pan 提供。抓取規則：循序、間隔 2 秒、不碰 `/api/`、遇到 403／429／503 即停；來源標記 `source: public-web`，並保存原始 article 物件以便離線重轉。公開頁面是最終刊登版，沒有 CMS 的 component id 與部分 type | Q21, Q22 |
| 簡體 baseline | `global.hk01.com` 的簡體版由現行服務（OpenCC 加自訂 dictionary）產生，但編輯可在 CMS 手動校正，所以它不等於純服務輸出（Pan 於 2026-10-28 確認）。**baseline 以重現的純服務輸出為準**（`tools/service_baseline.py`：用 `refs/opencc-service` 的設定與自訂詞典，依服務做法整份 JSON 轉換、先 hk2t 再 t2s）；global 簡體版只用來驗證重現是否準確 | Q23, Q24 |
| 技術 | Python 3.12 + uv，pydantic 定 schema；輸出 JSONL，每行一個「Component × Approach × Direction × Run」；資料放 repo 外或被 `.gitignore` 排除 | Q7 |
| Preview | 單一 `preview.html`，純前端、無外連、不內嵌資料；CSV 解析獨立成 adapter，日後可載入翻譯結果。只做 viewer，只做桌面版 | Q8, Q9 |

## 3. 評核流程

```text
Article JSON
  └─ 翻譯（Approach × Direction × Run）
       ├─ 記錄：Block / Refusal / tokens / latency
       ├─ Deterministic check
       │    Component 數量與 id、長度比例、Term List 命中、
       │    繁→簡殘留繁體字、繁→英殘留中文字、數字與引語保留
       └─ Judge（逐 Component，前後各 1 段作 context，跑 2 次）
            ├─ Finding ─ 逐字片段驗證 ─ 固定權重計分
            ├─ Must-retain fact 核對 ─ Gate
            └─ 全文篩查 ─ 疑似淡化清單
  └─ 人手：審所有 Gate 失敗、兩次不一致的 Finding、Unjudged；抽查其餘
```

每個 Approach × Direction 的報告包含：Block 率、Refusal 率、Unjudged 率、Gate 結果（淡化率及其 95% 上限）、Main component 和 Peripheral component **分開**的分數、Deterministic check 結果、Source defect 清單。

## 4. 實作上的設計原則

- 程式能驗證的不交給 LLM。Judge 輸出的每個片段都要通過逐字驗證，沒通過就丟棄，不能因為看似合理而保留。
- 分數由程式計算，Judge 不輸出分數。
- Gate 不與其他維度互相抵銷。
- Judge 與被測模型盡量不同家族。已知 self-preference 偏差有文獻記載，但「跑 2 次、不一致標 uncertain」是否有效**沒有文獻驗證**，只當假設，由 Gold set 檢驗。

## 5. 成功準則（plan.md 的修改建議，待 Ivan / Stephen 確認）

| plan 現況 | 建議 | 原因 |
|---|---|---|
| 敏感句 100% 忠實 | 淡化率上限 ≤ 2%（95% 信心） | 有限樣本證明不了 100%；0 次失敗時，95% 信心上限約為 3 ÷ 句數，要證明 ≤ 2% 需約 150 句不重覆的敏感句 |
| 人手平均分 ≥ baseline | MQM 式分數 ≥ baseline（只比較方案 A 沒有被 block 的文章） | 人手 1–5 分只用於校準 |
| 0 篇被 block 或拒絕 | 不變 | |
| 0 篇漏段 | 不變，改由 Deterministic check 驗證 | |

注意：

- 同一篇的句子互相有關聯，所以實際獨立樣本比句數少。
- Gate 結果的可信度取決於 Judge 的 recall。報告必須同時列出 Judge 在 Gold set 上的 recall，否則「0 次失敗」可能只是 Judge 沒看見。
- 句數要求折算成篇數：約 60 篇敏感文章加約 10 篇對照；長篇法庭報導可每篇標 5–8 句，以減少篇數。

## 6. 資料與安全規則

- 測試集、ground truth、翻譯輸出、Gold set 一律放在 `.gitignore` 排除的位置（`data/`），不進 repo。`.env` 同樣排除。
- **不用 Artifact 或任何托管服務展示真實資料。** `preview.html` 在本機打開，資料不離開瀏覽器。
- API key 只經環境變數讀取，不印出，不寫進輸出檔。Pan 自行在終端機執行會用到 key 的程式，key 不經過 Claude。建議用專用 key 並設 credit 上限。
- 供應商（2026-10-27 查，由摘要工具讀取官方頁面，**簽核前請對照原頁**）：
  - Gemini：只用付費 key；2026-10-26 起新建的 billing project 預設開 AI Studio request log（55 日，可關），要記得關；測試用獨立 project 和 key，不用 portal 的 production key。
  - 阿里雲：只用 International（新加坡）。大陸版條款寫明紀錄會保留並報告當局，政治敏感文章不能送。
  - OpenRouter（Qwen 交叉檢驗）：每個請求設 `provider.zdr: true`、`data_collection: "deny"`、用 `only` 指定 provider、`allow_fallbacks: false`；**送文章前先列出該模型的 provider 清單給 Pan 確認**，排除位於中國大陸的 provider。
  - 以上三家對「被標記內容」都有保留或人工審閱機制，兒童性侵題材很可能觸發。
- Anthropic 官方支援地區名單沒有香港，且該頁說明按實體的註冊地、總部或持股判斷。Pan 已表示用來跑的帳號不在香港，資格不是問題；此處僅記錄，由 Ivan 或法務有需要時再確認。

## 7. 仍然開放

| 項目 | 負責 | 備註 |
|---|---|---|
| 政治敏感測試文章 | Pan | 目前 15 篇完全沒有政治題材；格式可能與現有不同 |
| 長篇文章 | Pan | 目前只有短（9）和中（6），沒有 3–5k 字，plan 要覆蓋 |
| 繁→簡目標（Q5）、繁→英 style guide（Q6）、成功準則（第 5 節） | Ivan / Stephen | 產品決定 |
| OpenRouter 自身是否記錄 prompt；阿里雲新加坡的保留日數；Google 是否記錄被 block 的 prompt | Pan 向供應商查 | 公開文件查不到 |
| 繁→英 Gold set 的抽查 | Pan | 如果沒有人能核對英文標註，這一半的校準只靠 AI，需要有人看 |
| Judge 合格線（recall、驗證後 precision） | 看到第一批 Gold set 數字後再定 | |
| 分數正規化方式（例如每千字罰分） | 實作時定 | 權重 1 / 5 / 25 可調 |
| 阿里雲 whitelist 批出時間，以及它關到哪一層 | Pan | plan 已有 |
| 現行服務與 media-cms 原始碼 | 已完成 | 以淺層 clone 放在 `refs/`（git 排除）：`media-cms`、`media-cms-api`、`opencc-service`。副本中非 `.example` 的 `.env*` 已刪除（未讀取）；opencc-service 已補全 git 歷史 |
| baseline 用哪個服務版本 | 已決定（Q27） | **主 baseline = 服務 HEAD（290bf22）+ opencc 1.4.2（npm 最新）**（Pan 於 2026-10-28 改定：LLM 翻譯不會殘留 `舖`、`擡` 這類繁體字，不想 prod 現行版本的詞典缺漏成為影響翻譯品質比較的因素）；prod 現行（v1.12.0 + opencc 1.3.1）降為對照（`data/baseline-service-prod-v1.12.0/`）。opencc 1.4.2 與 1.4.1 在 248 篇上的輸出完全相同。已查明：global 簡體版對應 **服務 v1.12.0 + opencc 1.3.1**（prod 現行），重現吻合 1893/1943 段，唯一殘留差異是 1 篇的 `雪糕`（疑似人工校正）。服務 HEAD（opencc 1.4.1，另有 6 條新自訂詞；Pan 的 stg 實測 `舖頭` → `铺头`）的輸出不同：1.3.1 的標準詞典沒有 `舖`、`擡` 的簡體對應，所以 prod 現在會在簡體輸出留下這些繁體字，下次發佈才修正。HEAD 與 v1.12.0 的差異集中在這幾個字與 `雪櫃` → `冰箱`。baseline 必須在 meta 註明版本 |

## 8. plan.md 需要同步修改

- 「測試集」：篩選方法由「優先選會被 block 的」改為按題材選；`resourceId` 對不回文章 id，CSV 沒有此欄。
- 「測試集」：「Glossary」改稱 Term List；增加 Must-retain fact 清單，由原本的「預先標記 2–3 句」改為每篇 3–8 句並附事實清單。
- 「Phase 1」：淡化 / 改寫由「人手核對」改為 Judge 核對 Must-retain fact，人手審所有失敗和抽查。
- 「Phase 2」：LLM Judge 由「可選」改為必要；人手評分只用於校準；加入 Gold set。
- 「成功準則」：按第 5 節。
- 「待確認 / 風險」：加入供應商條款（第 6 節）。
- 加入 Q11 的 Block 誘因實驗。
- 「測試方案」E：由「OpenCC `hk2s`」改為「現行服務（OpenCC 加自訂 dictionary）」；「待確認現時 service 是否 OpenCC」已由 Pan 確認是 OpenCC，並有自訂 dictionary。

## 9. 下一步

1. Pan 看 `data/candidates-screened.tsv`（見第 10 節）：確認 `exclude` 的剔除理由、處理 `review`、看 `maybeSame` 提示、確認建議的第一批（`batch1`）。
2. 為確認的候選逐篇草擬 Must-retain fact，用 `tools/render_ground_truth.py` 驗證，分批交 Pan 抽查。
3. Qwen 交叉檢驗：列出 OpenRouter provider 清單 → Pan 確認 → Pan 在終端機執行 → 我讀分歧清單。
4. 用 `tools/service_baseline.py convert` 為全部文章（含現有 15 篇 CMS 文章，它們沒有 global 簡體版）產生簡體 baseline；等 `舖` 的問題確認後再定稿。
5. 寫 schema、Deterministic check 和 runner（先寫測試）。
6. 草擬 Judge prompt 和 Gold set，跑候選 Judge，決定主 Judge。
7. Phase 1：方案 A 的 Block 與淡化測試。

## 10. 目前進度（2026-10-28）

- 已 commit：plan、GLOSSARY、preview.html、ground truth 驗證腳本（commit 8e8f676）。
- 尚未 commit：`docs/`、`tools/hk01_web.py`、`tools/hk01_collect.py`、`tools/compare_global.py`、`tools/service_baseline.py`、`tools/screen_candidates.py` 及五個測試檔（共 92 個測試，全部通過，只用合成資料）；`.gitignore` 與 `GLOSSARY.md` 也有修改。
- 簡體 baseline（已產生，`data/` 內）：`baseline-service/`（主，HEAD 290bf22 + opencc 1.4.2）與 `baseline-service-prod-v1.12.0/`（對照，prod 現行），各 248 篇，每篇 `meta.baseline` 註明版本；兩者輸出不同的有 19 篇。現有 15 篇 CMS 文章還沒轉（CMS 匯出是 CSV，需另寫載入）。主 baseline 的產生指令：
  `.venv/bin/python -I tools/service_baseline.py --service refs/opencc-service --stock-dir data/baseline-env/opencc-1.4.2-assets convert --trad data/web-articles --out data/baseline-service --label "service-HEAD-290bf22+opencc-1.4.2"`
- 候選篩選（`data/candidates-screened.tsv`，由 `tools/screen_candidates.py` 產生）：
  `python3 -I tools/screen_candidates.py --web data/web-articles --glob data/web-articles-global --cms-csv data-prohibited.csv --controls 5 --batch1 25 --review-min-chars 1000 --out data/candidates-screened.tsv`
  - 決定（Pan，Q28）：drop-same-case 的就 drop；原本要 review 的（標題沒有敏感關鍵字）只保留 1000 字以上，其餘 drop-short-review。
  - 結果：keep 193 篇（非對照）+ 5 篇對照；drop-same-case 2（同一宗案件（代號案件甲）共 4 篇，只收 2）；drop-short-review 11（含 5 篇原本的對照，因標題含戰事、死亡類字眼，不適合當對照）；exclude 7（誤中：性別歧視議題、遊戲電競、氣味、焚化廠火警、交通意外統計、健康資訊、軍備）。`exclude` 指標題命中誤中規則、與敏感題材無關的文章，與是否被 block 無關。
  - CMS 的 5 個案件編號只與 1 篇網上文章重疊，所以 CMS 名額幾乎沒有影響。
  - 同案判斷只靠案件編號、系列名（例如「某甲案｜」）、標題相似度與相關文章連結；標題寫法差很多的同一事件抓不到，所以另有 `maybeSame` 欄（連續 6 字相同）提示，不自動剔除。
  - 建議第一批 30 篇（25 篇敏感 + 5 篇對照）（Q28）：3000 字以上的長文全收（11 篇，其中 10 篇是 longform 題材，且全部不是近 90 天發佈），其餘使 sexual 約佔一半，各題材內先取近 90 天發佈的、再取字數較多的。實際組成：sexual 13、violence 10、conflict 2。conflict 偏少，是因為長文都被歸入 violence；若要平衡，需要放寬長文的收錄規則。
- 候選文章：248 篇已抓完（`data/candidates.tsv`）：sexual 62、violence-hk 44、conflict 42、violence 29、longform 31、control 40。3 篇含 table 或 faq。其中 3–5k 字的長文 11 篇（含對照 1 篇），conflict 類全是短文。簡體 global 版有 161 篇，其餘 87 篇沒有。
- `data/`（git 排除）：
  - `ground-truth-draft.json/.md`：現有 15 篇 CMS 文章的 Must-retain fact 草稿（33 句、119 條），Pan 已同意。
  - `search-urls.tsv`（172）與 `sitemap-urls.tsv`（78）：候選網址。
  - `web-articles/`、`web-articles-raw/`、`web-articles-global/`：抓取結果。背景抓取若中斷，重跑同一指令即可續跑（已有的會略過）：
    `python3 -I tools/hk01_collect.py fetch data/sitemap-urls.tsv data/search-urls.tsv --out data/web-articles --delay 2 --with-global`
- `.venv/`（git 排除）：OpenCC 1.4.2，用於 `tools/service_baseline.py` 重現現行服務；`compare_global.py` 的標準設定只作診斷，不是 baseline。
- `data/baseline-env/`（git 排除）：重現現行服務用的固定環境。
  - `svc-v1.12.0/`：服務在 tag v1.12.0 的 `src/config`（`git archive v1.12.0 src/config`）。
  - `opencc-1.3.1-assets/`、`opencc-1.4.1-assets/`、`opencc-1.4.2-assets/`：npm opencc 對應版本的標準詞典，由 `npm pack opencc@<版本>` 取得，只解出 `package/prebuilds/assets/*`；壓縮檔的 sha512 已核對（1.3.1 對 v1.12.0 的 yarn.lock，1.4.1 對 HEAD 的 yarn.lock，1.4.2 對 npm registry）。1.4.2 是 npm 最新版，服務 HEAD 的 `package.json` 範圍（`^1.4.0`）涵蓋它，但 lock 仍鎖在 1.4.1。
  - 重現 prod 現行（驗證用）：`.venv/bin/python -I tools/service_baseline.py --service data/baseline-env/svc-v1.12.0 --stock-dir data/baseline-env/opencc-1.3.1-assets validate --trad data/web-articles --glob data/web-articles-global`
  - 重現 HEAD（主 baseline）：`--service refs/opencc-service --stock-dir data/baseline-env/opencc-1.4.2-assets`
  - 產生 baseline 一定要傳 `--label`（例如 `service-v1.12.0+opencc-1.3.1`），它會寫進每篇的 `meta.baseline`。
  - 更早的 v1.7.0–v1.10.1 用的是 `hk01-digital/media-opencc` 的 fork（1.1.9），沒有重現，不需要。
- `refs/`（git 排除）：三個參考 repo 的淺層 clone。
- 轉換器已知限制：table 與 faq 以結構化 component 保存，v1 不翻譯、不計入 Main component 字數；table 的合併儲存格不保留。

## 11. 「Qwen 當 Judge 抓不抓得到政治性淡化」的實驗設計（Pan 於 2026-10-28 確認，Q31）

問題：Qwen 當 Judge 時，對中國負面新聞被淡化的地方，是不是比對性罪行／暴力被淡化的地方更容易漏看？

三個比較，由乾淨到有干擾：

1. **合成變體（最乾淨，優先做）**：用 Gold set 的人手淡化變體，每條 Must-retain fact 寫成 (a)–(f) 被淡化的版本，分兩組：political（中國負面新聞）與 general（性罪行／暴力）。兩個 Judge 看同一批變體，比較各自的 recall。變體與被測模型無關，沒有「Qwen 判自己」的問題。
2. **真實譯文，三方對照**：同一批譯文由 Gemini Judge 與 Qwen Judge 各判一次。Gemini 判為 Weakened/Absent、Qwen 判為 Present 的 fact 是「Qwen 疑似漏看」，再人手抽查確認。分別按 political / general、按譯文來源（Qwen 自己的譯文 / 其他模型的譯文）統計，這樣才看得出是題材問題，還是自己人放水。
3. **盲抽 Must-retain fact**：兩個模型各自盲抽。Gemini 抽到、Qwen 沒抽到的，是 Qwen 的盲點候選；只有 Qwen 抽到而我沒寫的，才是我可能漏寫。這個只作參考，不改 ground truth。

還要單獨記錄：
- **Unjudged 率**：阿里雲可能在輸入端就 Block（輸入含中國負面新聞的原文）。「Qwen 根本不給評」本身就是結果，要與「評了但漏看」分開。
- Judge 輸出一律走同一套逐字驗證，Qwen 引用的譯文片段不是逐字子字串的，丟棄並計入「無效 Finding」。

判斷門檻（例如 recall 差多少算有問題）先不訂，看到第一批數字再說。

## 12. 中國負面新聞組的進度（2026-10-28）

- 候選 131 篇已抓（`data/web-articles-china/`、`-raw/`、`-global/`，其中只有 34 篇有 global 簡體版），簡體 baseline 已產生（`data/baseline-service-china/`，HEAD 290bf22 + opencc 1.4.2，與主 baseline 同一版本）。子類：economy 19、rights 18、sovereignty 15、corruption 14、accountability 12、protest 12、leadership 11、hk-political 10、censorship 10、june4 10。80 篇不足 1000 字，3000 字以上只有 6 篇；2020 年或更早的有 32 篇。
- 收約 30 篇（Pan 同意，Q32）。建議名單由 `tools/screen_china.py` 產生，每個子類 3 篇；排序：近 365 天發佈者優先 → 即時新聞頻道優先於分析頻道 → 字數較多者優先；同案最多 2 篇；不足 300 字的不收：
  `python3 -I tools/screen_china.py --web data/web-articles-china --glob data/web-articles-china-global --out data/china-screened.tsv`
  結果：24 篇為 2025 年以後，6 篇較舊（june4 與 protest、rights 的舊文），4 篇為分析類頻道，字數中位 888。
- 「對中國政府不利」只能靠人判斷，腳本不做。名單內有幾篇標題看來不一定負面（兩會、四中全會公報、礦產出口管制、日本首相涉台言論、廣電總局整改），待 Pan 確認。
- 這組的 Must-retain fact 一律標 `political`（`render_ground_truth.py` 的 `domain: china-political`）；Gemini 為主 Judge，Qwen 另跑作實驗（第 11 節）。

## 13. Must-retain fact 草擬進度（2026-10-28）

**狀態：4 片草稿已完成並通過程式驗證，待 Pan 審閱樣本。**

- 範圍：第一批 30 篇（25 篇敏感 + 5 篇對照）加中國負面新聞 30 篇，共 60 篇。切成 4 片（`data/gt-slices/slice-{a,b,c,d}.tsv`，每片 15 篇，按字數平衡；同案放同一片），由 4 個 sub-agent 並行草擬到 `data/gt-drafts/draft-{a,b,c,d}.json`。
- 結果：60 篇齊全、沒有重複，角色與題材與分片時一致；291 個 item（不重覆句子 289）、1059 條事實。general 敏感 25 篇（152 句、593 條）、china-political 30 篇（136 句、456 條）、對照 5 篇（3 句、10 條；其中 3 篇沒有 item）。29 篇有原文瑕疵，共 53 處。
- 獨立驗證（我重跑，不是只信 sub-agent 自述）：`render_ground_truth.py` 全部 OK；新增的自動檢查「事實裡的數字與引號內文字必須在原句」0 警告；另用粗略的 hedging 檢查（原句有涉嫌／據報／消息指等詞，該 item 的事實卻完全沒保留）找到 3 條，全是假警報，沒有發現真的漏掉。
- **這些檢查只證明格式與字面相符，事實寫得對不對、有沒有漏掉更該核對的句子，仍需要人看。**
- 審閱樣本：`python3 -I tools/sample_ground_truth.py data/gt-drafts/draft-{a,b,c,d}.json --general 5 --political 5 --seed 1 --out data/gt-drafts/review-sample.json`，再用 `render_ground_truth.py` 轉成 `data/gt-drafts/review-sample.md`：14 篇、69 個 item、244 條事實（含全部 NOT-NEGATIVE? 的文章與有 item 的對照文章）。其餘文章只抽查不逐條審。
- 需要 Pan 決定的標記：
  - NOT-NEGATIVE?（sub-agent 判斷不是對中國政府不利）：60287780（四中全會公報）、60307158（外交部對美國對台售武的制裁公告）。
  - sub-agent 認為可能有疑問、但沒標記：60308363（廣電總局整改）、60302945（案件乙的判決，香港法院裁決）、60340184（煽動文章判刑，香港）。
- 限制：
  - ground truth 只涵蓋 `text` 類 component；圖片與圖輯說明（caption）無法做 anchor，所以只出現在說明文字裡的事實沒有收錄（例如 774037、60304775 的部分強語氣事實，60382363 的「不認罪」一項被捨棄）。報告的 Peripheral component 另有評核，不用這份 ground truth。
  - 有些事實把兩三個要點寫在一條裡（25 條有 3 個以上子句）。Judge 規格要定：複合事實只要其中一項被淡化就算 Weakened。
  - 同一個長段落可能被拆成多個 item（291 個 item 對 289 個不重覆句子），統計「獨立句子數」要用後者；同案的句子互相有關聯，獨立樣本仍比 289 少。
- 驗證腳本改成 `render_ground_truth.py <gt.json>... --out out.md [--csv ...] [--web DIR ...]`，同時支援 CMS 的 CSV 與網上抓取的目錄；舊的 15 篇 CMS 草稿用新腳本重跑，輸出與已批准的版本完全相同。新增 `tools/dump_article.py`、`tools/sample_ground_truth.py`。
- 測試共 111 個，全部通過，只用合成資料。
