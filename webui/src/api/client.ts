// WS 客户端：连接 /api/v1/events（启动 URL 携带 ?token=），指数退避重连。
// 命令只发不等待 ack：界面状态由 server 推送的 snapshot 驱动，错误回显由
// lastError 承接。断线期间**不缓存补发**运动命令——重连后重新申请控制权，
// 一切动作须由用户显式重新触发（安全纪律，与旧实现 stopAllMotion 一致）。
import { useCallback, useEffect, useRef, useState } from "react";
import type { Adapter, ServerMessage, Snapshot } from "./types";

const BACKOFF_BASE_MS = 1500;
const BACKOFF_MAX_MS = 10_000;

export interface Connection {
  connected: boolean;
  hasControl: boolean;
  snapshot: Snapshot | null;
  adapters: Adapter[];
  adaptersReady: boolean;
  lastError: string;
  sendCommand: (command: string, body?: Record<string, unknown>) => void;
}

export function wsUrl(): string {
  const params = new URLSearchParams(location.search);
  const token = params.get("token");
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const suffix = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${protocol}//${location.host}/api/v1/events${suffix}`;
}

export function useEventStream(
  onMessage?: (message: ServerMessage) => void,
): Connection {
  const [connected, setConnected] = useState(false);
  const [hasControl, setHasControl] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [adapters, setAdapters] = useState<Adapter[]>([]);
  const [adaptersReady, setAdaptersReady] = useState(false);
  const [lastError, setLastError] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    let closed = false;
    let attempt = 0;
    let timer: number | undefined;

    const connect = () => {
      if (closed) return;
      const socket = new WebSocket(wsUrl());
      socketRef.current = socket;

      socket.onopen = () => {
        attempt = 0;
        setConnected(true);
        setLastError("");
        socket.send(JSON.stringify({ command: "acquire_control" }));
        socket.send(JSON.stringify({ command: "list_adapters" }));
      };
      socket.onmessage = (event) => {
        let message: ServerMessage;
        try {
          message = JSON.parse(event.data as string) as ServerMessage;
        } catch {
          return;
        }
        switch (message.type) {
          case "adapters":
            setAdapters(message.data ?? []);
            setAdaptersReady(true);
            break;
          case "state":
            setSnapshot(message.data);
            break;
          case "control":
            setHasControl(message.owned === true);
            // 控制权释放后重新申请：持有者断开时服务器广播
            // available:true，此时本客户端应立即重新 acquire，否则所有
            // 控件会一直禁用到手动刷新页面。
            if (message.available === true && message.owned !== true) {
              socket.send(JSON.stringify({ command: "acquire_control" }));
            }
            break;
          case "error":
            setLastError(message.error);
            break;
          default:
            break;
        }
        handlerRef.current?.(message);
      };
      socket.onclose = () => {
        setConnected(false);
        setHasControl(false);
        setAdaptersReady(false);
        if (closed) return;
        const delay = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt);
        attempt += 1;
        timer = window.setTimeout(connect, delay);
      };
      socket.onerror = () => socket.close();
    };

    connect();
    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      socketRef.current?.close();
    };
  }, []);

  const sendCommand = useCallback(
    (command: string, body?: Record<string, unknown>) => {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      socket.send(JSON.stringify({ command, ...(body ?? {}) }));
    },
    [],
  );

  return {
    connected,
    hasControl,
    snapshot,
    adapters,
    adaptersReady,
    lastError,
    sendCommand,
  };
}