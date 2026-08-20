# ECAT Test · Web HMI 设计交付

> Electron 桌面 HMI（Python 后端 `Runtime` 复用 + WebSocket 通信 + 现代 CSS 设计系统），深浅双主题可切换。

## 决策背景
项目不再使用 PySide6。Electron 负责桌面窗口，Python `Runtime` 通过 WebSocket
提供硬件状态和控制命令。

## 架构
- **后端** `src/dm3c_ecat/websocket_hmi.py`
  - `Runtime` 原样复用（`snapshot()` 新增返回 `interface` 字段）。
  - 监听 `ws://127.0.0.1:8765`，复用 `hmi.py` 的 `Runtime`。
  - JSON 消息支持 `state`、`log`、`adapters`、`ack`、`error`。
  - WebSocket 断开时停止运动；Electron 关闭时终止 Python 后端。
- **Electron** `electron/main.cjs`
  - 启动 Python WebSocket 子进程并加载 `src/dm3c_ecat/web/index.html`。
- **前端** `src/dm3c_ecat/web/`
  - `index.html` — 侧边状态栏 + 顶部状态条 + 单页控制区。
  - `styles.css` — 设计令牌（浅色默认 / `[data-theme="dark"]` 覆盖），统一圆角、间距、阴影、字体。
  - `app.js` — WebSocket 渲染、PV/PP 模式切换、PP 定位、按住 Jog 和安全停止。

## 设计系统（双主题）
- **色彩令牌**：`--bg/--surface/--border/--text/--text-muted/--primary/--success/--warning/--danger` 等，深/浅各一套，对比度满足 WCAG AA。
- **间距**：4px 基数（4/8/12/16/20/24/32/40）。
- **圆角**：8/12/16px + 药丸；**阴影** sm/md 两档。
- **字体**：Segoe UI / 系统中文回退；等宽 `JetBrains Mono` 用于数值与日志。
- **可访问性**：触控目标 ≥44px（Jog 按钮 110px 高）、`:focus-visible` 焦点环、`prefers-reduced-motion` 关闭动画、状态用颜色+文字双重表达。

## 当前页面功能
1. **连接与状态** — 本地连接指示、运行状态、使能状态和 WKC。
2. **PV 点动** — 目标速度、加减速时间、按住正/反向按钮和统一停止。
3. **PP 点位** — 绝对位置或相对位移、目标位置、轮廓速度、加减速、到位反馈。
4. **模式保护** — 只有未使能且无运动时才能切换模式；失焦、隐藏、卸载、断开和心跳超时会停止运动。

## 安全行为（沿用后端）
窗口失焦 / 心跳超时（0.35s）→ 停止当前运动并保持使能；驱动故障 / WKC 异常 / 程序退出 → 停止并禁能。前端在 `blur`、`beforeunload`、`visibilitychange` 时也主动发送停止运动命令。

## 验证
- `py_compile` 通过；用桩 `pysoem` 把服务跑起来实测：`/`、`/styles.css`、`/app.js`、`/api/status`、`/api/adapters`、`/api/stream`(SSE)、`POST /api/enable` 全部 200，`index.html` 正确引用脚本与样式；SSE 断开不再抛堆栈。
- 真实硬件需在装好 Npcap 的目标机运行：`py start_ecat_test.py`。

## 文件清单
- `src/dm3c_ecat/hmi.py`（扩展后端 + API + SSE）
- `src/dm3c_ecat/web/index.html`
- `src/dm3c_ecat/web/styles.css`
- `src/dm3c_ecat/web/app.js`

建议走查：连接确认网卡 → 勾选 4 项安全 → 打开使能 → 低速短时按住 Jog → 松开停止 → 切到诊断看指标/日志 → 切深色主题确认可读性。
