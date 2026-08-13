import assert from "node:assert/strict";
import test from "node:test";

import { LiveConnection } from "../js/live-connection.js";

test("gap recovery uses the authoritative latest event tail", async () => {
  const calls = [];
  const replacements = [];
  const store = {
    setConnection(value) { calls.push(["connection", value]); },
    replaceFromRest(...args) { replacements.push(args); },
  };
  const api = {
    state: async () => ({ tick: 1400 }),
    plan: async () => ({ tick: 1400 }),
    events: async () => { throw new Error("recovery must not fetch the oldest page"); },
    eventsTail: async () => ({ events: [{ seq: 1400 }], lastSeq: 1400 }),
  };

  await new LiveConnection({ store, api }).recover();

  assert.equal(calls[0][1].stale, true);
  assert.deepEqual(replacements, [[
    { tick: 1400 },
    { tick: 1400 },
    1400,
    [{ seq: 1400 }],
  ]]);
});
