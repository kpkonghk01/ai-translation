# 16: Ground truth 交叉檢驗

**What to build:** Pan 執行 `crosscheck`，讓 Qwen（經 OpenRouter，只用已確認的 provider）在沒看過 ground truth 的情況下，盲選每篇最敏感的句子並盲抽 Must-retain fact，然後產出與 ground truth 的分歧清單，由 Pan 決定要不要改 ground truth。中國負面新聞組同時用 Gemini 跑，以找出 Qwen 的盲點（Q20、Q31 比較 3）。詳見 spec 第 12 節。

**Blocked by:** 06, 10

**Status:** ready-for-agent

- [x] 模型只收到文章原文；輸出最敏感的句子（必須逐字取自原文，否則丟棄並計數），以及每句的 Must-retain fact
- [x] 分歧清單寫在 `data/`，包括：只有模型選了的句子、只有 ground truth 有的句子；兩邊都有的句子，把事實並排列出，數字和引語的差異由程式標出
- [x] 中國負面新聞組同時用 Gemini 跑，另外列出「Gemini 抽到、Qwen 沒抽到」的事實，並標明只作參考
- [x] 測試確認 `crosscheck` 不會寫入 ground truth 檔
- [x] OpenRouter 請求沿用 06 的允許清單和資料保護欄位
- [ ] Pan 用真 key 執行並審閱分歧清單（要 Pan 自己做，我沒有跑）

## Comments

### 2026-11-09 實作完成

除了最後一項（Pan 用真 key 執行並審閱分歧清單，要 Pan 自己做）以外，全部勾選項完成，沒有用真模型跑過。規格沒寫、實作時做了選擇的地方：

- **設定**：`crosscheck.judge`（盲抽的模型，Qwen）與 `crosscheck.reference_judge`（對照，Gemini）都指向 `judging.judges` 裡的 Judge 設定，所以 OpenRouter 的允許清單、`zdr`／`data_collection`／`allow_fallbacks` 和 provider 違規時停止，都沿用正式評核的程式（`send_judge_request`），不另寫一份。兩個 Judge 設定本來就是 Judge 的欄位（temperature 固定 0、只用 `article-json`）。
- **Judge prompt 是我寫的**（`prompt_templates/crosscheck.txt`，英文）：讓模型自己決定最敏感的句子（最多 10 句，最敏感的在前）、逐字取句、每句抽事實並標 kind。**它的說明沒有借用 ground truth 的任何寫法或 item**，但我是照 GLOSSARY 的 Must-retain fact 的種類寫的：請 Pan 看過，因為它決定盲抽到的是什麼。
- **每篇每個模型一個請求**（不跑兩次）：交叉檢驗是找分歧，不是判定；兩次的一致性不是 spec 對它的要求。
- **模型看到什麼**：標題和各 Component 的原文（不翻譯的 `code` 等不給）；沒有 Direction、domain、ground truth、別的模型的結果。測試確認請求裡沒有 ground truth 才有的字串。
- **「兩邊都有」的配對**：同一個 Component、兩段文字（忽略空白）的位置有重疊。ground truth 的 anchor 可能只是句子的一段，模型選的是整句，所以不要求相等。一個 ground truth 句子可以對上好幾個模型選的句子，反之亦然。
- **數字與引語的差異**只比阿拉伯數字（去掉千分位）和引號（「」『』“”）內的文字；**中文數字（三名、十年）不比**，要 Pan 自己看並排的事實。
- **「Gemini 抽到、Qwen 沒抽到」的判法是粗略的啟發式**（事實是自由文字，沒辦法精確比）：整句 Qwen 沒選的，列那一句的全部事實；兩邊都選了的句子，列出 Qwen 沒有「同一種 kind、而且數字與引語都涵蓋」的 Gemini 事實。只作參考，清單裡也這樣寫。
- **沒有 ground truth 的文章不送**（實驗內有、ground truth 沒有）；有 ground truth 但沒有敏感句的對照文章會送：模型在那裡選的句子全部是「只有模型選了」，正好用來看對照文章是不是真的無害。
- **舊結果過期**：文章原文、prompt 或 Judge 設定改了，舊的盲抽不用於分歧清單（顯示「還沒有結果」），下次 `crosscheck` 會重送。
- 報告（`report`）沒有放交叉檢驗的結果：它不是評核的一部分，結果在 `crosscheck.md`。
- 共用的小改動：`RequestRecord` 抽出 `JudgeRecord` 與 `CrosscheckCall` 共有的欄位；`textrules` 加 `digit_strings`、`quoted_phrases`。

