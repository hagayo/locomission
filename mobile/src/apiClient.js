/**
 * LocoMission transport: isolate authentication, HTTP errors and binary
 * uploads from the view layer. All tokens stay in JS memory.
 */
export function createApiClient({
  baseUrl = "", getClerkSession, getGameSession,
  onSessionExpired = () => {}, fetchImpl = fetch
}) {
  const root = baseUrl.replace(/\/$/, "");
  async function request(path, options = {}, gameSession = true) {
    const session = getClerkSession();
    if (!session) throw new Error("נדרשת התחברות ל-Clerk");
    const jwt = await session.getToken();
    if (!jwt) throw new Error("ההתחברות הסתיימה, יש להתחבר מחדש");
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", "Bearer " + jwt);
    const gameToken = getGameSession();
    if (gameSession && gameToken) {
      headers.set("X-Game-Session", gameToken);
    }
    const response = await fetchImpl(root + path, {
      ...options, headers, cache: "no-store"
    });
    let body = {};
    try { body = await response.json(); }
    catch { /* generic safe failure message */ }
    if (!response.ok) {
      if (response.status === 401 && gameSession) onSessionExpired();
      const error = new Error(
        typeof body.detail === "string"
          ? body.detail : "לא ניתן להשלים את הפעולה"
      );
      error.status = response.status;
      const delay = Number(response.headers.get("Retry-After"));
      if (Number.isFinite(delay) && delay > 0) error.retryAfterSeconds = delay;
      throw error;
    }
    return body;
  }

  async function publicRequest(path) {
    const response = await fetchImpl(root + path, { cache: "no-store" });
    if (!response.ok) throw new Error("לא ניתן לטעון כרגע נתונים");
    return response.json();
  }
  return { request, publicRequest };
}
