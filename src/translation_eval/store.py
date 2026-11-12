"""輸出目錄的結構與讀寫。所有輸出預設寫在 data/ 底下，每個實驗一個目錄。

  <data>/experiments/<實驗 id>/
    runs.jsonl           每行一個 Run 紀錄
    translations/<run>.json   每個 ok 的 Run 一份譯文
    raw/<run>.body       原始回應（只有 response body，沒有 request headers）

寫出 Run 的檔案時，所有內容都會先把 key 換成 [REDACTED]：寫出去的地方只有這一處，所以無論 key 是從哪個欄位
（錯誤訊息、模型版本、譯文、原始回應）被帶回來，都不會進入任何輸出檔。
"""
import json
import os
from pathlib import Path

from pydantic import ValidationError

from .errors import InputError
from .schemas import CheckMeta, ComponentResult, CrosscheckCall, GoldCall, JudgeCall, RunKey, RunRecord, Task, Translation

REDACTED = "[REDACTED]"


def run_stem(key: RunKey) -> str:
    return f"{key.article_id}.{key.approach}.{key.direction}.{key.input_variant}.r{key.run_index}"


def judge_call_id(run: RunKey, component_id: str, judge: str, task: str, call_index: int) -> tuple:
    """Judge 請求的身分：續跑靠它判斷哪些請求已經有紀錄。MQM 與 fact 是兩個任務，各有各的請求。"""
    return (*run.model_dump().values(), component_id, judge, task, call_index)


def judge_call_key(call: JudgeCall) -> tuple:
    return judge_call_id(RunKey.of(call), call.component_id, call.judge, call.task, call.call_index)


def gold_call_key(call: GoldCall) -> tuple:
    """校準請求的身分：續跑靠它判斷哪些請求已經有紀錄。"""
    return (call.item_id, call.judge, call.task, call.call_index)


def crosscheck_call_key(call: CrosscheckCall) -> tuple:
    """交叉檢驗請求的身分：一個模型對一篇文章一個請求。"""
    return (call.article_id, call.judge)


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


def _scrub_bytes(data: bytes, secrets: tuple[str, ...]) -> bytes:
    for secret in secrets:
        data = data.replace(secret.encode("utf-8"), REDACTED.encode("utf-8"))
    return data


class OutputDir:
    def __init__(self, data_dir: str, experiment_id: str):
        self.root = Path(data_dir) / "experiments" / experiment_id

    @property
    def runs_path(self) -> Path:
        return self.root / "runs.jsonl"

    @property
    def components_path(self) -> Path:
        return self.root / "components.jsonl"

    @property
    def judge_calls_path(self) -> Path:
        return self.root / "judge-calls.jsonl"

    @property
    def check_meta_path(self) -> Path:
        return self.root / "check-meta.json"

    @property
    def gold_calls_path(self) -> Path:
        return self.root / "gold-calls.jsonl"

    @property
    def crosscheck_calls_path(self) -> Path:
        return self.root / "crosscheck-calls.jsonl"

    @property
    def experiment_misses_path(self) -> Path:
        return self.root / "experiment-judge-misses.md"

    @property
    def crosscheck_path(self) -> Path:
        return self.root / "crosscheck.md"

    @property
    def gold_sample_path(self) -> Path:
        return self.root / "gold-sample.md"

    @property
    def calibration_path(self) -> Path:
        return self.root / "calibration.md"

    @property
    def disagreements_path(self) -> Path:
        return self.root / "calibration-disagreements.md"

    @property
    def summary_path(self) -> Path:
        return self.root / "summary.md"

    @property
    def showcase_path(self) -> Path:
        return self.root / "showcase.html"

    @property
    def decisions_path(self) -> Path:
        return self.root / "review-decisions.json"

    @property
    def review_path(self) -> Path:
        return self.root / "review.md"

    @property
    def report_path(self) -> Path:
        return self.root / "report.md"

    def read_runs(self) -> list[RunRecord]:
        """讀既有的 Run 紀錄。壞掉的一行令指令失敗，不略過：略過就會重送請求，或漏算結果。"""
        if not self.runs_path.exists():
            return []
        return self._read_records(self.runs_path, RunRecord, "Run 紀錄")

    def require_runs(self) -> list[RunRecord]:
        records = self.read_runs()
        if not records:
            raise InputError(f"{self.runs_path}：沒有 Run 紀錄，先執行 translate")
        return records

    def read_judge_calls(self, task: Task | None = Task.MQM) -> list[JudgeCall]:
        """某個任務的 Judge 紀錄（預設是 MQM）；task 是 None 就是全部。不認識的任務名稱（打錯字）會失敗，不是回傳空的。"""
        calls = self._read_all_judge_calls()
        return calls if task is None else [c for c in calls if c.task is Task(task)]

    def _read_all_judge_calls(self) -> list[JudgeCall]:
        return self._read_records(self.judge_calls_path, JudgeCall, "Judge 紀錄")

    def write_judge_call(self, call: JudgeCall, raw: bytes | None, *, secrets: tuple[str, ...] = (), replaces_existing: bool = False) -> JudgeCall:
        """原始回應按 Run 與 Judge 存成 jsonl（每個請求一行，重做的不蓋掉舊的），最後才寫 Judge 紀錄。"""
        raw_path = self._append_raw(f"judge-raw/{run_stem(call)}.{call.judge}.jsonl", raw, secrets,
                                    {"task": call.task, "component": call.component_id, "call": call.call_index})
        return self._write_record(self.judge_calls_path, call.model_copy(update={"raw_response": raw_path}) if raw_path else call, judge_call_key,
                                  lambda: self.read_judge_calls(None), secrets, replaces_existing)

    def read_translation(self, record: RunRecord) -> Translation:
        if record.translation is None:
            raise InputError(f"{record.label}：ok 的 Run 沒有譯文檔")
        try:
            return Translation.model_validate_json((self.root / record.translation).read_bytes())
        except OSError as e:
            raise InputError(f"{record.label}：讀不到譯文檔（{type(e).__name__}）") from e
        except ValidationError as e:
            raise InputError(f"{record.label}：譯文檔不是譯文的結構") from e

    def read_components(self) -> list[ComponentResult]:
        if not self.components_path.exists():
            return []
        with open(self.components_path, encoding="utf-8") as f:
            try:
                return [ComponentResult.model_validate_json(line) for line in f if line.strip()]
            except ValidationError as e:
                raise InputError(f"{self.components_path}：不是合法的 Component 結果（{e.error_count()} 個錯誤）") from e

    def write_components(self, rows: list[ComponentResult]) -> None:
        self._replace(self.components_path, "".join(r.model_dump_json() + "\n" for r in rows))

    def read_check_meta(self) -> CheckMeta | None:
        if not self.check_meta_path.exists():
            return None
        try:
            return CheckMeta.model_validate_json(self.check_meta_path.read_bytes())
        except ValidationError as e:
            raise InputError(f"{self.check_meta_path}：不是合法的 check 紀錄（{e.error_count()} 個錯誤）") from e

    def write_check_meta(self, meta: CheckMeta) -> None:
        self._replace(self.check_meta_path, meta.model_dump_json(indent=2) + "\n")

    def write_report(self, text: str) -> None:
        self._replace(self.report_path, text)

    def read_gold_calls(self) -> list[GoldCall]:
        return self._read_records(self.gold_calls_path, GoldCall, "校準紀錄")

    def write_gold_call(self, call: GoldCall, raw: bytes | None, *, secrets: tuple[str, ...] = (), replaces_existing: bool = False) -> GoldCall:
        """原始回應按 Judge 存成 jsonl（每個請求一行，重做的不蓋掉舊的），最後才寫校準紀錄。"""
        raw_path = self._append_raw(f"gold-raw/{call.judge}.jsonl", raw, secrets, {"item": call.item_id, "task": call.task, "call": call.call_index})
        return self._write_record(self.gold_calls_path, call.model_copy(update={"raw_response": raw_path}) if raw_path else call, gold_call_key,
                                  self.read_gold_calls, secrets, replaces_existing)

    def read_crosscheck_calls(self) -> list[CrosscheckCall]:
        return self._read_records(self.crosscheck_calls_path, CrosscheckCall, "交叉檢驗紀錄")

    def write_crosscheck_call(self, call: CrosscheckCall, raw: bytes | None, *, secrets: tuple[str, ...] = (), replaces_existing: bool = False) -> CrosscheckCall:
        """原始回應按模型存成 jsonl（每個請求一行，重做的不蓋掉舊的），最後才寫紀錄。"""
        raw_path = self._append_raw(f"crosscheck-raw/{call.judge}.jsonl", raw, secrets, {"article": call.article_id})
        return self._write_record(self.crosscheck_calls_path, call.model_copy(update={"raw_response": raw_path}) if raw_path else call, crosscheck_call_key,
                                  self.read_crosscheck_calls, secrets, replaces_existing)

    def write_experiment_misses(self, text: str) -> None:
        """疑似漏看的詳細清單。沒有內容就刪掉舊的：舊的含原文，不能留著讓報告指向一份過期的清單。"""
        if text:
            self._replace(self.experiment_misses_path, text)
        else:
            self.experiment_misses_path.unlink(missing_ok=True)

    def write_crosscheck(self, text: str) -> None:
        self._replace(self.crosscheck_path, text)

    def write_gold_sample(self, text: str) -> None:
        self._replace(self.gold_sample_path, text)

    def write_calibration(self, text: str, disagreements: str) -> None:
        self._replace(self.calibration_path, text)
        self._replace(self.disagreements_path, disagreements)

    def write_summary(self, text: str) -> None:
        self._replace(self.summary_path, text)

    def write_showcase(self, html: str) -> None:
        self._replace(self.showcase_path, html)

    def write_review(self, text: str) -> None:
        self._replace(self.review_path, text)

    def write_run(self, record: RunRecord, raw: bytes | None, translation: dict | None, *,
                  secrets: tuple[str, ...] = (), replaces_existing: bool = False, raw_extension: str = "body") -> RunRecord:
        """先寫譯文和原始回應，最後才寫 Run 紀錄：有紀錄的 Run 一定有它的檔案。

        replaces_existing 用於重做 error 或 empty 的 Run：舊紀錄到新紀錄寫出的這一刻才被換掉。
        所以中途停止（例如 transport 拒絕送出）時，還沒重做的舊紀錄不會不見。"""
        stem = run_stem(record)
        updates = {}
        if raw is not None:
            updates["raw_response"] = self._write(f"raw/{stem}.{raw_extension}", _scrub_bytes(raw, secrets))
        if translation is not None:
            text = _scrub(json.dumps(translation, ensure_ascii=False, indent=2) + "\n", secrets)
            updates["translation"] = self._write(f"translations/{stem}.json", text.encode("utf-8"))
        record = record.model_copy(update=updates)
        line = _scrub(record.model_dump_json(), secrets) + "\n"
        if replaces_existing:
            kept = "".join(r.model_dump_json() + "\n" for r in self.read_runs() if RunKey.of(r) != RunKey.of(record))
            self._replace(self.runs_path, kept + line)
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self.runs_path, "a", encoding="utf-8") as f:
                f.write(line)
        return record

    def copy_runs(self, source: "OutputDir", records: list[RunRecord]) -> None:
        """另一個實驗的這些 Run 紀錄，連同譯文與原始回應，原樣複製過來。先寫檔案，最後才寫紀錄（有紀錄的 Run 一定有它的檔案）；
        缺少檔案就停止，什麼都不寫。"""
        files = [p for r in records for p in (r.translation, r.raw_response) if p]
        missing = [p for p in files if not (source.root / p).is_file()]
        if missing:
            raise InputError([f"缺少檔案：{p}" for p in missing])
        for relative in files:
            self._write(relative, (source.root / relative).read_bytes())
        self._replace(self.runs_path, "".join(r.model_dump_json() + "\n" for r in records))

    def write_sample_note(self, note: dict) -> None:
        """抽樣實驗從哪裡抽、怎麼抽、抽了哪些文章（給人看，程式不讀）。"""
        self._replace(self.root / "sample.json", json.dumps(note, ensure_ascii=False, indent=2) + "\n")

    def _read_records(self, path: Path, model, what: str) -> list:
        """JSONL 的紀錄檔。壞掉的一行令指令失敗，不略過：略過就會重送請求，或漏算結果。"""
        if not path.exists():
            return []
        records = []
        try:
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if not line.strip():
                        continue
                    try:
                        records.append(model.model_validate_json(line))
                    except ValidationError as e:
                        raise InputError(f"{path}：第 {n} 行不是合法的{what}（{e.error_count()} 個錯誤）") from e
        except UnicodeDecodeError as e:   # 檔案不是 UTF-8：只說位置，不回顯內容
            raise InputError(f"{path}：不是 UTF-8 的文字（第 {e.start} 個位元組）") from e
        return records

    def _append_raw(self, relative: str, raw: bytes | None, secrets: tuple[str, ...], fields: dict) -> str | None:
        """原始回應加一行到 jsonl（key 先換成 [REDACTED]）；沒有回應（例如 timeout）就不寫。回傳寫入的相對路徑。"""
        if raw is None:
            return None
        line = json.dumps({**fields, "body": raw.decode("utf-8", errors="replace")}, ensure_ascii=False)
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(_scrub(line, secrets) + "\n")
        return relative

    def _write_record(self, path: Path, record, key, read_all, secrets: tuple[str, ...], replaces_existing: bool):
        """寫一筆紀錄（key 先換成 [REDACTED]）。replaces_existing：整份重寫，換掉同一個請求的舊紀錄；否則附加在檔案後面。"""
        text = _scrub(record.model_dump_json(), secrets) + "\n"
        if replaces_existing:
            kept = "".join(c.model_dump_json() + "\n" for c in read_all() if key(c) != key(record))
            self._replace(path, kept + text)
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
        return record

    def _write(self, relative: str, data: bytes) -> str:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return relative

    def _replace(self, path: Path, text: str) -> None:
        """整份重寫：先寫暫存檔再換名，中途中斷也不會留下半份。"""
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
