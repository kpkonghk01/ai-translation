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

