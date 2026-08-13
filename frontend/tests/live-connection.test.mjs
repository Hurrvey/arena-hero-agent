import assert from "node:assert/strict";
import test from "node:test";

import { reconnectDelay } from "../js/live-connection.js";

test("reconnect delay is deterministic and bounded", () => {
  assert.equal(reconnectDelay(0), 500);
  assert.equal(reconnectDelay(3), 4000);
  assert.equal(reconnectDelay(99), 10000);
});
