"""统一错误码：日志、界面提示、人工接管原因都以此为基准。"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    E_LOGIN = "E_LOGIN"                # 登录失败
    E_CAPTCHA = "E_CAPTCHA"            # 验证码/滑块
    E_RISK = "E_RISK"                  # 风控拦截
    E_SELECTOR = "E_SELECTOR"          # 元素找不到/结构变化
    E_DOWNLOAD = "E_DOWNLOAD"          # 下载失败/超时
    E_UNZIP = "E_UNZIP"                # 解压失败
    E_AI = "E_AI"                      # DeepSeek 调用失败
    E_BROWSER = "E_BROWSER"            # 浏览器崩溃/断连
    E_IO = "E_IO"                      # 磁盘写入失败
    E_CONFIG = "E_CONFIG"              # 配置缺失/非法
    E_UNKNOWN = "E_UNKNOWN"


class AppError(Exception):
    """带错误码的业务异常。"""

    def __init__(self, code: ErrorCode, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"


class NeedHumanError(AppError):
    """必须暂停等待人工处理（登录、验证码、风控等）。"""

    def __init__(self, code: ErrorCode, message: str, hint: str = "") -> None:
        super().__init__(code, message, hint)
        self.requires_human = True
