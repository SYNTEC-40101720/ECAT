# ECAT Test Electron HMI — UI 设计方案

> Electron 桌面 HMI 设计方案。Python 只负责 EtherCAT Runtime 与 WebSocket 通信。
> 当前页面是面向伺服 Jog 操作员的单页控制台，集中提供 PV 点动、PP 点位和实时状态。

## 1. 设计基础（Design Foundations）

### 色彩系统（浅色，WCAG AA 对比度 ≥ 4.5:1）
| 角色 | 值 | 用途 |
|---|---|---|
| 页面背景 `--page` | `#eef2f7` | 窗口底色 |
| 表面 `--surface` | `#ffffff` | 卡片/面板 |
| 描边 `--border` | `#dde3ec` | 默认分隔线 |
| 文本 `--text` | `#1f2937` | 主文本 |
| 主色 `--primary` | `#2563eb` | 正向 Jog / 主操作 |
| 成功 `--success` | `#15a34a` | 已使能 / 正常 |
| 警示 `--warning` | `#d97706` | 使能中 / 注意 |
| 危险 `--danger` | `#dc2626` | 停止 / 故障 / 急停 |

### 字体与圆角
- 字体栈：`"Segoe UI", "Microsoft YaHei", system-ui, sans-serif`；字号 12/13/14/16/18/22/26。
- 圆角：卡片 12px、按钮 8px、Jog 大按钮 12px、药丸 999px。
- 间距基准 4px，序列 4/8/12/16/20/24/32。

## 2. 前端组件
`index.html` 提供页面结构，`styles.css` 提供设计令牌和组件样式，`app.js` 提供模式切换、
状态渲染、PP 定位、按住 Jog、安全停止和 WebSocket 重连。

## 3. 当前控制台
1. **连接与状态** — 显示本地 WebSocket/EtherCAT 连接、运行状态、使能状态和 EtherCAT WKC。
2. **PV 点动** — 目标速度、加减速时间、按住正/反向 Jog 和统一停止。
3. **PP 点位** — 目标位置、轮廓速度、加减速、绝对位置/相对位移选择和到位反馈。
4. **安全行为** — 只有未使能且无运动时才能切换 PV/PP；失焦、隐藏、卸载或心跳超时会停止运动。

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
