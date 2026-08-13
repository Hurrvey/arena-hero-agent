import assert from "node:assert/strict";
import test from "node:test";

import { AppStore } from "../js/app-store.js";

test("duplicate seq is ignored and a gap requests replay", () => {
  const store = new AppStore();
  const seen = [];
  store.subscribe((state) => seen.push(state.lastSeq));

  assert.equal(store.applyEvent({ seq: 1, type: "one" }), true);
  assert.equal(store.applyEvent({ seq: 1, type: "duplicate" }), false);
  assert.equal(store.applyEvent({ seq: 3, type: "gap" }), false);
  assert.equal(store.snapshot.connection.needsReplay, true);
  assert.deepEqual(seen, [1, 1]);
});

test("canonical business event type advances the event stream", () => {
  const store = new AppStore();
  const event = {
    schemaVersion: 1,
    seq: 1,
    type: "plan.accepted",
    at: "2026-08-13T00:00:00Z",
    runtimeId: "runtime",
    tick: 1,
    payload: {},
  };

  assert.equal(store.applyEvent(event), true);
  assert.equal(store.snapshot.events[0].type, "plan.accepted");
});

test("EVENT_GAP replaces state from authoritative REST", () => {
  const store = new AppStore();
  store.replaceFromRest({ tick: 8, units: [] }, { tick: 8 }, 14);

  assert.equal(store.snapshot.state.tick, 8);
  assert.equal(store.snapshot.lastSeq, 14);
  assert.equal(store.snapshot.connection.needsReplay, false);
});

test("UNKNOWN beacon never creates a guessed carrier", () => {
  const store = new AppStore();
  store.replaceFromRest({ beacon: { position: [9, 9], status: null } }, {}, 0);

  assert.equal(store.snapshot.state.beacon.carrierId, undefined);
});

test("empty authoritative state can initialize before the first Turn", () => {
  const store = new AppStore();

  store.replaceFromRest(null, null, 0, []);

  assert.equal(store.snapshot.state, null);
  assert.equal(store.snapshot.plan, null);
  assert.equal(store.snapshot.connection.needsReplay, false);
});
