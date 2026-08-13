import { MapCamera } from "./map-camera.js";
import { loadMapAssets } from "./map-assets.js";
import { drawTacticalLayers } from "./map-layers.js";
import { hitTest } from "./map-hit-test.js";
import { updateMapDescription } from "./map-accessibility.js";

export class TacticalMap {
  constructor(canvas, description, onSelect) {
    this.canvas = canvas; this.description = description; this.onSelect = onSelect;
    this.ctx = canvas.getContext("2d"); this.camera = new MapCamera(); this.assets = {};
    this.state = null; this.entities = []; this.drag = null;
    loadMapAssets().then((assets) => { this.assets = assets; this.render(); });
    this.installControls();
    this.resizeObserver = new ResizeObserver(() => this.render());
    this.resizeObserver.observe(canvas.parentElement);
  }
  update(state) {
    this.state = state; const anchor = state?.core?.position || state?.units?.[0]?.position;
    if (anchor && !this.hasAnchor) { this.camera.home(anchor.map(BigInt)); this.hasAnchor = true; }
    updateMapDescription(this.description, state); this.render();
  }
  render() {
    const rect = this.canvas.getBoundingClientRect(); const ratio = devicePixelRatio || 1;
    if (!rect.width || !rect.height) return;
    this.canvas.width = Math.round(rect.width * ratio); this.canvas.height = Math.round(rect.height * ratio);
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.entities = drawTacticalLayers(this.ctx, { width: rect.width, height: rect.height, cell: 30, assets: this.assets, state: this.state, camera: this.camera });
  }
  installControls() {
    this.canvas.addEventListener("pointerdown", (event) => { this.drag = [event.clientX, event.clientY]; this.canvas.setPointerCapture(event.pointerId); });
    this.canvas.addEventListener("pointermove", (event) => { if (!this.drag) return; this.camera.pan((event.clientX - this.drag[0]) / 30, (event.clientY - this.drag[1]) / 30); this.drag = [event.clientX, event.clientY]; this.render(); });
    this.canvas.addEventListener("pointerup", (event) => { if (this.drag) { const rect = this.canvas.getBoundingClientRect(); const found = hitTest(this.entities, [event.clientX - rect.left, event.clientY - rect.top]); if (found) this.onSelect?.(found); } this.drag = null; });
    this.canvas.addEventListener("wheel", (event) => { event.preventDefault(); this.camera.zoomBy(event.deltaY < 0 ? .15 : -.15); this.render(); }, { passive: false });
    this.canvas.addEventListener("keydown", (event) => { const steps = { ArrowUp: [0, 1], ArrowDown: [0, -1], ArrowLeft: [1, 0], ArrowRight: [-1, 0] }; if (steps[event.key]) { this.camera.pan(...steps[event.key]); this.render(); } if (event.key === "Home") { this.hasAnchor = false; this.update(this.state); } });
  }
  dispose() {
    this.resizeObserver?.disconnect();
  }
}
