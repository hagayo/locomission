/** Fixed, accessible preset avatars. No media uploads or remote image URLs. */
export const AVATARS = Object.freeze([
  { id: "explorer", icon: "🧭", name: "חוקר" },
  { id: "compass", icon: "🗺️", name: "מפה" },
  { id: "torch", icon: "🔦", name: "פנס" },
  { id: "map", icon: "📍", name: "מיקום" },
  { id: "star", icon: "⭐", name: "כוכב" }
]);

export function avatarGlyph(id) {
  return AVATARS.find((avatar) => avatar.id === id)?.icon || AVATARS[0].icon;
}
