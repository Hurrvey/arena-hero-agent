import { escapeHtml } from "../formatters.js";

export function renderRuntimeHeader(runtime, connection) {
  const status = runtime?.status || "STOPPED";
  const labels = {
    STOPPED: "已停止", STARTING: "启动中", RUNNING: "运行中", PAUSED: "已暂停",
    STOPPING: "停止中", ERROR: "错误",
  };
  const className = status === "RUNNING" ? "running" : status === "PAUSED" ? "paused" : status === "ERROR" ? "error" : "";
  return `
    <span class="status-dot ${className}" aria-hidden="true"></span>
    <span class="status-label ${status === "ERROR" ? "danger" : ""}">${labels[status] || status}</span>
    <span class="runtime-divider"></span>
    <span>Tick&nbsp; <strong class="tick-value">${escapeHtml(runtime?.lastTick ?? "—")}</strong></span>
    <span class="runtime-divider"></span>
    <span class="strategy-label">连接 <strong>${escapeHtml(connection?.status || "CONNECTING")}</strong></span>`;
}
