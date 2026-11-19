import test from "node:test";
import assert from "node:assert/strict";
import { AVATARS, avatarGlyph } from "./avatars.js";

test("preset avatar IDs are unique and never contain external URLs", () => {
  assert.equal(new Set(AVATARS.map((a) => a.id)).size, AVATARS.length);
  assert.equal(AVATARS.length, 5);
  assert.ok(AVATARS.every((a) => a.name && !a.icon.includes("http")));
});

test("unknown avatar IDs render an in-app fallback", () => {
  assert.equal(avatarGlyph("explorer"), "🧭");
  assert.equal(avatarGlyph("injected-url"), "🧭");
});
