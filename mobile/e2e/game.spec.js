import { test, expect } from "@playwright/test";

const mission = {
  id: "c8a1cf63-7d3b-47fa-9c8b-ace3c1c45601",
  title_he: "סיפור היסטורי לבדיקת דפדפן",
  historical_fact_he: "עובדת בדיקה של ממשק המשתמש בלבד",
  instructions_he: "צלם את המקום",
  source_title: "בדיקת אוטומציה",
  source_url: "https://example.org/test",
  visual_criteria_he: ["פרט ברור בתמונה"],
  points: 7,
  lat: 32.0853, lon: 34.7818,
  completed: false, started_today: false
};

function mockBackend(page, { interruptFirstPhoto = false } = {}) {
  const state = {
    token: "e2e-session-only",
    started: false, completed: false, score: 0, active: true,
    avatar: "explorer", photoCalls: [], requests: [], photoResult: null,
    interruptFirstPhoto
  };
  page.route("https://test.clerk.local/**", route => route.fulfill({
    status: 200, contentType: "application/javascript",
    headers: { "access-control-allow-origin": "*" },
    body: "window.__internal_ClerkUICtor = function () {};"
  }));
  page.route(/https:\/\/.*tile\.openstreetmap\.org\/.*/, route => route.abort());
  page.route("**/api/**", async route => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    state.requests.push({ path, method: req.method() });
    const json = (body, status = 200) => route.fulfill({
      status, contentType: "application/json", body: JSON.stringify(body)
    });
    if (path === "/api/sessions" && req.method() === "POST") {
      state.active = true;
      return json({ session_token: state.token }, 201);
    }
    if (path === "/api/sessions/current") {
      state.active = false;
      return json({ ended: true });
    }
    if (path === "/api/location") return json({ accepted: true });
    if (path === "/api/lobby") return json({
      missions: [{ ...mission, started_today: state.started,
                   completed: state.completed }],
      groups: [], presence: { cells: [] },
      profile: { total_points: state.score, avatar_id: state.avatar }
    });
    if (path === "/api/missions/today") {
      return json({ missions: [{ ...mission, started_today: state.started }] });
    }
    if (path.endsWith("/start") && req.method() === "POST") {
      state.started = true;
      return json({ started: true, first_start_today: true });
    }
    if (path.includes("/proof-requests/")) {
      return json(state.photoResult
        ? { state: "completed", ...state.photoResult }
        : { state: "retry_required" });
    }
    if (path.endsWith("/proof") && req.method() === "POST") {
      const id = req.headers()["x-request-id"];
      state.photoCalls.push(id);
      if (state.interruptFirstPhoto && state.photoCalls.length === 1) {
        return route.abort("failed");
      }
      state.completed = true;
      state.score = 7;
      state.photoResult = {
        accepted: true, awarded: true,
        points: 7, reason_he: "אימות התמונה עבר"
      };
      return json(state.photoResult);
    }
    if (path === "/api/me" && req.method() === "GET") {
      return json({
        display_name: "שחקן בדיקה",
        total_points: state.score,
        missions_started: state.started ? 1 : 0,
        completed_missions: state.completed ? 1 : 0,
        avatar_id: state.avatar
      });
    }
    if (path === "/api/me/avatar") {
      state.avatar = req.postDataJSON().avatar_id;
      return json({ avatar_id: state.avatar });
    }
    if (path === "/api/me/history") return json({
      items: state.completed ? [{
        id: "7c8297c8-aa23-4ea2-8047-3221bb56ac84",
        title_he: mission.title_he, points_awarded: 7,
        completed_at: "2026-11-20T12:00:00Z"
      }] : [],
      next_before: null
    });
    if (path === "/api/me/achievements") return json({
      achievements: [{
        id: "first", title_he: "משימה ראשונה",
        unlocked: state.completed,
        current: state.completed ? 1 : 0, goal: 1
      }]
    });
    if (path === "/api/leaderboard") return json({ leaders: [] });
    return json({ detail: "Unmocked E2E API call: " + path }, 404);
  });
  return state;
}

async function openGame(page) {
  await page.goto("/");
  await expect(page.locator("#welcome")).toBeVisible();
  await page.locator("#adult").check();
  await page.locator("#start").click();
  await expect(page.locator("#browse")).toBeVisible();
  await expect(page.locator("#browse-items")).toContainText(mission.title_he);
}

test("mobile journey: GPS, discovery, camera, scoring, profile and logout", async ({ page }) => {
  const state = mockBackend(page);
  await openGame(page);
  await page.locator("#browse-items button").first().click();
  await expect(page.locator("#story-title")).toHaveText(mission.title_he);
  await page.locator("#mission-action").click();
  await expect(page.locator("#mission-action")).toHaveText("צלם הוכחה");
  await page.locator("#camera").setInputFiles({
    name: "proof.png", mimeType: "image/png",
    buffer: Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0])
  });
  await expect(page.locator("#result-title")).toHaveText("המשימה הושלמה");
  expect(state.photoCalls).toHaveLength(1);
  await page.locator("#result-next").click();
  await expect(page.locator("#points")).toHaveText("7");
  await page.locator("#profile-button").click();
  await expect(page.locator("#profile-stats")).toContainText("משימות שהשלמת");
  await expect(page.locator("#history-list")).toContainText(mission.title_he);
  await expect(page.locator("#achievement-list")).toContainText("משימה ראשונה");
  await page.getByRole("button", { name: "פנס" }).click();
  await expect(page.locator("#profile-button")).toHaveText("🔦");
  await page.locator("#sign-out").click();
  await expect(page.locator("#setup")).toBeVisible();
  expect(state.active).toBe(false);
});

test("interrupted photo response: check status, retry same UUID, no double credit", async ({ page }) => {
  const state = mockBackend(page, { interruptFirstPhoto: true });
  await openGame(page);
  await page.locator("#browse-items button").first().click();
  await page.locator("#mission-action").click();
  await expect(page.locator("#mission-action")).toHaveText("צלם הוכחה");
  await page.locator("#camera").setInputFiles({
    name: "proof.png", mimeType: "image/png",
    buffer: Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0])
  });
  await expect(page.locator("#mission-action")).toHaveText("בדוק תוצאה");
  await page.locator("#mission-action").click();
  await expect(page.locator("#result-title")).toHaveText("המשימה הושלמה");
  expect(state.photoCalls).toHaveLength(2);
  expect(state.photoCalls[0]).toBe(state.photoCalls[1]);
  expect(state.score).toBe(7);
  expect(state.requests.some(x => x.path.includes("/proof-requests/"))).toBe(true);
});

test("denied geolocation: explicit message and no location upload", async ({ page, context }) => {
  const state = mockBackend(page);
  await context.clearPermissions();
  await page.goto("/");
  await expect(page.locator("#welcome")).toBeVisible();
  await page.locator("#adult").check();
  await page.locator("#start").click();
  await expect(page.locator("#error")).toContainText("נדרשת הרשאת מיקום");
  expect(state.requests.filter(x => x.path === "/api/location")).toHaveLength(0);
});
