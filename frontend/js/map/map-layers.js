const COLORS = {
  grid: "rgba(97, 138, 176, .16)",
  unknown: "rgba(1, 4, 8, .98)",
  explored: "rgba(0, 10, 28, .66)",
  exploredGrid: "rgba(59, 170, 224, .38)",
  visible: "rgba(37, 111, 153, .10)",
  visibleGrid: "rgba(85, 205, 252, .30)",
  frontier: "rgba(85, 205, 252, .72)",
  risk: "rgba(255, 77, 95, .20)",
  friendly: "#2d9cff",
  enemy: "#ff4d5f",
  resource: "#35d17c",
  beacon: "#ffc844",
  route: "#55cdfc",
};

const CARDINALS = [[0, -1], [1, 0], [0, 1], [-1, 0]];

export function positionKey(position) {
  return `${position[0]},${position[1]}`;
}

export function visibleMapCells(state) {
  return new Set((state?.visibility?.currentCells || []).map(positionKey));
}

export function classifyFogCell(position, current, explored) {
  const key = positionKey(position);
  if (current.has(key)) return "VISIBLE";
  if (explored.has(key)) return "EXPLORED";
  return "UNKNOWN";
}

export function drawTacticalLayers(ctx, model) {
  const {
    width,
    height,
    cell,
    assets,
    state,
    camera,
    bounds,
    exploredCells = [],
    knownObstacleCells = [],
  } = model;
  ctx.clearRect(0, 0, width, height);
  const gradient = ctx.createRadialGradient(
    width * .45,
    height * .48,
    20,
    width * .45,
    height * .48,
    width * .72,
  );
  gradient.addColorStop(0, "#102137");
  gradient.addColorStop(1, "#030a13");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);
  drawGrid(ctx, width, height, cell);

  const entities = [];
  const center = [width / 2, height / 2];
  const scaledCell = cell * camera.zoom;
  const toScreen = (position) => {
    const [x, y] = camera.relative(position);
    return [center[0] + x * scaledCell, center[1] + y * scaledCell];
  };
  const current = visibleMapCells(state);
  const explored = new Set(exploredCells.map(positionKey));
  if (bounds) drawFog(ctx, bounds, current, explored, toScreen, scaledCell);

  ctx.save();
  ctx.globalAlpha = .45;
  for (const position of knownObstacleCells) {
    drawImage(ctx, assets.obstacle, toScreen(position), scaledCell * .9);
  }
  ctx.restore();
  for (const risk of state?.riskCells || []) {
    drawCell(ctx, toScreen(risk.position), scaledCell, COLORS.risk);
  }
  for (const position of terrain(state, "OBSTACLE")) {
    drawImage(ctx, assets.obstacle, toScreen(position), scaledCell * .9);
  }
  for (const position of terrain(state, "RESOURCE")) {
    drawImage(ctx, assets.resource, toScreen(position), scaledCell * .76);
  }
  drawRoutes(ctx, state?.planRoutes || [], toScreen);
  const core = state?.core;
  if (core?.position) {
    entities.push(
      drawEntity(ctx, assets.core, core, toScreen, COLORS.friendly, scaledCell * 1.18),
    );
  }
  for (const unit of state?.units || []) {
    const name = String(unit.unitType || unit.unit_type || "worker").toLowerCase();
    entities.push(
      drawEntity(ctx, assets[name], unit, toScreen, COLORS.friendly, scaledCell * .82),
    );
  }
  for (const enemy of state?.visibleEnemies || state?.visible_enemies || []) {
    const name = String(
      enemy.unitType || enemy.unit_type || enemy.kind || "ranger",
    ).toLowerCase();
    entities.push(
      drawEntity(
        ctx,
        assets[name === "unit" ? "ranger" : name],
        enemy,
        toScreen,
        COLORS.enemy,
        scaledCell * .82,
      ),
    );
  }

  // v0.14 keeps the Beacon coordinate public even when its status is hidden.
  const beacon = state?.beacon;
  if (beacon?.position) {
    const screen = toScreen(beacon.position);
    drawImage(ctx, assets.beacon, screen, scaledCell);
    ctx.fillStyle = COLORS.beacon;
    ctx.font = "700 10px ui-monospace";
    ctx.fillText(
      beacon.status || "UNKNOWN",
      screen[0] - 22,
      screen[1] + scaledCell * .8,
    );
  }
  return entities.filter(Boolean);
}

function drawFog(ctx, bounds, current, explored, toScreen, cell) {
  const known = new Set([...explored, ...current]);
  for (let x = bounds.minX; x <= bounds.maxX; x += 1) {
    for (let y = bounds.minY; y <= bounds.maxY; y += 1) {
      const state = classifyFogCell([x, y], current, explored);
      const screen = toScreen([x, y]);
      if (state === "VISIBLE") {
        drawFogCell(ctx, screen, cell, COLORS.visible, COLORS.visibleGrid);
      } else if (state === "EXPLORED") {
        drawFogCell(ctx, screen, cell, COLORS.explored, COLORS.exploredGrid);
      } else {
        drawCell(ctx, screen, cell + 1, COLORS.unknown);
      }
    }
  }
  ctx.save();
  ctx.strokeStyle = COLORS.frontier;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (const key of known) {
    const [x, y] = key.split(",").map(Number);
    if (x < bounds.minX || x > bounds.maxX || y < bounds.minY || y > bounds.maxY) {
      continue;
    }
    const [screenX, screenY] = toScreen([x, y]);
    const half = cell / 2;
    CARDINALS.forEach(([dx, dy], index) => {
      if (known.has(positionKey([x + dx, y + dy]))) return;
      if (index === 0) {
        ctx.moveTo(screenX - half, screenY - half);
        ctx.lineTo(screenX + half, screenY - half);
      } else if (index === 1) {
        ctx.moveTo(screenX + half, screenY - half);
        ctx.lineTo(screenX + half, screenY + half);
      } else if (index === 2) {
        ctx.moveTo(screenX - half, screenY + half);
        ctx.lineTo(screenX + half, screenY + half);
      } else {
        ctx.moveTo(screenX - half, screenY - half);
        ctx.lineTo(screenX - half, screenY + half);
      }
    });
  }
  ctx.stroke();
  ctx.restore();
}

function drawFogCell(ctx, screen, cell, fill, stroke) {
  const left = Math.round(screen[0] - cell / 2) + .5;
  const top = Math.round(screen[1] - cell / 2) + .5;
  ctx.fillStyle = fill;
  ctx.fillRect(left, top, cell, cell);
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 1;
  ctx.strokeRect(left, top, cell - 1, cell - 1);
}

function terrain(state, kind) {
  const direct = kind === "OBSTACLE" ? state?.obstacleCells : state?.resourceCells;
  if (Array.isArray(direct)) return direct;
  const objects = state?.objects || state?.terrain || [];
  return objects
    .filter((item) => item.kind === kind)
    .flatMap((item) => item.positions || []);
}

function drawGrid(ctx, width, height, cell) {
  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = width % cell / 2; x < width; x += cell) {
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
  }
  for (let y = height % cell / 2; y < height; y += cell) {
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
  }
  ctx.stroke();
}

function drawCell(ctx, screen, cell, fill) {
  ctx.fillStyle = fill;
  ctx.fillRect(screen[0] - cell / 2, screen[1] - cell / 2, cell, cell);
}

function drawImage(ctx, image, screen, size) {
  if (image?.complete && image.naturalWidth) {
    ctx.drawImage(image, screen[0] - size / 2, screen[1] - size / 2, size, size);
  } else {
    ctx.fillStyle = "#7f93a9";
    ctx.fillRect(screen[0] - 5, screen[1] - 5, 10, 10);
  }
}

function drawEntity(ctx, image, entity, toScreen, color, size) {
  if (!entity.position) return null;
  const screen = toScreen(entity.position);
  ctx.save();
  ctx.shadowColor = color;
  ctx.shadowBlur = 12;
  drawImage(ctx, image, screen, size);
  ctx.restore();
  ctx.fillStyle = color;
  ctx.font = "700 10px ui-monospace";
  ctx.textAlign = "center";
  ctx.fillText(entity.id || entity.shortId || "", screen[0], screen[1] + size * .72);
  return { ...entity, screen };
}

function drawRoutes(ctx, routes, toScreen) {
  ctx.save();
  ctx.strokeStyle = COLORS.route;
  ctx.lineWidth = 2;
  ctx.setLineDash([7, 7]);
  for (const route of routes) {
    const points = route.points || [];
    if (points.length < 2) continue;
    ctx.beginPath();
    points.forEach((point, index) => {
      const [x, y] = toScreen(point);
      if (index) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    });
    ctx.stroke();
  }
  ctx.restore();
}
