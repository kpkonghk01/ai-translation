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
- **比較的對象**：每個 Approach 與 baseline（包括 `import-baseline` 匯入的方案 E）對質素 baseline；繁→簡另外對 E。質素 baseline 自己不是被比的對象，所以「E 對 A」會列，「A 對 E」不另列。baseline 自己不與自己比；某個 Direction 沒有 Run 的組合（例如 E 的繁→英）不列。
- **三個條件決定一篇文章進不進比較**：baseline 這個 Direction 的所有 Run 都是 `ok` 而且紀錄數等於計畫（spec 第 7 節）；被比的 Approach 至少有一個 `ok` 的 Run；兩邊的 `ok` 的 Run 都評完。**被比的 Approach 被 Block 的文章也不比**（不當零分，否則 Block 反而讓分數好看），它們在 Block 率呈報；沒比的文章列出 id 與原因。
- **多個 Run 取平均**：每篇文章取 `ok` 的 Run 的總罰分平均，再加總；每千字罰分 = 平均總罰分的合計 ÷ 這些文章的原文字數。Main 與 Peripheral 分開；兩個數字（只計一致的／連 uncertain 一起計）都列。
- **只比 `full` 的 Run、只比 `primary` 的 Judge**。`main-only` 的 Run 與 `experiment` 角色的 Judge 的比較（Qwen Judge）留給第 17 張。
- **不比 Gate**：ticket 只要求分數的比較；Gate 本來就是各 Approach 自己對 1/50 之類的準則。
- **摘要版與完整版用同一批數字產生**（`report` 一次寫兩份），用同一套程式，所以兩份的數字不會不一致；摘要版另外把自由文字拿掉：Source defect 只列位置、同家族例外不列理由、provider 或檔案裡的字串（Block 原因、模型版本、baseline 標籤）只留得出是固定代碼的。測試放進虛構的原文、譯文、事實、Judge 理由、審閱備註與怪異的 provider 字串，確認摘要版沒有、完整版有。
- 「Judge 校準摘要」還沒有（第 14 張）；「Qwen Judge 實驗」一節留給第 17 張。報告開頭 ticket 列的項目都已經有，補了一個測試釘住。

