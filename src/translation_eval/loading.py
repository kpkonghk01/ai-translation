"""讀 JSON 檔並以 pydantic model 驗證。設定檔與文章檔共用，驗證失敗一律丟出 InputError。"""
import json

from pydantic import BaseModel, ValidationError

from .errors import InputError


def validation_problems(error: ValidationError, prefix: str) -> list[str]:
    """pydantic 的錯誤只列欄位位置和原因，不回顯輸入值。"""
    return [f"{prefix}：{'.'.join(str(p) for p in err['loc']) or '(根)'}：{err['msg']}" for err in error.errors()]


def read_model(path: str, model: type[BaseModel]):
    """錯誤訊息只列檔案、欄位位置和原因，不回顯輸入值。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as e:
        raise InputError(f"{path}：讀不到檔案（{type(e).__name__}）") from e
    except json.JSONDecodeError as e:
        raise InputError(f"{path}：不是合法的 JSON（第 {e.lineno} 行）") from e
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        raise InputError(validation_problems(e, path)) from e
