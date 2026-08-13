import { escapeHtml, timeLabel } from "../formatters.js";

export function renderEvents(events) {
  if (!events?.length) return `<div class="empty-state"><div><strong>等待实时事件</strong>已提交计划和解析结果会按 seq 顺序出现。</div></div>`;
  return `<ul class="event-list">${[...events].reverse().slice(0, 40).map((event) => {
    const type = event.eventType || event.type || "event";
    const danger = /error|failed|threat|damage/i.test(type) ? "danger" : /warn|gap|pause/i.test(type) ? "warn" : "";
    return `<li class="event-row"><span class="mono muted">${timeLabel(event.createdAt)}</span><span class="event-mark ${danger}">${danger === "danger" ? "!" : danger === "warn" ? "△" : "✓"}</span><span>${escapeHtml(type)}</span><span class="mono muted">Tick ${escapeHtml(event.tick ?? "—")}</span></li>`;
  }).join("")}</ul>`;
}
