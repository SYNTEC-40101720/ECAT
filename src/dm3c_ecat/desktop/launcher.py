"""桌面启动器：单实例锁 + 随机回环端口 + 一次性令牌 + uvicorn 线程。

启动顺序（skill 骨架）：
1. 单实例锁（重复启动静默退出，``ECAT_ALLOW_SECOND_INSTANCE=1`` 旁路供测试）
2. 端口：``ECAT_PORT`` 优先，否则绑回环地址让 OS 选随机空闲端口
3. 令牌：``secrets.token_urlsafe(32)``，``ECAT_LOCAL_TOKEN`` 可注入供探活
4. uvicorn 线程起 FastAPI（只绑 ``localhost``），轮询 /health 就绪
5. 浏览器态（无窗口）与桌面态（WebView2）共用同一条 WS 通道
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .app import create_app
from .gateway import ControlGateway

LOGGER = logging.getLogger("ecat_test.desktop.launcher")

LOOPBACK_HOST = "localhost"
HEALTH_TIMEOUT = 10.0


class SingleInstanceError(RuntimeError):
    """同一台机器上已有一个 ECAT Test 桌面实例在运行。"""


STATE_DIR_DEFAULT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SYNTEC-ECAT-Test"


def _acquire_lock(port_file: Path) -> None:
    port_file.parent.mkdir(parents=True, exist_ok=True)
    if port_file.is_file():
        try:
            stored = json.loads(port_file.read_text(encoding="utf-8"))
            probe_url = f"http://{LOOPBACK_HOST}:{int(stored['port'])}/api/v1/system/health"
            with urllib.request.urlopen(probe_url, timeout=1.0) as response:
                if response.status == 200:
                    raise SingleInstanceError(
                        "ECAT Test is already running on this machine"
                    )
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            pass  # 陈旧锁文件或实例已死：覆盖
    atexit.register(lambda: port_file.unlink(missing_ok=True))


def _pick_port() -> int:
    explicit = os.environ.get("ECAT_PORT")
    if explicit:
        try:
            port = int(explicit)
        except ValueError:
            raise ValueError(f"ECAT_PORT must be an integer, got {explicit!r}") from None
        if not 1 <= port <= 65535:
            raise ValueError(f"ECAT_PORT must be within 1-65535, got {port}")
        return port
    # 随机路径的 bind-to-0 存在轻微 TOCTOU（bind 与 uvicorn 实际绑定之间
    # 端口可能被抢占）；完全消除需改 uvicorn 绑定模型，收益低，接受现状。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOOPBACK_HOST, 0))
        return int(sock.getsockname()[1])


def _write_port_file(port_file: Path, port: int, token: str) -> None:
    port_file.write_text(
        json.dumps({"port": port, "token": token}), encoding="utf-8"
    )


def start_server(
    gateway: ControlGateway,
    static_dir: Path | None,
    host: str | None = None,
    port: int | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """启动 uvicorn 线程并阻塞到 /health 就绪。

    返回 ``{"host", "port", "token", "url", "server"}``；host 参数仅用于测试
    显式监听地址，产品路径恒为 ``localhost``。``state_dir`` 非空时启用单
    实例锁（重复启动在探测到存活 /health 后以退出码 3 静默退出）。
    """
    import uvicorn

    bind_host = host or LOOPBACK_HOST
    if state_dir is not None:
        _acquire_lock(state_dir / "ecat-test.port.json")
    bind_port = port if port is not None else _pick_port()
    token = os.environ.get("ECAT_LOCAL_TOKEN") or secrets.token_urlsafe(32)
    gateway.local_token = token
    # 浏览器态靠 WS shutdown 命令退出；同一令牌同时授权连接与关闭。
    gateway.shutdown_token = token
    app = create_app(gateway, static_dir=static_dir)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=bind_host,
            port=bind_port,
            log_level="warning",
            # Windows 下 uvicorn 默认信号处理与线程运行方式冲突，关闭
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    deadline = time.monotonic() + HEALTH_TIMEOUT
    health_url = f"http://{LOOPBACK_HOST}:{bind_port}/api/v1/system/health"
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("uvicorn thread exited before becoming ready")
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    else:
        raise RuntimeError("backend did not become ready within 10s")
    if state_dir is not None:
        _write_port_file(state_dir / "ecat-test.port.json", bind_port, token)
    url = f"http://{LOOPBACK_HOST}:{bind_port}/?token={token}"
    return {"host": bind_host, "port": bind_port, "token": token, "url": url, "server": server, "thread": thread}


def run_desktop(
    gateway: ControlGateway,
    static_dir: Path | None,
    *,
    open_window: bool = True,
    state_dir: Path | None = None,
    window_ready_timeout: float = 15.0,
) -> int:
    """启动后端，可选打开 WebView2 窗口；窗口关闭即退出。

    退出顺序（skill 关闭纪律）：窗口关 → gateway 等待 shutdown →
    ``runtime.stop()/close()``（含最终安全帧）→ uvicorn 停止。
    ``state_dir`` 默认为 %LOCALAPPDATA%/SYNTEC-ECAT-Test（单实例锁目录）；
    ``ECAT_ALLOW_SECOND_INSTANCE=1`` 旁路锁供冒烟测试用。
    """
    import webview  # noqa: F401  (提前失败：缺 pywebview 时窗口模式直接报错)

    resolved_state_dir = state_dir
    if resolved_state_dir is None and os.environ.get("ECAT_ALLOW_SECOND_INSTANCE") != "1":
        resolved_state_dir = STATE_DIR_DEFAULT
    try:
        server_info = start_server(gateway, static_dir, state_dir=resolved_state_dir)
    except SingleInstanceError:
        LOGGER.info("Another ECAT Test instance is running; exiting silently")
        return 3
    url = server_info["url"]
    LOGGER.info("Desktop backend listening on %s", url.split("?")[0])
    if not open_window:
        # 浏览器/无头模式：阻塞到 shutdown 信号
        asyncio.run(_wait_shutdown(gateway))
        return _shutdown(server_info, gateway)

    window = webview.create_window(
        title="SYNTEC ECAT Test",
        url=url,
        width=1440,
        height=960,
        min_size=(960, 720),
        background_color="#eef2f7",
    )

    def _on_closed() -> None:
        gateway.shutdown_event.set()

    window.events.closed += _on_closed
    webview.start(gui="edgechromium")
    return _shutdown(server_info, gateway)


async def _wait_shutdown(gateway: ControlGateway) -> None:
    while not gateway.shutdown_event.is_set():
        try:
            await asyncio.wait_for(gateway.shutdown_event.wait(), timeout=0.25)
        except (asyncio.TimeoutError, TimeoutError):
            pass


def _shutdown(server_info: dict[str, Any], gateway: ControlGateway) -> int:
    runtime = gateway.runtime
    runtime.stop()
    runtime.close()
    server = server_info["server"]
    server.should_exit = True
    # uvicorn 线程是 daemon，不 join 会拖着 server 一起被 abruptly 终止，
    # 静态连接的 WS 客户端拿不到 close 帧；3s 足够 uvicorn 完成优雅关闭。
    server_info["thread"].join(timeout=3)
    return 0
