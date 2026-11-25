# 网商园采集下载 + 闲鱼发布辅助（本地可视化自动化）

Python + Flask + Playwright（可见 Edge 浏览器）实现的本地单机工具，包含两条独立业务链，共用一个前端页面：

1. **采集下载**：网商园登录 → 金牌档口前 5 家店铺 → 每家“30 日新品 / 男装”前 3 个商品 → 一键下载 → 解压到按店铺/商品划分的目录。
2. **闲鱼发布**：闲鱼登录（支持密码登录 + 人工滑块）→ 发布闲置 → 对已下载商品上传 4 张图、原标题、DeepSeek 描述、价格 +10%、第一个附近地址 → 测试模式只截图不真实发布。

## 1. 环境要求

- Windows 10/11
- Python 3.10+（本项目在 3.14 下验证）
- 本机已安装 Microsoft Edge
- 可访问 `www.wsy.com`、`www.goofish.com`、`api.deepseek.com`

## 2. 安装

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

说明：`config.py` 会把 `PLAYWRIGHT_BROWSERS_PATH` 指向项目内 `data/ms-playwright`，浏览器文件不会写入 `AppData`。

## 3. 配置

1. 复制 `.env.example` 为 `.env`，填写：
   - `WSY_USERNAME` / `WSY_PASSWORD`：网商园主账号
   - `WSY_USERNAME_2` / `WSY_PASSWORD_2`：网商园备用账号（可选）
   - `XY_USERNAME` / `XY_PASSWORD`：闲鱼账号
   - `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`
2. `.env` 已在 `.gitignore` 中，**不要提交或截图外发**。
3. `config.yaml` 常用项：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `mode.test_mode` | `true` | 测试模式：发布只截图不提交 |
| `mode.headless` | `false` | 必须可见浏览器 |
| `browser.user_data_dir` | `data/edge-profile-2` | 持久化 Edge 配置目录（保留登录态） |
| `wangshangyuan.manual_login` | `true` | 人工登录一次，程序再校验真实登录态 |
| `wangshangyuan.account_index` | `2` | 1=主账号，2=备用账号 |
| `wangshangyuan.shop_batch_size` | `2` | 每批最多访问的“有未下载商品”的店铺数 |
| `login.cooldown_minutes` | `30` | 同账号两次登录的最小间隔 |
| `login.max_per_run` | `1` | 单次任务最多登录次数 |

## 4. 启动

源码方式：

```powershell
.venv\Scripts\python.exe app.py
```

桌面窗口方式（pywebview，不修改原有代码）：

```powershell
.venv\Scripts\pythonw.exe launcher.py
```

打开 http://127.0.0.1:5000/ ，页面提供：

- 开始下载图片 / 开始发布服装
- 暂停 / 继续 / 停止
- 测试模式开关
- 任务状态、进度、实时日志、人工接管提示（“已处理，继续”）

### 打包为单文件 exe

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --noconsole --name AutoPublisher `
  --add-data "templates;templates" --add-data "static;static" --add-data "config.yaml;." `
  --collect-all playwright --collect-all py7zr --collect-all webview launcher.py
```

产物：`dist\AutoPublisher.exe`（约 60MB）。首次运行会在 exe 同目录生成 `config.yaml`、`data/`、`downloads/`、`logs/`；**把 `.env` 复制到 exe 同目录**才能带上账号与 Key。分发 exe 前务必删除同目录的 `.env`、`data/`、`downloads/`、`logs/`。

## 5. 使用流程

### 下载图片

1. 点“开始下载图片”，Edge 自动打开。
2. 若未登录：在 Edge 中手动完成网商园登录（含可能的滑块），回到页面点“已处理，继续”。
3. 程序自动校验“金牌档口”页，然后按每批 2 家店、店间 30–60 秒、商品间隔 5–12 秒下载。
4. 已下载完成的店铺/商品会自动跳过（`data/products.json` + `data/shop_plans.json`）。

### 发布服装（默认测试模式）

1. 点“开始发布服装”，程序自动填充发布页并截图 `publish_preview.png` 到商品目录。
2. 若出现滑块/风控，按页面提示人工处理后再点“已处理，继续”。
3. 测试模式不会点击发布；真实发布需把 `mode.test_mode` 改为 `false`（建议先单件验证）。

### 商品清单管理

页面下方“商品清单”面板列出全部已下载商品及其下载/发布状态，支持：

- **重置待发布**：把商品状态改回 `PENDING`，下次“开始发布服装”会重新处理它。只影响本地队列，**不会下架闲鱼已上架商品**（如需重发请先在闲鱼手动下架旧帖）。
- **删除文件**：删除该商品的下载目录与 `data/products.json` 记录；下次运行下载任务时会重新抓取。删除前有二次确认，且任务运行中不可操作。
- 列表每 5 秒自动刷新，也可点“刷新”。

## 6. 目录说明

| 路径 | 内容 |
| --- | --- |
| `downloads/<店铺>/<商品>/` | `raw/` 原始压缩包、`images/` 解压图、`meta.json`、`description.txt`、`publish_result.json`、`publish_preview.png` |
| `data/products.json` | 全局商品清单（发布阶段唯一数据源） |
| `data/shop_plans.json` | 店铺商品计划缓存 |
| `data/login_state.json` | 账号登录冷却记录（按账号哈希，不含明文） |
| `data/reports/report_<时间戳>.json` | 每次任务结束的运行报告 |
| `data/browser_state/state.json` | 登录态（storage_state） |
| `logs/app.log`、`logs/structured.jsonl` | 运行日志（敏感信息已脱敏） |
| `logs/screenshots/` | 失败/关键步骤截图 |

## 7. 常见问题

- **提示“登录冷却中”**：同一账号 30 分钟内只允许登录一次，等待冷却结束再运行。
- **店铺页被跳回登录页**：视为风控信号，任务会直接停止转人工，不会自动重登；请冷却或更换账号/网络。
- **没有可下载商品**：说明前 5 家店的商品都已下载完成，属于正常结束。
- **Edge 启动失败**：确认本机已安装 Edge；必要时删除 `data/edge-profile-*` 下的 `lockfile` 后重试（会丢失该目录登录态）。
- **发布页识别不到所在地**：页面已有默认地址时视为通过；无候选会在日志告警。

## 8. 故障注入自测

```powershell
.venv\Scripts\python.exe tools\fault_inject_test.py
```

覆盖断网、隐藏元素、模拟风控页三类异常，验证错误日志、截图与转人工行为。

## 9. 合规提示

- 仅本机运行，默认绑定 `127.0.0.1`。
- 不绕过验证码/滑块/风控，不伪造设备指纹。
- 采集图片的版权与平台规则由使用者自行确认。

## 10. 开源前检查（发布到 GitHub）

推送前逐项确认：

1. `.env` 与本地凭据文件不在提交列表（`.gitignore` 已忽略）；建议凭据只放 `.env`，不要额外保存明文文件。
2. `data/`、`downloads/`、`logs/` 全部被忽略——它们含登录 Cookie、本地绝对路径、商品图片、含账号/地址的截图。
3. 全文搜索手机号、密码、`sk-` 开头的 Key，确认只存在于本地 `.env`。
4. 如果曾把 `.env` 或凭据文件发给别人/上传过网盘，**立即轮换 DeepSeek Key 与平台密码**。
5. 第一次推送建议先建 **Private** 仓库，检查 `git status`/网页端文件列表无敏感项后再转 Public。
6. 按需添加 `LICENSE`（如 MIT）与仓库描述；不要上传含个人信息的截图作为演示图。
