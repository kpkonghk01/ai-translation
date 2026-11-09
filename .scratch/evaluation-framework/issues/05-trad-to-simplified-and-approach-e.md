# 05: 繁→簡與方案 E

**What to build:** Pan 可以用 LLM Approach 跑繁→簡，也可以把方案 E（Current service）已重現的 baseline 匯入成 Run。兩者都經過同一套 `check`，包括以 OpenCC 1.4.2 判定的殘留繁體字，並一起出現在報告中，報告開頭註明 baseline 的版本。詳見 spec 第 4、5 和第 13 節。

**Blocked by:** 01, 03

**Status:** resolved

- [x] 繁→簡 prompt 要求內地讀者自然的普通話書面語，粵語引語保留粗口強度，並注入 Term List；版本 id 和 hash 記入紀錄
- [x] `import-baseline` 把 baseline 目錄匯入成方案 E 的 Run：結果為 `ok`，沒有 tokens 和 latency，baseline 標籤記入紀錄
- [x] 沒有 `meta.baseline` 標籤的 baseline 會被拒絕
- [x] 殘留繁體字：OpenCC 1.4.2 的 t2s 會改動的字，記為 Locale & Format minor，每個 Component 中同一個字只記一次；Term List 允許的例外不記
- [x] 報告開頭列出每份 baseline 的標籤
- [x] 測試用虛構文字，涵蓋殘留字、例外，以及沒有標籤時的拒絕

## Comments

### 2026-11-09 實作完成

全部勾選項完成。規格沒寫、實作時做了選擇的地方：

- **繁→簡 prompt** 只寫 spec 要求的：內地讀者自然的普通話書面語、粵語引語保留粗口強度、Term List；沒有加其他指示。
- **baseline 的設定**：不放在 Approach 設定，而是實驗設定的 `baselines`（id、目錄、Direction），因為方案 E 不經 API。`import-baseline` 驗證全部通過才寫出；每篇文章一個 Run，`run_index` 是 1。
- **殘留繁體字**：逐字用 OpenCC 1.4.2 的 t2s 判斷（試過幾十個詞，逐字與整段轉換的結果相同）；每個 Component 同一個字只記一次。Term List 的例外只對原文含有那個詞的 Component 有效，而且只豁免例外裡寫的字。
- **繁→簡的數字警示**只核對阿拉伯數字（`萬` 與 `万` 只是字形不同，不換算）；「譯文與原文相同」的警示不用於繁→簡，因為殘留字的 Finding 已經記了。
- **繁→簡的長度比例預設 0.6–1.6（暫定）**：還沒有任何繁→簡譯文可以量，等有 LLM 的譯文再定。
- baseline 的 Run 不帶 prompt，所以不受「check 用的 Term List 要與翻譯時的一致」限制；但 Terminology 照樣核對它。

用現有的 baseline 檔實測（暫存目錄，不動 `data/`）：40 篇、868 個 Component 匯入並 `check` 通過，沒有漏段，殘留字只有 1 個；警示 22 個。

**發現**：現行服務連 `code`（嵌入的 HTML）裡的文字也一起轉了，40 篇的樣本裡 30 個 code Component 有 20 個內容被改動，所以方案 E 在報告裡會有一批「不翻譯的 Component 被改動」警示。這是服務本身的行為，不是報告的問題；要不要處理（例如 baseline 只比對要翻譯的 Component）由 Pan 決定。
