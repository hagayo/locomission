import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "./apiClient.js";

function makeClient(fetchImpl, onSessionExpired = () => {}) {
  return createApiClient({
    baseUrl: "https://api.example.test/",
    getClerkSession: () => ({ getToken: async () => "jwt-fixture" }),
    getGameSession: () => "session-fixture",
    fetchImpl, onSessionExpired
  });
}

test("every private request carries Clerk JWT and game session", async () => {
  let seen;
  const client = makeClient(async (url, opts) => {
    seen = { url, opts };
    return new Response(JSON.stringify({ ok: true }), {
      status: 200, headers: { "Content-Type": "application/json" }
    });
  });
  assert.deepEqual(await client.request("/api/lobby"), { ok: true });
  assert.equal(seen.url, "https://api.example.test/api/lobby");
  assert.equal(seen.opts.headers.get("Authorization"), "Bearer jwt-fixture");
  assert.equal(seen.opts.headers.get("X-Game-Session"), "session-fixture");
  assert.equal(seen.opts.cache, "no-store");
});

test("session creation excludes game token and preserves binary upload", async () => {
  let seen;
  const client = makeClient(async (_url, opts) => {
    seen = opts;
    return new Response("{}", { status: 201 });
  });
  await client.request("/api/sessions", { method: "POST" }, false);
  assert.equal(seen.headers.has("X-Game-Session"), false);
  const bytes = new Uint8Array([1, 2, 3]);
  await client.request("/api/proof", {
    method: "POST",
    headers: { "Content-Type": "image/png", "X-Request-ID": "test-id" },
    body: bytes
  });
  assert.equal(seen.body, bytes);
  assert.equal(seen.headers.get("X-Request-ID"), "test-id");
});

test("429 retry metadata remains available to caller", async () => {
  const client = makeClient(async () => new Response(
    JSON.stringify({ detail: "מכסת הבקשות מוצתה" }), {
      status: 429, headers: { "Retry-After": "23" }
    }
  ));
  await assert.rejects(() => client.request("/api/lobby"), error => {
    assert.equal(error.status, 429);
    assert.equal(error.retryAfterSeconds, 23);
    return true;
  });
});

test("expired session resets UI without logging or leaking bearer values", async () => {
  let reset = 0;
  const client = makeClient(async () => new Response(
    "{}", { status: 401 }
  ), () => reset++);
  await assert.rejects(() => client.request("/api/me"));
  assert.equal(reset, 1);
  const publicResult = await makeClient(
    async () => new Response('{"leaders":[]}', { status: 200 })
  ).publicRequest("/api/leaderboard");
  assert.deepEqual(publicResult, { leaders: [] });
});
