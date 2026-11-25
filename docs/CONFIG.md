# 配置说明（CONFIG）

> 原则：**非敏感项进 `config.yaml`，敏感项只进 `.env`**。本文件仅写占位符，禁止写入真实账号、密码、Key。
> 加载优先级：`默认值 < config.yaml < 环境变量（.env）`；启动时做类型与范围校验，校验失败直接拒绝启动并打印明确错误。

---

## 0. 已确认取值（2026-11-23，实际生效以仓库内 `config.yaml` 为准）

| 项 | 取值 | 来源 |
| --- | --- | --- |
| 运行模式 | `test_mode: true`，不真实发布；`simulated: false` 真实浏览器 | 用户要求 |
| 站点 | 网商园 `https://www.wsy.com/`；闲鱼 `https://www.goofish.com/` | 用户确认 |
| 下载根目录 | `downloads/`（项目内） | 用户要求 |
| 店铺 / 每店商品 | 10 / 5（共 50）；每批最多档口数 1~10（界面可调，默认 2），店间休整 30–60 秒 | 用户要求 / 只在需要时增加访问面 |
| 登录保护 | 冷却 30 分钟、单次任务最多登录 1 次 | 用户要求（账号被标记） |
| 随机间隔 | 1.5–4.0 秒 | 默认 |
| 压缩包 | zip，约 10000KB；下载超时 180 秒 | Q-02 |
| 发布图片 | 4 张，原图不压缩 | Q-04 / Q-10 |
| 描述 | DeepSeek 生成，≤1500 字 | Q-04 |
| 标题 | 使用原标题，超限截断 | Q-13 |
| 规格 | 单规格 | Q-07 |
| 所在地 | 附近地址第一个 | Q-05 |
| 加价 | +10%，最低 +1 元 | Q-06 |
| 浏览器 | 可见 Chromium，安装在 `.venv` | Q-12 |
| 凭据 | 仅 `.env`，已 gitignore | D-14 |

> 安全说明：真实账号、密码与 DeepSeek Key 只写入本地 `.env`（未提交、未展示）；项目内不保存任何凭据副本。
> 环境准备：`python -m venv .venv` → `.venv\Scripts\python.exe -m pip install -r requirements.txt` → `$env:PLAYWRIGHT_BROWSERS_PATH="<项目>\data\ms-playwright"` → `.venv\Scripts\python.exe -m playwright install chromium`。
> 说明：`config.py` 启动时会自动 `setdefault` 该环境变量，保证浏览器固定在项目内 `data/ms-playwright/`（已 gitignore）。

## 1. 文件分工

| 文件 | 内容 | 是否提交 |
| --- | --- | --- |
| `config.yaml` | 行为参数、路径、数量、规则、选择器开关 | ✅ 提交 |
| `.env` | 网商园/闲鱼账号密码、DeepSeek Key | ❌ 不提交 |
| `.env.example` | 占位示例 | ✅ 提交 |
| `.gitignore` | 忽略 `.env`、`downloads/`、`logs/`、`data/browser_state/` | ✅ 提交 |

## 2. `.env` 示例（仅占位符）

```dotenv
# ===== 网商园账号 =====
WSY_USERNAME=your_username_here
WSY_PASSWORD=your_password_here

# ===== 闲鱼账号（若平台支持账号密码登录；否则留空，走扫码人工接管）=====
XY_USERNAME=your_xianyu_account_here
XY_PASSWORD=your_xianyu_password_here

# ===== DeepSeek =====
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# ===== Flask =====
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
FLASK_SECRET_KEY=change_me_random_string
```

## 3. `config.yaml` 完整示例

```yaml
# ============ 运行模式 ============
mode:
  test_mode: true              # true=只填写+截图不提交；false=真实点击发布（当前交付默认只测试）
  simulated: false             # false=真实浏览器自动化；true=界面联调模拟
  dry_run_download: false      # true=只演练不真正保存文件（调试用）
  headless: false              # 必须 false（可见浏览器）
  keep_browser_open: false     # 任务结束关闭浏览器

# ============ 浏览器 ============
browser:
  engine: chromium
  viewport: { width: 1440, height: 900 }
  slow_mo_ms: 120              # Playwright slow_mo，辅助肉眼观察
  user_agent: ""               # 空=使用默认
  locale: zh-CN
  timezone: Asia/Shanghai
  storage_state_path: data/browser_state/state.json
  accept_downloads: true

# ============ 节奏控制（秒）============
timing:
  delay_min: 1.5               # 任意动作后的随机间隔下限
  delay_max: 4.0               # 随机间隔上限
  page_load_timeout: 30
  element_timeout: 15
  download_timeout: 120
  upload_timeout: 60
  between_products_min: 3.0    # 商品之间额外停顿
  between_products_max: 8.0

# ============ 网商园采集 ============
wangshangyuan:
  base_url: https://www.17zwd.com/     # 待确认实际域名，确认后更新
  login_entry_text: "用户登录"          # 文案定位（待实测）
  gold_stall_entry_text: "金牌档口"
  shop_count: 10                       # 前 10 家店铺
  product_count: 5                     # 每家前 5 个商品
  shop_batch_size: 2                   # 每批最多访问的店铺数（1~shop_count，界面可调）
  filter_new_days: 30                  # “30 日新品”
  filter_category: "男装"
  download_button_text: "一键下载"
  detail_row_text: "当前的详情图,主图,颜色图:"   # 弹窗目标行文案
  max_retries: 3
  retry_backoff_sec: 2

# ============ 下载与文件 ============
download:
  root_dir: downloads                  # 下载根目录（可改绝对路径）
  raw_subdir: raw
  images_subdir: images
  name_max_length: 80                  # 目录/文件名单段最大长度
  keep_raw_archive: true               # 解压后保留原始压缩包
  image_extensions: [".jpg", ".jpeg", ".png", ".webp"]
  max_images_per_product: 20           # 单商品最多处理图片数（防异常爆量）
  extract_formats: [".zip", ".rar", ".7z"]

# ============ 闲鱼 ============
xianyu:
  home_url: https://www.goofish.com/   # 待确认当前 Web 端域名
  login_check_timeout: 20
  publish_entry_text: "发布闲置"
  login_requires_manual: auto          # auto=能自动则自动，否则人工接管；always=直接人工
  title_source: original               # 已确认：直接使用原商品标题
  title_max_len: 30
  desc_max_len: 1500                   # 已确认：描述上限 1500 字
  spec_mode: single                    # 已确认：单规格
  default_condition: "全新"
  default_stock: 1
  location_pick: first_nearby          # 已确认：附近地址取第一个
  max_images_upload: 4                 # 已确认：发布 4 张
  wait_publish_success_timeout: 60

# ============ 价格加价规则 ============
pricing:
  markup_type: percent          # percent=按比例；fixed=固定加价
  markup_value: 10              # percent: 10 表示 +10%；fixed: 10 表示 +10 元
  min_markup_amount: 1.0        # 最低加价金额（元）
  min_price: 9.9                # 最低售价
  round_mode: ceil_to_0_1       # none / ceil_to_0_1 / ceil_to_1 / end_with_9
  currency_symbol: "¥"

# ============ DeepSeek 描述生成 ============
deepseek:
  enabled: true
  temperature: 0.8
  max_tokens: 500
  timeout_sec: 30
  retry: 2
  fallback_to_template: true    # 失败时用模板，不中断发布
  style_keywords: ["口语化", "真实感受", "分点短句", "不夸张", "学生党/日常通勤"]
  forbidden_keywords: ["微信", "QQ", "加V", "第一", "最便宜", "全网最低", "绝对"]
  template: |
    【{title}】
    全新未拆，实拍如图，颜色/尺码可选。
    - 面料：{fabric}
    - 版型：{fit}
    - 适合：日常通勤、学生党
    价格可小刀，喜欢直接拍，当天发货～

# ============ 日志 ============
logging:
  level: INFO
  console: true
  file: logs/app.log
  structured_file: logs/structured.jsonl
  screenshot_dir: logs/screenshots
  buffer_size: 2000             # 内存环形缓冲条数（供 UI 拉取）
  mask_patterns: ["password", "passwd", "token", "api_key", "cookie", "authorization", "phone"]
  ui_poll_interval_ms: 2000

# ============ 任务与重试 ============
job:
  max_product_total: 50         # 10 店 × 5 商品
  stop_on_first_failure: false  # true=任一失败即停
  skip_existing_success: true   # 已成功商品默认跳过
  retry_per_step: 2
  retry_backoff_sec: 2
  human_timeout_sec: 0          # 0=无限等待人工处理

# ============ 服务 ============
server:
  host: 127.0.0.1
  port: 5000
  debug: false
```

## 4. 配置项说明与校验规则

| 键 | 类型 | 校验 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `mode.test_mode` | bool | — | `true` | 安全默认：先测试模式 |
| `mode.headless` | bool | 生产必须 `false` | `false` | 违反时启动警告 |
| `timing.delay_min/max` | float | `0.2 ≤ min < max ≤ 60` | 1.5 / 4.0 | 随机间隔 |
| `wangshangyuan.shop_count` | int | `1 ≤ n ≤ 20` | 10 | 店铺数 |
| `wangshangyuan.product_count` | int | `1 ≤ n ≤ 10` | 5 | 每店商品数 |
| `wangshangyuan.shop_batch_size` | int | `1 ≤ n ≤ shop_count` | 2 | 每批最多档口数（界面可调，下次任务生效） |
| `download.root_dir` | str | 可创建、可写 | `downloads` | 下载根目录 |
| `pricing.markup_type` | enum | `percent/fixed` | `percent` | 加价方式 |
| `pricing.markup_value` | float | `≥ 0` | 10 | 加价数值 |
| `xianyu.max_images_upload` | int | `1 ≤ n ≤ 20` | 4 | 图片上限（已确认 4 张） |
| `deepseek.enabled` | bool | 无 Key 时自动降级 | true | 关闭则直接模板 |
| `logging.buffer_size` | int | `100 ≤ n ≤ 10000` | 2000 | 内存日志条数 |
| `job.max_product_total` | int | `≤ shop_count*product_count` | 50 | 总处理上限 |

## 5. 敏感信息处理

1. `.env` 只由 `config.py` 通过 `python-dotenv` 读取，代码中不出现字面量。
2. 配置对象打印：`Config.__repr__` 对敏感字段固定输出 `***`。
3. 日志脱敏：按 `logging.mask_patterns` 与内置正则过滤；例外开关不提供（避免误开泄密）。
4. UI 的 `/api/config/public` 只返回：模式、数量、延时区间、加价规则、是否已配置凭据（`true/false`），**不返回值本身**。
5. `.env` 缺失时：启动成功但“开始”按钮禁用，页面提示“未配置凭据/Key”，不伪造成功。
6. `data/browser_state/state.json` 含登录态，视同敏感文件，加入 `.gitignore`。

## 6. 配置与代码的映射

| 配置块 | 使用模块 |
| --- | --- |
| `mode.*` | `services/job_manager.py`、`automation/publish_action.py` |
| `browser.*` / `timing.*` | `automation/browser.py` |
| `wangshangyuan.*` | `automation/wangshangyuan.py`、`automation/selectors.py` |
| `download.*` | `services/unzip_service.py`、`services/product_store.py` |
| `xianyu.*` | `automation/xianyu_login.py`、`automation/xianyu_publish.py` |
| `pricing.*` | `services/pricing.py`（阶段 8 新增） |
| `deepseek.*` | `services/descriptor.py` |
| `logging.*` | `services/log_service.py` |
| `job.*` / `server.*` | `app.py`、`services/job_manager.py` |

## 7. 需要用户提供的配置（待确认）

| 项 | 说明 |
| --- | --- |
| 网商园账号密码 | 写入 `.env`，勿发聊天记录 |
| 闲鱼账号 | 若仅支持扫码，保持 `login_requires_manual: auto` |
| DeepSeek API Key | 无 Key 则 `deepseek.enabled: false` 走模板 |
| 下载根目录 | 默认 `downloads/`，可指定其他盘符路径 |
| 店铺/商品数量 | 默认 5 / 3，是否需要调整 |
| 随机间隔范围 | 默认 1.5–4.0 秒，是否需要更慢 |
| 加价规则 | 默认 +10%、最低 +1 元、向上取到 0.1 |
| 真实发布范围 | 建议先 1 件验证，再批量 |

## 8. 后续可能新增的配置（预留，不提前实现）

- 多账号切换（`accounts: []`）
- 图片压缩（`image: {max_width, quality}`）
- 失败即停策略细化（按错误码）
- 代理/网络配置（若平台要求，仅限合法使用）
