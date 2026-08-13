import { escapeHtml, timeLabel } from "../formatters.js";

export function renderEvents(events) {
  if (!events?.length) return `<div class="empty-state"><div><strong>等待实时事件</strong>已提交计划和解析结果会按 seq 顺序出现。</div></div>`;
  const rows = events.flatMap(expandResolutionEvent);
  return `<ul class="event-list">${[...rows].reverse().slice(0, 40).map((event) => {
    const type = event.type || "event";
    const danger = /error|failed|threat|damage/i.test(type) ? "danger" : /warn|gap|pause/i.test(type) ? "warn" : "";
    return `<li class="event-row"><span class="mono muted">${timeLabel(event.at)}</span><span class="event-mark ${danger}">${danger === "danger" ? "!" : danger === "warn" ? "△" : "✓"}</span><span>${escapeHtml(event.label || type)}</span><span class="mono muted">Tick ${escapeHtml(event.tick ?? "—")}</span></li>`;
  }).join("")}</ul>`;
}

function expandResolutionEvent(event) {
  if (event?.type !== "resolution.results" || !Array.isArray(event.payload?.events)) return [event];
  return event.payload.events.map((result) => ({
    ...event,
    tick: result.planTick ?? event.tick,
    type: result.eventType || "resolution.result",
    label: resolutionLabel(result),
  }));
}

function resolutionLabel(event) {
  const id = event.actorId || event.targetId || event.shortId || "对象";
  const position = Array.isArray(event.position) ? ` → (${event.position[0]}, ${event.position[1]})` : "";
  const amount = Number(event.values?.amount || 0);
  const labels = {
    HARVEST_SUCCEEDED: `${id} 采集成功${amount ? ` +${amount}` : ""}`,
    DEPOSIT_SUCCEEDED: `${id} 提交资源${amount ? ` +${amount}` : ""}`,
    UNIT_MOVE_SUCCEEDED: `${id} 移动成功${position}`,
    SHOT_HIT: `${id} 射击命中 ${event.targetId || "目标"}`,
    UNIT_DAMAGED: `${event.targetId || id} 受到 ${event.values?.damage ?? "?"} 伤害`,
    CORE_DAMAGED: `Core 受到 ${event.values?.damage ?? "?"} 伤害`,
    BEACON_PICKED_UP: `${id} 拾取 Beacon`,
  };
  return labels[event.eventType] || `${id} ${event.eventType || "解析完成"}${position}`;
}
