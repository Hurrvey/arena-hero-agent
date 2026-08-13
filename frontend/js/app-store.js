const EMPTY_STATE = Object.freeze({
  runtime: { status: "STOPPED", runtimeId: "", lastTick: null },
  state: null,
  plan: null,
  metrics: null,
  strategy: null,
  adaptive: null,
  events: [],
  lastSeq: 0,
  connection: { status: "CONNECTING", stale: true, needsReplay: false },
  selection: null,
});

export class AppStore {
  constructor() {
    this.snapshot = structuredClone(EMPTY_STATE);
    this.listeners = new Set();
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit() {
    for (const listener of this.listeners) listener(this.snapshot);
  }

  patch(name, value) {
    this.snapshot = { ...this.snapshot, [name]: value };
    this.emit();
  }

  setConnection(patch) {
    this.snapshot = {
      ...this.snapshot,
      connection: { ...this.snapshot.connection, ...patch },
    };
    this.emit();
  }

  applyEvent(event) {
    const seq = Number(event.seq || 0);
    if (!Number.isSafeInteger(seq) || seq <= this.snapshot.lastSeq) return false;
    if (seq !== this.snapshot.lastSeq + 1) {
      this.snapshot = {
        ...this.snapshot,
        connection: { ...this.snapshot.connection, needsReplay: true, stale: true },
      };
      this.emit();
      return false;
    }
    const events = [...this.snapshot.events, event].slice(-300);
    this.snapshot = { ...this.snapshot, events, lastSeq: seq };
    this.emit();
    return true;
  }

  replaceFromRest(state, plan, lastSeq, events = []) {
    const safeState = state ? structuredClone(state) : null;
    normalizeDashboardState(safeState);
    if (safeState?.beacon && safeState.beacon.status == null) {
      delete safeState.beacon.carrierId;
      delete safeState.beacon.carrier_id;
    }
    this.snapshot = {
      ...this.snapshot,
      state: safeState,
      plan,
      events: events.slice(-300),
      lastSeq: Number(lastSeq || 0),
      connection: { status: "LIVE", stale: false, needsReplay: false },
    };
    this.emit();
  }

  select(selection) {
    this.patch("selection", selection);
  }
}

function normalizeDashboardState(state) {
  if (!state || !Array.isArray(state.objects)) return;
  const controlled = state.objects.filter((item) => item?.controlled === true);
  state.core ??= controlled.find((item) => String(item.kind).toUpperCase() === "CORE") || null;
  state.units ??= controlled.filter((item) => String(item.kind).toUpperCase() === "UNIT");
  state.visibleEnemies ??= state.objects.filter((item) => item?.controlled === false && ["CORE", "UNIT"].includes(String(item.kind).toUpperCase()));
  state.obstacleCells ??= terrainPositions(state.objects, "OBSTACLE");
  state.resourceCells ??= terrainPositions(state.objects, "RESOURCE");
  state.resourceCapacity ??= Math.max(10, Number(state.population || state.units.length) * 5);
  state.beacon ??= state.championBeacon || null;
}

function terrainPositions(objects, kind) {
  return objects
    .filter((item) => String(item?.kind).toUpperCase() === kind)
    .flatMap((item) => Array.isArray(item.positions) ? item.positions : []);
}
