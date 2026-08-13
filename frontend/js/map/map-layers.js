const COLORS = {
  grid: "rgba(97, 138, 176, .16)", fog: "rgba(2, 8, 15, .72)",
  risk: "rgba(255, 77, 95, .20)", friendly: "#2d9cff", enemy: "#ff4d5f",
  resource: "#35d17c", beacon: "#ffc844", route: "#55cdfc",
};

export function drawTacticalLayers(ctx, model) {
  const { width, height, cell, assets, state, camera } = model;
  ctx.clearRect(0, 0, width, height);
  const gradient = ctx.createRadialGradient(width * .45, height * .48, 20, width * .45, height * .48, width * .72);
  gradient.addColorStop(0, "#102137"); gradient.addColorStop(1, "#030a13");
  ctx.fillStyle = gradient; ctx.fillRect(0, 0, width, height);
  drawGrid(ctx, width, height, cell);
  const entities = [];
  const center = [width / 2, height / 2];
  const toScreen = (position) => {
    const [x, y] = camera.relative(position);
    return [center[0] + x * cell * camera.zoom, center[1] + y * cell * camera.zoom];
  };
  for (const risk of state?.riskCells || []) drawCell(ctx, toScreen(risk.position), cell, COLORS.risk);
  for (const position of terrain(state, "OBSTACLE")) drawImage(ctx, assets.obstacle, toScreen(position), cell * .9);
  for (const position of terrain(state, "RESOURCE")) drawImage(ctx, assets.resource, toScreen(position), cell * .76);
  drawRoutes(ctx, state?.planRoutes || [], toScreen);
  const core = state?.core;
  if (core?.position) entities.push(drawEntity(ctx, assets.core, core, toScreen, COLORS.friendly, cell * 1.18));
  for (const unit of state?.units || []) {
    const name = String(unit.unitType || unit.unit_type || "worker").toLowerCase();
    entities.push(drawEntity(ctx, assets[name], unit, toScreen, COLORS.friendly, cell * .82));
  }
  for (const enemy of state?.visibleEnemies || state?.visible_enemies || []) {
    const name = String(enemy.unitType || enemy.unit_type || enemy.kind || "ranger").toLowerCase();
    entities.push(drawEntity(ctx, assets[name === "unit" ? "ranger" : name], enemy, toScreen, COLORS.enemy, cell * .82));
  }
  const beacon = state?.beacon;
  if (beacon?.position) {
    drawImage(ctx, assets.beacon, toScreen(beacon.position), cell);
    ctx.fillStyle = COLORS.beacon; ctx.font = "700 10px ui-monospace";
    ctx.fillText(beacon.status || "UNKNOWN", toScreen(beacon.position)[0] - 22, toScreen(beacon.position)[1] + cell * .8);
  }
  return entities.filter(Boolean);
}

function terrain(state, kind) {
  const direct = kind === "OBSTACLE" ? state?.obstacleCells : state?.resourceCells;
  if (Array.isArray(direct)) return direct;
  const objects = state?.objects || state?.terrain || [];
  return objects.filter((item) => item.kind === kind).flatMap((item) => item.positions || []);
}
function drawGrid(ctx, width, height, cell) {
  ctx.strokeStyle = COLORS.grid; ctx.lineWidth = 1; ctx.beginPath();
  for (let x = width % cell / 2; x < width; x += cell) { ctx.moveTo(x, 0); ctx.lineTo(x, height); }
  for (let y = height % cell / 2; y < height; y += cell) { ctx.moveTo(0, y); ctx.lineTo(width, y); }
  ctx.stroke();
}
function drawCell(ctx, screen, cell, fill) { ctx.fillStyle = fill; ctx.fillRect(screen[0] - cell / 2, screen[1] - cell / 2, cell, cell); }
function drawImage(ctx, image, screen, size) {
  if (image?.complete && image.naturalWidth) ctx.drawImage(image, screen[0] - size / 2, screen[1] - size / 2, size, size);
  else { ctx.fillStyle = "#7f93a9"; ctx.fillRect(screen[0] - 5, screen[1] - 5, 10, 10); }
}
function drawEntity(ctx, image, entity, toScreen, color, size) {
  if (!entity.position) return null;
  const screen = toScreen(entity.position); ctx.save(); ctx.shadowColor = color; ctx.shadowBlur = 12;
  drawImage(ctx, image, screen, size); ctx.restore();
  ctx.fillStyle = color; ctx.font = "700 10px ui-monospace"; ctx.textAlign = "center";
  ctx.fillText(entity.id || entity.shortId || "", screen[0], screen[1] + size * .72);
  return { ...entity, screen };
}
function drawRoutes(ctx, routes, toScreen) {
  ctx.save(); ctx.strokeStyle = COLORS.route; ctx.lineWidth = 2; ctx.setLineDash([7, 7]);
  for (const route of routes) {
    const points = route.points || []; if (points.length < 2) continue;
    ctx.beginPath(); points.forEach((point, index) => { const [x, y] = toScreen(point); index ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke();
  }
  ctx.restore();
}
