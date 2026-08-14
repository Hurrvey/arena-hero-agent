import assert from "node:assert/strict";
import test from "node:test";

import { MapCamera } from "../js/map/map-camera.js";

test("camera preserves int64 world coordinates as relative deltas", () => {
  const origin = 9_007_199_254_740_000n;
  const camera = new MapCamera([origin, origin]);

  assert.deepEqual(camera.relative([origin + 12n, origin - 7n]), [12, -7]);
});

test("camera zoom remains bounded and home recenters", () => {
  const camera = new MapCamera([10n, 20n]);
  camera.pan(4, -3);
  camera.zoomBy(100);
  assert.equal(camera.zoom, 3);
  camera.home([100n, 200n]);
  assert.deepEqual(camera.relative([100n, 200n]), [0, 0]);
});

test("camera converts canvas viewport to inclusive bounded world coordinates", () => {
  const camera = new MapCamera([100n, -50n]);
  const bounds = camera.worldBounds({ width: 300, height: 180, cell: 30, padding: 2 });

  assert.deepEqual(bounds, { minX: 93, minY: -55, maxX: 107, maxY: -45 });
});

test("camera rejects HTTP viewport coordinates outside safe JavaScript integers", () => {
  const camera = new MapCamera([BigInt(Number.MAX_SAFE_INTEGER) + 100n, 0n]);

  assert.throws(
    () => camera.worldBounds({ width: 300, height: 180, cell: 30 }),
    /outside safe HTTP coordinate bounds/,
  );
});
