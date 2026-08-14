import { formatPosition, typeLabel } from "../formatters.js";

export function updateMapDescription(element, state, exploration = null) {
  const units = state?.units || [];
  const enemies = state?.visibleEnemies || state?.visible_enemies || [];
  const beacon = state?.beacon;
  const current = new Set(
    (state?.visibility?.currentCells || []).map((cell) => `${cell[0]},${cell[1]}`),
  );
  const exploredDarkCount = (exploration?.exploredCells || [])
    .filter((cell) => !current.has(`${cell[0]},${cell[1]}`)).length;
  const parts = [
    `当前可见 ${current.size} 格，已探索暗区 ${exploredDarkCount} 格；已探索不代表当前安全。`,
    `友军 ${units.length}，当前可见敌军 ${enemies.length}`,
  ];
  if (beacon) parts.push(`Beacon ${beacon.status || "未知"}，坐标 ${formatPosition(beacon.position)}`);
  element.textContent = parts.join("；");
}

export function entityAccessibleLabel(entity) {
  return `${typeLabel(entity.unitType || entity.kind)} ${entity.id || ""} ${formatPosition(entity.position)}`;
}
