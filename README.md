# ClipMinute

**A ready-to-post short, in about a minute.**

ClipMinute is a Windows desktop app for independent short-form creators: turn a topic into a
finished vertical video — script, AI voice-over, karaoke captions, stock footage — and publish it
to **your own TikTok**.

- 🌐 Website: **https://st0wrg.github.io/clipminute/**
- ⬇ Download: **[Releases](https://github.com/St0wRG/clipminute/releases)** (Windows 10/11, 64-bit)
- 🔒 [Privacy Policy](https://st0wrg.github.io/clipminute/privacy.html) · 📄 [Terms of Service](https://st0wrg.github.io/clipminute/terms.html)
- 🛡️ [Security policy](SECURITY.md)

---

## What it does

| | |
|---|---|
| 🎙️ **AI voice-over** | Natural multilingual narration, adjustable pace — no mic, no face |
| 🔤 **Karaoke captions** | Word-synced highlighted subtitles that lift retention |
| 🎞️ **Stock footage** | Auto-matched royalty-free background clips, credited automatically |
| 🗓️ **Rolling calendar** | Always keeps 7 days planned ahead; the AI can tune the posting schedule from your real stats |
| ✂️ **Series auto-split** | Long stories cut at sentence ends into Part 1 / 2 / 3 with cliffhangers |
| 📈 **Your stats** | Views, likes and followers for your own account, inside the app |
| ⬆ **Built-in updates** | One-click, integrity-checked updates from inside the app |

## How it works

```
topic ──► script (Claude)  ──► voice-over (edge-tts, word timings)
                               │
                               ├─► karaoke subtitles (ASS)
                               ├─► scene-matched stock footage (Pexels / Pixabay / archives)
                               └─► 9:16 render (ffmpeg) ──► your TikTok (official Content Posting API)
```

Everything runs **locally on the creator's own computer**. Each creator connects their own TikTok
account with TikTok Login; ClipMinute never touches any other account.

## Architecture

| Path | Role |
|---|---|
| `app/dashboard.py` | Flask app (local UI on `127.0.0.1:5877`), routes and API |
| `app/pipeline/` | Production engine: `story` (script), `tts`, `subtitles`, `background`, `render`, `clipper`, `publish`, `tiktok`, `stats`, `calendrier`, `tendances`, `abonnement`, `comptes` |
| `app/run_pipeline.py` | CLI entry point for a single job (runs as a detached subprocess) |
| `app/scheduler.py` | Periodic tick: due slots, rolling calendar, stats, weekly report |
| `app/templates/`, `app/static/` | UI (dark theme, native-window title bar, launch screen) |
| `app/installer/` | Build scripts: payload assembly + Inno Setup script |

**Multi-tenancy** — every account's data lives in its own `data/<uid>/` directory
(`uid = sha1(email)[:16]`). A context variable plus path/dict proxies route every read and write to
the current account, so accounts stay isolated. Nothing sensitive is shared between them.

## Build from source

Requires Windows, Python 3.12, and [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```bash
python -m venv venv && venv\Scripts\pip install -r app/requirements.txt
venv\Scripts\python app/installer/build_installer.py
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" app/installer/clipminute.iss
```

The installer lands in `app/installer/Output/`. The build **strips every secret** from the embedded
config (API keys, TikTok tokens) and aborts if any is still present.

## Configuration

Each user supplies their own API keys in **Settings**, stored only on their machine:

| Key | Used for | Required |
|---|---|---|
| Anthropic | script writing | yes |
| Pexels / Pixabay | stock footage | optional (falls back to gradients) |
| TikTok Client Key + Secret | publishing to your account | optional |

## Security

Security is treated as a first-class concern: local-only data, strict same-origin and host checks,
integrity-verified updates, and output escaping throughout. See **[SECURITY.md](SECURITY.md)** for
the threat model, hardening measures and how to report a vulnerability.

## Legal

ClipMinute is an independent creator tool and is **not affiliated with, sponsored by, or endorsed
by TikTok**. TikTok is a trademark of its respective owner. Creators are responsible for the content
they publish and for respecting the rights attached to any material they use.
