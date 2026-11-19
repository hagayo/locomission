import { Clerk } from "@clerk/clerk-js";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./style.css";
import { inspectOrRetry } from "./proofRecovery.js";
import { AVATARS, avatarGlyph } from "./avatars.js";

const $ = (id) => document.getElementById(id);
const panels = ["setup", "welcome", "browse", "story", "group", "result", "profile"];
const app = {
  clerk: null, signInMounted: false, session: null, playing: false,
  watch: null, interval: null, reporting: false, lastReport: 0,
  ready: false, focused: false, location: null,
  missions: [], groups: [], leaders: [], presence: [],
  tab: "missions", selectedMission: null, selectedGroup: null,
  currentMissionStarted: false, cameFromGroup: false,
  pins: [], selfMarker: null, pendingProof: null, proofBusy: false,
  historyCursor: null, historyBusy: false
};
const apiBase = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const map = L.map("map", { zoomControl: false }).setView([31.6, 34.85], 8);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: "© OpenStreetMap contributors"
}).addTo(map);
L.control.zoom({ position: "bottomleft" }).addTo(map);

function show(panel) {
  for (const name of panels) $(name).hidden = name !== panel;
  reportError("");
  requestAnimationFrame(() => map.invalidateSize());
}
function reportError(message) {
  const target = $("error");
  target.textContent = message || "";
  target.hidden = !message;
}
function node(tag, text, cls) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  if (cls) element.className = cls;
  return element;
}
function addEntry(container, title, detail, end, onClick) {
  const element = node(onClick ? "button" : "div", undefined, "entry");
  if (onClick) element.type = "button";
  const block = node("div");
  block.append(node("strong", title), node("small", detail || ""));
  element.append(block, node("aside", end || ""));
  if (onClick) element.addEventListener("click", onClick);
  container.append(element);
}
async function request(path, options = {}, gameSession = true) {
  if (!app.clerk?.session) throw new Error("נדרשת התחברות ל-Clerk");
  const jwt = await app.clerk.session.getToken();
  if (!jwt) throw new Error("ההתחברות הסתיימה, יש להתחבר מחדש");
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", "Bearer " + jwt);
  if (gameSession && app.session) headers.set("X-Game-Session", app.session);
  const response = await fetch(apiBase + path, {
    ...options, headers, cache: "no-store"
  });
  let body = {};
  try { body = await response.json(); } catch { /* sanitized fallback */ }
  if (!response.ok) {
    if (response.status === 401 && gameSession) {
      resetGame();
      show("welcome");
    }
    const error = new Error(typeof body.detail === "string" ? body.detail : "לא ניתן להשלים את הפעולה");
    error.status = response.status;
    throw error;
  }
  return body;
}
async function publicRequest(path) {
  const response = await fetch(apiBase + path, { cache: "no-store" });
  if (!response.ok) throw new Error("לא ניתן לטעון כרגע נתונים");
  return response.json();
}
function stopLocation() {
  if (app.watch !== null) navigator.geolocation.clearWatch(app.watch);
  if (app.interval !== null) clearInterval(app.interval);
  app.watch = null; app.interval = null;
}
function resetGame() {
  stopLocation();
  app.session = null; app.playing = false; app.ready = false;
  app.pendingProof = null; app.proofBusy = false;
  app.historyCursor = null; app.historyBusy = false;
  app.reporting = false; app.lastReport = 0; app.focused = false;
  app.location = null; app.missions = []; app.groups = []; app.presence = [];
  app.pins.forEach((p) => p.remove()); app.pins = [];
  app.selfMarker?.remove(); app.selfMarker = null;
  $("actions").hidden = true;
  $("start").disabled = false;
}
async function endGame() {
  stopLocation();
  if (app.session) {
    try { await request("/api/sessions/current", { method: "DELETE" }); }
    catch { /* Redis TTL will expire even if the network is lost */ }
  }
  resetGame();
}
function renderClerkIdentity() {
  if (!app.clerk) return;
  if (app.clerk.isSignedIn) {
    if (app.signInMounted) {
      app.clerk.unmountSignIn($("sign-in"));
      app.signInMounted = false;
    }
    if (!app.playing) show("welcome");
  } else {
    resetGame();
    show("setup");
    if (!app.signInMounted) {
      app.clerk.mountSignIn($("sign-in"));
      app.signInMounted = true;
    }
  }
}
async function initClerk() {
  const key = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || "";
  if (!key.startsWith(("pk_test_")) && !key.startsWith("pk_live_") ||
      key.includes("REPLACE")) {
    $("setup-note").textContent =
      "יש למלא תחילה VITE_CLERK_PUBLISHABLE_KEY בקובץ mobile/.env";
    return;
  }
  try {
    // Current ClerkJS requires the separate UI bundle for mountSignIn.
    const encodedDomain = key.split("_")[2];
    const decoded = atob(encodedDomain);
    const domain = decoded.endsWith("$") ? decoded.slice(0, -1) : decoded;
    if (!/^[a-z0-9.-]+$/i.test(domain)) throw new Error("כתובת Clerk אינה תקינה");
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://" + domain + "/npm/@clerk/ui@1/dist/ui.browser.js";
      script.async = true; script.crossOrigin = "anonymous";
      script.onload = resolve;
      script.onerror = () => reject(new Error("Clerk UI לא נטען"));
      document.head.appendChild(script);
    });
    const clerk = new Clerk(key);
    await clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
    app.clerk = clerk;
    clerk.addListener(() => renderClerkIdentity());
    renderClerkIdentity();
  } catch {
    $("setup-note").textContent =
      "לא ניתן להתחבר לשירות Clerk. בדוק את מפתח הפרסום והחיבור לרשת.";
  }
}
async function startPlaying() {
  if (!$("adult").checked) {
    reportError("המשחק מיועד לבני 18 ומעלה ויש לאשר את הגיל");
    return;
  }
  $("start").disabled = true;
  try {
    const result = await request("/api/sessions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ adult_confirmed: true })
    }, false);
    app.session = result.session_token;
    app.playing = true; app.ready = false;
    show("browse");
    renderBrowse();
    beginLocation();
  } catch (err) {
    $("start").disabled = false;
    reportError(err.message);
  }
}
function onLocationError(err) {
  reportError(err?.code === 1 ?
    "נדרשת הרשאת מיקום בזמן שימוש כדי להפעיל את המשחק." :
    "לא התקבל מיקום מדויק. בדוק שירותי מיקום וחיבור.");
}
async function locationReceived(position) {
  if (!app.playing || document.hidden || app.reporting) return;
  const now = Date.now();
  if (now - app.lastReport < 32_000) return;
  app.reporting = true;
  try {
    const { latitude, longitude, accuracy } = position.coords;
    await request("/api/location", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lat: latitude, lon: longitude, accuracy_m: accuracy, foreground: true
      })
    });
    app.lastReport = Date.now();
    app.location = { lat: latitude, lon: longitude };
    if (!app.focused) {
      map.setView([latitude, longitude], 15, { animate: false });
      app.focused = true;
    }
    if (!app.selfMarker) {
      app.selfMarker = L.circleMarker([latitude, longitude], {
        radius: 7, fillColor: "#267bb0", fillOpacity: 1, color: "#fff", weight: 2
      }).addTo(map);
    } else app.selfMarker.setLatLng([latitude, longitude]);
    app.ready = true;
    await refreshGame();
  } catch (err) {
    reportError(err.message);
  } finally { app.reporting = false; }
}
function oneLocation() {
  if (!app.playing || document.hidden) return;
  navigator.geolocation.getCurrentPosition(
    locationReceived, onLocationError,
    { enableHighAccuracy: true, maximumAge: 0, timeout: 15_000 }
  );
}
function beginLocation() {
  stopLocation();
  if (document.hidden || !app.playing) return;
  if (!navigator.geolocation) {
    reportError("המכשיר אינו תומך בשירותי מיקום"); return;
  }
  app.watch = navigator.geolocation.watchPosition(
    locationReceived, onLocationError,
    { enableHighAccuracy: true, maximumAge: 0, timeout: 15_000 }
  );
  app.interval = setInterval(oneLocation, 42_000);
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopLocation();
  else if (app.playing) { beginLocation(); oneLocation(); }
});
window.addEventListener("pagehide", stopLocation);

async function refreshGame() {
  if (!app.session || !app.ready) return;
  try {
    // One mobile network round-trip; individual API routes remain supported.
    const snapshot = await request("/api/lobby");
    app.missions = snapshot.missions;
    app.groups = snapshot.groups;
    app.presence = snapshot.presence.cells;
    $("points").textContent = snapshot.profile.total_points;
    $("profile-button").textContent = avatarGlyph(snapshot.profile.avatar_id);
    $("actions").hidden = false;
    renderBrowse();
    renderPins();
  } catch (err) { reportError(err.message); }
}
function renderPins() {
  app.pins.forEach((pin) => pin.remove()); app.pins = [];
  // Red presence pins are coarse cells, never exact location or user identity.
  for (const p of app.presence) {
    const pin = L.circleMarker([p.lat, p.lon], {
      radius: p.players > 1 ? 7 : 5,
      color: "#fff", weight: 1.5, fillColor: "#d94044", fillOpacity: .94
    }).addTo(map);
    app.pins.push(pin);
  }
  const showMissions = app.tab === "missions";
  const collection = showMissions ? app.missions : app.tab === "groups" ? app.groups : [];
  for (const value of collection) {
    const pin = L.circleMarker([value.lat, value.lon], {
      radius: 11, color: "#fff", weight: 2,
      fillColor: showMissions ? "#12343a" : "#3b8266", fillOpacity: 1
    }).addTo(map);
    pin.on("click", () => showMissions ? openMission(value) : openGroup(value));
    app.pins.push(pin);
  }
}
function renderBrowse() {
  $("tab-missions").classList.toggle("selected", app.tab === "missions");
  $("tab-groups").classList.toggle("selected", app.tab === "groups");
  $("tab-leaders").classList.toggle("selected", app.tab === "leaders");
  $("browse-title").textContent = { missions: "משימות", groups: "קבוצות", leaders: "מובילים" }[app.tab];
  const list = $("browse-items");
  list.replaceChildren();
  if (!app.ready) {
    list.append(node("p", "מחפש את המיקום שלך...", "helper"));
    return;
  }
  if (app.tab === "missions") {
    if (!app.missions.length) {
      list.append(node("p", "אין כרגע משימות היסטוריות מאושרות בקרבתך.", "helper")); return;
    }
    app.missions.forEach((m) => addEntry(
      list, m.title_he, m.completed ? "הושלמה" : "סיפור היסטורי ומשימת צילום",
      m.points + " נקודות", () => openMission(m)
    ));
  } else if (app.tab === "groups") {
    if (!app.groups.length) {
      list.append(node("p", "אין כרגע אתגר קבוצתי באזור שלך.", "helper")); return;
    }
    app.groups.forEach((g) => addEntry(
      list, g.title_he, g.completed + " מתוך " + g.missions + " הושלמו",
      g.members + " שחקנים", () => openGroup(g)
    ));
  } else {
    if (!app.leaders.length) {
      list.append(node("p", "אין עדיין שחקנים בלוח המובילים.", "helper")); return;
    }
    app.leaders.forEach((leader, index) => addEntry(
      list, (index + 1) + ". " + avatarGlyph(leader.avatar_id) + " " + leader.display_name,
      leader.completed_missions + " משימות הושלמו",
      leader.total_points + " נקודות", null
    ));
  }
}
async function switchTab(tab) {
  app.tab = tab;
  show("browse");
  if (tab === "leaders") {
    try { app.leaders = (await publicRequest("/api/leaderboard")).leaders; }
    catch (err) { reportError(err.message); }
  }
  renderBrowse(); renderPins();
}
function openMission(mission, fromGroup = false) {
  // A file remains only in memory until its result is recovered or abandoned.
  if (app.pendingProof?.missionId !== mission.id) app.pendingProof = null;
  app.selectedMission = mission;
  app.cameFromGroup = fromGroup;
  app.currentMissionStarted = !!mission.started_today;
  show("story");
  $("story-title").textContent = mission.title_he;
  $("story-fact").textContent = mission.historical_fact_he;
  $("story-instructions").textContent = mission.instructions_he;
  const link = $("story-source");
  link.textContent = "המקור ההיסטורי: " + mission.source_title;
  try {
    const uri = new URL(mission.source_url);
    link.href = uri.protocol === "https:" ? uri.href : "#";
  } catch { link.href = "#"; }
  const criteria = $("story-criteria"); criteria.replaceChildren();
  for (const criterion of mission.visual_criteria_he) criteria.append(node("li", criterion));
  const btn = $("mission-action");
  btn.disabled = !!mission.completed;
  btn.textContent = mission.completed ? "המשימה כבר הושלמה" :
    app.pendingProof ? "בדוק תוצאה" :
    app.currentMissionStarted ? "צלם הוכחה" : "התחל משימה";
}
async function openGroup(group) {
  app.selectedGroup = group;
  show("group");
  $("group-title").textContent = group.title_he;
  $("group-description").textContent = group.description_he;
  $("group-progress").textContent = group.completed + " מתוך " + group.missions + " משימות הושלמו";
  $("group-presence").hidden = true;
  $("group-list").replaceChildren();
  $("group-join").hidden = false;
  try {
    const goals = (await request("/api/groups/" + group.id + "/missions")).missions;
    if (goals.length) renderGroupGoals(goals);
    await loadGroupPresence(group.id);
  } catch (err) { reportError(err.message); }
}
async function loadGroupPresence(groupId) {
  try {
    const summary = await request("/api/groups/" + groupId + "/presence");
    if (app.selectedGroup?.id !== groupId) return;
    $("group-join").hidden = true;
    const target = $("group-presence");
    target.textContent = summary.active_nearby +
      (summary.truncated ? "+" : "") + " משתתפים פעילים בקרבתך";
    target.hidden = false;
  } catch (error) {
    if (error.status !== 403) reportError(error.message);
  }
}
function renderGroupGoals(goals) {
  $("group-join").hidden = true;
  const list = $("group-list"); list.replaceChildren();
  if (!goals.length) list.append(node("p", "טרם נקבעו משימות לאתגר.", "helper"));
  for (const mission of goals) addEntry(
    list, mission.title_he,
    mission.completed_by_group ? "הושלמה על ידי הקבוצה" : "משימה קבוצתית",
    mission.points + " נקודות", () => openMission(mission, true)
  );
}
function showProofResult(outcome) {
  app.pendingProof = null; // Release photo bytes from application memory.
  if (outcome.accepted) app.selectedMission.completed = true;
  $("result-title").textContent = outcome.accepted ?
    "המשימה הושלמה" : "צריך ניסיון נוסף";
  $("result-description").textContent =
    (outcome.points > 0 ? "+" + outcome.points + " נקודות. " : "") +
    (outcome.reason_he || "");
  $("result-next").textContent = outcome.accepted ? "חזרה למפה" : "צלם שוב";
  $("result-next").onclick = outcome.accepted ? async () => {
    show("browse"); await refreshGame();
  } : () => show("story");
  show("result");
}
async function postPendingProof(pending) {
  return request("/api/missions/" + pending.missionId + "/proof", {
    method: "POST",
    headers: {
      "Content-Type": pending.file.type, "X-Request-ID": pending.requestId
    },
    body: pending.file
  });
}
async function handlePendingProof(recover = false) {
  const pending = app.pendingProof;
  if (!pending || app.proofBusy || !app.session) return;
  const initialSession = app.session;
  const button = $("mission-action");
  app.proofBusy = true;
  button.disabled = true;
  button.textContent = recover ? "בודק את התוצאה..." : "מאמת את התמונה...";
  try {
    const decision = recover ? await inspectOrRetry(
      () => request(
        "/api/missions/" + pending.missionId + "/proof-requests/" + pending.requestId
      ),
      () => postPendingProof(pending)
    ) : { state: "completed", outcome: await postPendingProof(pending) };
    if (app.session !== initialSession || app.pendingProof !== pending) return;
    if (decision.state === "processing") {
      reportError("אימות התמונה עדיין מתבצע. אפשר לבדוק שוב בעוד רגע.");
      return;
    }
    showProofResult(decision.outcome);
  } catch (error) {
    if (app.session !== initialSession || app.pendingProof !== pending) return;
    const duplicatePhoto = error.status === 409 &&
      error.message.includes("התמונה כבר נשלחה");
    if ([400, 403, 404, 413, 415, 422].includes(error.status) || duplicatePhoto) {
      // A permanent validation/location failure needs a fresh photo or mission.
      app.pendingProof = null;
      reportError(error.message);
    } else if (error.status === 429) {
      reportError("שירות בדיקת התמונות עמוס. נסה לבדוק שוב בעוד רגע.");
    } else if (error.status === 409) {
      reportError("כבר מתבצע אימות למשימה. אפשר לבדוק את התוצאה שוב.");
    } else {
      // No second photo is required. The same UUID can be polled and retried.
      reportError("התקשורת נקטעה או שהאימות לא הושלם. לחץ שוב לבדיקת התוצאה.");
    }
  } finally {
    app.proofBusy = false;
    button.disabled = false;
    if (app.pendingProof === pending) button.textContent = "בדוק תוצאה";
    else if (!$("story").hidden) button.textContent =
      app.currentMissionStarted ? "צלם הוכחה" : "התחל משימה";
  }
}
async function submitPhoto(file) {
  if (!file || !app.selectedMission || app.pendingProof) return;
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    reportError("יש לבחור תמונת JPEG, PNG או WebP");
    return;
  }
  if (!file.size || file.size > 5 * 1024 * 1024) {
    reportError("יש לבחור תמונה בגודל עד 5MB");
    return;
  }
  app.pendingProof = {
    missionId: app.selectedMission.id,
    requestId: crypto.randomUUID(),
    file
  };
  await handlePendingProof();
}
function showStat(label, value) {
  const element = node("div");
  element.append(node("span", label), node("strong", value));
  $("profile-stats").append(element);
}
function renderAvatarOptions(selected) {
  const picker = $("avatar-options");
  picker.replaceChildren();
  for (const preset of AVATARS) {
    const button = node("button", preset.icon, "avatar-choice");
    button.type = "button";
    button.title = preset.name;
    button.setAttribute("aria-label", preset.name);
    button.setAttribute("aria-pressed", String(preset.id === selected));
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await request("/api/me/avatar", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ avatar_id: preset.id })
        });
        $("profile-button").textContent = avatarGlyph(result.avatar_id);
        renderAvatarOptions(result.avatar_id);
      } catch (err) { reportError(err.message); }
      finally { button.disabled = false; }
    });
    picker.append(button);
  }
}

async function loadAchievements() {
  const result = await request("/api/me/achievements");
  const list = $("achievement-list");
  list.replaceChildren();
  const achieved = result.achievements.filter((item) => item.unlocked);
  if (!achieved.length) {
    list.append(node("p", "ההישג הראשון ייפתח אחרי השלמת משימה.", "helper"));
  } else {
    for (const item of achieved) {
      list.append(node("div", "✓ " + item.title_he, "achievement-entry"));
    }
  }
  const next = result.achievements.find((item) => !item.unlocked);
  if (next) {
    list.append(node("p",
      "הבא: " + next.title_he + " (" + next.current + "/" + next.goal + ")",
      "helper"));
  }
}

async function loadHistory(firstPage = false) {
  if (app.historyBusy) return;
  const session = app.session;
  app.historyBusy = true;
  $("history-more").disabled = true;
  try {
    const params = new URLSearchParams({ limit: "10" });
    if (!firstPage && app.historyCursor) {
      params.set("before", app.historyCursor);
    }
    const data = await request("/api/me/history?" + params);
    if (app.session !== session) return;
    const list = $("history-list");
    if (firstPage) list.replaceChildren();
    if (firstPage && !data.items.length) {
      list.append(node("p", "עדיין לא השלמת משימות.", "helper"));
    }
    for (const item of data.items) {
      const date = new Date(item.completed_at).toLocaleDateString("he-IL");
      addEntry(list, item.title_he, date, "+" + item.points_awarded + " נקודות", null);
    }
    app.historyCursor = data.next_before;
    $("history-more").hidden = !data.next_before;
  } catch (err) { reportError(err.message); }
  finally {
    app.historyBusy = false;
    $("history-more").disabled = false;
  }
}

async function openProfile() {
  try {
    const me = await request("/api/me");
    show("profile");
    $("display-name").value = me.display_name;
    $("profile-button").textContent = avatarGlyph(me.avatar_id);
    renderAvatarOptions(me.avatar_id);
    $("profile-stats").replaceChildren();
    showStat("נקודות משחק", me.total_points);
    showStat("משימות שהתחלת", me.missions_started);
    showStat("משימות שהשלמת", me.completed_missions);
    app.historyCursor = null;
    $("history-list").replaceChildren();
    $("achievement-list").replaceChildren();
    await Promise.all([loadAchievements(), loadHistory(true)]);
  } catch (err) { reportError(err.message); }
}
$("start").addEventListener("click", startPlaying);
$("tab-missions").addEventListener("click", () => switchTab("missions"));
$("tab-groups").addEventListener("click", () => switchTab("groups"));
$("tab-leaders").addEventListener("click", () => switchTab("leaders"));
$("rank-button").addEventListener("click", () => switchTab("leaders"));
$("profile-button").addEventListener("click", openProfile);
$("history-more").addEventListener("click", () => loadHistory());
$("story-back").addEventListener("click", () => {
  if (app.cameFromGroup && app.selectedGroup) openGroup(app.selectedGroup);
  else { show("browse"); renderBrowse(); }
});
$("group-back").addEventListener("click", () => { show("browse"); renderBrowse(); });
$("profile-back").addEventListener("click", () => { show("browse"); renderBrowse(); });
$("mission-action").addEventListener("click", async () => {
  if (!app.selectedMission) return;
  if (app.pendingProof) { await handlePendingProof(true); return; }
  if (app.currentMissionStarted) { $("camera").click(); return; }
  $("mission-action").disabled = true;
  try {
    await request("/api/missions/" + app.selectedMission.id + "/start",
      { method: "POST" });
    app.currentMissionStarted = true;
    app.selectedMission.started_today = true;
    $("mission-action").textContent = "צלם הוכחה";
  } catch (err) { reportError(err.message); }
  finally { $("mission-action").disabled = false; }
});
$("camera").addEventListener("change", (event) => {
  const file = event.target.files?.[0]; event.target.value = "";
  if (file) submitPhoto(file);
});
$("group-join").addEventListener("click", async () => {
  if (!app.selectedGroup) return;
  const button = $("group-join"); button.disabled = true;
  try {
    await request("/api/groups/" + app.selectedGroup.id + "/join", { method: "POST" });
    renderGroupGoals((await request(
      "/api/groups/" + app.selectedGroup.id + "/missions"
    )).missions);
    await loadGroupPresence(app.selectedGroup.id);
    await refreshGame();
  } catch (err) { reportError(err.message); }
  finally { button.disabled = false; }
});
$("profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await request("/api/me", {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: $("display-name").value.trim() })
    });
    await openProfile();
  } catch (err) { reportError(err.message); }
});
$("leave").addEventListener("click", async () => {
  await endGame(); show("welcome");
});
$("sign-out").addEventListener("click", async () => {
  await endGame(); await app.clerk?.signOut();
  show("setup");
});
initClerk();
