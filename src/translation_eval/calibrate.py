"""calibrate：對 Gold set 執行 MQM 任務和 fact 任務，各評兩次，量度 Judge 的準確度。

用法：
  uv run --locked python -I -m translation_eval calibrate --config <實驗設定> [--judge <id>] [--data-dir data]
      [--dry-run] [--limit <N>] [--redo-errors]

有 --judge 才送請求（要 key，經 tools/run-real.sh 執行）：這個 Judge 對 Gold set 每個項目的 MQM 任務、每個有 facts 的項目的 fact 任務，各兩個
獨立的請求（temperature 0，與正式評核同一份 prompt 和同一種請求格式）。已有新的紀錄的請求略過，所以可以續跑；prompt、Judge 設定或項目內容改了，
舊的紀錄過期，會重送。不寫 --judge 就只重算結果，不送請求。請求之後（或只重算時）寫出 calibration.md（各 Judge 的指標，只有數字）和
calibration-disagreements.md（Judge 對標註、Judge 對 Judge 的分歧，含原文與譯文，只在本機看）。Gold set 驗證不合就停止，什麼也不送、不寫。
Judge 的角色（primary／experiment）不限：候選的 Judge 都要校準，才選得出主 Judge。校準不涉及被評的 Approach，所以沒有同家族的限制。
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from .calibration import evaluate_judge, is_current
from .calibration_report import disagreement_lines, evaluation_lines
from .config import Experiment, PlannedJudge
from .errors import InputError, provider_violation_stop
from .gold import GoldSet, count_lines, item_hash, item_pair, item_sentences, load_gold_for
from .ground_truth import GroundTruth, Sentence, input_hash
from .judging import (fact_request_message, interpret_facts, interpret_output, mqm_message, print_endpoint, require_judges_ready,
                      select_pending, send_judge_request, task_templates)
from .pairs import ComponentPair
from .prompts import PromptTemplate
from .schemas import CALLS_PER_TASK, GoldCall, GoldItem, Task
from .store import OutputDir, gold_call_key
from .terms import LoadedTermList
from .transport import Transport



@dataclass(frozen=True)
class PlannedGoldCall:
    item: GoldItem
    judge: PlannedJudge
    task: str
    call_index: int
    sentences: tuple[Sentence, ...]
    item_hash: str

    @property
    def input_hash(self) -> str | None:
        """fact 任務：送出的事實與 anchor 的 hash（與正式評核一樣）。"""
        return input_hash(list(self.sentences)) if self.task is Task.FACT else None

    @property
    def key(self) -> tuple:
        return (self.item.id, self.judge.id, self.task, self.call_index)


def plan_gold_calls(gold: GoldSet, truth: GroundTruth | None, judge: PlannedJudge) -> list[PlannedGoldCall]:
    plan = []
    for item in gold.items:
        sentences, digest = tuple(item_sentences(item, truth)), item_hash(item, truth)
        for task in (Task.MQM, *((Task.FACT,) if item.facts else ())):
            plan.extend(PlannedGoldCall(item, judge, task, n, sentences if task is Task.FACT else (), digest) for n in range(1, CALLS_PER_TASK + 1))
    return plan


def gold_message(call: PlannedGoldCall, term_list: LoadedTermList) -> str:
    """與正式評核同一種格式：MQM 的前後文是項目自己的 context（沒有就是 None）。"""
    item, pair = call.item, item_pair(call.item)
    if call.task is Task.FACT:
        return fact_request_message(item.direction, pair, list(call.sentences), term_list)
    before = [ComponentPair(f"{item.id}-before", item.component_type, item.context_before.source, item.context_before.translation)] if item.context_before else []
    after = [ComponentPair(f"{item.id}-after", item.component_type, item.context_after.source, item.context_after.translation)] if item.context_after else []
    return mqm_message(item.direction, [*before, pair, *after], len(before), term_list)


def run_gold_call(transport: Transport, call: PlannedGoldCall, template: PromptTemplate, term_list: LoadedTermList, out: OutputDir, *,
                  replaces_existing: bool) -> tuple[GoldCall, bool]:
    """送出一個請求並記錄。第二個回傳值是：回應是不是來自不在允許清單內的 provider。"""
    exchange, api_key = send_judge_request(transport, call.judge, template, gold_message(call, term_list))
    judge = call.judge.config
    result, pair = exchange.interpretation, item_pair(call.item)
    findings, defects, verdicts = [], [], []
    if call.task is Task.FACT:
        outcome, reason, verdicts, invalid = interpret_facts(result, pair, call.sentences)
    else:
        outcome, reason, findings, defects, invalid = interpret_output(result, pair)
    record = GoldCall(
        item_id=call.item.id, judge=judge.id, task=call.task, call_index=call.call_index, outcome=outcome, reason=reason, attempts=exchange.attempts,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens, thought_tokens=result.thought_tokens,
        model_version=result.model_version, served_provider=result.served_provider, prompt_hash=template.hash, config_hash=call.judge.config_hash,
        item_hash=call.item_hash, input_hash=call.input_hash, findings=findings, source_defects=defects, verdicts=verdicts, invalid=invalid,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return out.write_gold_call(record, exchange.raw_body, secrets=(api_key,), replaces_existing=replaces_existing), result.provider_violation


def select_judge(experiment: Experiment, judge_id: str) -> PlannedJudge:
    judge = next((j for j in experiment.judges if j.id == judge_id), None)
    if judge is None:
        raise InputError(f"--judge 不在實驗設定的 judging 裡：{judge_id}")
    return judge


def handle(args, transport: Transport) -> int:
    gold, experiment, truth = load_gold_for(args.config)
    out = OutputDir(args.data_dir, experiment.config.id)
    judge = select_judge(experiment, args.judge) if args.judge else None
    templates, existing = task_templates(), {gold_call_key(c): c for c in out.read_gold_calls()}
    template_hashes = {task: template.hash for task, template in templates.items()}

    pending, skipped = select_pending(plan_gold_calls(gold, truth, judge) if judge else [], existing, redo_errors=args.redo_errors, limit=args.limit,
                                      is_stale=lambda recorded, call: not is_current(recorded, call.item_hash, call.judge, template_hashes))
    if args.dry_run:
        return dry_run(pending, skipped, judge, templates)
    require_judges_ready([judge] if pending else [])
    outcomes = Counter()
    for call in pending:
        record, violation = run_gold_call(transport, call, templates[call.task], experiment.term_list, out, replaces_existing=call.key in existing)
        outcomes[record.outcome] += 1
        if violation:   # 資料已經送到不在允許清單內的 provider：不能再送下一個請求
            raise provider_violation_stop(f"Judge {judge.id}", sum(outcomes.values()))
    if judge:
        print(f"完成 {len(pending)} 個請求（已有新的紀錄而略過：{skipped}）：" + ("、".join(f"{o} {n}" for o, n in sorted(outcomes.items())) or "沒有要執行的請求"))
    write_outputs(experiment, gold, truth, out)
    return 0


def write_outputs(experiment: Experiment, gold: GoldSet, truth: GroundTruth | None, out: OutputDir) -> None:
    calls = out.read_gold_calls()
    evaluations = [evaluate_judge(gold, truth, calls, judge) for judge in experiment.judges]
    recorded = [e for e in evaluations if e.has_records]
    lines = [f"# Judge 校準：{experiment.config.id}", "", *count_lines(gold), "",
             "合格線（recall、precision）尚未訂：要等第一批數字出來，不寫死在程式裡。", ""]
    for evaluation in recorded:
        lines += evaluation_lines(gold, evaluation)
    if not recorded:
        lines.append("還沒有校準紀錄：用 --judge 對候選的 Judge 各跑一次。")
    out.write_calibration("\n".join(lines).rstrip("\n") + "\n", "\n".join(disagreement_lines(gold, recorded)).rstrip("\n") + "\n")
    print(f"已寫入 {out.calibration_path} 和 {out.disagreements_path}（{len(recorded)} 個 Judge 有紀錄）")


def dry_run(pending: list[PlannedGoldCall], skipped: int, judge: PlannedJudge | None, templates: dict[str, PromptTemplate]) -> int:
    print("dry-run：不送出任何請求，也不寫出任何檔案")
    print(f"請求數：{len(pending)}（已有新的紀錄而略過：{skipped}）")
    if judge is not None and pending:
        # 估計輸入字數：prompt、項目的原文、譯文、前後文和事實（Term List 不算）
        chars = sum(len(templates[c.task].text) + len(c.item.source) + len(c.item.translation)
                    + sum(len(x.source) + len(x.translation) for x in (c.item.context_before, c.item.context_after) if x)
                    + sum(len(f.text) for s in c.sentences for f in s.facts) for c in pending)
        print(f"Judge {judge.id}：估計輸入字數約 {chars}")
        print_endpoint(judge)
    return 0
