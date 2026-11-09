# 10: Must-retain fact 核對與 Gate

**What to build:** Judge 逐條核對每個 Sensitive sentence 的 Must-retain fact，每條評兩次。報告的 Gate 一節列出淡化率和 95% 信心上限，按 domain、淡化類型和事實種類分列，Amplification 另列。證據不齊時，Gate 標示為「未完成」。詳見 spec 第 6 和第 8 節。

**Blocked by:** 09

**Status:** ready-for-agent

- [x] 讀取現有格式的 ground truth，驗證規則與現有的渲染工具相同：文章存在、Component id 存在、anchor 逐字、kind 和 domain 在允許清單內
- [x] fact key 由文章 id、Component id 和事實文字的 hash 組成；事實文字改了，舊的 verdict 就失效
- [x] fact 任務：每條事實輸出 verdict（Present／Weakened／Absent）、淡化類型或 Amplification、譯文引用（Present 和 Weakened 必須有）、理由；複合事實只要任一要點被淡化就判 Weakened
- [x] 引用不是逐字子字串時，該條 verdict 作廢，事實列入審閱；一致性判定和 Unjudged 的規則與 09 相同
- [x] 缺少的 Component 如含 Sensitive sentence，其所有事實直接判 Absent，不送給 Judge
- [x] 淡化率 = 在任一 Run 失敗過的不重覆句子數 ÷ 有評核結果的不重覆句子數；另列每個 Run 的淡化率和涉及的文章數
- [x] `block` 和 `refusal` 的 Run 不計入分母
- [x] 95% 上限用單尾 Clopper–Pearson；測試確認 0／150 約為 2.0%
- [x] 按 domain 分組（general、china-political，以及實驗設定中的覆寫，供 Q34 使用）；列出每個 Approach 的 political 與 general 差距；按淡化類型 (a)–(f) 和事實 kind 分列；Amplification 另計
- [x] 實驗設定為每個 domain 分組指定主 Judge；general 組還沒指定時，標示「主 Judge 暫定」
- [x] 仍有 uncertain、無效或 Unjudged 的事實時，Gate 標為「未完成」並列出待審數量
- [x] 沒有校準結果時，Gate 旁寫明「Judge recall 未量度」

## Comments

### 2026-11-09 實作完成

全部勾選項完成，沒有用真 Judge 跑過。真實 ground truth（`data/ground-truth-draft.json` 與 `data/gt-drafts/draft-a`–`d`）用新的載入器檢查過：五個檔案、324 個敏感句、1178 條事實，驗證全部通過（只看數量，沒有讀內容）。規格沒寫、實作時做了選擇的地方：

- **Judge prompt 是我寫的**（`prompt_templates/judge-facts.txt`，英文）：定義三個 verdict、複合事實的規則（任一要點被淡化就是 Weakened；全部要點都沒了才是 Absent）、類型 (a)–(f) 與 `amplification`、引用要逐字、法律狀態與歸因被混淆就不是 Present、Term List 的慣用譯法不算淡化。它直接影響 Gate 的結果，請 Pan 在 ticket 14 之前看一遍。
- **一個請求 = 一個含敏感句的 Component**（不是一句一個請求）：同一個 Component 內的所有句子（anchor）和事實一起送，每個 Judge 評兩次。事實在請求裡的 id 是 F1、F2…（Judge 抄 hash 容易抄錯），程式記錄時換回事實 key（SHA-256）。
- **verdict 與類型的規則**：Present 與 Weakened 要有逐字的引用（比對規則和 Finding 相同，忽略空白），Absent 的引用忽略不存。類型寫成 `e`、`(e)`、`E` 都讀成 e。Weakened 可以沒有類型（報告歸在「未標類型」）；Weakened 配 `amplification`、Present 配 a–f、Absent 配 `amplification` 是矛盾，該條作廢。兩次的 verdict 相同但類型不同，判定仍一致，類型記為未標。
- **只有一個有效的 verdict**（另一次失敗或作廢）是 uncertain；兩次都沒有有效的 verdict 是無效。模型沒回的事實、重覆回的事實（兩個都不用）、回了不認識的 id，都記為無效。
- **「未完成」照字面**：只要有任何一條事實是 uncertain、無效、Unjudged、尚未評完或過期，就是未完成，即使那一句已經因為別的事實失敗了。尚未評完（請求還沒送）和過期（ground truth 改了）是我加的，不算漏掉：證據不齊就不下結論。
- **沒有結果的句子不在分母**：一句沒有失敗、但有事實待審，這個 Run 對那一句還沒有結果，不算通過，也不算失敗。
- **Gate 按分組各算各的**，沒有跨分組的總數：每個分組有自己的主 Judge，把不同 Judge 的結果混在一起沒有意義。實驗設定沒有為某個分組指定主 Judge 時（不只是 general），一律用第一個 `primary` 的 Judge 並標示「主 Judge 暫定」。`role` 是 `experiment` 的 Judge 不決定 Gate（它們的 fact 結果有存，ticket 17 才用）。
- **失敗的事實按類型分列**：Weakened 與 Absent 都按它的類型字母算；沒有類型的歸「未標類型」；「其中 Absent」另外列出。同一條事實在不同 Run 以不同類型失敗時，每種類型各算一次。事實種類（kind）列的是「失敗的事實／有結果的事實」，同一條事實跨 Run 只算一次。
- **Qwen 與 Gemini 的差距（Q29）**：「political 與 general 的差距」一節把每個 Approach 的兩組淡化率並列；兩組的主 Judge 不同時註明。沒有為 Qwen／Gemini 特別寫死。
- **main-only 的 Run**：沒有送的 Component（例如圖說）裡的敏感句，不在那個 Run 的核對範圍。baseline 也算（每個 ok 的 Run 都核對）。
- **ground truth 的狀態以實驗設定為準**（`draft`／`approved`）；檔案自己的 `status` 備註不讀。ground truth 的 schema 不接受多餘的欄位（比 `render_ground_truth.py` 嚴：它會忽略拼錯的欄位名，`domain` 拼錯就靜默變成 general）。
- 作廢的 verdict 與無效的條目都保留原文在 `judge-calls.jsonl`；「列入審閱清單」要等 ticket 11 的 `review`。報告目前只列數量。
- 還沒有的：Judge recall（要 ticket 14 的校準，報告寫「未量度」）、審閱決定（ticket 11）、全文篩查（ticket 12）。

### 2026-11-09 review 之後的修正

- **Gate 不能在證據不齊時寫「完成」**：原本只看有沒有待審的事實。現在沒有任何句子有結果、計畫的 Run 還有沒有紀錄的、有 `error` 的 Run 也是「未完成」（block 與 refusal 是結果，不算缺漏；spec 第 8 節）。待審的數量附分母（事實 × Run），Unjudged 列出原因。
- **anchor 一樣的兩個 item 會互相蓋掉**：同一個 Component 內 anchor 一樣的 item 原本只留最後一個的結果，前一個的失敗會消失。現在視為同一句，合起來看（真實 ground truth 沒有這種情況，但載入器不該靠這個）。
- **Judge 回的 `fact_id` 不是字串（例如清單）會讓 `judge` 崩潰**，而且那個請求沒有紀錄，重跑會在同一處再崩潰。現在記為無效。
- **Amplification 只有一次標到時不能不見**：兩次的 verdict 一致但類型不一致時，Amplification 原本被丟掉。現在列出「兩次一致」與「只有一次標了」。
- **原文瑕疵（`source_defects`）也驗證**（Component 存在、anchor 逐字），與 `render_ground_truth.py` 一致。真實 ground truth 的 59 處全部通過。
- 報告開頭列出實驗內沒有 ground truth 的文章：id 打錯時看得到。
- 沒有照 review 改的：**ground truth 內不在實驗裡的文章仍是略過，不報錯**（`render_ground_truth.py` 會報錯，因為它拿全部文章來源驗證；這裡只載入實驗內的文章，ground truth 檔本來就比實驗大，所以只能列出篇數）。**Qwen 與 Gemini 的比較（Q29）我讀成各 Approach 的 political 與 general 差距並列**（story 48 講的是被測模型，Judge 的比較是 Q31，屬於 ticket 17）；若 Pan 的意思是用 Qwen Judge 重算這個差距，要在 ticket 17 處理。
- 其他是命名與測試的整理：函式改名避免與 `judge_report` 同名、測試共用的部分移進 `tests/gate_helpers.py`、幾個偏弱的斷言改強。

