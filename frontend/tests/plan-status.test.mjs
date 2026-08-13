import assert from "node:assert/strict";
import test from "node:test";

import { renderPlan } from "../js/components/plan-status.js";

test("manual effective action replaces stale agent explanation", () => {
  const html = renderPlan({
    tick: 8,
    status: "ACCEPTED",
    plan: { tick: 8, unitActions: { E1: { type: "WAIT" } } },
    receipts: {
      AGENT: { plan: { unitActions: { E1: { type: "HARVEST" } } } },
      MANUAL: { plan: { unitActions: { E1: { type: "WAIT" } } } },
    },
    explanation: {
      actions: [{ entityId: "E1", actionType: "HARVEST", reasonCode: "ECONOMY" }],
    },
  });

  assert.match(html, />WAIT</);
  assert.match(html, /MANUAL/);
  assert.doesNotMatch(html, />HARVEST</);
});
