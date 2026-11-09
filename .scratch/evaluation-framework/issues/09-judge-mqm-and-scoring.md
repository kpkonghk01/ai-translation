# 09: Judge MQM 與計分

**What to build:** Pan 在實驗設定中指定 Judge 後執行 `judge`，每個要翻譯的 Component 都由 Judge 評兩次，產生 Finding。所有片段經逐字驗證，不合格的丟棄並計數。程式按固定權重計分，Main 和 Peripheral 分開，報告列出分數、Unjudged 率、無效 Finding 率和 Source defect 清單。詳見 spec 第 6 和第 7 節。

**Blocked by:** 03

**Status:** resolved

- [x] Judge 設定：provider、模型、模型家族、prompt 版本、角色（`primary`／`experiment`），temperature 固定為 0
- [x] 同家族規則：Judge 與被評的 Approach 同家族時，指令拒絕執行，除非實驗設定寫明例外和理由；例外列在報告開頭
- [x] MQM 任務涵蓋每個要翻譯的 Component，包括 Main 和 Peripheral；title 和 tags 使用保留的 Component id；gallery 的說明和 related-article 的標題以換行連接成一段
- [x] 每個請求帶前後各一個要翻譯的 Component 作 context，以及 Direction 的要求、相關的 Term List 條目，和 Dimension、Severity、Softening (a)–(f)、Amplification、法律狀態與歸因、Source defect 的定義
- [x] 每個任務是兩個獨立請求
- [x] 逐字驗證：連續空白壓縮後，片段必須是原文或譯文的子字串；片段不合格、enum 不合法或結構不符的輸出記為無效，保留供審閱，並計入無效 Finding 率
- [x] 一致性判定：同一 Dimension 加片段重疊視為同一個 Finding；Severity 不同時取較高者並加標記；配對不到的標為 uncertain；只有一次成功時全部標為 uncertain；兩次都失敗則記為 Unjudged，並附原因
- [x] Fidelity-of-force 的 Finding 不計分
- [x] Source defect 以「文章、Component、原文片段」去重，並記錄是哪些 Judge 發現的；子類「疑似修正原文錯誤」記為 Accuracy
- [x] 計分：權重可以設定（預設 1／5／25）；Main 和 Peripheral 分開；列出總罰分和每千字罰分，並按 Dimension、Severity 分列；同時列出「只計一致的 Finding」和「連 uncertain 一起計」兩個數字
- [x] 實驗設定可以限定 MQM 只評部分 Run
- [x] `--dry-run` 列出 Judge 的請求數
- [x] 報告列出各 Judge 的 Unjudged 率、無效 Finding 率、分數和 Source defect 清單

## Comments

### 2026-11-09 實作完成

全部勾選項完成，沒有用真 Judge 跑過。規格沒寫、實作時做了選擇的地方：

- **Judge prompt 是我寫的**（`prompt_templates/judge-mqm.txt`，英文）：定義了五個 Dimension、三個 Severity、Softening (a)–(f)、Amplification、法律狀態與歸因（一律 critical）、Source defect，並說明 Term List 的慣用譯法不算 Softening。Judge 的 prompt 影響 Gold set 量到的 recall，請 Pan 在 ticket 14 之前看一遍。
- **逐字驗證忽略所有空白**（不只是把連續空白壓成一個）：spec 寫「連續空白以同一規則壓縮」，我採用比較寬的做法，跟 `render_ground_truth` 的 anchor 比對一致，模型在中文片段裡多插了空白也不會被誤判為不逐字。其他字元必須完全相同，大小寫也是。
- **兩個片段都是空的**的 Finding 無效；其中一個是空的可以（漏譯只有原文片段，多譯只有譯文片段）。模型多給的欄位（例如 confidence）不算結構不符，只看需要的欄位。
- **分數：Deterministic 的 Finding 一律計入，Judge 的 Finding 只有評完的 Component 才有**：漏掉的 Component 的漏段 Finding、多出的 Component 都計入，否則漏譯的譯文會比完整的譯文分數更好（spec 第 7 節）。每千字原文的罰分的分母是這些 Run 的**原文總字數**（含漏掉的 Component）。Judge 沒評完或 Unjudged 的 Component 沒有 Judge 的 Finding，這個缺口由「評完」和 Unjudged 率呈報，不從分母扣掉。（我一度寫成「只計評完的 Component」，review 發現會讓漏段消失，已改正。）
- **Fidelity-of-force 只列數量**（一致／uncertain），不計分，也還沒有併進全文篩查的清單（那是 ticket 12）。
- **無效 Finding 率**只算 Finding（不含 Source defect）：無效的 / （有效的 + 無效的），跨該 Judge 在這一組 Run 的所有請求。
- **同家族規則**比對 `family` 字串是否相同；baseline 不是 LLM，沒有家族，不受限。例外要寫在 Approach × Judge 的組合上，沒有理由或指向不存在的 Approach／Judge 的例外會被拒絕。
- **Judge 的 id 不能與 Approach 的 id 相同**，也不能重覆。
- `mqm_run_limit` 寫的是「Run 序號不超過 N」；`--limit` 限制的是請求數；`--dry-run` 的估計字數只算 prompt 和 Component 本身。
- Source defect 在報告裡含原文片段（完整報告只放 `data/`）；摘要版（ticket 13）要另外處理。
- **逐字驗證忽略所有空白**的副作用：`indecentassault` 也算是 `indecent assault` 的逐字片段；其他字元（含大小寫）必須完全相同。
- 還沒有的：Judge 的設定範例檔（`configs/judges/`）與真實模型名稱，要等 Pan 決定用哪些模型（見 spec 的候選：Claude、大 Qwen、Gemini Pro）。

### 2026-11-09 review 之後的修正

- **Provider 違規**：OpenRouter 的 Judge 回應來自不在允許清單內的 provider 時，指令立刻停止（已記錄那一個請求），不再送後面的請求；原本會把所有請求（每個都帶著文章內容）送完。
- **漏段與多出的 Component 計入分數**（見上）。
- **Judge 輸出的深層巢狀**：無效的條目原本整個存成巢狀結構，太深時紀錄寫不進去，甚至寫進去之後整個 `judge` 和 `report` 都讀不回來。現在存成有長度上限（2000 字，超過標「（截斷）」）的 JSON 文字。
- **報告不能被 Judge 寫的文字改版**：Source defect 的 `issue` 和片段壓成一行才放進報告，否則 `issue` 裡的換行可以長出真的 `## Gate` 標題。
- **同家族規則**：比對時不分大小寫、不計前後空白；Approach 已不在實驗設定內的 Run（沒辦法確認家族）一律拒絕，baseline 除外。
- 「疑似修正原文錯誤」的子類必須是 Accuracy，不是的話那一條無效（原本只靠 prompt）。
- `mqm_run_limit` 刻意沒評的 Run 不再被算成「尚未評完」。
- Judge 的設定、同家族的例外、權重和正規化方式移到報告開頭（「## Judge 設定」），結果在後面。
- 沒有 Judge 時，`report` 不再讀文章檔（我引進的退步：文章檔不在時 `report` 會崩潰）。
- Judge 的 prompt「版本」欄位沒有做：Pan 說不要版本梯級，Judge 紀錄記 prompt 的 hash。
- GLOSSARY 把 Unjudged 定義成 Judge 被 block 或拒絕；實作照 spec 第 6 節，兩次都失敗（不論是 block、refusal、malformed 或 error）都是 Unjudged，原因列出。
