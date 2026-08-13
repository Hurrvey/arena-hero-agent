import { metricCard } from "../components/metric-card.js";
import { renderPlan } from "../components/plan-status.js";
import { renderEvents } from "../components/event-list.js";
import { renderUnitTable } from "../components/unit-table.js";
import { escapeHtml, formatNumber } from "../formatters.js";

export function renderOverview(root, snapshot) {
  const state = snapshot.state || {};
  const core = state.core || {};
  const population = state.population ?? state.units?.length ?? 0;
  const capacity = state.resourceCapacity ?? state.resource_capacity ?? "—";
  const beaconStatus = state.beacon?.status || "未知";
  root.innerHTML = `
    <section class="metrics-grid" aria-label="实时指标">
      ${metricCard({label:"资源", value:formatNumber(state.resources), unit:`/ ${capacity}`, icon:"metric-resource"})}
      ${metricCard({label:"人口", value:formatNumber(population), icon:"metric-population"})}
      ${metricCard({label:"Core", value:formatNumber(core.hp), unit:`HP · ${formatNumber(core.shield)} Shield`, icon:"metric-shield"})}
      ${metricCard({label:"Beacon", value:escapeHtml(beaconStatus), icon:"map-beacon", color:"var(--beacon)"})}
      ${metricCard({label:"威胁", value:escapeHtml(state.defenseLevel || state.threat || "CLEAR"), icon:"status-danger", color:state.defenseLevel === "CLEAR" ? "var(--success)" : "var(--danger)"})}
    </section>
    <section class="operations-grid">
      <article class="panel map-panel">
        <header class="panel-header"><h2 class="panel-title">实时战术地图</h2><span class="mono muted">Tick ${escapeHtml(state.tick ?? snapshot.runtime.lastTick ?? "—")}</span></header>
        <div class="map-stage"><canvas id="tactical-map" tabindex="0" aria-describedby="map-description"></canvas><p id="map-description" class="sr-only"></p><div id="map-stale" class="fog-stale">连接中断 · 显示最后快照</div><div class="map-tools"><button class="map-tool" data-map="home" aria-label="地图复位">⌖</button><button class="map-tool" data-map="in" aria-label="放大">＋</button><button class="map-tool" data-map="out" aria-label="缩小">−</button></div><div class="map-legend"><span><i class="legend-dot" style="background:var(--friendly)"></i>友军</span><span><i class="legend-dot" style="background:var(--danger)"></i>当前敌军</span><span><i class="legend-dot" style="background:var(--success)"></i>资源</span><span><i class="legend-dot" style="background:var(--beacon)"></i>Beacon</span></div><aside id="entity-detail" class="entity-detail" hidden></aside></div>
      </article>
      <div class="right-stack"><article class="panel"><header class="panel-header"><h2 class="panel-title">当前计划</h2><span class="mono muted">${escapeHtml(snapshot.plan?.status || "DRAFT")}</span></header><div id="plan-panel">${renderPlan(snapshot.plan)}</div></article><article class="panel"><header class="panel-header"><h2 class="panel-title">实时事件</h2><span class="mono muted">SEQ ${escapeHtml(snapshot.lastSeq)}</span></header><div id="event-panel">${renderEvents(snapshot.events)}</div></article></div>
    </section>
    <section class="panel roster-panel"><header class="panel-header"><h2 class="panel-title">单位状态</h2><div class="tabs" role="tablist"><button class="tab active" data-filter="ALL">全部</button><button class="tab" data-filter="WORKER">Worker</button><button class="tab" data-filter="VANGUARD">Vanguard</button><button class="tab" data-filter="RANGER">Ranger</button></div></header><div id="unit-table">${renderUnitTable(state)}</div></section>`;
}
