# ECAT Test · Web HMI 设计交付

> Electron 桌面 HMI（Python 后端 `Runtime` 复用 + WebSocket 通信 + 现代 CSS 设计系统），深浅双主题可切换。

## 决策背景
项目不再使用 PySide6。Electron 负责桌面窗口，Python `Runtime` 通过 WebSocket
提供硬件状态和控制命令。

## 架构（当前实现）
- **后端** `src/dm3c_ecat/websocket_hmi.py`
  - 复用 `Runtime`；`snapshot()` 返回接口、驱动能力、统一运动状态和 HM/CSP 状态字段。
  - 监听 `ws://127.0.0.1:8765`，复用 `hmi.py` 的 `Runtime`。
  - JSON 消息支持 `state`、`log`、`adapters`、`control`、`ack`、`error`；命令覆盖 PV/PP/HM/CSP。
  - 只允许一个控制客户端；观察者只能接收状态，控制者释放或断开时安全停止。
  - Electron 关闭时先发送带随机令牌的 shutdown 请求，后端超时才由主进程强杀。
- **Electron** `electron/main.cjs`
  - 开发态启动 Python WebSocket 子进程；packaged 态启动随包后端，并加载页面。
- **前端** `src/dm3c_ecat/web/`
  - `index.html` — 侧边状态栏 + 顶部状态条 + 模式目录 + 单页控制区。
  - `styles.css` — 设计令牌（浅色默认 / `[data-theme="dark"]` 覆盖），统一圆角、间距、阴影、字体。
  - `app.js` — WebSocket 渲染、能力过滤、PV/PP/HM/CSP 控制、按住 Jog 和安全停止。
- **模式元数据** `src/dm3c_ecat/motion_modes.py`、`device_profiles.py`
  - 集中定义 CiA 402 模式值和 RxPDO/过程镜像长度，避免只改 `0x6060` 就误用其它模式的 PDO。

## 设计系统（双主题）
- **色彩令牌**：`--bg/--surface/--border/--text/--text-muted/--primary/--success/--warning/--danger` 等，深/浅各一套，对比度满足 WCAG AA。
- **间距**：4px 基数（4/8/12/16/20/24/32/40）。
- **圆角**：8/12/16px + 药丸；**阴影** sm/md 两档。
- **字体**：Segoe UI / Microsoft YaHei / 系统中文回退；等宽 `JetBrains Mono` 用于数值与日志。
- **可访问性**：触控目标 ≥44px（Jog 按钮 110px 高）、`:focus-visible` 焦点环、`prefers-reduced-motion` 关闭动画、状态用颜色+文字双重表达。

## 当前页面功能
1. **连接与状态** — 本地连接指示、接口、运行状态、使能状态和 WKC。
2. **CiA 402 模式目录** — 展示 PP、VM、PV、PT、HM、IP、CSP、CSV、CST；只有当前 profile 有完整 PDO 元数据的模式可操作。
3. **PV/PP** — 左侧设置速度和加减速时间，右侧填写目标位置并执行；PP 支持绝对/相对位置。
4. **HM/CSP** — HM 使用专属回零方法、速度、加速时间和偏置；CSP 使用目标位置与轨迹时间，按 10 ms 周期发送。
5. **不可用模式** — VM、PT、IP、CSV、CST 当前因本地 ESI 未确认完整映射而保留目录入口并禁用。
6. **模式保护** — 只有未使能且无运动时才能切换模式；失焦、隐藏、卸载、断开和对应心跳超时会停止运动。

## 安全行为（沿用后端）
窗口失焦 / 心跳超时（0.35s）→ 停止当前运动并保持使能；驱动故障 / WKC 异常 / 程序退出 → 停止并禁能。前端在 `blur`、`beforeunload`、`visibilitychange` 时也主动发送停止运动命令。HM/CSP 也有独立运动看门狗。

## 验证与边界
- 软件验证：WebSocket 命令、控制权、安全停止、Runtime 模拟过程数据和 JavaScript 静态检查。
- 发布配置和 Python 后端产物验证已完成；Electron 安装包因下载 Electron `38.8.6` 网络超时未完成，因此 packaged GUI 烟雾测试和域控安装验收未完成。
- 真实浏览器待验证：桌面/移动视口、控制权交接、失焦/隐藏/卸载和关闭窗口时的实际停止响应。
- 真实网卡与设备待验证：过程帧送达、WKC、驱动/I/O/焊机动作及安全链路。不能将软件测试表述为硬件验收。
- 运行入口：`py start_ecat_test.py` 或 `npm start`；不再存在 `/api/*`、SSE 或独立 HTTP HMI。

## 文件清单
- `src/dm3c_ecat/hmi.py`（Runtime 与实时周期）
- `src/dm3c_ecat/web/index.html`
- `src/dm3c_ecat/web/styles.css`
- `src/dm3c_ecat/web/app.js`

建议走查：在设置中确认网卡 → 选择已确认的 PV/PP/HM/CSP 模式 → 低速短时测试 → 松开或执行停止 → 查看状态和日志 → 切换深色主题确认可读性。HM/CSP 首次真实运动前必须确认驱动手册、限位、原点、STO 和急停链路。
