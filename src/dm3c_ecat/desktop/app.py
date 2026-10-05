"""FastAPI 应用工厂：WS 端点 + 静态托管 + 健康检查。

绑定策略：``create_app()`` 自身不含传输层；launcher 决定监听地址，
默认只绑 ``localhost``（修复旧网关绑定所有接口的安全缺口）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.staticfiles import StaticFiles

from ..hmi import LOG_BUFFER
from .gateway import ControlGateway, control_payload

LOGGER = logging.getLogger("ecat_test.desktop.app")

SNAPSHOT_PERIOD = 0.2


def _ring_buffer_new_lines(last: list[str], current: list[str]) -> list[str]:
    """求环形日志缓冲两轮快照之间的新增行。

    环形缓冲的既存内容是**前缀稳定**的：上轮列表的尾部若干行会原样
    出现在本轮列表的开头（旧实现按计数切片、缓冲填满后 len 恒为
    maxlen，切片永远为空、新日志静默丢失，即为此被替换）。对齐方式
    是找 ``k`` 使 ``last[-k:] == current[:k]``，取**最小**的满足值：
    对齐偏小只会造成旧行重发（无害），对齐偏大会把新行当旧行吞掉。
    找不到非空对齐（一个周期内追加超过容量的行，或内容完全换血）则
    全量重发——同样宁可重复也不能丢。
    """
    max_k = min(len(last), len(current))
    for k in range(1, max_k + 1):
        if last[-k:] == current[:k]:
            return current[k:]
    return list(current)


def create_app(
    gateway: ControlGateway,
    static_dir: Path | None = None,
) -> FastAPI:
    clients: set[WebSocket] = set()

    async def broadcast(payload: dict[str, object]) -> None:
        if not clients:
            return
        message = json.dumps(payload, ensure_ascii=False)
        # gather 并发发送；异常的连接移出集合（断开/超时），不影响其余。
        sent_to = tuple(clients)
        results = await asyncio.gather(
            *(client.send_text(message) for client in sent_to),
            return_exceptions=True,
        )
        for client, result in zip(sent_to, results):
            if isinstance(result, BaseException):
                clients.discard(client)

    async def publisher() -> None:
        last_logs = list(LOG_BUFFER)
        while True:
            await broadcast({"type": "state", "data": gateway.runtime.snapshot()})
            current_logs = list(LOG_BUFFER)
            for line in _ring_buffer_new_lines(last_logs, current_logs):
                await broadcast({"type": "log", "data": line})
            last_logs = current_logs
            await asyncio.sleep(SNAPSHOT_PERIOD)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(publisher())
        yield
        task.cancel()

    app = FastAPI(
        title="SYNTEC ECAT Test",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/api/v1/system/health")
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "product": "SYNTEC-ECAT-Test",
            "state": gateway.runtime.snapshot().get("state", ""),
        }

    @app.websocket("/api/v1/events")
    async def events(connection: WebSocket) -> None:
        if not gateway.ws_authorized(connection.query_params.get("token")):
            await connection.close(code=1008)
            return
        await connection.accept()
        clients.add(connection)
        LOGGER.info("HMI client connected")
        try:
            await connection.send_text(
                json.dumps({"type": "adapters", "data": gateway.adapter_payload()})
            )
            await connection.send_text(
                json.dumps({"type": "state", "data": gateway.runtime.snapshot()})
            )
            await connection.send_text(
                json.dumps(control_payload(gateway.control_owner, connection))
            )
            while True:
                raw = await connection.receive_text()
                try:
                    command = json.loads(raw)
                    response = await gateway.handle_command(command, connection)
                except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                    response = {"type": "error", "error": str(exc)}
                await connection.send_text(json.dumps(response, ensure_ascii=False))
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(connection)
            if gateway.release_owner(connection):
                # stop() 走一次 EtherCAT 收发（安全帧），放线程池避免
                # 阻塞同一事件循环上的其余连接。
                await asyncio.to_thread(gateway.runtime.stop)
                LOGGER.info("Control client disconnected; runtime stopped")
            await broadcast(control_payload(gateway.control_owner))

    if static_dir is not None:
        index_file = static_dir / "index.html"
        if not index_file.is_file():
            raise RuntimeError(
                f"web UI build output is missing: {index_file}. "
                "Run 'npm run build' in webui/ first."
            )
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="webui")

    return app