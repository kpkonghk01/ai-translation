# 08: CMS 15 篇的載入與 baseline

**What to build:** CMS 匯出的 15 篇（曾被 Gemini block 的文章）轉成與網上文章相同結構的文章檔，可以放進實驗設定翻譯，也可以用現有的 baseline 工具產生方案 E 的輸出，再匯入成 Run。詳見 spec 第 13 節。

**Blocked by:** 05

**Status:** resolved

- [x] `import-cms` 讀 CMS 匯出（取 history 中第一個 user turn 的 JSON），以 session id 為文章 id，寫成統一結構的文章檔，放在 `data/`，`meta.source` 為 `cms`
- [x] 遇到不認識的 Component type 時失敗並列出
- [x] 原文中每一段看得見的文字都必須出現在轉換結果中，否則失敗，規則與網上文章的 verify 一致
- [x] 現有的 baseline 工具可以為這 15 篇產生方案 E 的輸出，標籤與其他 baseline 一致；之後經 `import-baseline` 匯入
- [x] 本機有 CMS 匯出時，`tools/check.sh` 會加跑轉換覆蓋檢查
- [x] 測試只用虛構的 CMS 格式資料

## Comments

### 2026-11-09 實作完成

全部勾選項完成，並用真實的 CMS 匯出跑過一遍：15 篇轉成 `data/cms-articles/`，用現有工具產生 `data/baseline-service-cms/`（標籤 `service-HEAD-290bf22+opencc-1.4.2`，與其他 baseline 一致），匯入後 `check`：335 個 Component，沒有漏段、沒有 Finding，警示 5 個（都是 `code` 被服務改動，見 ticket 05 的留言）。

- **現有 baseline 工具原本會靜默略過這 15 篇**：它只讀檔名是純數字的文章，而 CMS 的 session id 是 24 位 hex，含字母的檔案會被略過，工具照樣印「轉換 N 篇」。已改成接受數字或 24 位 hex，其他檔名的 `.json` 一律失敗並列出；有測試。
- **轉換只保留已知的欄位**：不認識的欄位若有文字，指令失敗並列出欄位名（不印文字）；沒有文字的（例如數字）才略過。CMS 目前的 15 篇沒有這種欄位。
- `video` 在 CMS 匯出裡沒有 caption，網上文章有；兩者都保留。
- 現有 baseline 工具跑的時候會印 `config does not conform to schema` 的警告，這是工具自己原有的（設定檔的 schema 檢查），不影響輸出，其他 baseline 也一樣。
- `tools/check.sh` 本機有 `data-prohibited.csv` 時多跑一項：轉到暫存目錄檢查覆蓋，並與 `data/cms-articles` 逐檔比對。
