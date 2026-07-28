# Security Policy

## Reporting a vulnerability

Please report security issues privately to **truchy.alex@orange.fr** rather than opening a public
issue. Include steps to reproduce and the impact you observed. We aim to acknowledge reports within
72 hours.

## Threat model

ClipMinute is a **desktop application**: a Flask server bound to `127.0.0.1:5877`, rendered in a
native window (WebView2) on the user's own machine. That shape drives the threats we defend against:

| # | Threat | Why it matters here |
|---|---|---|
| T1 | A malicious web page in the user's browser reaching `127.0.0.1:5877` (CSRF, DNS rebinding) | The local API can publish videos, change settings, run an installer |
| T2 | Untrusted third-party content rendered in the UI (video titles, TikTok display names, file names, remote release notes) | XSS inside the app window would run with full app privileges |
| T3 | A tampered update binary | Updates download and execute an `.exe` |
| T4 | Server-side request forgery via a pasted link | `yt-dlp` would otherwise reach the user's local network |
| T5 | Cross-account data leakage | Multiple accounts can exist on one machine |
| T6 | Secrets leaking into the public repository or the distributed installer | API keys and OAuth tokens |

## Hardening measures

**Network boundary (T1)**
- Every request must carry an allow-listed `Host` (`127.0.0.1:5877` / `localhost:5877`); anything
  else is rejected with `403`. This defeats DNS rebinding, where an attacker's domain re-resolves to
  the loopback address.
- Every mutating request (`POST`/`PUT`/`PATCH`/`DELETE`) must originate from the app's own origin,
  checked via `Origin` (falling back to `Referer`). Rejections are logged.
- Session cookies are `HttpOnly` and `SameSite=Strict` unconditionally.

**Output encoding (T2)**
- The activity log is rendered through DOM construction with `textContent` — untrusted values can
  never become markup.
- Clip titles, TikTok metadata, job labels and remote release notes are HTML-escaped, including
  quotes, so values placed in attributes cannot break out.
- Weekly reports are served as `text/plain`, never interpolated into HTML.

**Update integrity (T3)**
- The update manifest URL is a **fixed constant**; it cannot be overridden through configuration.
- The download URL must be HTTPS **and** on a pinned domain.
- The manifest must carry a SHA-256 digest. The downloaded file is hashed and compared **before**
  execution; on mismatch the file is deleted and nothing runs.
- The version string is validated against a strict pattern before being used in a file path.

**Outbound requests (T4)**
- Pasted links are restricted to `http`/`https`, and the resolved address must be public. Private,
  loopback, link-local, reserved and multicast ranges are refused.

**Tenant isolation (T5)**
- Each account's data lives under `data/<uid>/`. A context variable set per request routes all
  reads and writes; background jobs receive the account explicitly (`--user`) rather than inheriting
  ambient state.

**Secret handling (T6)**
- Secrets (API keys, OAuth tokens), account records, the session key and all user data are excluded
  from version control; the repository is built from an explicit allow-list of publishable files.
- The installer build strips secrets from the embedded configuration and aborts if any remain.

## Known limitations

We prefer stating these plainly over implying stronger guarantees than we provide:

- **Secrets are stored unencrypted at rest** in the user's profile directory. Any process running as
  that user can read them. OS-level encryption (Windows DPAPI) is planned.
- **Releases are not code-signed yet.** Windows SmartScreen will warn on first download. SHA-256
  digests are published on every release so downloads can be verified manually.
- **Signed-in convenience:** after a successful sign-in, the app remembers the last profile on that
  machine and lets it sign back in with one click, without re-entering the password — a
  trusted-device model appropriate for a local desktop app, not for a shared computer.
- Anyone with local administrator access to the machine can read application data. ClipMinute does
  not defend against a compromised operating system.
