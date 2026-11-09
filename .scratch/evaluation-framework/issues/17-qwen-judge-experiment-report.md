# 17: Qwen Judge 實驗報告

**What to build:** 報告有一個獨立章節，回答「Qwen 當 Judge 時，是否比較容易漏看中國負面新聞被淡化的地方」（Q31）。這一節的結果不影響主結果和 Gate。詳見 consensus-summary 第 9 節，以及 spec 第 10 和第 11 節。

**Blocked by:** 14

**Status:** ready-for-agent

- [x] 比較 1：從校準結果取 Gemini 與 Qwen 在 political 和 general 變體上的 recall，並列出兩者的差距
- [x] 比較 2：同一批真實譯文由兩個 Judge 各判一次；Gemini 判為 Weakened 或 Absent、Qwen 判為 Present 的事實，列為「Qwen 疑似漏看」；按 domain 和譯文來源（Qwen 自己的譯文、其他模型的譯文）分別統計
- [x] 分開列出 Qwen 的 Unjudged 率（包括輸入端被 Block）和無效 Finding 率，並與「評了但漏看」分開呈報
- [x] 角色為 `experiment` 的 Judge 不影響 Gate 和分數；以測試確認
- [x] 判斷門檻不寫死，只列數字

## Comments

### 2026-11-09 實作完成

全部勾選項完成，沒有用真 Judge 跑過。規格沒寫、實作時做了選擇的地方：

- **哪個是「Qwen」、哪個是「Gemini」由角色決定**：角色為 `experiment` 的 Judge 是被實驗的（Qwen），對照的是這一組的主 Judge（`primary` 角色，按文章的 domain 取：`primary_judges` 指定的，沒指定就是第一個 `primary`）。所以這一節對任何 experiment 的 Judge 都成立，不寫死模型名稱。
- **比較 1**：用校準結果裡 `variant` 在 political／general 的「失敗的事實的 recall」（第 14 張的定義：uncertain 與 Unjudged 算沒有抓到）。列兩個 Judge 各自的數字、差距（experiment − 主 Judge，單位是百分點）和各自的 political − general；任何一邊沒有資料就寫「不能比」。
- **比較 2 的「譯文來源」**：Approach 的 `family` 與這個 Judge 的 `family` 相同就是「自己的譯文」，其他 Approach 和 baseline（方案 E）是「其他模型的譯文」。baseline 沒有家族，一律是其他。
- **漏段的事實不比**：譯文缺少含 Sensitive sentence 的 Component 時，事實是程式判的 Absent，不是 Judge 的判定。
- **疑似漏看的詳細清單**（含原文與譯文）寫在 `data/.../experiment-judge-misses.md`，報告與摘要版只列位置。這是為了 ticket 的「再人手抽查確認」（consensus 第 9 節）；清單沒有進審閱清單，因為審閱清單只審 primary 的 Judge（第 11 張的決定）。
- **Unjudged 與無效**：experiment 的 Judge 與主 Judge 並列，按 domain；MQM 的 Unjudged（兩次都失敗）與事實任務的 Unjudged 分開，原因碼（block 的 `reason`）另列；摘要版只放得出是代碼的原因碼。
- **比較 3** 只放數字（對照模型抽到而盲抽的模型沒抽到的句子數，和盲抽的模型被擋的篇數）；內容在第 16 張的 `crosscheck.md`。
- **「不影響 Gate 和分數」**的測試：同一批資料，experiment 的 Judge 一次把所有事實判成 Absent 並給 critical 的 Finding、一次什麼也沒抓到，Gate、主 Judge 的分數、審閱清單完全相同。
- 判斷門檻沒有寫進程式；這一節不判「有問題」或「沒問題」。

### 2026-11-09 review 之後的修正

- **比較 3 不能把「盲抽的模型被擋」算成「沒抽到」**：原本被擋或還沒跑的盲抽，會讓對照模型的每個句子都變成缺口，同一行又寫「與漏看分開」。現在兩個模型都有結果的才算句數；盲抽被擋或失敗、還沒有結果、對照模型沒有結果各自分開列。
- **參考的主 Judge 按 Gate 的分組取**（原本按文章的 domain）：`primary_judges` 與 `group_overrides` 怎樣改 Gate，比較 2 就怎樣跟，每一列寫出對的是哪個主 Judge。統計也按分組。
- **舊的 `experiment-judge-misses.md` 不再殘留**：拿掉 experiment 的 Judge、或這次沒有疑似漏看時，`report` 會刪掉它（舊的含原文）。
- **比較 1 補上但書**：Judge 被擋、uncertain、尚未評完、過期的請求的數量寫在 recall 後面（原本只有分子分母，兩邊的項目不一樣也看不出來）。
- **全文篩查納入 Unjudged 率**；**無效的 verdict 附分母**；**主 Judge 沒有最終 verdict 的事實數出來**（不比，但不能無聲無息）；**主 Judge 判 Present、這個 Judge 沒有最終 verdict 的**也列；Approach 已不在設定內的譯文另列，baseline 歸「其他 Approach 或 baseline 的譯文」（「模型」一詞 GLOSSARY 列在 Approach 的 Avoid）。
- **結構**：`_gather` 拆成 `Collector` 的幾個方法（健康、事實比較、疑似漏看的詳細），狀態用 enum（`Relation`、`Source`、`Pending`），不再是裸字串；只走一次（完整版與摘要版共用）；`one_line`、`ratio` 只剩一份。

### 2026-11-09 全框架稽核（CODING_STANDARDS.md）之後的修正

稽核是因為前面幾張的 review 沒有把 `CODING_STANDARDS.md` 列給 reviewer。逐條對照整個 package：先驗證後寫入、只經 transport、判定由程式計算、終端輸出與測試的資料都是乾淨的；修了下面幾項：

- **`translate` 遇到 provider 違規後會繼續跑後面的 Run**（與 judge、calibrate、crosscheck 不一致）：現在停在第一個違規的 Run（紀錄保留），有測試（3 個 Run，第一個被未確認的 provider 服務，只送出 1 個請求）。
- **cli 的錯誤開頭在中途失敗時說謊**（「沒有送出請求，也沒有寫出任何檔案」）：新的 `StoppedAfterSending`（provider 違規、`providers` 收到不認識的回應）印「請求已經送出，已寫的紀錄保留」。
- **`cli.py` 的用法說明有兩處不對**（`--data-dir`、需要 key 的指令），改了。
- **報告不再靜默漏掉整節**：設了 Judge／comparison 卻還沒有 MQM 紀錄時，原本整節消失，看起來像沒設；現在寫「還沒有 Judge 的 MQM 紀錄：先執行 judge」。
- **schema 只定義一次**：Judge 的任務（`Task`）、輸出不合格的種類（`InvalidKind`）、「每個任務評兩次」（`CALLS_PER_TASK`）、事實的待審原因（`Pending`）改用 enum／常數；`store.read_judge_calls` 打錯任務名會失敗，不再靜默回傳空的；`tools/render_ground_truth.py` 抄的 domain 與事實種類加了防漂移的測試；`_one_line`、`ratio` 各剩一份。
- **GLOSSARY 的 Unjudged**：改成與程式一致（兩次請求都沒有結果，含 block、refusal、malformed、error，原因列出）。
- **檔案不是 UTF-8 時是 traceback**：現在是受控的錯誤（只說第幾個位元組，不回顯內容）。
- 沒有改的（請 Pan 決定）：**`providers` 指令會印 API 給的 provider 名稱與政策欄位**（有白名單過濾，模組 docstring 有說明，但 `CODING_STANDARDS.md` 的「終端輸出只印 id、類型和數量」沒有這個例外）；**`provider_listing` 自己組請求、不經 `exchange` 的共用層**（單次 GET，沒有重試與 Attempt 紀錄）；OpenRouter 的模型名格式在 schemas 與 `providers` 指令各有一份檢查（目的不同：設定檔、命令列參數）。

