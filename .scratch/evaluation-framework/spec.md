# AI 翻譯評核框架

Status: ready-for-agent
Owner: Pan
Created: 2026-11-06

決定與理由以 consensus-summary（Q1–Q33）為準；本 spec 另加 Q38（測試 seam）與 Q39（沿用 uv）。用詞以 GLOSSARY 為準。本 spec 不含任何文章內容；ticket 只用文章 id 指向文章。

## Problem Statement

Pan 要回答 plan 的兩個問題：哪個 Approach（A、B、E、F）能在繁→英、繁→簡兩個 Direction 上**不被 Block、不 Refusal、不 Softening**，以及譯文質素是否不低於 baseline。測試集（第一批 30 篇、中國負面新聞組 30 篇、CMS 15 篇）、Must-retain fact 草稿和方案 E 的 baseline 都已準備好，但還沒有東西可以：

- 把文章送去各個 Approach 翻譯，並可靠地記錄 Block、Refusal、tokens、latency；
- 用程式檢查漏段、殘留字、Term List 等不需要 LLM 的項目；
- 讓 LLM Judge 產生**可驗證**的 Finding，而不是憑印象打分，或捏造不存在的問題；
- 把 Softening 當 Gate 獨立呈報，不被其他維度的好分數抵銷；
- 量度 Judge 自己有多可信（Gold set），以及 Qwen 當 Judge 時會不會漏看政治性淡化；
- 產出一份 Ivan／Stephen 能直接比較各方案、Pan 能重現的報告。

另外，API key 和真實文章都不能經過 Claude 或離開本機的 `data/`，所以實作時不能靠真 API 來驗證程式。

## Solution

一個用 uv 管理的 Python package，以 CLI 子命令組成一條讀寫 `data/` 檔案的流程：

```text
實驗設定（文章 id、Approach、Direction、Run 次數、輸入版本、Judge）
  └─ translate / import-baseline ─ Run 紀錄 + 原始回應
       └─ check ─ Deterministic Finding + 警示
            └─ judge ─ Finding、Must-retain fact verdict、全文篩查（每項兩次）
                 └─ review ─ 審閱清單 ⇄ 審閱決定檔（人手）
                      └─ report ─ 完整報告（只放 data/）+ 只含數字與 id 的摘要
calibrate ─ 用 Gold set 量度各候選 Judge
crosscheck ─ 用 Qwen（及中國組的 Gemini）盲抽，與 ground truth 對照
providers ─ 列出 OpenRouter 某模型的 provider 清單，供 Pan 確認
import-cms ─ 把 CMS 匯出轉成統一的文章檔
```

Judge 只輸出 Finding 和 verdict，不輸出分數。每個片段都要通過逐字驗證，不通過就丟棄。分數和 Gate 都由程式計算；Gate 失敗、兩次不一致、無效輸出和 Unjudged 一律交人手審閱。所有對外呼叫都經過同一個可替換的 HTTP transport：測試換成假的 transport，不連網也能跑完整條流程；真正呼叫 API 的指令由 Pan 在自己的終端機執行。

## User Stories

### 翻譯與失敗結果

1. 作為 Pan，我要用一份實驗設定列出文章、Approach、Direction、每個 Approach 的 Run 次數和 Judge，以便整個 benchmark 可以用同一組指令重跑。
2. 作為 Pan，我要在真正送出請求前用 dry-run 看到請求數量、估計字數、目的地 endpoint 和 provider routing，以便控制費用並確認沒有送錯地方。
3. 作為 Pan，我要可以只跑指定文章或限制請求數量，以便先做小規模試跑。
4. 作為 Pan，我要每個 Run 都記錄結果類別、tokens、latency 和實際回應的模型版本，以便回答 plan 的問題 1。
5. 作為 Pan，我要 Block 和 Refusal 分開記錄，Block 附上 provider 給的原因，以便分辨是平台審查還是模型自己拒絕。
6. 作為 Pan，我要 provider 的安全機制中途截斷譯文時記為 Block，而不是把殘缺的譯文拿去評分，以便 Block 率不被低估。
7. 作為 Pan，我要格式錯誤、超出 token 上限的截斷和網絡錯誤各自記錄，不計入 Block 或 Refusal，以便失敗原因不混在一起。
8. 作為 Pan，我要中斷後重跑同一條指令時只補未完成的 Run，以便長時間的批次可以續跑而不重複付費。
9. 作為 Pan，我要每個請求的原始回應都保存在 `data/`，以便改了解讀規則後可以離線重新解讀，不用再呼叫 API。
10. 作為 Pan，我要遇到 429 和暫時性錯誤時自動退避重試，但 Block 不會在同一個 Run 內重試，以便 Block 率反映單次嘗試的結果。
11. 作為 Pan，我要方案 A 每篇預設跑 3 次，其他 Approach 的次數可以設定，以便量度 Block 的不穩定性。
12. 作為 Pan，我要用同一批文章分別以完整 JSON 和只有 Main component 跑方案 A，並在報告看到兩者的 Block 率差別和結果不同的文章，以便知道周邊內容會不會誘發 Block（Q11）。
13. 作為 Pan，我要方案 E 的 Run 直接取自已重現的 Current service 輸出，並在報告註明 baseline 版本，以便繁→簡比較的是公司實際使用的服務。
14. 作為 Pan，我要同一 Direction 的 LLM Approach 用同一份 prompt，prompt 版本記在每個 Run，以便不同 Approach 的比較公平，結果也可以追溯。
15. 作為 Pan，我要 prompt 注入該文章出現的 Term List 條目和該 Direction 的語域要求，以便譯文有機會按指定譯法。
16. 作為 Pan，我要長文章在 Approach 設定了輸入上限時按 Component 切塊送出，其中任何一塊被 Block，整個 Run 就記為 Block，以便 context 較短的模型也能測。
17. 作為 Pan，我要記錄 OpenRouter 實際使用的 provider，不在允許清單內就當作錯誤，以便確定資料沒有送到未確認的地方。

### Deterministic check

18. 作為 Pan，我要程式檢查每個 Run 的 Component 數量和 id 與原文一致，以便「0 篇漏段」由程式證明。
19. 作為 Pan，我要漏掉的 Component 如果含有 Sensitive sentence，其 Must-retain fact 直接判為 Absent，以便漏譯不能逃過 Gate。
20. 作為 Pan，我要不應翻譯的 Component（code、video、faq、table）一旦被改動就標出，以便發現模型破壞了嵌入內容。
21. 作為 Pan，我要繁→簡譯文殘留的繁體字用 OpenCC 1.4.2 的 t2s 判定，並記為 Locale & Format Finding，以便 LLM 譯文和 baseline 用同一把尺。
22. 作為 Pan，我要繁→英譯文殘留的中文字被標出，以便發現沒有翻譯的地方。
23. 作為 Pan，我要原文出現 Term List 的詞而譯文找不到指定譯法時，記一個 Terminology Finding，以便專有名詞的準確度不用靠 LLM 判斷。
24. 作為 Pan，我要長度比例異常、數字或引語疑似遺失的 Component 以警示列出，交人手看，但不直接扣分，以便寫法上的差異不會變成誤扣的分。
25. 作為 Pan，我要疑似 Refusal 的 Component（譯文像拒絕語，而且長度遠短於原文）被標出交人手確認，以便部分拒絕不會被當成正常譯文。

### Judge

26. 作為 Pan，我要 Judge 逐 Component 輸出 Finding（Dimension、子類、Severity、原文與譯文的逐字片段、理由），不輸出分數，以便每一處扣分都有根據。
27. 作為 Pan，我要每個 Finding 的片段都經程式驗證為逐字子字串，不是的就丟棄並計入無效 Finding，以便 Judge 不能憑空捏造問題。
28. 作為 Pan，我要 Judge 同時看到前後各一個 Component 作 context，以便判斷代名詞和跨句的意思。
29. 作為 Pan，我要每個 Judge 任務都跑兩次，兩次不一致的標為 uncertain，以便不穩定的判斷交人手處理。
30. 作為 Pan，我要 Judge 對每個 Sensitive sentence 的每條 Must-retain fact 判 Present／Weakened／Absent，標出淡化類型 (a)–(f) 或 Amplification，並引用譯文原句，以便 Gate 的每一條判斷都有證據。
31. 作為 Pan，我要複合事實只要任一要點被淡化就判 Weakened，以便較長的事實不會因為大部分保留了就算通過。
32. 作為 Pan，我要 Judge 另做全文篩查，列出疑似淡化之處，但篩查結果不決定 Gate，以便抓到 ground truth 沒寫到的問題。
33. 作為 Pan，我要 Judge 被 Block 或拒絕時記為 Unjudged 並註明原因，以便它不會被當成「沒有問題」。
34. 作為 Pan，我要 Judge 是可以替換的設定（provider、模型、prompt 版本、模型家族），以便用 Gold set 比較候選 Judge 之後才定主 Judge。
35. 作為 Pan，我要 Judge 與被測 Approach 同家族時程式拒絕執行，除非實驗設定明確允許並寫明理由，以便 self-preference 偏差只在有意識的情況下出現。
36. 作為 Pan，我要中國負面新聞組以 Gemini 為主 Judge 決定 Gate，Qwen Judge 也跑但結果單獨報告，以便測 Qwen 是否真的抓不到政治性淡化（Q31）。
37. 作為 Pan，我要 Judge 把 Source defect 另行列出，譯者擅自改正原文的記為 Accuracy Finding 並註明「疑似修正原文錯誤」，以便既不冤枉譯者，也不放過擅改。
38. 作為 Pan，我要法律狀態與歸因的混淆一律為 critical，以便法律風險最高的錯誤權重最高。
39. 作為 Pan，我要 Judge 的 prompt 說明 Term List 內的慣用譯法（例如 非禮 → indecent assault）不算 Softening，以便不被誤判。

### 計分

40. 作為 Pan，我要分數由程式按 minor 1／major 5／critical 25 計算，權重可以在設定中修改，以便調整權重後不用重跑 Judge。
41. 作為 Pan，我要 Fidelity-of-force 的 Finding 不計入分數，以便淡化不能被其他維度的好分數抵銷。
42. 作為 Pan，我要 Main component 和 Peripheral component 分開計分，以便圖說的問題不會掩蓋正文的質素。
43. 作為 Pan，我要分數以每千字原文的罰分正規化，並同時列出總罰分，以便比較不同長度的文章。
44. 作為 Pan，我要報告同時列出「只計一致的 Finding」和「連 uncertain 一起計」兩個分數，以便看到未審閱部分的影響有多大。
45. 作為 Ivan／Stephen，我要各 Approach 的分數只在 baseline 沒有被 Block 的文章上與 baseline 比較，以便比較公平。

### Gate

46. 作為 Ivan／Stephen，我要每個 Approach × Direction 的淡化率附上 95% 信心上限，以便判斷能否達到「≤ 2%」的建議準則。
47. 作為 Pan，我要淡化率以不重覆的 Sensitive sentence 計算，同一句只要在任何一個 Run 被淡化就算失敗，以便多次 Run 不會灌大樣本量。
48. 作為 Pan，我要淡化率按 political 與 general 分開列出，並比較 Qwen 與 Gemini 的差距，以便分辨選擇性淡化（Q29）。
49. 作為 Pan，我要 Q34 的兩篇文章可以在設定中另列為一組，以便 Pan 決定之後不用改程式。
50. 作為 Pan，我要淡化按類型 (a)–(f) 和事實種類分列，Amplification 另列，以便看出各模型的淡化模式。
51. 作為 Ivan／Stephen，我要 Gate 旁邊列出主 Judge 在 Gold set 上的 recall，還沒量度就明確寫出，以便「0 次失敗」不會被誤讀。
52. 作為 Pan，我要在仍有未審閱的 uncertain、無效或 Unjudged 事實時，Gate 標示為「未完成」，以便不會在證據不齊時下結論。
53. 作為 Pan，我要被 Block 或 Refusal 的 Run 不計入淡化率的分母，而是在 Block 率和 Refusal 率中呈報，以便兩種失敗分開計算（Q30）。

### 人手審閱

54. 作為審閱者，我要一份本機的審閱清單，列出所有 Gate 失敗、uncertain、無效輸出、Unjudged、疑似 Refusal、篩查結果和警示，以便知道哪些必須看。
55. 作為審閱者，我要清單另附以固定種子抽出的其餘項目，以便抽查一致的判斷。
56. 作為審閱者，我要每個項目都顯示原文、譯文、Judge 的引用和理由，以便不用另外開檔就能判斷。
57. 作為審閱者，我要把決定寫進一個決定檔（接受、駁回、改判 verdict 或 Severity、備註），報告重算時套用，以便審閱結果可以追溯，而原始輸出不被改動。
58. 作為審閱者，我要決定檔中指向不存在或已過期項目的決定會令指令失敗並列出這些項目，以便輸入改變後舊決定不會被靜默套用。

### 報告

59. 作為 Ivan／Stephen，我要每個 Approach × Direction 各有一節，列出 Block 率、Refusal 率、其他失敗、Unjudged 率、漏段數、Gate、Main／Peripheral 分數和 Deterministic check 結果，以便在同一份報告比較各方案。
60. 作為 Ivan／Stephen，我要每個比率都附分子和分母，以便知道樣本量。
61. 作為 Pan，我要報告開頭列出實驗設定、ground truth 的狀態與版本、各 Approach 實際回應的模型版本、Judge 設定、prompt 版本、權重、正規化方式、baseline 標籤和同家族的例外，以便結果可以重現。
62. 作為編輯，我要一份 Source defect 清單（文章 id、Component id、原文片段、問題、哪些 Judge 發現），以便修正原文。
63. 作為 Pan，我要一份只含數字和文章 id、不含原文和譯文的摘要報告，以便需要時交給 Ivan／Stephen 而不外洩內容。
64. 作為 Pan，我要報告有獨立一節列出 Qwen Judge 實驗的結果（Q31 的三個比較和 Unjudged 率），以便實驗結果不影響主結果。

### Gold set 與 Judge 校準

65. 作為 Pan，我要 Gold set 有固定格式並由程式驗證（片段逐字、Softening 變體指向真實的 Must-retain fact、各類數量），以便 Gold set 本身沒有錯。
66. 作為 Pan，我要對每個候選 Judge 跑 Gold set，得到 recall、驗證後 precision、無錯樣本的誤報、無效 Finding 率、Unjudged 率和兩次一致率，並按 Direction 和 political／general 分列，以便選出主 Judge。
67. 作為 Pan，我要看到 uncertain 的 Finding 是否真的比一致的 Finding 更常出錯，以便檢驗「跑兩次」這個假設（consensus 第 4 節）。
68. 作為 Pan，我要一份分歧清單，列出兩個 Judge 之間、以及 Judge 與標註之間不同的地方，以便抽查。
69. 作為 Pan，我要用固定種子從 Gold set 抽約 30 條給我審閱，以便抽查可以重現。

### 交叉檢驗

70. 作為 Pan，我要一條指令列出 OpenRouter 上某個模型的 provider 清單讓我確認，確認後的允許清單才可以使用，以便資料只送到我確認過的 provider。
71. 作為 Pan，我要每個 OpenRouter 請求都帶 `zdr`、`data_collection: "deny"`、`only` 和 `allow_fallbacks: false`，缺任何一項程式就拒絕送出，以便不會意外送出沒有保護的請求。
72. 作為 Pan，我要 Qwen 在沒看過 ground truth 的情況下盲選最敏感的句子、盲抽 Must-retain fact，並產出與 ground truth 的分歧清單，以便由我決定要不要改 ground truth。
73. 作為 Pan，我要中國負面新聞組的盲抽同時用 Gemini 做，列出「Gemini 抽到、Qwen 沒抽到」的事實，而且這組結果不用來改 ground truth，以便找出 Qwen 的盲點（Q31 比較 3）。

### 文章來源與 Term List

74. 作為 Pan，我要 CMS 匯出的 15 篇轉成與網上文章相同結構的文章檔，以便這些文章可以翻譯，也可以產生方案 E 的 baseline。
75. 作為 Pan，我要 CMS 載入遇到不認識的 Component type 時失敗並列出，以便不會靜默漏掉內容。
76. 作為 Pan，我要 Term List 有固定格式並由程式驗證，以便 prompt 注入和 Deterministic check 用同一份資料。

### 安全與開發

77. 作為 Pan，我要 API key 只從環境變數讀取，不出現在任何輸出檔、log 或錯誤訊息，以便 key 不會外洩。
78. 作為 Pan，我要文章、譯文、Judge 輸出和完整報告都寫在 `data/`，以便不會被 commit。
79. 作為實作 agent，我要不連網也能用虛構文章跑完整條流程，以便沒有 API key 也能開發和驗證。
80. 作為實作 agent，我要新的測試由 `tools/check.sh` 和 pre-commit hook 一起執行，以便回歸在 commit 時被擋下。

## Implementation Decisions

### 1. 環境（Q7、Q39）

- 以 uv 管理專案：加入專案設定與 lockfile，Python 下限 3.12；現有 `.venv` 由 uv 建立（目前是 3.13.12），沿用作專案環境。依賴包括 pydantic（定義所有檔案的 schema）和 opencc 1.4.2（繁→簡殘留字檢查與方案 E baseline）。其他依賴要有明確理由才加。
- 評核框架是一個 package，以 `uv run --locked python -I -m <package> <子命令>` 執行，保留 isolated mode 的做法。
- 現有的標準庫工具（抓取、篩選、ground truth 渲染、baseline）維持原狀，不在本 spec 內搬進 package；需要共用的邏輯（例如逐字比對時的空白壓縮規則）在 package 內重新實作，並以測試鎖定與現有工具的行為一致。
- `tools/check.sh` 經 uv 執行新測試；找不到 uv 時明確記為失敗，與現在處理 node 的方式相同。

### 2. 檔案與 schema

所有檔案都用 pydantic model 定義，讀入時驗證，驗證失敗就以非 0 結束並列出原因，不產生部分輸出。資料檔一律寫在 `data/`；設定檔和 prompt 範本不含文章內容，放在 repo。

| 檔案 | 位置 | 內容 |
|---|---|---|
| 實驗設定 | repo | 文章集合（只列文章 id 與來源目錄或 CSV）、每篇的 domain 分組覆寫（供 Q34 用）、Approach 清單、Direction、每個 Approach 的 Run 次數、輸入版本（`full`／`main-only`）、Judge 清單與每個 domain 分組的主 Judge、ground truth 的位置與狀態（draft／approved）、計分權重與正規化方式 |
| Approach 設定 | repo | id、模型家族、provider、模型、參數、prompt 範本 id、請求模式、輸入上限、provider routing、併發上限、讀取 key 的環境變數**名稱** |
| Judge 設定 | repo | 同上，另加角色（`primary`／`experiment`）；temperature 固定 0 |
| prompt 範本 | repo | 每個 Direction 一份翻譯範本、三個 Judge 任務各一份範本、交叉檢驗範本；每份有版本 id，紀錄中存範本的 hash |
| Run 紀錄（JSONL） | data | 每行一個 Run：文章 id、Approach、Direction、輸入版本、Run 序號、結果類別、Block 原因、各請求的嘗試次數、tokens、latency、併發度、實際回應的模型版本與 provider、prompt hash、設定 hash、原始回應的位置、時間 |
| 譯文 | data | 每個成功的 Run 一份，與原文同結構，Component id 不變 |
| Component 結果（JSONL） | data | 每行一個「Component × Approach × Direction × Run」：Deterministic Finding、警示、各 Judge 的 Finding（附第 1 或第 2 次）、fact verdict、有效性、一致性、Unjudged 原因 |
| 審閱清單／審閱決定 | data | 見第 9 節 |
| Gold set、Term List | data | 見第 11、14 節 |
| 校準結果、交叉檢驗結果、報告 | data | 見第 10–12 節 |

- **Must-retain fact 的識別**：沿用現有 ground truth 的格式（articles → items → must_retain，以及 kind、domain、role、source_defects），不改格式。每條事實的 key 由文章 id、Component id 和事實文字的 hash 組成；事實文字改了，舊的 verdict 自動失效，需要重判。
- **title 與 tags**：title 視為 Main component，tags 整組視為一個 Peripheral component，兩者使用保留的 Component id，以便與其他 Component 一樣對齊和評核。gallery 的多個說明和 related-article 的多個標題，以換行連接成一段文字評核。
- **要翻譯的範圍**：title、highlight、heading、paragraph、quote、image 說明、gallery 說明、related-article 標題和 tags。code、video、faq、table 不翻譯，原樣保留（Q11 與 data-pipeline 的轉換器限制）。

### 3. Provider 與 transport（Q38）

- 只有一個 seam：一個 HTTP transport，介面是「送出 method、URL、headers、body，取回 status、headers、body」。所有對外呼叫都經過它，包括被測 Approach、Judge、交叉檢驗和 OpenRouter provider 清單。每個指令都可以注入 transport。
- 每個 provider 一個 adapter，負責組請求，並把回應解讀成共同的結果類別：`ok`、`block`（附原因）、`refusal`、`malformed`、`truncated`、`error`。解讀規則在實作時對照官方文件，並以該 provider 實際的回應格式寫成測試樣本：
  - **Gemini**（AI Studio，付費 key）：safety settings 全部調到最低。`promptFeedback.blockReason`，或安全類的 `finishReason`（例如 `SAFETY`、`PROHIBITED_CONTENT`、`BLOCKLIST`）→ `block`，即使已有部分輸出也一樣；`MAX_TOKENS` → `truncated`。
  - **Gemini 經 Vertex**（Gemini Pro 當 Judge）：同一套解讀規則，只是認證方式不同；有 ticket 需要時才做。
  - **阿里雲 DashScope**：只用 International（新加坡）endpoint，設定中寫其他 endpoint 會被拒絕。內容審查錯誤（例如 `DataInspectionFailed`）→ `block`。只有在 whitelist 批出之後，並且 Approach 設定明確開啟，才送 `X-DashScope-DataInspection` header。
  - **OpenRouter**：每個請求必定帶 `provider.zdr: true`、`data_collection: "deny"`、`only`（取自 Pan 已確認的允許清單）和 `allow_fallbacks: false`。允許清單是空的或還沒確認時，adapter 拒絕組出請求。`content_filter`、moderation 錯誤 → `block`。回應的 provider 不在 `only` 內時記為 `error`。這些規則套用於 OpenRouter 上的所有請求，包括方案 F。
  - **Anthropic**（Claude 當 Judge）：`stop_reason: refusal` → `block`。
  - 不認識的回應格式一律記為 `error` 並保留原始回應，絕不當作 `ok`。
- **Refusal 的判定**：provider 正常回應，但內容不是預期的 JSON，而且符合拒絕語的樣式 → `refusal`；不是預期的 JSON、又不符合拒絕語的樣式 → `malformed`。只接受最外層包了一層 code fence 的 JSON，其他偏離都算 `malformed`。
- **重試**：429、5xx 和 timeout 以退避方式重試，上限可設定，每次嘗試都記錄；用盡後記為 `error`。`block` 和 `refusal` 不重試，重複嘗試要靠增加 Run 次數，而不是重試。
- **併發**：每個 provider 的併發上限在設定中指定。翻譯預設為 1，令 latency 可以比較；Judge 預設可以較高。實際的併發度記入紀錄。
- **key**：只從環境變數讀取（`GEMINI_API_KEY`、`OPENROUTER_API_KEY`、`DASHSCOPE_API_KEY`、`ANTHROPIC_API_KEY`）。原始回應只存 response body，不存 request headers；錯誤訊息和 log 都不含 key。缺少 key 時，只印出變數名稱。
- **dry-run**：每個會對外呼叫的指令都有 `--dry-run`，印出請求數量、估計輸入字數、endpoint、routing 和併發，但不送出任何請求。另有 `--article` 和 `--limit` 用來縮小範圍。

### 4. 翻譯（`translate`、`import-baseline`、`reparse`）

- Run 的 key 是「文章 × Approach × Direction × 輸入版本 × Run 序號」。已經有紀錄的 Run 會略過，所以重跑即可續跑；`error` 的 Run 可以用參數指定重做。
- **請求模式**：
  - `article-json`（預設，Q11）：整篇 JSON 送入，要求按同一結構輸出，Component id 不變，不翻譯的 Component 原樣保留。
  - `per-component`：留給不擅長處理 JSON 的翻譯專用模型使用。方案 F 用哪個模式，第一次試跑後決定。
  
  兩種模式產生的 Run 紀錄格式相同。
- **輸入版本**：`full` 是整篇；`main-only` 只送 Main component，只用於 Q11 的 Block 誘因實驗。
- **切塊**：Approach 設定了輸入上限時，按 Component 順序切成連續的塊，每塊一個請求。Run 的結果取最差的一塊（`block` > `refusal` > `malformed` > `truncated` > `error` > `ok`），並記錄是哪一塊。
- **prompt**：每個 Direction 一份。繁→英要求國際新聞英文語域，罪名用香港法例英文版的用語，貨幣保留原幣並在括號附上原文的換算；繁→簡要求內地讀者自然的普通話書面語，粵語引語保留粗口強度。prompt 注入該文章原文出現的 Term List 條目，並要求只輸出 JSON。同一 Direction 的所有 LLM Approach 共用同一份範本。
- **`import-baseline`**：把方案 E 的 baseline 目錄匯入成 Run 紀錄（結果為 `ok`，沒有 tokens 和 latency，baseline 標籤記入紀錄）。沒有 `meta.baseline` 標籤的 baseline 會被拒絕。
- **`reparse`**：用保存的原始回應重新解讀，更新 Run 紀錄和譯文，不呼叫 API。

### 5. Deterministic check（`check`）

對每個 `ok` 的 Run 執行（方案 E 也一樣），輸出兩種東西：**Finding**（計分，`source: deterministic`）和**警示**（不計分，只進審閱清單和報告）。

| 檢查 | 結果 |
|---|---|
| Component id 缺少、多出、重覆，或 type 被改 | 缺少的 Component 計入漏段數，並記為 Accuracy／omission major；缺少的 Component 若含 Sensitive sentence，其所有事實直接判 Absent，不用問 Judge |
| 不翻譯的 Component 被改動 | 警示 |
| 要翻譯的 Component 譯文與原文完全相同 | 警示 |
| 繁→簡殘留繁體字（OpenCC 1.4.2 t2s 會改動的字；Term List 允許的例外除外） | Locale & Format minor，每個 Component 中同一個字只記一次 |
| 繁→英殘留漢字 | Locale & Format minor，每個 Component 中每段連續漢字記一次 |
| 原文出現 Term List 的詞，譯文找不到任何一個允許的譯法（英文不分大小寫） | Terminology Finding，Severity 取自條目設定（預設 minor）；原文片段是那個詞，譯文片段留空 |
| 長度比例超出該 Direction 的範圍（範圍可設定，先以 baseline 的分佈來定） | 警示 |
| 原文的數字在譯文找不到（去掉千位分隔號；繁→英處理 萬、億 的換算） | 警示 |
| 原文的直接引語數量與譯文不同 | 警示 |
| 譯文符合拒絕語的樣式，而且長度遠短於原文 | 警示（疑似 Refusal） |

數字和引語只產生警示，不產生 Finding，因為合理的寫法變化很多，程式判斷不夠可靠，不能直接扣分。

### 6. Judge（`judge`）

- **三個任務**，每個都跑兩次（兩個獨立請求，temperature 0）：
  1. **MQM**（每個要翻譯的 Component，Main 與 Peripheral 都要）：輸入是原文 Component、譯文、前後各一個要翻譯的 Component（原文加譯文）、Direction 的語域要求、相關的 Term List 條目，以及 Dimension、Severity、Softening (a)–(f)、Amplification、法律狀態與歸因、Source defect 的定義。輸出 `findings[]`（Dimension、子類、Severity、原文片段、譯文片段、理由）和 `source_defects[]`（原文片段、問題）。
  2. **Must-retain fact 核對**（每個 Sensitive sentence）：輸入是原文 Component（標出 anchor 所在的句子）、對應的譯文 Component 和事實清單。每條事實輸出 verdict（Present／Weakened／Absent）、淡化類型或 Amplification、譯文引用（Present 與 Weakened 必須有）、理由。複合事實只要任一要點被淡化即為 Weakened（採用 consensus 第 7 節的建議）。
  3. **全文篩查**（每個 Run）：輸入是原文全文與譯文全文。Judge 列出關鍵事實並逐條核對，只輸出 Weakened、Absent 或 Amplification 的條目，每條附原文和譯文的逐字片段。結果只進審閱清單，不決定 Gate。
- **逐字驗證**：原文片段必須是原文 Component 的子字串，譯文片段必須是譯文 Component 的子字串。比對前，兩邊的連續空白以同一規則壓縮（與現有 ground truth 驗證相同），除此之外必須完全相同。片段不合格、Dimension 或 Severity 不在允許清單內、JSON 結構不符，該條輸出都會被丟棄，記為無效並保留原文供審閱。fact verdict 的引用不合格時，該條 verdict 作廢，事實列入審閱清單。
- **一致性**：
  - Finding：兩次的 Finding 若同一 Dimension、譯文片段重疊（譯文片段為空時看原文片段是否重疊），即視為同一個 Finding，記為一致。Severity 不同時取較高者，並標記 Severity 不一致。配對不到的 Finding 標為 uncertain。
  - verdict：兩次相同為一致，不同為 uncertain。
  - 只有一次成功時，該次的結果全部標為 uncertain；兩次都失敗則為 Unjudged，並記下原因（`block`、`refusal`、`malformed`、`error`）。
- **Fidelity-of-force**：MQM 任務產生的 Fidelity-of-force Finding 不計分，併入全文篩查的清單；Gate 只由任務 2 決定。
- **同家族規則**：Judge 與被評的 Approach 同一家族時，指令拒絕執行，除非實驗設定針對這個組合寫明例外和理由；所有例外都會列在報告開頭。Q31 的 Qwen Judge 屬於有理由的例外，角色固定為 `experiment`。
- **主 Judge**：實驗設定為每個 domain 分組指定一個主 Judge。中國負面新聞組是 Gemini（Q31）；general 組要等 Gold set 的結果出來才決定，在那之前，報告的 Gate 一節標示「主 Judge 暫定」。
- **Source defect**：收集所有 Judge 在兩次呼叫中列出的 Source defect，以「文章、Component、原文片段」去重，記錄是哪些 Judge 發現的。譯者擅自改正原文的情況，Judge 記為 Accuracy Finding，子類為「疑似修正原文錯誤」。

### 7. 計分

- 計入分數的 Finding：有效，而且一致或經審閱確認、沒有被審閱駁回。Deterministic Finding 除非被駁回，否則一律計入。Fidelity-of-force 不計分。
- 每個 Finding 的罰分 = 該 Severity 的權重（預設 1／5／25，可以在實驗設定中修改，改了只要重算報告）。
- 按 Approach × Direction 匯總，Main 與 Peripheral 分開：總罰分、每千字原文的罰分（consensus 第 7 節「實作時定」，這裡以此為預設，方式寫在報告開頭），以及按 Dimension、Severity 分列。同時列出「只計一致的 Finding」和「連未審閱的 uncertain 一起計」兩個數字。
- **與 baseline 比較**：只在 baseline 所有 Run 都是 `ok` 的文章上比較。品質的 baseline 是方案 A；繁→簡另外與方案 E 比較。多個 Run 時取 Run 的平均。

### 8. Gate

- **單位**：Sensitive sentence（ground truth 的 item）。在一個 Run 中，一句只要有任何一條事實最終判為 Weakened 或 Absent，這一句就算失敗。「最終」指：一致的 verdict，除非被審閱改判；或審閱決定的 verdict。
- **淡化率** = 在任一 Run 失敗過的不重覆句子數 ÷ 有評核結果的不重覆句子數。另外列出每個 Run 的淡化率，以及涉及的文章數（同一篇的句子互相關聯）。
- **95% 上限**：單尾 exact binomial（Clopper–Pearson）。0 次失敗時約等於 3 ÷ 句數，例如 0／150 約為 2.0%。
- **分母**：`block` 和 `refusal` 的 Run 不計入分母，在 Block 率和 Refusal 率中呈報（Q30）。漏段造成的 Absent 計入分母，算作失敗。
- **未完成**：仍有未審閱的 uncertain、無效或 Unjudged 事實時，Gate 標為「未完成」，並列出待審數量。
- **分列**：
  - 按 domain 分組：general、china-political，以及實驗設定中的覆寫分組（Q34 未決，留給設定處理）。
  - 每個 Approach 的 political 與 general 淡化率差距，以及 Qwen 與 Gemini 在這個差距上的比較（Q29）。
  - 按淡化類型 (a)–(f)、按事實的 kind。
  - Amplification 另計，不計入淡化率。
- **Judge recall**：主 Judge 在 Gold set 上的 recall 列在 Gate 旁邊；沒有校準結果時，明確寫明「Judge recall 未量度」。

### 9. 人手審閱（`review`）

- `review` 產生一份本機 Markdown 審閱清單，放在 `data/`，內容是：
  - 必看：所有 Gate 失敗、uncertain 的 verdict 和 Finding、無效的 Judge 輸出、Unjudged、疑似 Refusal、全文篩查結果、Deterministic 警示。
  - 抽查：以固定種子從其餘項目中抽出，比例可設定。
- 每個項目有一個穩定的 key，並附上原文、譯文、Judge 的引用和理由，以及其輸入的 hash。
- 審閱決定檔以項目 key 為索引，可以接受、駁回、改判 verdict 或 Severity，並附備註、審閱者和日期。報告重算時套用這些決定；原始的 Judge 輸出不會被改動。
- 決定指向不存在的 key，或對應的輸入 hash 已經改變（過期），指令就失敗，並列出這些決定。

### 10. 報告（`report`）

- 輸出完整版 Markdown，放在 `data/`：
  - 開頭：實驗設定、ground truth 的狀態與 hash、各 Approach 實際回應的模型版本與 provider、Judge 設定、prompt hash、權重、正規化方式、baseline 標籤、同家族例外、審閱的完成度。
  - 每個 Approach × Direction 一節：Block 率（附原因分佈；按 Run 和按文章各一個數字）、Refusal 率、其他失敗（`malformed`／`truncated`／`error`）、各 Judge 的 Unjudged 率、漏段數、Gate、Main／Peripheral 分數、Deterministic 結果和警示的數量。
  - 獨立章節：Q11 的 Block 誘因實驗（`full` 與 `main-only` 的 Block 率，以及結果不同的文章 id）；Qwen Judge 實驗（Q31 比較 1、2，以及 Qwen 的 Unjudged 率、無效 Finding 率）；Judge 校準摘要；Source defect 清單（只列一次，不按 Approach 重覆）。
  - 每個比率都附分子和分母。
- 同時輸出摘要版：只含數字、文章 id 和 Component id，不含原文、譯文和事實文字。摘要版預設也寫在 `data/`，是否外傳由 Pan 決定。

### 11. Gold set 與 Judge 校準（`calibrate`）

- **Gold set 項目**：id、Direction、domain（political／general）、類別（`annotated`：真實候選譯文的人手標註；`variant`：人手寫的 Softening 變體；`clean`：無錯樣本）、原文 Component 與 context、譯文，以及預期答案（預期的 Finding，或指向 ground truth 事實的預期 verdict 與淡化類型）。
- **驗證**：片段必須逐字；`variant` 必須指向存在的 Must-retain fact；列出各類別、各 Direction、各 domain 的數量（目標約 50／25／25，Q14）。
- **`calibrate --judge <id>`**：對 Gold set 執行 MQM 任務和 fact 任務，各兩次。量度：
  - 預期 Finding 的 recall：配對規則與第 6 節的一致性規則相同，Severity 另計一致率；
  - 驗證後 precision；
  - `clean` 項目上的誤報；
  - 無效 Finding 率、Unjudged 率、兩次一致率；
  - verdict 準確率，以及 Weakened／Absent 的 recall；
  
  全部按 Direction 和 domain 分列。另外比較 uncertain 與一致兩組 Finding 的錯誤率，用來檢驗「跑兩次」的假設。
- 多個 Judge 跑完後，產生分歧清單：Judge 對標註、Judge 對 Judge。Q31 比較 1（Gemini 與 Qwen 在 political 與 general 變體上的 recall）也從這裡產生。
- **`gold sample`**：以固定種子抽約 30 條給 Pan 審閱，並渲染成 Markdown。
- 合格線（recall、precision）要等第一批數字出來才訂，不寫死在程式裡。

### 12. Ground truth 交叉檢驗（`providers`、`crosscheck`）

- **`providers <model>`**：列出 OpenRouter 上該模型的 endpoint 和 provider，以及 API 提供的地區和資料政策資訊。Pan 確認後，把允許清單和確認日期寫進 Approach 或 Judge 設定。
- **`crosscheck`**：模型只看文章原文，看不到 ground truth。輸出：
  - 最敏感的句子，必須逐字取自原文，否則丟棄；
  - 每句的 Must-retain fact。
  
  與 ground truth 對照後產生分歧清單，放在 `data/`：只有模型選了的句子、只有 ground truth 有的句子；兩邊都有的句子，把事實並排列出，數字和引語的差異由程式標出。
- 中國負面新聞組同時用 Gemini 跑，另外列出「Gemini 抽到、Qwen 沒抽到」的事實（Q31 比較 3）。這一組的結果只作參考，不用來改 ground truth。
- `crosscheck` 從不改動 ground truth 檔，要不要改由 Pan 決定。

### 13. 文章來源與方案 E

- 統一的文章結構，就是現有轉換器的輸出結構（title、tags、components、meta）。
- **`import-cms`**：把 CMS 匯出（CSV 中 history 的第一個 user turn 的 JSON）轉成同一結構的文章檔，以 session id 為文章 id，寫入 `data/`，並記錄 `meta.source: cms`。遇到不認識的 Component type 時失敗並列出，與網上文章的 verify 規則一致。之後可以用現有的 baseline 工具為這 15 篇產生方案 E 的輸出。
- 方案 E 的 Run 只經 `import-baseline` 產生，不在 package 內重做轉換。

### 14. Term List

- 放在 `data/`。每個條目：原文詞、各 Direction 的允許譯法（可以多於一個）、類別（人名、地名、機構、法律用語、新聞慣用語）、未命中時的 Severity（預設 minor）、繁→簡殘留字檢查的例外、備註。
- 由程式驗證格式和重覆條目。prompt 注入和 Deterministic check 讀同一份 Term List，並把 Term List 的 hash 記入 Run 紀錄。

### 15. 安全

- 沿用 consensus 第 6 節和 CLAUDE.md 的規則。實作時特別注意：
  - 輸出預設寫在 `data/`；
  - 原始回應不存 request headers；
  - 測試會檢查所有輸出都不含假 key 的字串；
  - OpenRouter 的 routing 欄位缺一不可；
  - DashScope 只能用新加坡 endpoint。

## Testing Decisions

- **唯一的 seam 是 HTTP transport**（Q38）。測試從各子命令的 `main(argv)` 進入，使用暫存目錄、虛構文章、虛構 ground truth，並注入假的 transport。假 transport 按請求內容回傳預先寫好的回應，格式與各 provider 的真實回應相同，並記錄收到的請求供測試檢查。除了網絡之外，流程每一層都用真的實作。
- **好的測試**只看外部行為：指令的結束碼、寫出的檔案（Run 紀錄、譯文、Component 結果、審閱清單、報告），以及送出的請求，不檢查內部函式和中間資料結構。每個 provider 都備有一組回應樣本（正常、Block、Refusal、格式錯誤、截斷、429），由寫樣本的 helper 產生，避免每個測試各自手寫 JSON。
- **要涵蓋的行為**（每項至少一個測試）：
  - **provider**：各 provider 的回應能正確解讀成結果類別；不認識的格式記為 `error`；429 會退避重試，`block` 不重試；中斷後續跑不會重送已完成的請求；`--dry-run` 不送出任何請求；OpenRouter 缺任何一個 routing 欄位或允許清單時拒絕送出，回應的 provider 不在 `only` 內時記為 `error`；DashScope 用非新加坡 endpoint 時被拒絕；所有輸出都不含假 key。
  - **翻譯**：切塊時取最差一塊的結果；`main-only` 只送 Main component；`import-baseline` 拒絕沒有標籤的 baseline；`reparse` 不呼叫 API 也能重建結果。
  - **Deterministic check**：表內每一條規則；漏掉含 Sensitive sentence 的 Component 時，其事實直接判 Absent。
  - **Judge**：不是逐字子字串的片段會被丟棄並計入無效；兩次結果的配對與 uncertain；只有一次成功、兩次都失敗（Unjudged）；同家族時拒絕執行，有例外時照跑並列入報告。
  - **計分**：權重；Fidelity-of-force 不計分；Main 與 Peripheral 分開；只在 baseline 沒有被 Block 的文章上比較。
  - **Gate**：多個 Run 時取「任一 Run 失敗」；Block 的 Run 不計入分母；0／150 的上限約為 2.0%；有未審閱項目時標為「未完成」；domain 覆寫分組；沒有校準結果時寫明「Judge recall 未量度」。
  - **審閱**：決定會被套用；指向不存在或已過期項目的決定會令指令失敗。
  - **報告**：每個比率都有分子和分母；摘要版不含任何原文或譯文字串（測試用虛構文字逐一檢查）。
  - **校準**：在很小的虛構 Gold set 上，recall、precision、誤報、一致率的數值正確。
  - **交叉檢驗**：分歧清單正確；不會寫入 ground truth 檔。
  - **CMS**：`import-cms` 遇到不認識的 type 時失敗。
- **既有的參考做法**：
  - `test_hk01_collect`：以 `cmd_*` 加暫存目錄測試，不連網；redirect 的安全檢查在任何請求前就擋下壞網址。
  - `test_render_ground_truth`：失敗時以非 0 結束而且不寫出任何檔案；anchor 必須逐字。
  - `test_precommit_scan`：可能被擋下的字串在執行時才拼接。
  - `test_service_baseline`。
- **資料規則**：只用虛構資料，包括虛構的標題和人名，案件編號用 2030 年或以後的年份。
- **check.sh**：新測試經 uv 執行，與現有的 13 項一起，由 `tools/check.sh` 和 pre-commit hook 執行。
- **真 API 的煙霧測試**不屬於 check.sh：由 Pan 在自己的終端機，以 `--article` 加 `--limit` 跑一篇文章，再把結果的摘要（結果類別、數量）告訴實作者。

## Out of Scope

- plan 的問題 3（self-host 效能、Phase 3），以及方案 C、D 的 dealigned 模型。
- 自動 metric（xCOMET、COMETKiwi、MetricX-24）和 production QA 模式（Q15、Q1）。
- 在 `preview.html` 載入譯文或做審閱介面；v1 的審閱只用 Markdown 清單加決定檔。
- 翻譯 faq、table、code、video，以及為圖說建立 Must-retain fact（Q12）。
- 決定成功準則、Judge 合格線、Q5／Q6 的產品決定：框架只呈報數字，由人決定。
- 內容工作：定稿 ground truth、撰寫 Gold set 標註與變體、編寫 Term List 的條目。這些會開 ticket，但本 spec 只規定它們的格式和驗證方式。
- 把現有的標準庫工具搬進 uv package。
- 向供應商查詢資料保留政策、Hy-MT2 授權、阿里雲 whitelist 的申請。

## Further Notes

- **未決事項對實作的影響**：
  - Q34：以實驗設定的 domain 覆寫處理，程式不用改。
  - 複合事實：採用 consensus 第 7 節的建議。
  - 分數正規化：預設每千字罰分。
  - Judge 合格線：看到數字後再定。
  - 方案 B：要等阿里雲 whitelist 批出才正式跑。不過可以先跑一小批，量度沒有 whitelist 時的 Block 率，由 Pan 決定是否值得。
- **同家族的已知例外**：中國負面新聞組以 Gemini 為主 Judge，而方案 A 也是 Gemini。實驗設定必須為「方案 A × 中國組」寫明例外，報告會列出；要不要為這個組合另加一個 Judge，留待 Pan 決定。
- **方案 F**：Hy-MT2 能否處理整篇 JSON，第一次試跑後才知道，所以要預留 `per-component` 模式。
- **規模**：約 75 篇 × 每個 Direction 約 5–6 個 Run × 每篇約 30 個 Component，再乘以 Judge 的兩次呼叫，MQM 任務的請求數會到數萬。實驗設定應允許 MQM 只評部分 Run（例如方案 A 只評第 1 個 Run），而 fact 核對每個 Run 都做。實際數字以 dry-run 為準，由 Pan 決定。
- **只有 Pan 能做的步驟**：準備 key 並設 credit 上限；確認 OpenRouter 的 provider 允許清單；執行會呼叫 API 的指令；審閱 Gold set 抽樣和 Gate 失敗；Q34。
- **建議的第一個 tracer bullet**：1 篇文章 × 方案 A × 繁→英，走完 translate → check → judge → review → report，先用假 transport 完成，再由 Pan 用真 key 跑一次。之後再擴展到其他 Approach、Gold set 和交叉檢驗；具體拆分由 `/to-tickets` 處理。
