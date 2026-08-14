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

  worldBounds({ width, height, cell, padding = 2 }) {
    if (![width, height, cell].every((value) => Number.isFinite(value) && value > 0)) {
      throw new RangeError("viewport dimensions must be positive finite numbers");
    }
    const halfX = Math.ceil(width / (2 * cell * this.zoom)) + padding;
    const halfY = Math.ceil(height / (2 * cell * this.zoom)) + padding;
    const centerX = this.origin[0] - BigInt(Math.round(this.offset[0]));
    const centerY = this.origin[1] - BigInt(Math.round(this.offset[1]));
    const values = {
      minX: centerX - BigInt(halfX),
      minY: centerY - BigInt(halfY),
      maxX: centerX + BigInt(halfX),
      maxY: centerY + BigInt(halfY),
    };
    const minimum = BigInt(Number.MIN_SAFE_INTEGER);
    const maximum = BigInt(Number.MAX_SAFE_INTEGER);
    if (Object.values(values).some((value) => value < minimum || value > maximum)) {
      throw new RangeError("viewport is outside safe HTTP coordinate bounds");
    }
    return Object.fromEntries(
      Object.entries(values).map(([key, value]) => [key, Number(value)]),
    );
  }

  pan(dx, dy) { this.offset = [this.offset[0] + dx, this.offset[1] + dy]; }
  zoomBy(delta) { this.zoom = Math.max(0.45, Math.min(3, this.zoom + delta)); }
  home(origin = this.origin) { this.origin = origin.map(BigInt); this.offset = [0, 0]; this.zoom = 1; }
}
