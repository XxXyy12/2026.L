"""Flask 入口：创建应用、初始化日志/任务管理器，并提供统一界面。"""

from __future__ import annotations

import os

from flask import Flask, render_template

from api.routes import api_bp
from config import Config, get_config
from core.state import JobRegistry
from services.job_manager import JobManager
from services.log_service import LogService
from services.product_store import ProductStore


def create_app() -> Flask:
    cfg: Config = get_config()
    logs = LogService(cfg)
    registry = JobRegistry()
    manager = JobManager(cfg, logs, registry)

    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "local-only-dev")
    app.json.ensure_ascii = False
    app.extensions["cfg"] = cfg
    app.extensions["logs"] = logs
    app.extensions["registry"] = registry
    app.extensions["job_manager"] = manager
    app.extensions["product_store"] = ProductStore(cfg)
    app.register_blueprint(api_bp)

    @app.get("/")
    def index():
        return render_template("index.html")

    creds = cfg.public_view()["credentials"]
    logs.emit(
        "服务启动：{mode}，浏览器={headless}，下载目录={root}，凭据(网商园/闲鱼/DeepSeek)={c1}/{c2}/{c3}".format(
            mode="测试模式" if cfg.test_mode else "真实模式",
            headless="headless" if cfg.mode.headless else "可见",
            root=cfg.download_root,
            c1="已配置" if creds["wangshangyuan"] else "未配置",
            c2="已配置" if creds["xianyu"] else "未配置",
            c3="已配置" if creds["deepseek"] else "未配置",
        ),
        module="app",
        step="startup",
        status="INFO",
    )
    if cfg.mode.headless:
        logs.emit(
            "警告：当前为 headless 模式，需求要求可见浏览器，请改为 headless=false",
            module="app",
            step="startup",
            status="WARNING",
            level="WARNING",
        )
    return app


app = create_app()


if __name__ == "__main__":
    server = app.extensions["cfg"].server
    app.run(
        host=str(server.host),
        port=int(server.port),
        debug=bool(server.debug),
        threaded=True,
        use_reloader=False,
    )
