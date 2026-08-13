export class MapCamera {
  constructor(origin = [0n, 0n]) {
    this.origin = origin.map(BigInt);
    this.offset = [0, 0];
    this.zoom = 1;
  }

  relative(position) {
    return [
      Number(BigInt(position[0]) - this.origin[0]) + this.offset[0],
      Number(BigInt(position[1]) - this.origin[1]) + this.offset[1],
    ];
  }

  pan(dx, dy) { this.offset = [this.offset[0] + dx, this.offset[1] + dy]; }
  zoomBy(delta) { this.zoom = Math.max(0.45, Math.min(3, this.zoom + delta)); }
  home(origin = this.origin) { this.origin = origin.map(BigInt); this.offset = [0, 0]; this.zoom = 1; }
}
