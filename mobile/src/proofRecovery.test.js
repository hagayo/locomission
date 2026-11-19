import test from "node:test";
import assert from "node:assert/strict";
import { inspectOrRetry } from "./proofRecovery.js";

test("lost response recovered without posting image a second time", async () => {
  let submissions = 0;
  const result = await inspectOrRetry(
    async () => ({ state: "completed", accepted: true, points: 7 }),
    async () => { submissions++; return {}; }
  );
  assert.equal(result.outcome.points, 7);
  assert.equal(submissions, 0);
});

test("in-flight photo is not resubmitted", async () => {
  let submissions = 0;
  const outcome = await inspectOrRetry(
    async () => ({ state: "processing" }),
    async () => { submissions++; return {}; }
  );
  assert.equal(outcome.state, "processing");
  assert.equal(submissions, 0);
});

test("abandoned attempt is retried only after server says retry_required", async () => {
  let submissions = 0;
  const outcome = await inspectOrRetry(
    async () => ({ state: "retry_required" }),
    async () => { submissions++; return { accepted: false, points: 0 }; }
  );
  assert.equal(outcome.state, "completed");
  assert.equal(outcome.outcome.points, 0);
  assert.equal(submissions, 1);
});

test("status outages and malformed statuses never trigger a second model request", async () => {
  let submissions = 0;
  await assert.rejects(() => inspectOrRetry(
    async () => { throw new TypeError("offline"); },
    async () => { submissions++; return {}; }
  ), /offline/);
  await assert.rejects(() => inspectOrRetry(
    async () => ({ state: "unexpected" }),
    async () => { submissions++; return {}; }
  ), /Unknown proof status/);
  assert.equal(submissions, 0);
});
