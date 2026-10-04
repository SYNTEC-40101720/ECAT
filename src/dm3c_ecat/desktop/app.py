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


def create_app(
    gateway: ControlGateway,
    static_dir: Path | None = None,
) -> FastAPI:
    clients: set[WebSocket] = set()

    async def broadcast(payload: dict[str, object]) -> None:
        # 单次快照客户端集合：gather 期间断开的连接由各自 send 的异常自行
        # 退出，不再用 zip 二次配对（旧实现的错位 bug 在此消除）。
        if not clients:
            return
        message = json.dumps(payload, ensure_ascii=False)
        for client in tuple(clients):
            try:
                await client.send_text(message)
            except Exception:
                clients.discard(client)

    async def publisher() -> None:
        last_log_count = len(LOG_BUFFER)
        while True:
            await broadcast({"type": "state", "data": gateway.runtime.snapshot()})
            current_logs = list(LOG_BUFFER)
            if len(current_logs) < last_log_count:
                last_log_count = 0
            for line in current_logs[last_log_count:]:
                await broadcast({"type": "log", "data": line})
            last_log_count = len(current_logs)
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
                gateway.runtime.stop()
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