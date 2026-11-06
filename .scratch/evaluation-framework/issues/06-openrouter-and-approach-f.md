# 06: OpenRouter、provider 清單與方案 F

**What to build:** Pan 先用 `providers` 指令看到 OpenRouter 上某個模型的 provider 清單，確認後寫進設定，之後才可以跑方案 F（Hy-MT2）。每個 OpenRouter 請求都有資料保護欄位，缺任何一項程式就拒絕送出。翻譯專用模型可以改用逐 Component 的請求模式，長文章可以切塊。這個 adapter 也是日後經 OpenRouter 使用 Qwen 或 Claude 的基礎。詳見 spec 第 3、4 和第 12 節。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] `providers <model>` 經 transport 列出該模型的 endpoint 和 provider，以及 API 提供的地區和資料政策資訊；指令本身不寫入設定
- [ ] Approach 和 Judge 設定可以記錄允許清單和確認日期；允許清單是空的或還沒確認時，adapter 拒絕組出請求
- [ ] 每個 OpenRouter 請求都帶 `provider.zdr: true`、`data_collection: "deny"`、`only`、`allow_fallbacks: false`；測試檢查每一個送出的請求
- [ ] 記錄實際服務的 provider；不在 `only` 內時記為 `error`
- [ ] `content_filter` 和 moderation 錯誤 → `block`
- [ ] 請求模式 `per-component`：產生的 Run 紀錄格式與 `article-json` 相同
- [ ] 切塊：Approach 設定了輸入上限時，按 Component 順序切塊；Run 取最差一塊的結果，並記錄是哪一塊
- [ ] key 只從 `OPENROUTER_API_KEY` 讀取
- [ ] Pan 執行 `providers`，確認允許清單，再用真 key 對 1 篇文章跑方案 F，並決定方案 F 用哪個請求模式
