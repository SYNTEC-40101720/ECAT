// 数字 I/O 页：DI 监视 + DO 开关（通道控件按 snapshot 动态生成）。
import type { Snapshot } from "../api/types";
import { channelRange, maskToHex } from "../logic/gates";

interface IoPageProps {
  snap: Snapshot | null;
  send: (command: string, body?: Record<string, unknown>) => void;
}

export function IoPage({ snap, send }: IoPageProps) {
  const hasDigitalIo =
    snap?.hasDigitalIo ?? (snap?.deviceType === "digital_io" || snap?.deviceType === "mixed");
  const ioConnected = snap?.ioConnected ?? (hasDigitalIo && Boolean(snap?.connected));
  const inputChannels = Math.max(0, snap?.ioInputChannels ?? 0);
  const outputChannels = Math.max(0, snap?.ioOutputChannels ?? 0);
  const outputEnabled = hasDigitalIo && ioConnected && snap?.state !== "ERROR";
  const deviceName = snap?.ioDevice || snap?.device || "数字 I/O";
  const inputMaskHex = maskToHex(snap?.ioInputMaskHex ?? snap?.ioInputMask ?? 0, inputChannels);
  const outputMaskHex = maskToHex(snap?.ioOutputMaskHex ?? snap?.ioOutputMask ?? 0, outputChannels);
  const inputMask = BigInt(snap?.ioInputMask ?? 0);
  const outputMask = BigInt(snap?.ioOutputMask ?? 0);

  return (
    <section className="page io-page" aria-labelledby="ioPageTitle">
      <div className="io-summary">
        <div className="io-summary-copy">
          <span className="section-kicker">DIGITAL I/O</span>
          <h2 id="ioPageTitle">{deviceName}</h2>
          <p aria-live="polite">
            {hasDigitalIo
              ? snap?.message || "等待数字 I/O 周期。"
              : "当前设备不是数字 I/O。"}
          </p>
        </div>
        <div className="io-readout">
          <span>输入掩码</span>
          <strong>{inputMaskHex}</strong>
          <small>{channelRange("DI", inputChannels)}</small>
        </div>
        <div className="io-readout">
          <span>输出掩码</span>
          <strong>{outputMaskHex}</strong>
          <small>{channelRange("DO", outputChannels)}</small>
        </div>
        <div className="io-readout io-readout-status">
          <span>过程数据 WKC</span>
          <strong>{snap?.wkc ?? 0} / {snap?.expectedWkc ?? 0}</strong>
          <small>{deviceName}</small>
        </div>
      </div>
      <div className="grid-2 io-layout">
        <div className="card io-card">
          <div className="card-head">
            <h2>数字输入</h2>
            <span className="badge" data-off={hasDigitalIo && ioConnected ? "0" : "1"}>
              {hasDigitalIo && ioConnected ? "实时" : "等待"}
            </span>
          </div>
          <div className="card-body">
            <div className="io-channel-grid" aria-label={`${inputChannels} 路数字输入`}>
              {Array.from({ length: inputChannels }, (_, channel) => {
                const active = ((inputMask >> BigInt(channel)) & 1n) === 1n;
                return (
                  <div key={channel} className="io-channel"
                    aria-label={`DI ${String(channel).padStart(2, "0")} ${active ? "ON" : "OFF"}`}>
                    <span className="io-channel-label">DI {String(channel).padStart(2, "0")}</span>
                    <span className="io-channel-name">输入 {channel}</span>
                    <span className="io-signal" data-active={active ? "1" : "0"} aria-hidden="true" />
                    <span className="io-signal-state" data-state={active ? "on" : "off"}>
                      {active ? "ON" : "OFF"}
                    </span>
                  </div>
                );
              })}
            </div>
            <p className="hint">输入状态来自 EtherCAT TxPDO。</p>
          </div>
        </div>
        <div className="card io-card">
          <div className="card-head">
            <h2>数字输出</h2>
            <span className="badge" data-off={outputEnabled ? "0" : "1"}>
              {outputEnabled ? "可控" : "禁止"}
            </span>
          </div>
          <div className="card-body">
            <div className="io-channel-grid" aria-label={`${outputChannels} 路数字输出`}>
              {Array.from({ length: outputChannels }, (_, channel) => {
                const checked = ((outputMask >> BigInt(channel)) & 1n) === 1n;
                return (
                  <label key={channel} className="io-channel io-output-channel"
                    aria-label={`DO ${String(channel).padStart(2, "0")}`}>
                    <span className="io-channel-label">DO {String(channel).padStart(2, "0")}</span>
                    <span className="io-channel-name">输出 {channel}</span>
                    <span className="io-output-control">
                      <input
                        type="checkbox"
                        className="io-output-input"
                        checked={checked}
                        disabled={!outputEnabled}
                        onChange={(e) =>
                          send("set_output", { channel, enabled: e.target.checked })
                        }
                      />
                      <span className="io-output-track" aria-hidden="true" />
                    </span>
                  </label>
                );
              })}
            </div>
            <p className="hint io-safety">
              输出会在页面断开、程序退出或点击停止后归零。
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}