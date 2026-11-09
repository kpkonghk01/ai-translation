"""校準測試共用：很小的虛構 Gold set、Judge 的假回應，和 CalibrationCase。只用虛構資料。

Gold set 有 6 條，指向 GROUND_TRUTH 的 9000001（001 有兩條事實，002 有一條）：
  g01 annotated 繁→英 general：預期一個 Accuracy／major（預算超支 → over budget）
  g02 variant   繁→英 general：001 的變體，預期「工程延誤半年」Weakened (c)、「預算超支」Absent (d)
  g03 clean     繁→英 general：001 的原譯，「預算超支」預期 Present
  g04 variant   繁→簡 china-political：002 的變體，預期「不滿的是市民陳虛構本人」Weakened (a)
  g05 clean     繁→簡 china-political
  g06 annotated 繁→簡 general：有前文；預期一個 Fluency／minor（目標片段「延误半年」）
"""
import contextlib
import copy
import io
import json
import os

from translation_eval import cli
from fake_transport import FunctionTransport
from fixtures import JUDGE
from gate_helpers import ReviewCase
from judge_helpers import fact_output, finding, user_payload

GOOD_EN = "The renovation is six months late and over budget."
SOURCE_001 = "翻新工程延誤半年，預算超支。"
SOURCE_002 = "市民陳虛構表示，工程拖延令他十分不滿。"
GOOD_ZH = "翻新工程延误半年，预算超支。"
FACT_DELAY, FACT_BUDGET, FACT_WHO = "工程延誤半年", "預算超支", "不滿的是市民陳虛構本人"

J2 = JUDGE | {"id": "j2", "role": "experiment", "family": "qwen", "model": "gemini-3.1-other"}


def gold_item(item_id, category, direction, domain, source, translation, **extra) -> dict:
    return {"id": item_id, "category": category, "direction": direction, "domain": domain, "source": source, "translation": translation, **extra}


GOLD = {"items": [
    gold_item("g01", "annotated", "trad-to-en", "general", SOURCE_001, GOOD_EN,
              findings=[{"dimension": "Accuracy", "severity": "major", "source_span": "預算超支", "target_span": "over budget"}]),
    gold_item("g02", "variant", "trad-to-en", "general", SOURCE_001, "The renovation ran a little behind schedule.",
              ground_truth={"article_id": "9000001", "component_id": "9000001-001"},
              facts=[{"fact": FACT_DELAY, "verdict": "Weakened", "weakening_type": "c"}, {"fact": FACT_BUDGET, "verdict": "Absent", "weakening_type": "d"}]),
    gold_item("g03", "clean", "trad-to-en", "general", SOURCE_001, GOOD_EN,
              ground_truth={"article_id": "9000001", "component_id": "9000001-001"}, facts=[{"fact": FACT_BUDGET, "verdict": "Present"}]),
    gold_item("g04", "variant", "trad-to-simplified", "china-political", SOURCE_002, "市民陈虚构表示，工程有点延迟。",
              ground_truth={"article_id": "9000001", "component_id": "9000001-002"},
              facts=[{"fact": FACT_WHO, "verdict": "Weakened", "weakening_type": "a"}]),
    gold_item("g05", "clean", "trad-to-simplified", "china-political", SOURCE_002, "市民陈虚构表示，工程拖延令他十分不满。"),
    gold_item("g06", "annotated", "trad-to-simplified", "general", SOURCE_001, GOOD_ZH,
              context_before={"source": "虛構的前文。", "translation": "虚构的前文。"},
              findings=[{"dimension": "Fluency", "severity": "minor", "target_span": "延误半年"}]),
]}


def mqm(items: dict) -> dict:
    """{(項目 id, 第幾次): Finding 的清單}（也可以是 dict 輸出）→ judge_transport 用的 outputs。"""
    return {key: value if isinstance(value, dict) else {"findings": value, "source_defects": []} for key, value in items.items()}


def verdict_changes(**by_item) -> dict:
    return by_item


def j1_mqm() -> dict:
    """g01 兩次都報（一致，Severity 相同）；g02 只有第 1 次報 Fidelity-of-force（uncertain，變體沒有預期的 Finding）；g03 第 1 次有一個片段不是逐字的
    （無效）；g05 只有第 1 次報 Fluency（clean 的誤報，uncertain）；g06 兩次都報 Fluency（第 2 次是 major）和一個標註沒有的 Accuracy。"""
    accuracy = finding(severity="major", source_span="預算超支", target_span="over budget")
    fof = finding(dimension="Fidelity-of-force", subtype="softening (c)", severity="major", source_span="延誤半年", target_span="a little behind")
    fluency = lambda severity: finding(dimension="Fluency", subtype="awkward", severity=severity, source_span="", target_span="延误半年")   # noqa: E731
    extra = finding(dimension="Accuracy", subtype="mistranslation", severity="minor", source_span="預算超支", target_span="预算超支")
    return mqm({
        ("g01", 1): [accuracy], ("g01", 2): [accuracy],
        ("g02", 1): [fof],
        ("g03", 1): [finding(source_span="不存在的片段")],
        ("g05", 1): [finding(dimension="Fluency", subtype="awkward", severity="minor", source_span="", target_span="市民陈虚构")],
        ("g06", 1): [fluency("minor"), extra], ("g06", 2): [fluency("major"), extra],
    })


def weak(fact_text, verdict, type_, quote="") -> dict:
    return {fact_text: {"verdict": verdict, "type": type_, "quote": quote}}


def j1_facts() -> dict:
    """g02 兩次都對（Weakened (c)、Absent (d)）；g03 預設 Present；g04 第 1 次 Weakened、第 2 次 Present（uncertain）。"""
    g02 = lambda p: fact_output(p, {**weak(FACT_DELAY, "Weakened", "c", "a little behind"), **weak(FACT_BUDGET, "Absent", "d")})   # noqa: E731
    g04_first = lambda p: fact_output(p, weak(FACT_WHO, "Weakened", "a", "工程有点延迟"))   # noqa: E731
    return {("g02", 1): g02, ("g02", 2): g02, ("g04", 1): g04_first}


def routed(by_model: dict) -> FunctionTransport:
    """按請求網址裡的模型名分派給不同的假 Judge：{模型名: (mqm outputs, fact outputs)}。MQM 請求的 Component id 是 Gold set 項目的 id。"""
    from judge_helpers import fact_transport, judge_transport
    sides = {model: (judge_transport(m), fact_transport(f)) for model, (m, f) in by_model.items()}

    def reply(request):
        model = request.url.split("/models/")[1].split(":")[0]
        mqm_side, fact_side = sides[model]
        return (fact_side if "sentences" in user_payload(request) else mqm_side).send(request)

    return FunctionTransport(reply)


def j1_transport() -> FunctionTransport:
    return routed({"gemini-3.1-pro": (j1_mqm(), j1_facts())})


class CalibrationCase(ReviewCase):
    gold = GOLD

    def setUp(self):
        super().setUp()
        self.gold_path = os.path.join(self.tmp, "gold.json")
        self.write_gold(self.gold)
        self.use_judging(judges=(JUDGE, J2))
        self.extra_experiment["calibration"] = {"gold_set": self.gold_path}
        self.write_json(self.config_path, self.experiment())

    def write_gold(self, gold: dict) -> None:
        self.write_json(self.gold_path, gold)

    def edited_gold(self, mutate) -> None:
        gold = copy.deepcopy(self.gold)
        mutate(gold["items"])
        self.write_gold(gold)

    def run_gold(self, sub: str, *extra: str):
        """gold 的子命令的 --config 要放在子命令之後：回傳 (結束碼, stdout, stderr)。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["gold", sub, "--config", self.config_path, "--data-dir", self.data_dir, *extra])
        return code, out.getvalue(), err.getvalue()

    def calibrate(self, *args, transport=None):
        return self.run_cli("calibrate", *args, transport=transport)

    def calibrate_j1(self, *args):
        code, out, err = self.calibrate("--judge", "j1", *args, transport=j1_transport())
        self.assertEqual(code, 0, err)
        return out

    def gold_calls(self):
        text = self.output_text("gold-calls.jsonl")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def forget_gold_calls(self):
        os.remove(os.path.join(self.out_dir, "gold-calls.jsonl"))

    @staticmethod
    def group(text: str, heading: str, level: str = "###") -> str:
        """calibration.md 的一個小節（到下一個同級或更高的標題為止）。"""
        return text.split(f"{level} {heading}\n", 1)[1].split("\n#", 1)[0]
