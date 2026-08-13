import assert from "node:assert/strict";
import test from "node:test";

import { mapAssetUrl } from "../js/map/map-assets.js";

test("resource map layer uses the provided crystal asset", () => {
  assert.equal(
    mapAssetUrl("resource"),
    "/assets/arena-hero/png/resource-crystal-128.png",
  );
});
