import { formatPosition, typeLabel } from "../formatters.js";

export function updateMapDescription(element, state) {
  const units = state?.units || [];
  const enemies = state?.visibleEnemies || state?.visible_enemies || [];
  const beacon = state?.beacon;
  const parts = [`友军 ${units.length}，当前可见敌军 ${enemies.length}`];
  if (beacon) parts.push(`Beacon ${beacon.status || "未知"}，坐标 ${formatPosition(beacon.position)}`);
  element.textContent = parts.join("；");
}

export function entityAccessibleLabel(entity) {
  return `${typeLabel(entity.unitType || entity.kind)} ${entity.id || ""} ${formatPosition(entity.position)}`;
}
