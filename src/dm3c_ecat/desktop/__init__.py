"""本地 Web 桌面壳层：FastAPI + WebSocket 网关 + pywebview/WebView2 窗口。

架构（local-web-desktop）：
- 单 Python 进程，uvicorn 线程承载 FastAPI（默认只绑回环 ``localhost``）
- 控制协议走 WS ``/api/v1/events``，随机一次性令牌鉴权，单控制客户端
- UI 为 ``webui/``（React + Vite）的编译产物 ``webui/dist``，同源静态托管
- 窗口由系统 WebView2 渲染，无 Chromium 随包
"""

APP_VERSION = "1.0.0"