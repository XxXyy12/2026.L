# 架构设计（ARCHITECTURE）

> 目标：Flask 负责“看得见、控得住”，Playwright 负责“点得准、等得稳”，二者用队列/事件/共享状态解耦。
> 本文档不含业务实现代码，仅定义模块边界、数据流、状态机与接口约定。

---

## 1. 总体结构

```
┌───────────────────────────────────────────────────────┐
│ 浏览器（用户）  http://127.0.0.1:5000                   │
│  templates/index.html + static/app.js                  │
└──────────────┬────────────────────────────────────────┘
               │ HTTP(JSON) 轮询状态/日志；POST 控制指令
┌──────────────▼────────────────────────────────────────┐
│ Flask 层（主线程）                                      │
│  app.py            路由与页面                            │
│  api/routes.py     /api/job/* /api/logs /api/status     │
│  services/job_manager.py   任务提交/暂停/恢复/停止       │
│  services/log_service.py   环形缓冲 + 脱敏 + JSONL       │
└──────────────┬────────────────────────────────────────┘
               │ queue.Queue(命令/事件)  + threading.Event(暂停/停止)
┌──────────────▼────────────────────────────────────────┐
│ AutomationWorker（唯一后台线程，Playwright sync API）    │
│  automation/browser.py      浏览器生命周期/等待/截图     │
│  automation/selectors.py    所有选择器（唯一改版入口）   │
│  automation/wangshangyuan.py  登录/店铺/筛选/下载        │
│  automation/xianyu_login.py   登录检测/登录/接管         │
│  automation/xianyu_publish.py 发布页填写流程            │
│  automation/publish_action.py 策略：Test/Real           │
│  automation/steps.py        步骤装饰器：日志/重试/间隔   │
│  services/descriptor.py     DeepSeek 描述生成            │
│  services/unzip_service.py  解压（zip/rar/7z 兜底）      │
│  services/product_store.py  meta.json / products.json   │
│  core/state.py              任务状态机与 TaskRecord      │
│  core/control.py            暂停/停止/人工接管信号       │
└──────────────┬────────────────────────────────────────┘
               │
        downloads/  data/  logs/
```

## 2. 目录结构（建议）

```
d:\aaaaa\51.project_1\
├─ app.py                     # Flask 入口（创建 app、启动 worker 线程）
├─ config.py                  # 加载 .env + config.yaml，校验并暴露只读配置
├─ config.yaml                # 非敏感配置（提交到仓库）
├─ .env                       # 敏感配置（不提交；提供 .env.example）
├─ .env.example
├─ requirements.txt
├─ README.md                  # 启动说明（后续生成）
├─ automation/
│  ├─ __init__.py
│  ├─ browser.py              # BrowserSession：启动/关闭/等待/随机间隔/截图/新页面
│  ├─ selectors.py            # SELECTORS：网商园 + 闲鱼 全部选择器
│  ├─ wangshangyuan.py        # 网商园采集下载任务
│  ├─ xianyu_login.py         # 闲鱼登录检测与登录
│  ├─ xianyu_publish.py       # 发布页信息填写（不含提交）
│  ├─ publish_action.py       # PublishAction 接口 + Test/Real 实现 + 工厂
│  └─ steps.py                # step() 装饰器：计时/日志/重试/间隔/错误码
├─ core/
│  ├─ __init__.py
│  ├─ state.py                # JobState/TaskState 状态机、TaskRecord/JobRecord
│  ├─ control.py              # ControlFlags：pause/resume/stop/human_done 事件
│  ├─ errors.py               # 错误码枚举（E_LOGIN/E_CAPTCHA/E_SELECTOR/...）
│  └─ human.py                # NeedHumanError 异常（触发 PAUSED_HUMAN）
├─ services/
│  ├─ __init__.py
│  ├─ job_manager.py          # 接收 UI 指令、驱动 worker、维护快照
│  ├─ log_service.py          # 内存环形日志 + 文件 + 脱敏 + 增量游标
│  ├─ descriptor.py           # DeepSeek 调用与模板兜底
│  ├─ unzip_service.py        # 解压与图片枚举
│  └─ product_store.py        # 目录规划、meta/products.json 读写
├─ api/
│  ├─ __init__.py
│  └─ routes.py               # /api/* 蓝图
├─ templates/
│  └─ index.html              # 统一界面
├─ static/
│  ├─ css/style.css
│  └─ js/app.js               # 轮询、按钮状态、日志渲染
├─ docs/
│  ├─ REQUIREMENTS.md
│  ├─ ARCHITECTURE.md
│  └─ CONFIG.md
│     （PROJECT_MEMORY.md / PROGRESS.md 为本地工作记录，不随仓库发布）
├─ data/
│  ├─ products.json           # 商品总清单（发布数据源）
│  ├─ reports/                # 运行报告
│  └─ browser_state/          # storage_state（含登录态，加入 .gitignore）
├─ downloads/                 # 下载根目录（默认）
│  └─ 01_<shop_slug>/03_<product_slug>/
│     ├─ raw/                 # 原始压缩包
│     ├─ images/              # 解压后的图片
│     ├─ meta.json
│     ├─ description.txt
│     ├─ publish_preview.png  # 测试模式截图
│     └─ publish_result.json
└─ logs/
   ├─ app.log
   ├─ structured.jsonl
   └─ screenshots/            # 异常/步骤截图
```

> 说明：`data/browser_state/`、`.env`、`downloads/`、`logs/` 必须写入 `.gitignore`。

## 3. Flask 与 Playwright 协作方式

### 3.1 为什么用单后台线程

Playwright **sync API** 不能在已有 asyncio 事件循环的线程中运行（Flask 开发服务器/某些扩展可能创建循环），也不能跨线程共享同一个 Playwright 对象。因此：

- Flask 主线程：只处理 HTTP 与状态读取，**绝不触碰** Playwright 对象。
- `AutomationWorker`：独立 `threading.Thread`，线程内 `sync_playwright().start()`，独占浏览器与页面。
- 通信只通过线程安全结构：`queue.Queue`（指令）、`threading.Event`（暂停/停止）、加锁的 `JobRecord` 快照。

### 3.2 控制协议

| UI 动作 | 接口 | 进入 worker 的形式 |
| --- | --- | --- |
| 开始下载图片 | `POST /api/job/start {type:"download"}` | `Command(type=START_DOWNLOAD)` |
| 开始发布服装 | `POST /api/job/start {type:"publish"}` | `Command(type=START_PUBLISH)` |
| 暂停 | `POST /api/job/pause` | 置位 `pause_event`（步骤边界生效） |
| 继续 | `POST /api/job/resume` | 清除 `pause_event` |
| 停止 | `POST /api/job/stop` | 置位 `stop_event` |
| 人工已处理 | `POST /api/job/human-done` | 清除 `waiting_human`，worker 重新校验前置条件 |
| 测试模式开关 | `POST /api/config/test-mode {enabled:bool}` | 更新共享配置快照（仅影响未提交商品） |
| 状态查询 | `GET /api/status` | 返回 `JobRecord` 快照 + 计数 + 当前步骤 |
| 日志增量 | `GET /api/logs?cursor=N` | 返回自 cursor 之后的新日志 + 新 cursor |

约束：
- 指令入队前做状态校验（非 IDLE 时拒绝 START，重复点击返回 409 语义）。
- `pause/stop` 不阻塞 HTTP 请求，立即返回；worker 在下一个 `checkpoint()` 处响应。
- 每个步骤开始/结束都调用 `checkpoint()`，因此暂停粒度 = 步骤粒度。

### 3.3 步骤封装（steps.py 约定）

每个可观察动作统一经过 `step(module, name, product_id=None)` 包装：

1. 记录 `start_ts`；
2. `checkpoint()`（暂停/停止/人工等待点）；
3. 执行动作（内部使用 `browser.wait_visible()` 显式等待）；
4. `random_delay()` 随机间隔；
5. 记录耗时、成功/失败、截图路径到结构化日志；
6. 失败时按 `errors.py` 错误码抛出，由任务层决定重试或暂停。

## 4. 数据流

### 4.1 采集下载流

```
config.yaml/.env
   └─> ProductStore.prepare_product_dir(shop, product)
         └─> WangshangyuanJob.run()
               ├─ login()                      → 失败: NeedHuman(E_LOGIN)
               ├─ open_gold_stalls()           → 取前 N 店
               ├─ for shop in shops[:5]:
               │    filter_30days_new_menswear()
               │    for product in top3:
               │        open_product()
               │        click_one_click_download()
               │        expect_download() → raw/*.zip
               │        UnzipService.extract(raw, images/)
               │        ProductStore.write_meta(meta.json)
               └─> ProductStore.rebuild_products_index(data/products.json)
```

### 4.2 发布流

```
data/products.json（筛选待发布）
   └─> XianyuPublishJob.run()
         ├─ XianyuLogin.ensure_logged_in()      → 失败: NeedHuman(E_CAPTCHA/E_LOGIN)
         ├─ click_publish_idle()
         ├─ for product:
         │    upload_images(images/, limit=4)          # 已确认 4 张原图
         │    title = original_title(meta, max=30)      # 已确认原标题
         │    desc  = DescriptorService.generate(meta)   # ≤1500 字；失败→模板兜底
         │    price = PriceRule.apply(meta.price)        # +10%，最低 +1 元
         │    select_location(first_nearby)              # 已确认第一个附近地址
         │    validate_required()                        # 单规格
         │    PublishAction.submit(page, product)         # 策略：截图 or 点击发布
         │    back_to_home()
         └─> 写 publish_result.json / report.json
```

### 4.3 数据契约（meta.json 关键字段）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `product_id` | string | `shop序号-product序号-时间戳`，日志主键 |
| `shop_name` / `shop_url` | string | 店铺信息 |
| `title` / `source_url` | string | 原标题、原商品页 |
| `price` | number | 原价（用于加价） |
| `images` | string[] | 解压后的图片相对路径 |
| `raw_file` | string | 原始压缩包路径 |
| `downloaded_at` | ISO8601 | 下载完成时间 |
| `download_status` | enum | `SUCCESS/FAILED/PARTIAL` |
| `publish_status` | enum | `PENDING/TESTED/SUBMITTED/FAILED/SKIPPED` |
| `description_source` | enum | `deepseek/template` |

## 5. 任务状态机

### 5.1 Job（整体任务）

```
IDLE ──start──> RUNNING ──pause──> PAUSING ──> PAUSED
                   │                  │          └─resume─> RUNNING
                   ├──need_human────> WAITING_HUMAN ──human_done──> RUNNING
                   ├──stop──────────> STOPPING ──> STOPPED
                   ├──全部完成──────> COMPLETED
                   └──致命错误──────> FAILED
```

规则：
- `PAUSING` 只是等待当前步骤收尾的过渡态，UI 可显示“正在暂停”。
- `WAITING_HUMAN` 必须携带 `human_reason`（错误码 + 提示文案 + 建议操作），UI 醒目展示。
- `STOPPED/COMPLETED/FAILED` 之后允许再次 start（新建一次 run_id，保留历史）。

### 5.2 Task（单个商品）

```
PENDING ──> RUNNING ──> SUCCESS
              ├──retry──> RUNNING
              ├──need_human──> WAITING_HUMAN ──> RUNNING
              ├──fail──> FAILED
              └──skip──> SKIPPED
```

### 5.3 Publish 子步骤（用于日志与断点）

`OPEN_PAGE → UPLOAD_IMAGES → FILL_TITLE → FILL_DESC → FILL_PRICE → SELECT_LOCATION → VALIDATE → PRE_SUBMIT → SUBMIT → BACK_HOME`

- 测试模式在 `PRE_SUBMIT` 后执行截图并直接 `BACK_HOME`。
- 真实模式 `SUBMIT` 后等待成功标识，再 `BACK_HOME`。

## 6. 测试模式与真实发布解耦设计

### 6.1 统一接口

```python
# automation/publish_action.py（接口约定，非实现代码）
class PublishAction(Protocol):
    def submit(self, page, product: ProductMeta, run_ctx: RunContext) -> PublishResult: ...
```

- `TestPublishAction.submit`：确保所有必填项已填 → `page.screenshot(full_page=True, path=<商品目录>/publish_preview.png)` → 返回 `PublishResult(submitted=False, mode="test", screenshot=...)`。
- `RealPublishAction.submit`：点击“发布” → 等待成功提示/跳转 → 截图 `publish_result.png` → 返回 `PublishResult(submitted=True, mode="real", ...)`；超时则抛错并截图。
- 工厂：`build_publish_action(test_mode: bool) -> PublishAction`，在**每个商品开始发布前**读取一次当前模式，保证“运行中切换模式只影响后续商品”。

### 6.2 解耦红线

- 预提交流程（上传/标题/描述/价格/所在地/校验）**完全共用**，不含任何 `if test_mode` 分支。
- 采集下载、解压、描述生成模块**不得引用** `PublishAction` 或测试模式配置。
- 测试模式仅影响 `submit()` 的实现选择与结果字段，不影响数据产物结构（两者都写 `publish_result.json`）。

## 7. 异常与人工接管

| 场景 | 错误码 | 行为 |
| --- | --- | --- |
| 登录失败/需要验证码 | `E_LOGIN` / `E_CAPTCHA` | 截图 → `WAITING_HUMAN`，提示“请手动完成登录/验证后点击继续” |
| 风控提示页 | `E_RISK` | 暂停，不重试，等待人工 |
| 选择器找不到（超时） | `E_SELECTOR` | 重试 N 次 → 仍失败则该商品 `FAILED`，全局继续 |
| 下载超时 | `E_DOWNLOAD` | 重试，仍失败标记 `FAILED` |
| 解压失败 | `E_UNZIP` | 记录警告，`images` 为空并标记 `PARTIAL`，发布阶段跳过该商品 |
| DeepSeek 超时/额度 | `E_AI` | 模板兜底，`description_source=template` |
| 浏览器崩溃 | `E_BROWSER` | 重启一次，失败则 `WAITING_HUMAN` |
| 磁盘错误 | `E_IO` | 立即暂停（避免继续污染数据） |

## 8. 安全与脱敏

- `config.py` 读取 `.env` 后用 `Secret` 包装，`repr()` 固定输出 `***`。
- `log_service` 内置正则脱敏：`password|passwd|token|api_key|cookie|authorization|secret` 与手机号。
- 异常堆栈只输出类型与消息摘要，不打印请求体/凭据。
- Flask 绑定 `127.0.0.1`；无鉴权也仅本机可访问。

## 9. 并发与资源

- 单 worker、单浏览器、单页面序列执行；不并发。
- 浏览器随 worker 生命周期启停；任务结束可选择保留浏览器（便于人工检查）。
- 每个商品结束释放页面；`raw/` 保留不删除。

## 10. 待实测项（影响架构细节，不阻塞骨架）

1. 闲鱼 Web 端发布页是否为独立 URL 或多步弹层 → 决定 `open_publish_page()` 的导航方式。
2. 图片上传是否支持 `set_input_files`（隐藏 input）还是必须文件选择器 → 决定上传实现。
3. “附近地址第一个”是否为下拉/弹窗/自动定位 → 决定 `select_location()` 实现与授权策略。
4. ~~网商园压缩包格式~~ 已确认 zip（约 10MB），解压以 `zipfile` 为主、rar/7z 兜底。
5. ~~登录是否需要扫码~~ 网商园已确认无验证码；闲鱼登录方式仍需实测。

## 11. 当前实现状态（阶段 1–2，2026-11-23）

已落地：

| 文件 | 用途 |
| --- | --- |
| `config.py` / `config.yaml` / `.env` | 配置加载、校验、路径创建、脱敏展示；`.env` 已 gitignore |
| `core/errors.py` / `state.py` / `control.py` | 错误码、Job/Task 状态与记录、暂停/停止/人工信号 |
| `services/log_service.py` | 环形缓冲 + `logs/app.log` + `logs/structured.jsonl` + 脱敏 |
| `services/job_manager.py` | 任务队列与单 worker；**当前为模拟 runner**（`mode.simulated=true`） |
| `services/product_store.py` | 读取 `data/products.json`（写入在阶段 6） |
| `api/routes.py` / `app.py` | `/`、`/api/status`、`/api/logs`、`/api/job/*`、`/api/config/test-mode` |
| `templates/index.html` / `static/*` | 统一界面：下载/发布入口、暂停/继续/停止、测试模式开关、实时日志 |

尚未实现：`automation/`（浏览器、选择器、步骤封装、两个业务 Job、PublishAction）、`services/descriptor.py`、`services/unzip_service.py`、`services/pricing.py`、商品落盘与清单写入。

切换到真实流程的约定：

1. 阶段 3 完成 `automation/browser.py` 冒烟后，把 `mode.simulated` 改为 `false`；
2. `JobManager._execute` 按 `run_type` 调用 `WangshangyuanJob` / `XianyuPublishJob`，API、状态机与 UI 不变；
3. 模拟 runner 保留为 `simulated=true` 时的回归手段，不删除。
