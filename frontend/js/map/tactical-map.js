import { MapCamera } from "./map-camera.js";
import { loadMapAssets } from "./map-assets.js";
import { drawTacticalLayers } from "./map-layers.js";
import { hitTest } from "./map-hit-test.js";
import { updateMapDescription } from "./map-accessibility.js";

const CELL_SIZE = 30;

function boundsKey(bounds) {
  return `${bounds.minX}:${bounds.minY}:${bounds.maxX}:${bounds.maxY}`;
}

function capWindow(bounds, limit = 96) {
  const capped = { ...bounds };
  if (capped.maxX - capped.minX + 1 > limit) {
    const center = Math.floor((capped.minX + capped.maxX) / 2);
    capped.minX = center - Math.floor((limit - 1) / 2);
    capped.maxX = capped.minX + limit - 1;
  }
  if (capped.maxY - capped.minY + 1 > limit) {
    const center = Math.floor((capped.minY + capped.maxY) / 2);
    capped.minY = center - Math.floor((limit - 1) / 2);
    capped.maxY = capped.minY + limit - 1;
  }
  return capped;
}

export class TacticalMap {
  constructor(canvas, description, onSelect, loadExploration = null) {
    this.canvas = canvas;
    this.description = description;
    this.onSelect = onSelect;
    this.loadExploration = loadExploration;
    this.ctx = canvas.getContext("2d");
    this.camera = new MapCamera();
    this.assets = {};
    this.state = null;
    this.entities = [];
    this.drag = null;
    this.explorationEntry = null;
    this.lastRequestKey = null;
    this.pendingRequestKey = null;
    this.requestNonce = 0;
    this.disposed = false;
    loadMapAssets().then((assets) => {
      if (this.disposed) return;
      this.assets = assets;
      this.render();
    });
    this.installControls();
    this.resizeObserver = new ResizeObserver(() => this.render());
    this.resizeObserver.observe(canvas.parentElement);
  }

  update(state) {
    this.state = state;
    const anchor = state?.core?.position || state?.units?.[0]?.position;
    if (anchor && !this.hasAnchor) {
      this.camera.home(anchor.map(BigInt));
      this.hasAnchor = true;
    }
    this.render();
  }

  render() {
    const rect = this.canvas.getBoundingClientRect();
    const ratio = devicePixelRatio || 1;
    if (!rect.width || !rect.height) return;
    this.canvas.width = Math.round(rect.width * ratio);
    this.canvas.height = Math.round(rect.height * ratio);
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

    let bounds = null;
    let requestBounds = null;
    try {
      bounds = this.camera.worldBounds({
        width: rect.width,
        height: rect.height,
        cell: CELL_SIZE,
        padding: 2,
      });
      requestBounds = capWindow(bounds);
    } catch (error) {
      if (!(error instanceof RangeError)) throw error;
    }
    const entry = requestBounds
      && this.explorationEntry?.bounds
      && boundsKey(this.explorationEntry.bounds) === boundsKey(requestBounds)
      ? this.explorationEntry
      : null;
    this.entities = drawTacticalLayers(this.ctx, {
      width: rect.width,
      height: rect.height,
      cell: CELL_SIZE,
      assets: this.assets,
      state: this.state,
      camera: this.camera,
      bounds,
      exploredCells: entry?.exploredCells || [],
      knownObstacleCells: entry?.knownObstacleCells || [],
    });
    updateMapDescription(this.description, this.state, entry);
    if (requestBounds) this.scheduleExploration(requestBounds);
  }

  scheduleExploration(bounds) {
    if (!this.loadExploration || this.disposed) return;
    const revision = Number(this.state?.visibility?.explorationRevision || 0);
    const key = `${boundsKey(bounds)}:${revision}`;
    if (key === this.lastRequestKey || key === this.pendingRequestKey) return;
    clearTimeout(this.loadTimer);
    this.pendingRequestKey = key;
    const nonce = ++this.requestNonce;
    this.loadTimer = setTimeout(async () => {
      try {
        const entry = await this.loadExploration(bounds);
        if (this.disposed || nonce !== this.requestNonce) return;
        this.explorationEntry = entry;
      } catch (_error) {
        if (this.disposed || nonce !== this.requestNonce) return;
        this.explorationEntry = null;
      }
      this.pendingRequestKey = null;
      this.lastRequestKey = key;
      this.render();
    }, 100);
  }

  installControls() {
    this.canvas.addEventListener("pointerdown", (event) => {
      this.drag = [event.clientX, event.clientY];
      this.canvas.setPointerCapture(event.pointerId);
    });
    this.canvas.addEventListener("pointermove", (event) => {
      if (!this.drag) return;
      this.camera.pan(
        (event.clientX - this.drag[0]) / CELL_SIZE,
        (event.clientY - this.drag[1]) / CELL_SIZE,
      );
      this.drag = [event.clientX, event.clientY];
      this.render();
    });
    this.canvas.addEventListener("pointerup", (event) => {
      if (this.drag) {
        const rect = this.canvas.getBoundingClientRect();
        const found = hitTest(
          this.entities,
          [event.clientX - rect.left, event.clientY - rect.top],
        );
        if (found) this.onSelect?.(found);
      }
      this.drag = null;
    });
    this.canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      this.camera.zoomBy(event.deltaY < 0 ? .15 : -.15);
      this.render();
    }, { passive: false });
    this.canvas.addEventListener("keydown", (event) => {
      const steps = {
        ArrowUp: [0, 1],
        ArrowDown: [0, -1],
        ArrowLeft: [1, 0],
        ArrowRight: [-1, 0],
      };
      if (steps[event.key]) {
        this.camera.pan(...steps[event.key]);
        this.render();
      }
      if (event.key === "Home") {
        this.hasAnchor = false;
        this.update(this.state);
      }
    });
  }

  dispose() {
    this.disposed = true;
    this.requestNonce += 1;
    clearTimeout(this.loadTimer);
    this.resizeObserver?.disconnect();
  }
}
