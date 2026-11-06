# 01: 翻譯 tracer bullet：方案 A × 繁→英的 Block／Refusal 報告

**What to build:** Pan 用一份實驗設定（指定文章、方案 A、繁→英、Run 次數）依次執行 `translate`、`check`、`report`，得到一份報告，列出 Block 率、Refusal 率、其他失敗和漏段數。測試不連網，用假 transport 走同一條路。這是整個框架的骨架：建立 uv 專案、transport seam 和檔案 schema，後續 ticket 都建在這上面。詳見 spec 第 1–5 和第 10 節。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] uv 專案：專案設定與 lockfile，依賴 pydantic 和 opencc 1.4.2，沿用現有的 `.venv`；指令以 `uv run --locked python -I -m <package>` 執行
- [ ] `tools/check.sh` 經 uv 執行新測試；找不到 uv 時明確記為失敗
- [ ] 實驗設定與 Approach 設定的 schema 和驗證；設定不合法時以非 0 結束，而且不產生任何輸出
- [ ] 只有一個可注入的 HTTP transport；另有一個測試 helper，產生 Gemini 格式的回應樣本：正常譯文、`promptFeedback.blockReason`、`finishReason: SAFETY` 加部分輸出、`MAX_TOKENS`、非 JSON 的拒絕語、非 JSON 但不是拒絕語、429、5xx、不認識的格式
- [ ] Gemini adapter：safety settings 全部調到最低；回應解讀為 `ok`／`block`（附原因）／`refusal`／`malformed`／`truncated`／`error`；不認識的格式一律為 `error`，絕不當作 `ok`
- [ ] `translate` 使用 `article-json` 模式和繁→英 prompt，prompt 的版本 id 和 hash 記入 Run 紀錄
- [ ] Run key 為「文章 × Approach × Direction × 輸入版本 × Run 序號」，按設定的次數執行
- [ ] 寫出 Run 紀錄（JSONL）、譯文和原始回應；原始回應不含 request headers
- [ ] 續跑：已有紀錄的 Run 會略過；`error` 的 Run 可以用參數指定重做
- [ ] 429、5xx、timeout 以退避方式重試，嘗試次數記入紀錄；`block` 和 `refusal` 不重試
- [ ] `--dry-run` 不送出任何請求，印出請求數、估計輸入字數和 endpoint；另有 `--article` 和 `--limit`
- [ ] key 只從 `GEMINI_API_KEY` 讀取；缺少 key 時，在送出任何請求之前就停止，並只印出變數名稱；測試確認所有輸出檔和錯誤訊息都不含假 key 的字串
- [ ] 設有環境變數 `AI_TRANSLATION_OFFLINE` 時，真 transport 拒絕送出任何請求；`tools/check.sh` 已經設定這個變數並清掉所有 key。測試確認忘了注入假 transport 時，指令會失敗，而不會連網
- [ ] `tools/check.sh` 加入兩項機械式檢查：package 內只有 transport 模組可以 import 網絡相關的庫（例如 `urllib.request`、`http.client`、`socket`、`ssl`）；package 的程式碼不可提及 `.env` 或 dotenv。違反時檢查失敗，並列出檔案和行號
- [ ] 在 data-pipeline 文件寫明 Pan 執行真跑的指令（以 uv 的 `--env-file` 載入 `.env`），並註明只由 Pan 在自己的終端機執行；agent 的 permission 設定已禁止這個參數
- [ ] `check`：檢查 Component id 是否缺少、多出、重覆，type 是否被改；缺少的 Component 計入漏段，並記為 Accuracy／omission major
- [ ] `report` 開頭列出實驗設定、Approach 設定、prompt hash 和實際回應的模型版本
- [ ] `report` 為每個 Approach × Direction 列出 Block 率（附原因分佈）、Refusal 率、其他失敗和漏段數，每個比率都附分子和分母
- [ ] 所有輸出預設寫在 `data/`
- [ ] Pan 用真 key 對 1 篇文章跑一次，回報結果類別和數量（不貼內容）
