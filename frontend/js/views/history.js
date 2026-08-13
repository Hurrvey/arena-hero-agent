import { escapeHtml } from "../formatters.js";

export function renderHistory(root, series) {
  const points = series?.points || []; const markers = series?.markers || [];
  root.innerHTML = `<section class="page-heading"><div><span class="eyebrow">AUTHORITATIVE TICK SERIES</span><h1>运行历史</h1><p>横轴按实际 Tick 离散分布；断线或暂停造成的间隔不会被压平。</p></div></section><section class="panel history-panel"><header class="panel-header"><h2 class="panel-title">资源 / 人口 / Beacon</h2><span class="mono muted">${points.length} 个采样点</span></header>${points.length ? historySvg(points, markers) : `<div class="empty-state"><div><strong>还没有历史指标</strong>Agent 处理权威 Turn 后会自动写入。</div></div>`}<div class="marker-list">${markers.map((marker) => `<span><strong>Tick ${escapeHtml(marker.tick)}</strong>${escapeHtml(marker.eventType)}</span>`).join("")}</div><div id="history-detail" class="history-detail muted">选择图上的 Tick 查看对应权威快照与计划。</div></section>`;
}

function historySvg(points, markers) {
  const width = 1000; const height = 280; const padding = 48;
  const ticks = points.map((point) => Number(point.tick)); const minTick = Math.min(...ticks); const maxTick = Math.max(...ticks);
  const values = points.flatMap((point) => [Number(point.resources || 0), Number(point.population || 0)]); const maxValue = Math.max(1, ...values);
  const x = (tick) => padding + ((tick - minTick) / Math.max(1, maxTick - minTick)) * (width - padding * 2);
  const y = (value) => height - padding - (Number(value || 0) / maxValue) * (height - padding * 2);
  const path = (field) => points.map((point, index) => `${index ? "L" : "M"}${x(point.tick)},${y(point[field])}`).join(" ");
  return `<div class="history-chart-wrap"><svg id="history-chart" class="history-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="离散 Tick 历史图"><path class="history-line resources" d="${path("resources")}"></path><path class="history-line population" d="${path("population")}"></path>${points.map((point) => `<g class="history-select" data-tick="${escapeHtml(point.tick)}" tabindex="0" role="button"><circle class="history-point" cx="${x(point.tick)}" cy="${y(point.resources)}" r="6"></circle><text x="${x(point.tick)}" y="${height - 14}" text-anchor="middle">Tick ${escapeHtml(point.tick)}</text></g>`).join("")}${markers.map((marker) => `<path class="history-marker" d="M${x(marker.tick)},${padding}V${height-padding}"></path>`).join("")}</svg></div>`;
}

export function installHistory(root, api) {
  const select = async (tick) => {
    const detail = root.querySelector("#history-detail");
    detail.textContent = `正在读取 Tick ${tick}…`;
    try {
      const snapshot = await api.historyTick(tick);
      const actions = snapshot.plan?.explanation?.actions?.length || 0;
      detail.textContent = `Tick ${tick} · 资源 ${snapshot.state?.resources ?? "—"} · 人口 ${snapshot.state?.population ?? snapshot.state?.units?.length ?? "—"} · ${actions} 个计划动作`;
    } catch (error) { detail.textContent = `Tick ${tick} 已不在保留窗口：${error.message}`; }
  };
  root.querySelectorAll(".history-select").forEach((item) => {
    item.addEventListener("click", () => select(item.dataset.tick));
    item.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) select(item.dataset.tick); });
  });
}
