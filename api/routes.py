"""REST API：界面所有操作都经过这里，不直接触碰 Playwright。"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from core.state import RunType
from services.job_manager import ACTIVE_STATES, JobRejected, JobManager
from services.log_service import LogService
from services.product_store import ProductStore

api_bp = Blueprint("api", __name__, url_prefix="/api")

PUBLISH_STATUSES = ("PENDING", "TESTED", "SUBMITTED", "FAILED")


def _manager() -> JobManager:
    return current_app.extensions["job_manager"]


def _logs() -> LogService:
    return current_app.extensions["logs"]


def _store() -> ProductStore:
    return current_app.extensions["product_store"]


def _job_active() -> bool:
    job = current_app.extensions["registry"].get()
    return bool(job and job.state in ACTIVE_STATES)


@api_bp.get("/status")
def get_status():
    cfg = current_app.extensions["cfg"]
    return jsonify(
        {
            "ok": True,
            "job": current_app.extensions["registry"].snapshot(),
            "config": cfg.public_view(),
            "cursor": _logs().latest_cursor(),
        }
    )


@api_bp.get("/logs")
def get_logs():
    cursor = request.args.get("cursor", default=0, type=int)
    data = _logs().since(cursor)
    return jsonify({"ok": True, **data})


@api_bp.post("/job/start")
def start_job():
    payload = request.get_json(silent=True) or {}
    run_type = payload.get("type")
    if run_type not in RunType._value2member_map_:
        return jsonify({"ok": False, "error": "type 只能是 download 或 publish"}), 400
    try:
        job = _manager().start_job(RunType(run_type))
    except JobRejected as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, "job": job.to_dict()})


@api_bp.post("/job/pause")
def pause_job():
    try:
        state = _manager().pause()
    except JobRejected as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, "state": state.value})


@api_bp.post("/job/resume")
def resume_job():
    try:
        state = _manager().resume()
    except JobRejected as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, "state": state.value})


@api_bp.post("/job/stop")
def stop_job():
    try:
        state = _manager().stop()
    except JobRejected as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, "state": state.value})


@api_bp.post("/job/human-done")
def human_done():
    _manager().human_done()
    return jsonify({"ok": True})


@api_bp.post("/config/test-mode")
def set_test_mode():
    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"ok": False, "error": "enabled 必须是布尔值"}), 400
    cfg = current_app.extensions["cfg"]
    cfg.set_test_mode(enabled)
    _logs().emit(
        "测试模式已切换为：{}（仅影响之后开始处理的商品）".format("开" if enabled else "关"),
        module="config",
        step="test_mode",
        status="INFO",
    )
    return jsonify({"ok": True, "config": cfg.public_view()})


@api_bp.post("/config/shop-batch")
def set_shop_batch_size():
    payload = request.get_json(silent=True) or {}
    try:
        value = int(payload.get("value"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "value 必须是整数"}), 400
    cfg = current_app.extensions["cfg"]
    limit = int(cfg.wangshangyuan.shop_count)
    if not (1 <= value <= limit):
        return jsonify({"ok": False, "error": f"value 取值 1~{limit}"}), 400
    cfg.set_shop_batch_size(value)
    _logs().emit(
        f"下载批次上限已设为每批 {value} 家档口（下次任务生效）",
        module="config",
        step="shop_batch",
        status="INFO",
    )
    return jsonify({"ok": True, "config": cfg.public_view()})


@api_bp.get("/products")
def list_products():
    counts: dict[str, int] = {status: 0 for status in PUBLISH_STATUSES}
    rows: list[dict] = []
    for item in _store().load_all():
        status = str(item.get("publish_status") or "PENDING").upper()
        counts[status] = counts.get(status, 0) + 1
        rows.append(
            {
                "product_id": item.get("product_id", ""),
                "shop_name": item.get("shop_name", ""),
                "title": item.get("title", ""),
                "price": item.get("price"),
                "download_status": item.get("download_status", ""),
                "publish_status": status,
                "submitted": bool(item.get("submitted", False)),
                "publish_price": item.get("publish_price"),
                "publish_at": item.get("publish_at", ""),
                "image_count": item.get("image_count") or len(item.get("images") or []),
                "product_dir": item.get("product_dir", ""),
            }
        )
    return jsonify({"ok": True, "total": len(rows), "counts": counts, "products": rows})


@api_bp.post("/products/<product_id>/publish-status")
def set_product_publish_status(product_id: str):
    payload = request.get_json(silent=True) or {}
    status = str(payload.get("status") or "").upper()
    if status not in PUBLISH_STATUSES:
        return jsonify(
            {"ok": False, "error": "status 只能是 " + "/".join(PUBLISH_STATUSES)}
        ), 400
    if _job_active():
        return jsonify({"ok": False, "error": "任务运行中，请先停止或等待完成"}), 409
    if not _store().set_publish_status(product_id, status):
        return jsonify({"ok": False, "error": "商品不存在"}), 404
    _logs().emit(
        f"手动修改发布状态：{product_id} → {status}",
        module="products",
        step="publish_status",
        status="OK",
        product_id=product_id,
    )
    return jsonify({"ok": True})


@api_bp.post("/products/<product_id>/delete")
def delete_product(product_id: str):
    if _job_active():
        return jsonify({"ok": False, "error": "任务运行中，请先停止或等待完成"}), 409
    ok, detail = _store().delete_product(product_id)
    if not ok:
        code = 404 if "不存在" in detail else 400
        return jsonify({"ok": False, "error": detail}), code
    _logs().emit(
        f"已删除商品文件：{product_id}",
        module="products",
        step="delete",
        status="OK",
        product_id=product_id,
        file_path=detail,
    )
    return jsonify({"ok": True, "deleted": detail})
