# 15: Gold set 內容

**What to build:** 寫出約 100 個 Gold set 項目，放在 `data/`，用來校準 Judge：約 25 個 Softening 變體、約 25 個無錯樣本、約 50 個真實候選譯文的標註（Q14）。這是內容工作：由 Claude 撰寫，Pan 抽查約 30 條。標註真實譯文之前，要先有 Pan 用真 key 跑出的第一批譯文。

**Blocked by:** 14；另需 Pan 先用真 key 跑出第一批譯文（01、05、06 的輸出）

**Status:** ready-for-human（variant 與 clean 已完成，等 Pan 抽查；annotated 要等第一批真跑）

- [x] Softening 變體：每個指向一條 Must-retain fact，涵蓋 (a)–(f)，political 和 general 約各一半，兩個 Direction 都有
- [x] 無錯樣本約 25 個，兩個 Direction 都有
- [ ] 真實候選譯文的標註約 50 個，取自第一批 Run，涵蓋多個 Approach（**還沒做：要先有第一批真跑的譯文**）
- [x] 全部通過 14 的驗證，並列出各類別的數量（目前 56 條）
- [ ] 用 `gold sample` 抽約 30 條給 Pan，並把 Pan 的修正寫回（已產生：`data/experiments/first-batch/gold-sample.md`，30／56 條、種子 1；Pan 的修正還沒有）
- [ ] 繁→英的一半需要懂英文的人抽查；如果沒有人手，在校準結果中註明
- [x] ticket 的留言只寫數量，不貼內容

## Comments

### 2026-11-09 variant 與 clean 完成

- `data/gold-set.json`：56 條，`gold check` 通過。variant 31、clean 25、annotated 0。Direction：trad-to-en 29、trad-to-simplified 27；domain：china-political 28、general 28；兩個 domain 都有兩個 Direction。
- variant 的預期 verdict：Weakened 25、Absent 6（原本 25 個全是 Weakened，量度不到 Absent 的 recall，所以補了 6 個）。淡化類型：a 7、b 6、c 5、d 7、e 4、f 2（f 需要含粗口或惡毒字眼的直接引語事實，被分配的文章裡找得到的不多）。
- clean：17 條指向 ground truth 的 Sensitive sentence（2–4 條事實全標 Present，量度對敏感內容的誤報），8 條是沒有敏感句的普通段落。全部沒有前後文。
- 製作方式：4 個 agent 各負責不重疊的文章（25 variant＋25 clean），第 5 個補 6 個 Absent；再由另一個沒看過作者結論的 agent 獨立審查全部 56 條。審查找出 8 項問題或存疑：7 項已修（2 個 clean 的歸因被拆開、1 個 variant 的類型標錯、2 個 variant 另有未記錄的小偏差、1 個罪名用語、1 條 ground truth 事實有誤），1 項（gv18：原文「應約」沒說是誰要求，譯文的中性寫法保留）沒改。
- **ground truth 因此改了 1 條事實**：`draft-b.json` 裡一條事實寫了原文那一段沒有的歸因（「報道稱」是從整篇文章推斷的），已改成原文有的寫法並把 kind 改為 detail，`all.md` 已重新渲染。這條事實在正式評核裡同樣會被拿去核對，不改的話 Judge 可能把它判成 Absent。ground truth 其餘的事實沒有逐條重審，所以類似的推斷歸因可能還有。
- 用詞彙表（ticket 04）反查：25 個 clean 項目的譯文全部符合詞彙表；variant 的不一致（1 條）是它本來要的淡化。
- 以後 `gold-set.json` 是唯一來源；`data/gold-parts/` 只是撰寫時的分檔與審查筆記，不再維護。
- 注意：`gc20` 所在文章在 ground truth 的備註質疑它是否屬 china-political（Q34）；Pan 如果改了那篇的分組，這一條的 `domain` 要跟著改。

### 2026-11-09 Pan 用另一個模型抽查之後

- **gv01**：ground truth 對那一段漏了一條事實（同一段的另外兩處證詞都有收，唯獨漏了這一句），已補進 ground truth（總數 1059 → 1060 條），並列入 gv01 的 Present 事實。不是 Gold 項目寫錯；Gold 項目本來就只列「確定」的事實，沒列的不計分。
- **類型 a 與 d 的邊界**：抽查建議把「整句被刪走」一律標 d。沒有採用：GLOSSARY 的定義是按「被拿走的是什麼」分（施害者或行為主體 → a；其他細節 → d），不按手法分。已在 GLOSSARY 的 Softening 補上這句。gv26（事實種類 actor）與 gv28 標 a、gv29 與 gv30（detail）標 d，與這條規則一致。類型只影響分歧清單，不影響準確率。

### 2026-11-11 審查狀態

- 56 條的審查來源：我派的獨立 agent 看了全部 56 條；Gemini 3.8 看了 sample 的 30 條；其餘 26 條（包含我修過的 gc08、gc16、gv18、gv23、gv24、gv25）Gemini 因內容審查拒答，由 Pan 粗略檢查過，沒有發現問題。
- **沒有人逐條仔細審過 56 條**；Pan 抽查是粗略的，繁→英的部分也沒有懂英文的人看。校準結果要註明這一點（ticket 的原條件是「Pan 抽查 30 條」「繁→英一半要懂英文的人」）。
- 還沒做：annotated 約 50 條，要等第一批真跑出譯文。

