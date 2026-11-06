"""輸出目錄的結構與讀寫。所有輸出預設寫在 data/ 底下，每個實驗一個目錄。

  <data>/experiments/<實驗 id>/
    runs.jsonl           每行一個 Run 紀錄
    translations/<run>.json   每個 ok 的 Run 一份譯文
    raw/<run>.body       原始回應（只有 response body，沒有 request headers）
"""
import json
import os
from pathlib import Path

from pydantic import ValidationError

from .errors import InputError
from .schemas import ComponentResult, RunKey, RunRecord, Translation


def run_stem(key: RunKey) -> str:
    return f"{key.article_id}.{key.approach}.{key.direction}.{key.input_version}.r{key.run_index}"


class OutputDir:
    def __init__(self, data_dir: str, experiment_id: str):
        self.root = Path(data_dir) / "experiments" / experiment_id

    @property
    def runs_path(self) -> Path:
        return self.root / "runs.jsonl"

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

    @property
    def components_path(self) -> Path:
        return self.root / "components.jsonl"

    @property
    def report_path(self) -> Path:
        return self.root / "report.md"

    def read_translation(self, record: RunRecord) -> Translation:
        if record.translation is None:
            raise InputError(f"{record.article_id} {record.approach} r{record.run_index}：ok 的 Run 沒有譯文檔")
        path = self.root / record.translation
        try:
            return Translation.model_validate_json(path.read_bytes())
        except OSError as e:
            raise InputError(f"{record.article_id} {record.approach} r{record.run_index}：讀不到譯文檔（{type(e).__name__}）") from e
        except ValidationError as e:
            raise InputError(f"{record.article_id} {record.approach} r{record.run_index}：譯文檔不是譯文的結構") from e

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

    def write_report(self, text: str) -> None:
        self._replace(self.report_path, text)

    def _replace(self, path: Path, text: str) -> None:
        """整份重寫：先寫暫存檔再換名，中途中斷也不會留下半份。"""
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def write_run(self, record: RunRecord, raw: bytes | None, translation: dict | None) -> RunRecord:
        """先寫譯文和原始回應，最後才附加 Run 紀錄：有紀錄的 Run 一定有它的檔案。"""
        stem = run_stem(record)
        updates = {}
        if raw is not None:
            updates["raw_response"] = self._write(f"raw/{stem}.body", raw)
        if translation is not None:
            text = json.dumps(translation, ensure_ascii=False, indent=2) + "\n"
            updates["translation"] = self._write(f"translations/{stem}.json", text.encode("utf-8"))
        record = record.model_copy(update=updates)
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.runs_path, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
        return record

    def drop_runs(self, keys: set[RunKey]) -> None:
        """重做 error 的 Run 之前，把舊紀錄換掉：整份重寫成暫存檔再換名，中途中斷也不會留下半份紀錄。"""
        kept = [r for r in self.read_runs() if RunKey.of(r) not in keys]
        self._replace(self.runs_path, "".join(r.model_dump_json() + "\n" for r in kept))

    def _write(self, relative: str, data: bytes) -> str:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return relative
