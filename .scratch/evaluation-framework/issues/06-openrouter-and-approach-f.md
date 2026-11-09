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
- **地區與資料政策**：`providers` 用的 endpoint 列表，文件列的欄位沒有地區或資料政策。指令會把名字像這類的欄位印出來，沒有就明說「API 沒有提供」；這時請 Pan 到 OpenRouter 的模型頁確認。（2026-11-09：Pan 說明沒有要排除中國大陸的 provider，原本的排除條件來自 10-27 的供應商條款整理，不是 Pan 的決定，已拿掉。）
- **`only` 的 slug 寫錯會怎樣**：文件沒寫（只說跟帳號層級的允許清單都不符時回 404）；程式把 404 記為 `error`，不重試。
- **Block**：403 而且 `metadata.reasons` 有內容是 moderation，記為 Block（原因 `moderation:` 加上 reasons）；`finish_reason: content_filter` 也是 Block。其他 403（例如 guardrail、權限）是 `error`。文件說非串流的回應也可能是 200 但 body 只有 error 物件，程式照樣處理。408、429、5xx 退避重試；402（沒有 credit）、401、400、404 不重試。
- **逐 Component 模式不用拒絕語的字樣判 Refusal，Refusal 率會被低估**：這個模式模型輸出的是純譯文，譯文的引語裡本來就可能有「I can't」，用字樣判會誤判；只認 provider 明確給的訊號（`message.refusal`、`content_filter`）。模型若直接拿拒絕語當譯文，這個 Run 仍是 `ok`，但 `check` 會在那個 Component 列「疑似 Refusal」警示（一開頭就是拒絕語，或像拒絕語而且遠短於原文）；報告每節都有這個警示的數量，所以不會沒人看到，但它不會算進 Refusal 率。這和 spec 第 3 節「不是預期的 JSON 又符合拒絕語樣式 → refusal」不同，因為這個模式的預期輸出本來就不是 JSON。請 Pan 看了 Hy-MT2 的實際行為再決定要不要改成開頭是拒絕語就記為 Refusal（代價：譯文一開頭就是「I can't believe…」的獨立引語會被誤判）。
- **切塊**：title 和 tags 只在第一塊，其他塊送空的；一個 Component 超過上限時單獨一塊，不拆；遇到 Block 就不再送後面的塊；一個 Run 一筆紀錄，tokens 和 latency 是各塊的總和，原始回應存成 `.jsonl`。
- **請求模式不能同時設 `max_input_chars` 和 `per-component`**：逐 Component 每個請求本來就只有一段文字。
- 模型 id（Hy-MT2 在 OpenRouter 上叫什麼）沒有訂，由 Pan 在 `providers` 指令裡找。

### 2026-11-09 review 之後的修正

- **資料送到不在允許清單內的 provider，優先於其他所有結果**：一個 Run 有好幾個請求時，原本會被另一個請求更差的結果（refusal、malformed）蓋過，只剩 `served_provider` 看得出來。現在這個 Run 記為 `error`（原因「provider 不在允許清單內」），而且立刻停止，後面的請求不再送。
- **Block、Refusal、truncated 的回應沒有寫 provider 時照記**，不再變成 `error`（否則 Block 率會被少算）；報告的「實際服務的 provider」會列出有幾個 Run「回應沒有寫」。只有 `ok` 的結果沒有 provider 才是 `error`。
- **沒有任何要送出的字串**的文章（逐 Component 模式，例如沒有標題、沒有 tag，只有 code 的文章）原本會讓指令崩潰；現在在送出任何請求之前就停止並列出文章 id。
- 疑似 Refusal 的警示補上「一開頭就是拒絕語」（見上），因為只看長度攔不住「短的原文配一句拒絕語」。
- 請求模式改用 enum，不再在各處重打字串。
- 逐 Component 的譯文放回原文結構時，若有譯文沒有放回去，會停下來報錯，不會靜默留下原文、卻當作翻譯過了。

**未解決或請 Pan 知道**
- 「Approach 和 Judge」：Judge 的設定 schema 還不存在（ticket 09），所以現在只有 Approach 可以記允許清單；09 要沿用同一個 `routing` 結構。
- `providers` 只呼叫 `/models/{作者}/{名稱}/endpoints`。因為請求一律強制 `zdr: true`，Pan 其實需要知道哪些 endpoint 支援 ZDR；OpenRouter 文件似乎另有 ZDR endpoint 的清單，我沒辦法確認它的格式，所以沒做。請 Pan 看 `providers` 的輸出夠不夠用；不夠的話再補。
- 回應寫的 provider 名稱與 slug 只比較字母和數字：`Nebius AI Studio` 對 slug `nebius` 會被判為不在清單內（寧可擋下也不放行）。真跑時若遇到，把 slug 改成顯示名稱的寫法即可。
- 有好幾個請求的 Run，tokens 是各請求的總和；若某個請求的 usage 讀不出來，總和會少算，不會報錯。latency 只加每個請求最後一次嘗試的耗時。
