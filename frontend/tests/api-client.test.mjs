import assert from "node:assert/strict";
import test from "node:test";

import { ApiClient } from "../js/api-client.js";

test("exploration request sends normalized bounds and reuses ETag on 304", async (context) => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(null, { status: 304, headers: { ETag: '"v4"' } });
  };
  const api = new ApiClient("/api/v1");

  const result = await api.exploration(
    { minX: -2, minY: 3, maxX: 8, maxY: 9 },
    '"v4"',
  );

  assert.deepEqual(result, { notModified: true, etag: '"v4"' });
  assert.equal(
    calls[0].url,
    "/api/v1/exploration?minX=-2&minY=3&maxX=8&maxY=9",
  );
  assert.equal(calls[0].options.headers["If-None-Match"], '"v4"');
  assert.equal(calls[0].options.credentials, "same-origin");
});
