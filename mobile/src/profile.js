/**
 * All personal account UI is housed in the existing profile sheet.
 * No global browser state, storage or extra navigation is introduced.
 */
export function createProfileController({
  request, getSession, node, addEntry, show, reportError,
  avatarGlyph, avatars, onAvatarChanged, documentRef = document
}) {
  const $ = (id) => documentRef.getElementById(id);
  let historyCursor = null;
  let historyBusy = false;

  function showStat(label, value) {
    const element = node("div");
    element.append(node("span", label), node("strong", value));
    $("profile-stats").append(element);
  }

  function renderAvatarOptions(selected) {
    const picker = $("avatar-options");
    picker.replaceChildren();
    for (const preset of avatars) {
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
          onAvatarChanged(avatarGlyph(result.avatar_id));
          renderAvatarOptions(result.avatar_id);
        } catch (err) { reportError(err.message); }
        finally { button.disabled = false; }
      });
      picker.append(button);
    }
  }

  async function loadAchievements() {
    const session = getSession();
    const result = await request("/api/me/achievements");
    if (getSession() !== session) return;
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
    if (historyBusy || !getSession()) return;
    const session = getSession();
    historyBusy = true;
    $("history-more").disabled = true;
    try {
      const params = new URLSearchParams({ limit: "10" });
      if (!firstPage && historyCursor) params.set("before", historyCursor);
      const data = await request("/api/me/history?" + params);
      if (getSession() !== session) return;
      const list = $("history-list");
      if (firstPage) list.replaceChildren();
      if (firstPage && !data.items.length) {
        list.append(node("p", "עדיין לא השלמת משימות.", "helper"));
      }
      for (const item of data.items) {
        const date = new Date(item.completed_at).toLocaleDateString("he-IL");
        addEntry(
          list, item.title_he, date,
          "+" + item.points_awarded + " נקודות", null
        );
      }
      historyCursor = data.next_before;
      $("history-more").hidden = !data.next_before;
    } catch (err) { reportError(err.message); }
    finally {
      historyBusy = false;
      $("history-more").disabled = false;
    }
  }

  async function open() {
    try {
      const session = getSession();
      const me = await request("/api/me");
      if (getSession() !== session) return;
      show("profile");
      $("display-name").value = me.display_name;
      onAvatarChanged(avatarGlyph(me.avatar_id));
      renderAvatarOptions(me.avatar_id);
      $("profile-stats").replaceChildren();
      showStat("נקודות משחק", me.total_points);
      showStat("משימות שהתחלת", me.missions_started);
      showStat("משימות שהשלמת", me.completed_missions);
      historyCursor = null;
      $("history-list").replaceChildren();
      $("achievement-list").replaceChildren();
      await Promise.all([loadAchievements(), loadHistory(true)]);
    } catch (err) { reportError(err.message); }
  }

  $("history-more").addEventListener("click", () => loadHistory());
  return {
    open,
    reset: () => { historyCursor = null; historyBusy = false; }
  };
}
