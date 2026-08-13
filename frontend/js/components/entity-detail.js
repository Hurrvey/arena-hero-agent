import { escapeHtml, formatPosition, typeLabel } from "../formatters.js";

export function renderEntityDetail(entity) {
  if (!entity) return "";
  return `<h3>${escapeHtml(entity.id || entity.shortId || typeLabel(entity.unitType || entity.kind))}</h3><div class="muted">${typeLabel(entity.unitType || entity.kind)}</div><p>坐标 <span class="mono">${formatPosition(entity.position)}</span></p><p>HP ${escapeHtml(entity.hp ?? "—")} · Shield ${escapeHtml(entity.shield ?? "—")} · Cargo ${escapeHtml(entity.cargo ?? "—")}</p>`;
}
