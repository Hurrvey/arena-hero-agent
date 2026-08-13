import assert from "node:assert/strict";
import test from "node:test";

import { renderEvents } from "../js/components/event-list.js";
import { renderPlan } from "../js/components/plan-status.js";
import { decorateStateWithPlan } from "../js/map/plan-routes.js";

test("effective plan displays concrete movement details and deterministic reason", () => {
  const html = renderPlan({
    tick: 100_217,
    status: "ACCEPTED",
    plan: { tick: 100_217, unitActions: { E2: { type: "MOVE", direction: "LEFT" } } },
    explanation: {
      actions: [{ entityId: "E2", actionType: "MOVE", reasonCode: "RESOURCE_ROUTE", target: [-1140, -296] }],
    },
  });

  assert.match(html, /MOVE LEFT/);
  assert.match(html, /RESOURCE_ROUTE/);
  assert.match(html, /目标 \(-1140, -296\)/);
});

test("resolution result renders individual authoritative game events", () => {
  const html = renderEvents([{
    seq: 28,
    type: "resolution.results",
    at: "2026-08-13T15:00:52Z",
    tick: 100_217,
    payload: {
      count: 2,
      events: [
        { planTick: 100_216, eventType: "HARVEST_SUCCEEDED", actorId: "E2", values: { amount: 1 } },
        { planTick: 100_216, eventType: "UNIT_MOVE_SUCCEEDED", actorId: "E3", position: [-1138, -296] },
      ],
    },
  }]);

  assert.match(html, /E2 采集成功 \+1/);
  assert.match(html, /E3 移动成功 → \(-1138, -296\)/);
  assert.match(html, /Tick 100216/);
  assert.doesNotMatch(html, />resolution\.results</);
});

test("map receives concrete movement and attack routes from the accepted plan", () => {
  const state = {
    tick: 100_217,
    core: { id: "E1", position: [-1139, -296] },
    units: [{ id: "E2", position: [-1138, -296] }],
  };
  const plan = {
    tick: 100_217,
    status: "ACCEPTED",
    plan: { unitActions: { E2: { type: "MOVE", direction: "LEFT" } } },
    explanation: { actions: [{ entityId: "E2", actionType: "MOVE", target: [-1140, -296] }] },
  };

  const presented = decorateStateWithPlan(state, plan);

  assert.deepEqual(presented.planRoutes, [{ entityId: "E2", actionType: "MOVE", points: [[-1138, -296], [-1140, -296]] }]);
  assert.equal(presented.units[0].currentAction, "MOVE LEFT");
  assert.deepEqual(state, {
    tick: 100_217,
    core: { id: "E1", position: [-1139, -296] },
    units: [{ id: "E2", position: [-1138, -296] }],
  });
});

test("map refuses routes from another tick or a nonaccepted plan", () => {
  const state = { tick: 12, units: [{ id: "E2", position: [0, 0] }] };
  const effective = { unitActions: { E2: { type: "MOVE", direction: "RIGHT" } } };

  assert.equal(decorateStateWithPlan(state, { tick: 11, status: "ACCEPTED", plan: effective }), state);
  assert.equal(decorateStateWithPlan(state, { tick: 12, status: "DRAFT", plan: effective }), state);
  assert.equal(decorateStateWithPlan(state, { tick: 12, status: "REJECTED", plan: effective }), state);
});

test("core action reuses its real deterministic explanation", () => {
  const html = renderPlan({
    status: "ACCEPTED",
    plan: { coreAction: { type: "REPAIR_SHIELD" }, unitActions: { E2: { type: "WAIT" } } },
    explanation: { actions: [
      { entityId: "E1", actionType: "REPAIR_SHIELD", reasonCode: "CORE_RECOVERY" },
      { entityId: "E2", actionType: "WAIT", reasonCode: "HOLD_POSITION" },
    ] },
  });

  assert.match(html, /Core[\s\S]*CORE_RECOVERY/);
});
