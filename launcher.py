"""桌面启动器：后台线程启动现有 Flask 服务，再用 pywebview 打开桌面窗口。

仅做外部包裹，不修改任何现有 HTML / JS / Flask 代码。
用法（源码）：.venv\\Scripts\\python.exe launcher.py
用法（打包）：dist\\AutoPublisher.exe
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import webview
from werkzeug.serving import make_server


def app_base_dir() -> Path:
    """打包后以 exe 所在目录为工作根目录（不让数据写进临时目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def prepare_runtime() -> None:
    """冻结模式下把 config 的根目录与浏览器目录指向 exe 目录，并补一份默认 config.yaml。"""
    base = app_base_dir()
    if not getattr(sys, "frozen", False):
        return

    import config  # noqa: PLC0415 在导入 app 前必须完成补丁

    config.BASE_DIR = base
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(base / "data" / "ms-playwright")

    yaml_path = base / "config.yaml"
    bundled_yaml = Path(getattr(sys, "_MEIPASS", base)) / "config.yaml"
    if not yaml_path.exists() and bundled_yaml.exists():
        shutil.copyfile(bundled_yaml, yaml_path)


prepare_runtime()

import config  # noqa: E402  冻结模式下 BASE_DIR 已在上方改写
from app import app  # noqa: E402  必须在 prepare_runtime() 之后导入


def wait_until_ready(host: str, port: int, timeout: float = 30.0) -> None:
    """轮询端口，等待 Flask 服务可连接。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Flask 服务未在 {timeout} 秒内就绪：http://{host}:{port}")


def main() -> None:
    server_cfg = config.get_config().server
    host = str(server_cfg.host)
    port = int(server_cfg.port)

    httpd = make_server(host, port, app, threaded=True)
    threading.Thread(target=httpd.serve_forever, name="flask-server", daemon=True).start()
    wait_until_ready(host, port)

    webview.create_window(
        "网商园采集 + 闲鱼发布",
        f"http://{host}:{port}",
        width=1440,
        height=900,
    )
    try:
        webview.start()
    finally:
        httpd.shutdown()


def append_launcher_log(text: str) -> None:
    """打包态没有控制台，运行期异常/退出信息写入 logs/launcher_error.log。"""
    try:
        log_dir = app_base_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "launcher_error.log").open("a", encoding="utf-8") as fh:
            fh.write(text)
    except Exception:
        pass


if __name__ == "__main__":
    started = time.monotonic()
    try:
        main()
    except Exception:
        import traceback
        from datetime import datetime

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        append_launcher_log(f"\n[{stamp}] frozen={getattr(sys, 'frozen', False)}\n{traceback.format_exc()}")
        raise
    finally:
        append_launcher_log(f"窗口结束，进程存活 {time.monotonic() - started:.1f}s\n")
