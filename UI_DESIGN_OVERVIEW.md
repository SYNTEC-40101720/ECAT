# ECAT Test Electron HMI — UI 设计方案

> Electron 桌面 HMI 设计方案。Python 只负责 EtherCAT Runtime 与 WebSocket 通信。
> 当前页面是面向伺服测试操作员的单页控制台，集中提供 CiA 402 模式目录、PV/PP/HM/CSP 控制和实时状态。

## 1. 设计基础（Design Foundations）

### 色彩系统（浅色，WCAG AA 对比度 ≥ 4.5:1）
| 角色 | 值 | 用途 |
|---|---|---|
| 页面背景 `--bg` | `#eef2f7` | 窗口底色 |
| 表面 `--surface` | `#ffffff` | 卡片/面板 |
| 描边 `--border` | `#d9e1ea` | 默认分隔线 |
| 文本 `--text` | `#1f2937` | 主文本 |
| 主色 `--primary` | `#b45309` | 正向 Jog / 主操作 |
| 成功 `--success` | `#15803d` | 已使能 / 正常 |
| 警示 `--warning` | `#a16207` | 使能中 / 注意 |
| 危险 `--danger` | `#b91c1c` | 停止 / 故障 / 急停 |

### 字体与圆角
- 字体栈：`"Segoe UI", "Microsoft YaHei", system-ui, sans-serif`；等宽数值使用 `JetBrains Mono`。
- 圆角：卡片 8px、按钮 8px、Jog 大按钮 12px、药丸 999px。
- 间距基准 4px，序列 4/8/12/16/20/24/32。

## 2. 前端组件
`index.html` 提供页面结构，`styles.css` 提供设计令牌和组件样式，`app.js` 提供模式目录、
能力过滤、PP/HM/CSP 动作、按住 Jog、安全停止和 WebSocket 重连。

## 3. 当前控制台
1. **连接与状态** — 显示本地 WebSocket/EtherCAT 连接、运行状态、使能状态和 EtherCAT WKC。
1. **模式目录** — 展示 PV、PP、VM、PT、HM、IP、CSP、CSV、CST；只有驱动 ESI 声明的 PDO 模式可切换。
2. **PV/PP** — 目标速度、位置、加减速时间、绝对/相对位置选择和到位反馈。
3. **HM/CSP** — 回零参数与周期同步位置目标分离到专用设置/动作面板。
4. **安全行为** — 只有未使能且无运动时才能切换模式；失焦、隐藏、卸载或对应心跳超时会停止运动。

## 4. 安全与可访问性
- 状态驱动：按钮可用性与配色由 `Runtime.snapshot()` 决定，杜绝误动作。
- 对比度满足 WCAG AA；交互元素最小 44px 触控目标（Jog 按钮 96px 高）。
- 失焦/心跳超时自动停止运动并保持使能；通信断开和程序退出时停止并禁能。

## 5. 如何运行
```powershell
npm install
py start_ecat_test.py             # 推荐：Electron UI + Python WebSocket 后端
# 或 npm start
```
> 需在 Windows + 已安装 Npcap + pysoem 的环境运行（本机需真实 EtherCAT 硬件验证）。

## 6. 文件清单
- `electron/main.cjs` — Electron 主进程与 Python 后端生命周期
- `src/dm3c_ecat/websocket_hmi.py` — Python WebSocket 网关
- `src/dm3c_ecat/web/` — Electron 页面、样式和交互脚本

---
**UI Designer** · 2026-08-08 · 已通过 py_compile 与组件导入校验（pysoem 为运行环境依赖）。
