import { escapeHtml, formatPosition, typeLabel } from "../formatters.js";

export function renderUnitTable(state, filter = "ALL") {
  const units = [state?.core, ...(state?.units || [])].filter(Boolean).filter((unit) => filter === "ALL" || String(unit.unitType || unit.kind).toUpperCase() === filter);
  if (!units.length) return `<div class="empty-state"><div><strong>没有可显示的单位</strong>运行后会列出当前受控 Core 与 Units。</div></div>`;
  return `<div class="unit-table-wrap"><table class="unit-table"><thead><tr><th>ID</th><th>类型</th><th>坐标</th><th>HP</th><th>Cargo</th><th>当前动作</th><th>风险</th><th>状态</th></tr></thead><tbody>${units.map((unit) => {
    const hp = Number(unit.hp || 0); const max = Number(unit.maxHp || ({CORE:5,WORKER:2,VANGUARD:4,RANGER:2}[String(unit.unitType || unit.kind).toUpperCase()] || Math.max(1,hp)));
    return `<tr data-entity-id="${escapeHtml(unit.id || "")}" tabindex="0"><td class="accent mono">${escapeHtml(unit.id || "CORE")}</td><td>${typeLabel(unit.unitType || unit.kind)}</td><td class="mono">${formatPosition(unit.position)}</td><td>${hp} / ${max}<span class="hp-track"><span style="width:${Math.min(100,hp/max*100)}%"></span></span></td><td>${escapeHtml(unit.cargo ?? "—")}</td><td>${escapeHtml(unit.currentAction || "WAIT")}</td><td class="${unit.risk > 0 ? "danger" : "accent"}">${escapeHtml(unit.risk ?? 0)}</td><td class="good">${escapeHtml(unit.state || "ACTIVE")}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}
