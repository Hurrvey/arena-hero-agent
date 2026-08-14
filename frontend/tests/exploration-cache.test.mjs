import assert from "node:assert/strict";
import test from "node:test";

import { ExplorationCache } from "../js/map/exploration-cache.js";

test("cache isolates runtime generations and exact windows", () => {
  const cache = new ExplorationCache();
  cache.replace("runtime-a", { minX: 0, minY: 0, maxX: 2, maxY: 2 }, {
    revision: 4,
    exploredCells: [[0, 0], [1, 0]],
    knownObstacleCells: [[1, 0]],
  }, '"etag-a"');

  assert.equal(cache.classify("runtime-a", [0, 0], []), "EXPLORED");
  assert.equal(cache.classify("runtime-a", [9, 9], []), "UNKNOWN");
  assert.equal(cache.classify("runtime-b", [0, 0], []), "UNKNOWN");
  cache.reset("runtime-b");
  assert.equal(cache.classify("runtime-a", [0, 0], []), "UNKNOWN");
});

test("current visibility always wins over explored and unknown", () => {
  const cache = new ExplorationCache();
  cache.reset("runtime-a");
  assert.equal(cache.classify("runtime-a", [8, -2], [[8, -2]]), "VISIBLE");
});
