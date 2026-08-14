import assert from "node:assert/strict";
import test from "node:test";

import { classifyFogCell, visibleMapCells } from "../js/map/map-layers.js";

test("fog has current visible, explored dark, and unknown opaque states", () => {
  const current = visibleMapCells({ visibility: { currentCells: [[0, 0]] } });
  const explored = new Set(["1,0"]);
  assert.equal(classifyFogCell([0, 0], current, explored), "VISIBLE");
  assert.equal(classifyFogCell([1, 0], current, explored), "EXPLORED");
  assert.equal(classifyFogCell([2, 0], current, explored), "UNKNOWN");
});

test("old objects never create current visible cells", () => {
  const current = visibleMapCells({
    objects: [{ kind: "UNIT", controlled: false, position: [9, 9] }],
  });

  assert.deepEqual([...current], []);
});
