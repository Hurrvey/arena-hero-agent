import assert from "node:assert/strict";
import test from "node:test";

import { diffProfile, threeWayMerge } from "../js/components/profile-diff.js";

test("diff emits only changed fields with old and new values", () => {
  const result = diffProfile(
    { worker_target: 23, combat_priority: 0.75 },
    { worker_target: 21, combat_priority: 0.75 },
  );

  assert.deepEqual(result, [
    { field: "worker_target", before: 23, after: 21 },
  ]);
});

test("three-way merge preserves user edits and accepts unrelated server changes", () => {
  const result = threeWayMerge(
    { worker_target: 23, combat_priority: 0.75 },
    { worker_target: 20, combat_priority: 0.75 },
    { worker_target: 23, combat_priority: 1 },
  );

  assert.deepEqual(result.profile, { worker_target: 20, combat_priority: 1 });
  assert.deepEqual(result.conflicts, []);
});

test("three-way merge reports fields changed by both sides", () => {
  const result = threeWayMerge(
    { worker_target: 23 },
    { worker_target: 20 },
    { worker_target: 21 },
  );

  assert.equal(result.profile.worker_target, 20);
  assert.deepEqual(result.conflicts, ["worker_target"]);
});
