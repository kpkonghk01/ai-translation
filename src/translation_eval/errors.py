"""輸入不合法：設定、文章或既有的輸出檔驗證失敗。指令在寫出任何東西之前就以非 0 結束。"""


class InputError(Exception):
    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))
