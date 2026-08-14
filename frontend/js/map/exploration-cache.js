function positionKey(position) {
  return `${position[0]},${position[1]}`;
}

function boundsKey(bounds) {
  return `${bounds.minX}:${bounds.minY}:${bounds.maxX}:${bounds.maxY}`;
}

function visibleSet(currentCells) {
  if (currentCells instanceof Set) return currentCells;
  return new Set((currentCells || []).map(positionKey));
}

export class ExplorationCache {
  constructor() {
    this.runtimeId = null;
    this.entries = new Map();
  }

  reset(runtimeId) {
    if (this.runtimeId === runtimeId) return;
    this.runtimeId = runtimeId;
    this.entries.clear();
  }

  entry(runtimeId, bounds) {
    if (runtimeId !== this.runtimeId) return null;
    return this.entries.get(boundsKey(bounds)) || null;
  }

  etag(runtimeId, bounds) {
    return this.entry(runtimeId, bounds)?.etag || null;
  }

  replace(runtimeId, bounds, payload, etag) {
    this.reset(runtimeId);
    const exploredCells = (payload?.exploredCells || []).map((cell) => [...cell]);
    const knownObstacleCells = (payload?.knownObstacleCells || []).map((cell) => [...cell]);
    const value = {
      bounds: { ...bounds },
      revision: Number(payload?.revision || 0),
      exploredCells,
      knownObstacleCells,
      explored: new Set(exploredCells.map(positionKey)),
      obstacles: new Set(knownObstacleCells.map(positionKey)),
      etag: etag || null,
    };
    this.entries.set(boundsKey(bounds), value);
    return value;
  }

  exploredSet(runtimeId, bounds) {
    return this.entry(runtimeId, bounds)?.explored || new Set();
  }

  obstacleSet(runtimeId, bounds) {
    return this.entry(runtimeId, bounds)?.obstacles || new Set();
  }

  classify(runtimeId, position, currentCells) {
    if (visibleSet(currentCells).has(positionKey(position))) return "VISIBLE";
    if (runtimeId !== this.runtimeId) return "UNKNOWN";
    const key = positionKey(position);
    for (const entry of this.entries.values()) {
      if (entry.explored.has(key)) return "EXPLORED";
    }
    return "UNKNOWN";
  }
}
