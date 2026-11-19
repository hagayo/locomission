# Mobile-first UI contract

The Android native versus Web/PWA decision is pending. These design rules apply to either client and to the marketing site.

- Design at **320-375 CSS px** first, before adapting to larger screens.
- Map is the default gameplay surface; do not put large overlays on top of it.
- Only one primary action per step. Place the photo shutter control within natural thumb reach, accounting for the OS safe area.
- The shortest navigation path is: map discovery -> historical story -> camera -> result.
- Prefer progressive disclosure to status cards, badges, extra descriptions and redundant metadata.
- Group challenges appear on the same map and mission flow; no mandatory room-code gate.
- Hebrew UI is RTL, with deliberate handling of mixed numerals, times and coordinates.
- Do not assume full viewport height with the camera, system keyboard or navigation bars visible.
- The device explicitly requests foreground-only location permission and does not collect background movement.
- Home, lobby, payments (when added) and privacy views should repeat consistent, accurate privacy/permission language without duplicating it unnecessarily on every screen.
- Support a clear offline/no-permission state without imitating success or collecting location in the background.
