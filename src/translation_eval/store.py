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
from .schemas import CheckMeta, ComponentResult, RunKey, RunRecord, Translation

REDACTED = "[REDACTED]"


def run_stem(key: RunKey) -> str:
    return f"{key.article_id}.{key.approach}.{key.direction}.{key.input_variant}.r{key.run_index}"


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
    def check_meta_path(self) -> Path:
        return self.root / "check-meta.json"

    @property
    def report_path(self) -> Path:
        return self.root / "report.md"

    def read_runs(self) -> list[RunRecord]:
        """讀既有的 Run 紀錄。壞掉的一行令指令失敗，不略過：略過就會重送請求，或漏算結果。"""
        if not self.runs_path.exists():
            return []
        records = []
        with open(self.runs_path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    records.append(RunRecord.model_validate_json(line))
                except ValidationError as e:
                    raise InputError(f"{self.runs_path}：第 {n} 行不是合法的 Run 紀錄（{e.error_count()} 個錯誤）") from e
        return records

    def require_runs(self) -> list[RunRecord]:
        records = self.read_runs()
        if not records:
            raise InputError(f"{self.runs_path}：沒有 Run 紀錄，先執行 translate")
        return records

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

    def write_run(self, record: RunRecord, raw: bytes | None, translation: dict | None, *,
                  secrets: tuple[str, ...] = (), replaces_existing: bool = False, raw_extension: str = "body") -> RunRecord:
        """先寫譯文和原始回應，最後才寫 Run 紀錄：有紀錄的 Run 一定有它的檔案。

        replaces_existing 用於重做 error 的 Run：舊紀錄到新紀錄寫出的這一刻才被換掉。
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
