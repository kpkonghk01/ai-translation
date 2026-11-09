"""輸入不合法：設定、文章或既有的輸出檔驗證失敗。指令在寫出任何東西之前就以非 0 結束。"""


class InputError(Exception):
    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


class StoppedAfterSending(InputError):
    """請求已經送出之後才停止（例如回應來自不在允許清單內的 provider，或 API 回了不認識的格式）：已送出的請求和已寫的紀錄保留。
    cli 的開頭因此不能說「沒有送出請求」。"""


def provider_violation_stop(subject: str, recorded: int) -> StoppedAfterSending:
    """回應來自不在允許清單內的 provider：資料已經送過去了，不能再送下一個請求（正式評核、翻譯、校準、交叉檢驗共用）。subject 是「Judge j1」這類。"""
    return StoppedAfterSending(f"{subject} 的回應來自不在允許清單內的 provider：已停止，沒有再送出請求（已記錄 {recorded} 個）")
