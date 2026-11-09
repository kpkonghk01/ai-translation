# 13: 與 baseline 比較及摘要版報告

**What to build:** 報告把每個 Approach 的分數與 baseline 作比較：質素以方案 A 為 baseline，繁→簡另外與方案 E 比較，而且只比較 baseline 沒有被 Block 的文章。另外輸出一份只有數字和 id 的摘要版，可以交給 Ivan／Stephen。報告開頭補齊所有重現結果需要的資訊。詳見 spec 第 7 和第 10 節。

**Blocked by:** 05, 09

**Status:** ready-for-agent

- [x] 比較只包括 baseline 所有 Run 都是 `ok` 的文章；多個 Run 時取平均；列出比較所用的文章數
- [x] 質素的 baseline 是方案 A；繁→簡另外與方案 E 比較
- [x] 摘要版只含數字、文章 id 和 Component id；測試在輸入中放入虛構的原文、譯文和事實文字，確認這些字串都沒有出現在摘要版中
- [x] 報告開頭列出：ground truth 的狀態和 hash、Judge 設定、權重、正規化方式、baseline 標籤、同家族例外、審閱完成度

## Comments

### 2026-11-09 實作完成

全部勾選項完成，沒有用真資料跑過。規格沒寫、實作時做了選擇的地方：

- **哪個是「方案 A」由實驗設定的 `comparison.baseline` 指定**（Approach 或 baseline 的 id），繁→簡另外要比的寫在 `comparison.also_against`。不寫 `comparison` 就沒有這一節。
- **比較的對象**：每個 Approach 與每個匯入的 baseline（方案 E）都與這個 Direction 的 baseline（質素 baseline A，繁→簡另外加 E）比，自己不與自己比。所以「A 對 E」與「E 對 A」都列，各自以被當作 baseline 的那一邊篩選文章（review 之前我只列 E 對 A，那樣 E 當 baseline 時篩選用的是 A 的 Block）。某個 Direction 沒有 Run 的組合（例如 E 的繁→英）不列。
- **三個條件決定一篇文章進不進比較**：baseline 這個 Direction 的所有 Run 都是 `ok` 而且紀錄數等於計畫（spec 第 7 節）；被比的 Approach 至少有一個 `ok` 的 Run；兩邊的 `ok` 的 Run 都評完。**被比的 Approach 被 Block 的文章也不比**（不當零分，否則 Block 反而讓分數好看），它們在 Block 率呈報；沒比的文章列出 id 與原因。
- **多個 Run 取平均**：每篇文章取 `ok` 的 Run 的總罰分平均，再加總；每千字罰分 = 平均總罰分的合計 ÷ 這些文章的原文字數。Main 與 Peripheral 分開；兩個數字（只計一致的／連 uncertain 一起計）都列。
- **只比 `full` 的 Run、只比 `primary` 的 Judge**。`main-only` 的 Run 與 `experiment` 角色的 Judge 的比較（Qwen Judge）留給第 17 張。
- **不比 Gate**：ticket 只要求分數的比較；Gate 本來就是各 Approach 自己對 1/50 之類的準則。
- **摘要版與完整版用同一批數字產生**（`report` 一次寫兩份），用同一套程式，所以兩份的數字不會不一致；摘要版另外把自由文字拿掉：Source defect 只列位置、同家族例外不列理由、provider 或檔案裡的字串（Block 原因、模型版本、baseline 標籤）只留得出是固定代碼的。測試放進虛構的原文、譯文、事實、Judge 理由、審閱備註與怪異的 provider 字串，確認摘要版沒有、完整版有。
- 「Judge 校準摘要」還沒有（第 14 張）；「Qwen Judge 實驗」一節留給第 17 張。報告開頭 ticket 列的項目都已經有，補了一個測試釘住。

### 2026-11-09 review 之後的修正

- **A 對 E 補上**（見上）。
- **被比的一邊不完整時要標示**：平均只用手上 `ok` 的 Run，原本不說用了幾個。現在列「用了幾個 Run、計畫幾個」，不夠就標 ⚠；計畫的 Run 數按 `mqm_run_limit` 算（沒評的 Run 不算缺）。
- **baseline 的 Run 數比計畫多**（計畫後來減少）原本會讓所有文章被排除；現在只在比計畫少時排除。
- **沒比較的文章 id 全部列出**（原本截到 10 個）；原因改稱「沒有可評核的 ok 的 Run」（原本把「ok 但在 `mqm_run_limit` 之外」也說成沒有 ok）。
- **`comparison` 設定驗證加強**：對象在要比的 Direction 沒有 `full` 的 Run、或質素 baseline 不是 API 跑的 Approach（GLOSSARY 的定義），載入設定時失敗，不再悄悄什麼也不列。
- **`report.md` 與 `summary.md` 兩份都建好才寫**，建摘要版時出錯不會留下新的完整版配舊的摘要版（兩個檔案各自是原子換名，兩個之間不是）。
- **摘要版**：不放空白（一句英文不會通過篩選）；Source defect 每個 Component 一行。
- 沒有照 review 改的：摘要版仍有設定檔寫的字串（模型名稱、家族、provider、judge id、分組名）；它們不是文章內容。**spec 寫「只含數字、文章 id 和 Component id」，若 Pan 要連這些也拿掉，摘要版的 Approach 設定一節要改成只列 id。**

