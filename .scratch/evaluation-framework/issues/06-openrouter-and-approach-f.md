# 06: OpenRouter、provider 清單與方案 F

**What to build:** Pan 先用 `providers` 指令看到 OpenRouter 上某個模型的 provider 清單，確認後寫進設定，之後才可以跑方案 F（Hy-MT2）。每個 OpenRouter 請求都有資料保護欄位，缺任何一項程式就拒絕送出。翻譯專用模型可以改用逐 Component 的請求模式，長文章可以切塊。這個 adapter 也是日後經 OpenRouter 使用 Qwen 或 Claude 的基礎。詳見 spec 第 3、4 和第 12 節。

**Blocked by:** 01

**Status:** ready-for-human

- [x] `providers <model>` 經 transport 列出該模型的 endpoint 和 provider，以及 API 提供的地區和資料政策資訊；指令本身不寫入設定
- [x] Approach 和 Judge 設定可以記錄允許清單和確認日期；允許清單是空的或還沒確認時，adapter 拒絕組出請求
- [x] 每個 OpenRouter 請求都帶 `provider.zdr: true`、`data_collection: "deny"`、`only`、`allow_fallbacks: false`；測試檢查每一個送出的請求
- [x] 記錄實際服務的 provider；不在 `only` 內時記為 `error`
- [x] `content_filter` 和 moderation 錯誤 → `block`
- [x] 請求模式 `per-component`：產生的 Run 紀錄格式與 `article-json` 相同
- [x] 切塊：Approach 設定了輸入上限時，按 Component 順序切塊；Run 取最差一塊的結果，並記錄是哪一塊
- [x] key 只從 `OPENROUTER_API_KEY` 讀取
- [ ] Pan 執行 `providers`，確認允許清單，再用真 key 對 1 篇文章跑方案 F，並決定方案 F 用哪個請求模式

## Comments

### 2026-11-09 程式完成，只剩 Pan 的步驟

除最後一項（Pan 執行 `providers`、確認允許清單、真跑 1 篇、決定請求模式）外都完成。程式對照 OpenRouter 官方文件寫成，沒有用真 key 跑過。要請 Pan 知道的事：

- **回應裡的 provider**：官方文件沒有寫非串流回應的 body 有哪個欄位說明是哪個 provider 服務的（只在串流的 chunk 提到頂層的 `provider` 字串）。程式讀頂層的 `provider`，**沒有這個欄位就記為 `error`**，因為沒辦法確認資料有沒有送到未確認的地方。如果真跑時每個 Run 都是「回應沒有寫是哪個 provider 服務的」，就是這個欄位的名稱或位置與我假設的不同，看 `raw/` 的回應就能改。
- **slug 與顯示名稱**：請求的 `only` 用 slug（`providers` 指令列的 `tag`，例如 `deepinfra`、`novita/fp8`），回應寫的是顯示名稱（例如 `DeepInfra`）。比對時只看字母和數字、不分大小寫，slug 後面的 `/variant` 不計。
- **地區與資料政策**：`providers` 用的 endpoint 列表，文件列的欄位沒有地區或資料政策。指令會把名字像這類的欄位印出來，沒有就明說「API 沒有提供」；這時請 Pan 到 OpenRouter 的模型頁確認，排除位於中國大陸的 provider。
- **`only` 的 slug 寫錯會怎樣**：文件沒寫（只說跟帳號層級的允許清單都不符時回 404）；程式把 404 記為 `error`，不重試。
- **Block**：403 而且 `metadata.reasons` 有內容是 moderation，記為 Block（原因 `moderation:` 加上 reasons）；`finish_reason: content_filter` 也是 Block。其他 403（例如 guardrail、權限）是 `error`。文件說非串流的回應也可能是 200 但 body 只有 error 物件，程式照樣處理。408、429、5xx 退避重試；402（沒有 credit）、401、400、404 不重試。
- **逐 Component 模式不用拒絕語的字樣判 Refusal**：這個模式模型輸出的是純譯文，譯文的引語裡本來就可能有「I can't」，用字樣判會誤判；只認 provider 明確給的訊號（`message.refusal`、`content_filter`）。疑似 Refusal 由 `check` 的警示攔住。這和 spec 第 3 節「不是預期的 JSON 又符合拒絕語樣式 → refusal」不同，因為這個模式的預期輸出本來就不是 JSON。請 Pan 確認。
- **切塊**：title 和 tags 只在第一塊，其他塊送空的；一個 Component 超過上限時單獨一塊，不拆；遇到 Block 就不再送後面的塊；一個 Run 一筆紀錄，tokens 和 latency 是各塊的總和，原始回應存成 `.jsonl`。
- **請求模式不能同時設 `max_input_chars` 和 `per-component`**：逐 Component 每個請求本來就只有一段文字。
- 模型 id（Hy-MT2 在 OpenRouter 上叫什麼）沒有訂，由 Pan 在 `providers` 指令裡找。
