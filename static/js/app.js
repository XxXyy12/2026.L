/* 统一控制台：状态轮询 + 增量日志 + 任务控制。 */

const STATE_TEXT = {
  IDLE: "空闲",
  RUNNING: "运行中",
  PAUSING: "正在暂停",
  PAUSED: "已暂停",
  WAITING_HUMAN: "等待人工处理",
  STOPPING: "正在停止",
  STOPPED: "已停止",
  COMPLETED: "已完成",
  FAILED: "失败",
};

const ACTIVE_STATES = new Set(["RUNNING", "PAUSING", "PAUSED", "WAITING_HUMAN", "STOPPING"]);
const MAX_LOG_ROWS = 800;
const PUBLISH_TEXT = {
  PENDING: "待发布",
  TESTED: "已截图",
  SUBMITTED: "已上架",
  FAILED: "失败",
};
const DOWNLOAD_TEXT = {
  SUCCESS: "已下载",
  PARTIAL: "部分",
  FAILED: "失败",
};

const $ = (id) => document.getElementById(id);

const els = {
  badgeState: $("badge-state"),
  badgeMode: $("badge-mode"),
  badgeSim: $("badge-sim"),
  btnDownload: $("btn-download"),
  btnPublish: $("btn-publish"),
  btnPause: $("btn-pause"),
  btnResume: $("btn-resume"),
  btnStop: $("btn-stop"),
  btnHumanDone: $("btn-human-done"),
  chkTestMode: $("chk-test-mode"),
  humanBanner: $("human-banner"),
  humanReason: $("human-reason"),
  humanHint: $("human-hint"),
  vTask: $("v-task"),
  vStep: $("v-step"),
  vProduct: $("v-product"),
  vShop: $("v-shop"),
  vRunId: $("v-run-id"),
  vTotal: $("v-total"),
  vDone: $("v-done"),
  vSuccess: $("v-success"),
  vFailed: $("v-failed"),
  vSkipped: $("v-skipped"),
  vStarted: $("v-started"),
  vFinished: $("v-finished"),
  vMessage: $("v-message"),
  progressBar: $("progress-bar"),
  logList: $("log-list"),
  logEmpty: $("log-empty"),
  chkAutoscroll: $("chk-autoscroll"),
  selLevel: $("sel-level"),
  btnClearLog: $("btn-clear-log"),
  btnRefreshProducts: $("btn-refresh-products"),
  productRows: $("product-rows"),
  productSummary: $("v-product-summary"),
};

let cursor = 0;
let config = null;
let statusBusy = false;
let logsBusy = false;
let productsBusy = false;
let currentState = "IDLE";

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = {};
  try {
    data = await response.json();
  } catch (err) {
    data = {};
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
  }
  return data;
}

function showError(message) {
  els.vMessage.textContent = message;
  els.vMessage.style.color = "var(--err)";
}

function setBadgeState(state) {
  els.badgeState.textContent = STATE_TEXT[state] || state;
  els.badgeState.className = "badge";
  if (state === "RUNNING") els.badgeState.classList.add("state-running");
  else if (state === "PAUSING" || state === "PAUSED" || state === "STOPPING") els.badgeState.classList.add("state-paused");
  else if (state === "WAITING_HUMAN") els.badgeState.classList.add("state-human");
  else if (state === "COMPLETED") els.badgeState.classList.add("state-done");
  else if (state === "FAILED") els.badgeState.classList.add("state-failed");
}

function applyConfig(cfg) {
  if (!cfg) return;
  config = cfg;
  els.chkTestMode.checked = Boolean(cfg.test_mode);
  els.badgeMode.textContent = cfg.test_mode ? "测试模式（截图代替发布）" : "真实模式（会点击发布）";
  els.badgeSim.textContent = cfg.simulated ? "模拟联调（未操作浏览器）" : "真实浏览器";
  els.badgeSim.style.display = cfg.simulated ? "" : "none";
}

function applyButtons(state) {
  const active = ACTIVE_STATES.has(state || "IDLE");
  const creds = (config && config.credentials) || {};
  els.btnDownload.disabled = active || !creds.wangshangyuan;
  els.btnPublish.disabled = active || !creds.xianyu;
  els.btnPause.disabled = state !== "RUNNING";
  els.btnResume.disabled = !(state === "PAUSED" || state === "PAUSING");
  els.btnStop.disabled = !active || state === "STOPPING";

  const waiting = state === "WAITING_HUMAN";
  els.humanBanner.hidden = !waiting;
}

function applyJob(job) {
  currentState = job ? job.state : "IDLE";
  if (!job) {
    setBadgeState("IDLE");
    els.vTask.textContent = "—";
    els.vStep.textContent = "—";
    els.vProduct.textContent = "—";
    els.vShop.textContent = "—";
    els.vRunId.textContent = "尚未开始";
    els.progressBar.style.width = "0%";
    els.vMessage.textContent = "";
    applyButtons("IDLE");
    return;
  }

  setBadgeState(job.state);
  els.vTask.textContent = job.run_type === "download" ? "下载图片" : "发布服装";
  els.vStep.textContent = job.current_step || "—";
  els.vProduct.textContent = job.current_product || "—";
  els.vShop.textContent = job.current_shop || "—";
  els.vRunId.textContent = `run ${job.run_id}`;
  els.vTotal.textContent = job.total || 0;
  els.vDone.textContent = job.done || 0;
  els.vSuccess.textContent = job.success || 0;
  els.vFailed.textContent = job.failed || 0;
  els.vSkipped.textContent = job.skipped || 0;
  els.vStarted.textContent = job.started_at || "—";
  els.vFinished.textContent = job.finished_at || "—";
  els.vMessage.textContent = job.message || "";

  const percent = job.total > 0 ? Math.min(100, Math.round((job.done / job.total) * 100)) : 0;
  els.progressBar.style.width = `${percent}%`;

  if (job.state === "WAITING_HUMAN") {
    els.humanReason.textContent = job.human_reason || "等待人工处理";
    els.humanHint.textContent = job.human_hint || "";
  }
  applyButtons(job.state);
}

function chip(label, cls) {
  const span = document.createElement("span");
  span.className = `chip ${cls}`;
  span.textContent = label;
  return span;
}

function renderProducts(data) {
  const total = data.total || 0;
  const counts = data.counts || {};
  els.productSummary.textContent =
    `共 ${total} 件 · 待发布 ${counts.PENDING || 0} · 已截图 ${counts.TESTED || 0}` +
    ` · 已上架 ${counts.SUBMITTED || 0}` +
    (counts.FAILED ? ` · 失败 ${counts.FAILED}` : "");

  els.productRows.innerHTML = "";
  if (!total) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "muted";
    cell.textContent = "暂无商品，请先执行“开始下载图片”。";
    row.append(cell);
    els.productRows.append(row);
    return;
  }

  const active = ACTIVE_STATES.has(currentState);
  const fragment = document.createDocumentFragment();

  data.products.forEach((item) => {
    const row = document.createElement("tr");

    const titleCell = document.createElement("td");
    const title = document.createElement("div");
    title.className = "cell-title";
    title.textContent = item.title || "(无标题)";
    title.title = item.title || "";
    const meta = document.createElement("div");
    meta.className = "cell-meta";
    meta.textContent = `${item.product_id} · ${item.image_count} 张图`;
    titleCell.append(title, meta);

    const shopCell = document.createElement("td");
    shopCell.textContent = item.shop_name || "—";

    const priceCell = document.createElement("td");
    priceCell.className = "num";
    const original =
      item.price !== null && item.price !== undefined ? `¥${item.price}` : "—";
    priceCell.textContent = item.publish_price
      ? `${original} → ¥${item.publish_price}`
      : original;

    const downloadCell = document.createElement("td");
    downloadCell.append(
      chip(
        DOWNLOAD_TEXT[item.download_status] || item.download_status || "—",
        `chip-dl-${String(item.download_status || "").toLowerCase()}`
      )
    );

    const publishCell = document.createElement("td");
    publishCell.append(
      chip(
        PUBLISH_TEXT[item.publish_status] || item.publish_status || "—",
        `chip-pub-${String(item.publish_status || "").toLowerCase()}`
      )
    );

    const actionCell = document.createElement("td");
    actionCell.className = "actions-cell";
    if (item.publish_status !== "PENDING") {
      const reset = document.createElement("button");
      reset.className = "btn small";
      reset.textContent = "重置待发布";
      reset.dataset.action = "reset";
      reset.dataset.id = item.product_id;
      reset.disabled = active;
      actionCell.append(reset);
    }
    const remove = document.createElement("button");
    remove.className = "btn small danger";
    remove.textContent = "删除文件";
    remove.dataset.action = "delete";
    remove.dataset.id = item.product_id;
    remove.disabled = active;
    actionCell.append(remove);

    row.append(titleCell, shopCell, priceCell, downloadCell, publishCell, actionCell);
    fragment.append(row);
  });

  els.productRows.append(fragment);
}

async function fetchProducts() {
  if (productsBusy) return;
  productsBusy = true;
  try {
    const data = await api("/api/products");
    renderProducts(data);
  } catch (err) {
    els.productSummary.textContent = `商品清单加载失败：${err.message}`;
  } finally {
    productsBusy = false;
  }
}

async function productAction(action, id) {
  const suffix = action === "reset" ? "publish-status" : "delete";
  const body = action === "reset" ? JSON.stringify({ status: "PENDING" }) : "{}";
  try {
    await api(`/api/products/${encodeURIComponent(id)}/${suffix}`, {
      method: "POST",
      body,
    });
  } catch (err) {
    showError(err.message);
  }
  await fetchProducts();
}

async function refreshStatus() {
  if (statusBusy) return;
  statusBusy = true;
  try {
    const data = await api("/api/status");
    applyConfig(data.config);
    applyJob(data.job);
  } catch (err) {
    setBadgeState("FAILED");
    showError(`后端连接失败：${err.message}`);
  } finally {
    statusBusy = false;
  }
}

function formatTime(ts) {
  if (!ts) return "";
  const text = String(ts);
  const index = text.indexOf("T");
  return index >= 0 ? text.slice(index + 1) : text;
}

function appendLogs(records) {
  const levelFilter = els.selLevel.value;
  let appended = 0;
  const fragment = document.createDocumentFragment();

  records.forEach((record) => {
    if (levelFilter !== "ALL" && record.level !== levelFilter) return;

    const row = document.createElement("div");
    row.className = `log-row lv-${record.level}`;

    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = formatTime(record.ts);

    const level = document.createElement("span");
    level.className = `lv-${record.level}`;
    level.textContent = record.level;

    const src = document.createElement("span");
    src.className = "src";
    src.textContent = `${record.module}.${record.step || "-"}`;

    const status = document.createElement("span");
    status.className = "st";
    status.textContent = record.status || "";

    const message = document.createElement("span");
    message.className = "msg";
    message.textContent = record.message || "";

    row.append(ts, level, src, status, message);

    const extras = [];
    if (record.product_id) extras.push(`商品 ${record.product_id}`);
    if (record.duration_ms !== null && record.duration_ms !== undefined) extras.push(`耗时 ${record.duration_ms}ms`);
    if (record.error) extras.push(`错误 ${record.error}`);
    if (record.file_path) extras.push(`文件 ${record.file_path}`);
    if (record.screenshot_path) extras.push(`截图 ${record.screenshot_path}`);
    if (extras.length) {
      const extra = document.createElement("div");
      extra.className = "extra";
      extra.textContent = extras.join("  |  ");
      row.append(extra);
    }

    fragment.append(row);
    appended += 1;
  });

  if (!appended) return;

  if (els.logEmpty) els.logEmpty.style.display = "none";
  els.logList.append(fragment);

  while (els.logList.querySelectorAll(".log-row").length > MAX_LOG_ROWS) {
    const first = els.logList.querySelector(".log-row");
    if (!first) break;
    first.remove();
  }

  if (els.chkAutoscroll.checked) {
    els.logList.scrollTop = els.logList.scrollHeight;
  }
}

async function pollLogs() {
  if (logsBusy) return;
  logsBusy = true;
  try {
    const data = await api(`/api/logs?cursor=${cursor}`);
    if (data.logs && data.logs.length) appendLogs(data.logs);
    cursor = data.cursor;
  } catch (err) {
    /* 状态轮询会提示连接异常，这里避免刷屏 */
  } finally {
    logsBusy = false;
  }
}

async function startJob(type) {
  try {
    await api("/api/job/start", {
      method: "POST",
      body: JSON.stringify({ type }),
    });
    els.vMessage.style.color = "";
    await refreshStatus();
    await pollLogs();
  } catch (err) {
    showError(err.message);
  }
}

async function control(path) {
  try {
    await api(path, { method: "POST", body: "{}" });
    await refreshStatus();
    await pollLogs();
  } catch (err) {
    showError(err.message);
  }
}

els.btnDownload.addEventListener("click", () => startJob("download"));
els.btnPublish.addEventListener("click", () => startJob("publish"));
els.btnPause.addEventListener("click", () => control("/api/job/pause"));
els.btnResume.addEventListener("click", () => control("/api/job/resume"));
els.btnStop.addEventListener("click", () => control("/api/job/stop"));
els.btnHumanDone.addEventListener("click", () => control("/api/job/human-done"));
els.btnRefreshProducts.addEventListener("click", () => fetchProducts());

els.productRows.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  if (action === "delete") {
    const confirmed = window.confirm(
      `确定删除该商品的全部下载文件？\n${id}\n删除后下次运行下载任务会重新抓取。`
    );
    if (!confirmed) return;
  }
  button.disabled = true;
  await productAction(action, id);
  await pollLogs();
});

els.chkTestMode.addEventListener("change", async () => {
  try {
    const data = await api("/api/config/test-mode", {
      method: "POST",
      body: JSON.stringify({ enabled: els.chkTestMode.checked }),
    });
    applyConfig(data.config);
  } catch (err) {
    els.chkTestMode.checked = !els.chkTestMode.checked;
    showError(err.message);
  }
});

els.selLevel.addEventListener("change", () => {
  els.logList.querySelectorAll(".log-row").forEach((row) => {
    const level = Array.from(row.classList).find((c) => c.startsWith("lv-"));
    const rowLevel = level ? level.replace("lv-", "") : "";
    row.style.display = els.selLevel.value === "ALL" || rowLevel === els.selLevel.value ? "" : "none";
  });
});

els.btnClearLog.addEventListener("click", () => {
  els.logList.querySelectorAll(".log-row").forEach((row) => row.remove());
  if (els.logEmpty) els.logEmpty.style.display = "";
});

async function bootstrap() {
  await refreshStatus();
  await pollLogs();
  await fetchProducts();
  setInterval(refreshStatus, 2000);
  setInterval(pollLogs, 1000);
  setInterval(fetchProducts, 5000);
}

bootstrap();
