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
- **數字與引語的差異**比阿拉伯數字（去掉千分位）、接量詞的中文數字（三名、十年；沒有量詞的統一、唯一不算），和引號（「」『』“”）最外層裡的文字（review 之前中文數字不比、巢狀引號會被截斷）。
- **「Gemini 抽到、Qwen 沒抽到」的判法是粗略的啟發式**（事實是自由文字，沒辦法精確比）：整句 Qwen 沒選的，列那一句的全部事實；兩邊都選了的句子，列出 Qwen 沒有「同一種 kind、而且數字與引語都涵蓋」的 Gemini 事實。只作參考，清單裡也這樣寫。
- **沒有 ground truth 的文章不送**（實驗內有、ground truth 沒有）；有 ground truth 但沒有敏感句的對照文章會送：模型在那裡選的句子全部是「只有模型選了」，正好用來看對照文章是不是真的無害。
- **舊結果過期**：文章原文、prompt 或 Judge 設定改了，舊的盲抽不用於分歧清單（顯示「還沒有結果」），下次 `crosscheck` 會重送。
- 報告（`report`）沒有放交叉檢驗的結果：它不是評核的一部分，結果在 `crosscheck.md`。
- 共用的小改動：`RequestRecord` 抽出 `JudgeRecord` 與 `CrosscheckCall` 共有的欄位；`textrules` 加 `digit_strings`、`quoted_phrases`。

### 2026-11-09 review 之後的修正

- **被擋或失敗本身就是結果，不能蓋掉對照模型**（最重要的一項）：盲抽的 Qwen 在政治文章被 block 時，原本整篇只寫一行「請求結果是 block」，Gemini 的結果也消失；對照的 Gemini 沒有結果則完全沒有提示，看起來和「Gemini 抽到的都被涵蓋了」一樣。現在每個請求的結果都寫出來（block 與「還沒有跑」分開計）；Qwen 被擋時 Gemini 的事實全部列出；Gemini 沒有結果也明講。
- **china-political 整組只作參考**（spec 第 12 節、consensus 第 37 行：Qwen 是被測方，單看它分不出「ground truth 寫多了」和「Qwen 漏看」）：原本只有 Gemini 那一節標了，Qwen 對 ground truth 的清單沒有。現在整篇標明，數量按 domain 分開列。
- **丟掉的句子與事實分開計、兩個模型各列各的**（原本只列盲抽的模型、而且把壞事實和被丟的句子混在一起算）。
- **配對更準**：一個模型句子蓋住幾個 ground truth 的 anchor（或反過來）連成一組，事實與差異按整組算（原本每個 anchor 各拿整句的事實比，會把別的 anchor 的數字當成差異）；同一個詞在 Component 內出現兩次，任何一次重疊都算（原本只看第一次）；同一句選兩次只留一個。
- **逐字驗證不可以跨欄位**：quote 的出處、gallery 的多個說明等欄位用換行接在一起，忽略空白的比對原本會接受跨過兩個欄位的「句子」。
- **差異標示**：加了接量詞的中文數字（三名對兩名），引號改成會處理巢狀（原本「他說『不』然後走了」會被截成「他說『不」）。
- **「Gemini 抽到、Qwen 沒抽到」的判法**：原本只看 kind 加數字與引語，沒有數字或引語的事實只要同一種 kind 就算涵蓋，會漏報；現在還要字元二連字重疊到一半以上，而且不看 kind（同一件事兩個模型可能標成不同的種類）。
- **清單開頭**寫明每篇最多選 10 句、模型選滿時註明、實驗內沒有 ground truth 的文章（不送給模型的）列出 id。
- **結構**：`CrosscheckFact` 改用 `GtFact`（不重抄 schema）；`judging.py` 的 `is_verbatim`、`raw_text` 改成公開；正式評核、校準、交叉檢驗共用 `select_pending`、`require_judges_ready`、`print_endpoint`；store 的三組紀錄讀寫共用同一套；dry-run 補印併發。
- 沒有照 review 改的：**`crosscheck.judge` 沒有限制 provider 一定是 OpenRouter**（ticket 寫「Qwen 經 OpenRouter」，但 Judge 的 provider 本來就是可以替換的設定；Pan 若用阿里雲的 Qwen，資料會送到阿里雲的 International endpoint，受方案 B 的同一組限制）；**prompt 說明了敏感句的種類與 kind（照 GLOSSARY）**，這降低了「盲」的獨立性，請 Pan 看過；**模型選標題會被當成無效**（標題只是上下文，ground truth 也不能 anchor 在標題）。

