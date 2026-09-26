# SOC_BOT: Master Project Handoff

> **Purpose of this file:** a single document from which a new developer or AI agent (Claude Code, OpenCode, …) can understand Soc_bot end to end, from the first commit to the current state, without the earlier conversations.
>
> **Written:** 2026-09-26, against commit `49ce462` (working tree clean at the time of writing).
> **Source of truth:** the repository. Where this file and the code disagree, **the code wins**; fix this file.
> **Markers used:**
> - **[CURRENT]**: verified in the current source code or tests.
> - **[HISTORY]**: taken from the docs in `docs/` or from the development session record. It describes what happened then, not necessarily what the code does now.
> - **"Not verified from current repository."**: the claim could not be checked in the repo.

---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Product goal](#2-product-goal)
3. [Development timeline](#3-development-timeline)
4. [Final architecture](#4-final-architecture)
5. [Source code structure](#5-source-code-structure)
6. [Main entry point](#6-main-entry-point)
7. [TUI](#7-tui)
8. [Service layer](#8-service-layer)
9. [Account system](#9-account-system)
10. [OAuth](#10-oauth)
11. [Instagram](#11-instagram)
12. [Instagram cover](#12-instagram-cover)
13. [The large-video problem](#13-the-large-video-problem)
14. [Media providers](#14-media-providers)
15. [Cloudflare Quick Tunnel](#15-cloudflare-quick-tunnel)
16. [SharedMediaSession](#16-sharedmediasession)
17. [Multi-account fan-out](#17-multi-account-fan-out)
18. [Five-at-a-time queue](#18-five-at-a-time-queue)
19. [Automatic retry](#19-automatic-retry)
20. [Retry bug history](#20-retry-bug-history)
21. [Resume and crash safety](#21-resume-and-crash-safety)
22. [Failure isolation](#22-failure-isolation)
23. [Published links](#23-published-links)
24. [Content system](#24-content-system)
25. [Database](#25-database)
26. [Security](#26-security)
27. [Testing history](#27-testing-history)
28. [Real test results](#28-real-test-results)
29. [Bug and mistake history](#29-bug-and-mistake-history)
30. [Why the failed approaches failed](#30-why-the-failed-approaches-failed)
31. [Current configuration](#31-current-configuration)
32. [File and folder rules](#32-file-and-folder-rules)
33. [Git history](#33-git-history)
34. [Current project state](#34-current-project-state)
35. [Known limitations and known bugs](#35-known-limitations-and-known-bugs)
36. [Future development rules](#36-future-development-rules)
37. [How another AI should start](#37-how-another-ai-should-start)
38. [Quick reference](#38-quick-reference)
39. [Final architecture diagram](#39-final-architecture-diagram)
40. [Instructions to the next AI](#40-instructions-to-the-next-ai)

---

## 1. Project overview

| Item | Value |
|---|---|
| Project name | **Soc_bot** (the UI brand is "SOC_BOT"; `--version` prints `SOC_BOT v1.0.0`) |
| Local path | `D:\Soc_bot` (Windows 11; Python 3.10 on the development machine) |
| Repository | `https://github.com/ayushrijal83-ops/Soc_bot.git` (`git remote -v`) |
| Branch | `main` |
| License | MIT (`LICENSE`) |
| Type | Terminal application: a Textual + Rich full-screen TUI, plus a classic text menu (`--plain`) |
| Platforms | Instagram (Reels), YouTube, TikTok, all through official APIs |

**What it does [CURRENT].** Soc_bot publishes a finished local video, with a caption and an optional cover image, to many connected accounts on Instagram, YouTube and TikTok in one operation. Each account becomes an independent publish job with its own state, retries and result. Permanent post URLs are saved automatically to `content/published_links/`.

**What it intentionally does NOT do:**

| Not done | Why |
|---|---|
| AI content generation (video or captions) | ADR-009. The user supplies the finished content. |
| Scraping | ADR-003: official APIs only. Scraping YouTube or using yt-dlp was explicitly rejected (ADR-023). |
| Browser automation for publishing | Official APIs only. A browser is used only for the platforms' own OAuth consent pages. |
| Fake engagement (likes, views, follows) | Out of scope by design. |
| Account creation | Accounts are created by humans on the platforms and then connected with OAuth. |
| Social-media password login | Only OAuth. Soc_bot never sees passwords. |
| Unofficial or private APIs | ADR-003. |
| Permanent media hosting | Media is exposed only temporarily and only while a batch publishes. There is no media archive. |

---

## 2. Product goal

```
ONE finished video  +  ONE caption  +  ONE optional cover  +  N selected accounts
                                   │
                                   ▼
                      ONE publishing batch (= one post row)
                                   │
                                   ▼
          N platform-specific jobs (one per account, independent)
                                   │
                                   ▼
        automatic queue (Instagram: at most 5 active at a time)
                                   │
                                   ▼
            publication on each platform (official APIs)
                                   │
                                   ▼
    permanent links saved (content/published_links/*.json + .txt)
```

Soc_bot is fundamentally a **distribution tool**. Creative work happens elsewhere. Soc_bot's job is to take one finished asset and deliver it reliably to many destinations:

- one confirmation for the whole batch;
- correct per-account outcomes (one failure never blocks the others);
- no duplicate posts on resume;
- a clean record of where everything was published.

---

## 3. Development timeline

All dates come from `git log` and the docs. Development ran from **2026-09-24 to 2026-09-26**. Test counts are the ones recorded in the docs or session reports at that time.

| # | Phase | Purpose | Main implementation | Tests | Real verification | Problems found → change |
|---|---|---|---|---|---|---|
| 0 | Initialization (`90312a5`, `501107f`, 2026-09-24) | Skeleton + docs | Folder structure, 11+ docs, `.gitignore`, `.env.example`, requirements | none | none | none |
| 1 | Database foundation (`8618a2a`) | Persistence + token encryption | `src/storage/database.py` (SQLAlchemy models, migration runner), `src/storage/tokens.py` (Fernet), migration 001 | 47 | none | none |
| 2 | CLI framework (`2360f3d`, `1434e14` "phase 2A") | Menus, prompts, entry point | `main.py`, `src/cli/menu.py`, `prompts.py`, `display.py` | 100 | none | none |
| 3A | Account manager | CRUD, status, encrypted tokens | `src/accounts/manager.py`, accounts submenu | 138 | none | none |
| 3B | OAuth (`434adaa`, fixes `ab4b6c3`) | Official OAuth for 3 platforms | `src/auth/*` (state, PKCE, callback server, AuthManager), `src/platforms/*/auth.py` | 170 → **214** after the audit | none | Audit: TikTok needs hex PKCE; Instagram moved from Facebook Login to **Instagram Login**; dynamic loopback port; `expires_at` crash; 80 Ruff errors |
| 4 | Publishing engine (`a6a0fd9`) | Jobs, state machine, adapters | `src/core/{jobs,publisher,validation}.py`, 3 publishers, migration 002 | **305** | none (mocked only) | 4 bugs: `run_menu` crash, missing AuthManager, frozen timestamps, migration runner skipping SQL |
| 5 | Real provider setup (`0cb5c05`) | `.env` loading, real YouTube | `load_environment()` | +tests | **YouTube OAuth (2 channels) + upload: real** | `secrets/` added to `.gitignore` |
| 5A | Content intake (`d2070f7`) | Drop-folder publishing, profiles, covers | `src/content/*`, `src/cli/content_menu.py`, migration 003 | ~402 (with 5B) | Real YouTube private upload + custom thumbnail (`rAGivy-c3DM`) | Dead duplicate "Exit" menu item; `.gitignore` `content/` would have hidden `src/content/` |
| 5B | TikTok verification (`86cacd1`) | TikTok real-run prep | Scopes `user.info.basic,video.publish`, creator_info preflight, scope checks | **402** | **Not run** (no TikTok credentials) | `"error":{"code":"ok"}` treated as a failure → fixed |
| 5C | Instagram OAuth audit | Make Instagram login work | Registered redirect, **paste mode** + GitHub Pages callback page | **425** | Real Instagram OAuth later succeeded (noxivra_01) | Wrong app credentials in `.env` (Meta app ID instead of Instagram app ID); dynamic port can't match a registered redirect |
| M1 | Instagram media delivery (`47ca1fd`, `dc11345`) | Instagram needs a public `video_url` | `src/media_storage/` (S3 presigned), then 0x0.st, TempFile.org, `auto` router | 466 → 522 → 574 → **602** | **First real Reel via TempFile (2026-09-25)** | Stale reused video path; storage 403 treated as retryable; 0x0.st uploads disabled |
| M2 | Published-link library | Save permanent URLs | `src/core/published_links.py`, menu "Published Links" | **639** | Real links saved | none |
| M3 | Cloudflare Quick Tunnel (`ec6b76f`, 2026-09-25) | Videos > 99 MB without cloud hosting | `src/media_storage/cloudflare_tunnel.py`; AUTO = TempFile → Tunnel → S3 | **701** | Real 131.9 MB tunnel test (SHA-256 match) + real 131.9 MB Reel | Wrong hostname parsed (`api.trycloudflare.com`); DNS negative cache; token logged by httpx |
| M4 | Fan-out + custom cover (`2c0807d`, 2026-09-26) | 1 video + 1 cover → N accounts | `cover_url`, cover-aware router, thread pool (max 5), multi-select | **746** | Real 11-account and 5-account batches, covers verified | Stale RETRYING job resumed → unintended Reel; one noxivra_100 container ERROR (Instagram-side) |
| M5 | Link finalization | TXT exports, file lock | `.txt` per platform, OS file lock, `permalink_missing` | **754** | 1- and 5-account runs; 11-account run **failed with Cloudflare 429** | Misleading "did not start within 90s" error → fixed |
| M6 | **LOCKED batch architecture** (`e8f612a`) | Fix the 429 problem; add a retry | `SharedMediaSession` (1 tunnel per batch), one automatic retry round, migration 004 | 777 (session report) / 779 (PROJECT_STATUS) | Real 2 / 5 / 11 accounts + 131.9 MB × 2: **1 tunnel per batch every time** | Flaky ordering assertion in a new test → fixed |
| M7 | Final engine audit (`3f554fb`) | Freeze the engine | Retry-round tunnel-cooldown fix | **780** | Real regressions: posts 22 / 23 / 24 | HIGH: cached tunnel failure could consume the automatic retry → fixed. Windows socket reset flake + thread-start-order flake → fixed |
| T1 | Textual TUI | Full-screen terminal UI | `src/services/*`, `src/tui/*`; classic menu kept as `--plain` | **792** | Real TUI publish (post 25, noxivra_07) | Review Publish button hidden; sidebar active state; OAuth `asyncio.run` inside Textual's loop |
| T2 | TUI polish, docs, launcher (`ef43c56`, `49ce462`) | Daily usability | `force_reauth`, paste field, checksum cache, 15 s polling, delete history, `setup_guide/`, `Start_Soc_bot.bat`, README screenshots | **823** | Add-account flow manually confirmed by the user (not independently re-verified here) | Paste field off-screen; "Tiktok" capitalization; sidebar contrast |

---

## 4. Final architecture

### 4.1 Layers [CURRENT]

```
                    SOC_BOT TUI (src/tui)              Classic menu (src/cli, --plain)
                          │                                     │
                          ▼                                     │
                  Services (src/services)                       │
                          │                                     │
                          ▼                                     ▼
          PublisherEngine + JobStore (src/core)  ◄──── ContentIntake (src/content)
                          │
         ┌────────────────┼─────────────────┐
         ▼                ▼                 ▼
 InstagramPublisher  YouTubePublisher  TikTokPublisher    (src/platforms/*/publisher.py)
         │
         ▼
 SharedMediaSession ─► MediaSourceProvider (router / Cloudflare tunnel / TempFile / S3 / 0x0)
                                                          (src/media_storage)
 AccountManager + AuthManager (src/accounts, src/auth, src/platforms/*/auth.py)
 Database + TokenEncryption (src/storage)
 PublishedLinks (src/core/published_links.py) → content/published_links/
```

### 4.2 Instagram batch pipeline [CURRENT]

```
Batch (post row, auto_retry = 1)
  ▼
SharedMediaSession (one per platform per batch; lazy)
  ▼  first job that needs media calls prepare()
Local HTTP server on 127.0.0.1:<random port>, routes /media/<random>.mp4 (+ /media/<random>.jpg)
  ▼
ONE `cloudflared tunnel --url http://127.0.0.1:<port>` → https://<words>.trycloudflare.com
  ▼
ThreadPoolExecutor, at most INSTAGRAM_MAX_CONCURRENT_PUBLISHES (default 5) active jobs
  ▼
Per job: POST /media (REELS, video_url, cover_url, caption) → poll status_code every 15 s → media_publish → permalink
  ▼
Initial round fully drained
  ▼
Automatic retry round: this batch's failed Instagram jobs, once each (same pool limit, same session)
  ▼
Permanent links saved (per job, at the moment it publishes)
  ▼
session.close(): tunnel + server stopped exactly once
```

### 4.3 Who owns what

| Layer | Owns | Must never contain |
|---|---|---|
| `src/tui` | Presentation, navigation, key bindings, modals, handing engine events to the UI thread | Platform HTTP, OAuth protocol, tunnel logic, retry logic, token access |
| `src/services` | UI-facing facade: plan / create / publish batches, token-free views, friendly errors, delete history, the settings the UI may edit | Worker pools, retry policy, platform HTTP |
| `src/core` | Job state machine, atomic claim, engine (pool, retry round, resume, token renewal), validation, published links | Platform-specific HTTP |
| `src/platforms` | One adapter per platform: validation, publish steps, provider state, permalink, cover capability | Job state, DB writes, UI |
| `src/media_storage` | Turning a local file into a temporary HTTPS URL, and cleaning it up | Instagram API calls, DB access |
| `src/auth` | OAuth state, PKCE, callback server, paste mode, token refresh coordination | UI code |
| `src/storage` | SQLAlchemy models, migrations, Fernet encryption | Business logic |
| `src/content` | Drop-folder packages, profiles, package lifecycle | Platform HTTP (it calls the engine) |

---

## 5. Source code structure

Line counts are from `wc -l` at `49ce462`. The whole tracked tree is about 28.9k lines, including docs and tests.

| Path | Purpose | Called by | Calls | Must NOT go here |
|---|---|---|---|---|
| `main.py` (234) | Entry point: arguments, `.env` loading, logging, service wiring, mode selection | the user / `Start_Soc_bot.bat` | everything below | Business logic |
| `Start_Soc_bot.bat` | Windows double-click launcher: `cd` to its own folder, pick `.venv` Python or `python`, install requirements once, warn about a missing `.env`, run `main.py` | the user | `main.py` | Secrets |
| `src/accounts/manager.py` (406) | Account CRUD, status, token encrypt/decrypt, token-free displays | auth, engine, services, CLI | storage | OAuth HTTP |
| `src/auth/` (`base`, `callback_server`, `errors`, `manager`, `state`) | OAuth infrastructure: state (10 min TTL, single use, platform-bound), PKCE, loopback callback server (300 s timeout), paste mode, connect / refresh / revoke | CLI account menu, `AccountService` | platform auth adapters, AccountManager | UI |
| `src/cli/` | Classic menu (`--plain`); also reused by the TUI for the Content Inbox and profile editor through `run_classic` | `main.py`, TUI | services / engine / intake | New features (put them in services + TUI) |
| `src/content/` | Drop-folder intake: detector, validator, manager (moves), profile, intake | CLI, TUI (ContentService), `--scan` | JobStore, engine | Platform HTTP |
| `src/core/jobs.py` (375) | `JobStore`: posts / jobs / attempts, `TRANSITIONS`, atomic `claim`, provider state, auto-retry bookkeeping | engine, services | storage | HTTP |
| `src/core/publisher.py` (567) | `PublisherEngine`: plan, publish_post (pool + retry round + shared sessions), retry_job, resume, token renewal, link recording | services, CLI, intake | adapters, JobStore, PublishedLinks | Platform specifics |
| `src/core/validation.py` (173) | Generic media checks (ffprobe optional), JPEG structure check, cached SHA-256 | engine, adapters, services | stdlib | none |
| `src/core/published_links.py` (202) | Link library: JSON + TXT, locks, atomic writes, quarantine, clipboard | engine, LinkService | filesystem | Temporary URLs (it rejects them) |
| `src/media_storage/` | `base`, `provider` (interface, S3 provider, factory, router builder), `router` (AUTO), `shared` (SharedMediaSession), `cloudflare_tunnel`, `tempfile`, `zerox0`, `s3`, `service` (settings) | InstagramPublisher, services (status) | httpx, boto3, cloudflared | DB, Instagram calls |
| `src/platforms/base.py` (260) | `PlatformPublisher` interface, `PublishError` (retryable / uncertain), `PublishOutcome`, `redact()` | engine, adapters | none | none |
| `src/platforms/instagram/` | `auth.py` (Instagram Login, `force_reauth`), `publisher.py` (Reels) | AuthManager, engine | httpx, media_storage | none |
| `src/platforms/youtube/` | Google OAuth (installed-app loopback, PKCE), resumable upload, `thumbnails.set` | same | httpx | none |
| `src/platforms/tiktok/` | Login Kit (hex PKCE), Direct Post (creator_info → init → chunked PUT → status) | same | httpx | none |
| `src/services/` | `publishing.py`, `accounts.py`, `library.py` (links, content, settings), `build_services()` | TUI | engine, AccountManager, AuthManager, PublishedLinks, intake | See §8 |
| `src/storage/` | `database.py` (models, migration runner), `tokens.py`, `migrations/001-004.sql` | everything | SQLite | none |
| `src/tui/` | `app.py` (modes, keys, quit guard), `theme.py`, `widgets.py` (sidebar, modals, file picker, `run_classic`), `screens/` (dashboard, create_post, publishing, lists) | `main.py` | services only | See §7 |
| `tests/unit/` | 30 test modules, 823 tests (see §27) | pytest | fakes, `httpx.MockTransport`, Textual pilot | Real credentials or network |
| `tools/readme_screenshots.py` | Regenerates `docs/images/*.svg` with demo data (temp DB in `C:/SocBotDemo`, fake publishers) | developer | TUI, test fakes | Real data |
| `setup_guide/` | 4 end-user setup guides (master, Instagram, YouTube, TikTok) | humans | none | Secrets |
| `docs/` | Architecture, API notes, ADRs, troubleshooting, GitHub Pages files (`index.html`, `privacy.html`, `terms.html`, `oauth/instagram-callback.html`) | humans / AIs | none | Secrets |

---

## 6. Main entry point

`python main.py [--plain] [--dry-run] [--scan] [--version] [--help]` [CURRENT, `main.py`]

Startup order in `main()`:

1. `stdout` / `stderr` are reconfigured with `errors="replace"`, so ✓ and emoji can't crash a cp1252 console.
2. `parse_args()`. `--help` and `--version` exit here (`SOC_BOT v1.0.0`).
3. `load_environment()`: `python-dotenv` loads the project `.env`, found relative to `main.py`, with `override=False`, so real environment variables win. Values are never printed.
4. `configure_logging()`: `soc_bot.*` INFO lines go to stdout as `[INFO] …`.
5. Mode dispatch:

| Flag / condition | What happens |
|---|---|
| `--dry-run` | `run_dry_run()`: opens and migrates the DB and prints the plan for every open post, plus Instagram batch facts (accounts, concurrency, shared tunnels, provider, cover, retry) and the content-inbox plan. **No network, no uploads, no token refresh, nothing moved.** |
| `--scan` | `initialize_services()`, then `run_scan()`: lists the inbox. Publishes only if the saved profile is in **AUTO** mode; in VERIFY mode it only lists. |
| default, interactive terminal with Textual importable | `run_tui()`: file logging to `logs/soc_bot.log` (the screen belongs to the UI), then `SocBotApp` |
| `--plain` | Classic text menu (`run_menu`) |
| default, but no TTY or Textual missing | Prints "Full-screen UI not available here…" and uses the classic menu (automatic fallback) |

`initialize_services()` does the following:

1. `get_database()` + `create_all()` + `migrate()`.
2. Builds `TokenEncryption()` (reads `ENCRYPTION_KEY`), `AccountManager`, `AuthManager`, and `PublishedLinks().ensure_files()`.
3. Builds `PublisherEngine(..., links=links)` and `ContentIntake(...)`, which creates the `content/` folders.

`KeyboardInterrupt` prints "Interrupted. Goodbye!" (exit code 0). Any other exception prints `[ERROR] …` (exit code 1). The `.bat` launcher then pauses so the message can be read.

---

## 7. TUI

### 7.1 Technology [CURRENT]

- **Textual** (8.2.8 in the dev environment) + **Rich**. It is a pure terminal application: no browser, no web server.
- `SocBotApp` uses Textual **modes**, one per page. Theme and CSS live in `src/tui/theme.py`.
- Engine callbacks arrive on worker threads. The TUI runs batches in a Textual thread worker and hands events to the UI thread with `call_from_thread`.

### 7.2 Pages and keys

| Key | Page | Contents |
|---|---|---|
| D | Dashboard | Platform cards (connected / ready / set up), queue counts, recent activity, Cloudflare availability |
| C | Create Post | 5-step wizard: **Media** (video + optional cover, file picker or paste a path) → **Caption** → **Destinations** (platform cards, searchable multi-select, A all / N none) → **Options** (only what the chosen platforms need: YouTube title / privacy / made-for-kids, TikTok privacy) → **Review** |
| Q | Queue | Batches with progress. Enter opens a batch: **o** open post, **c** copy link, **r** retry a failed job, Enter shows details |
| H | History | Filterable job history. **Del** / "Delete selected" / "Delete all shown" (finished jobs only, with confirmation) |
| A | Accounts | Per-platform tabs: Connect, Reconnect, Disconnect (these run in worker threads) |
| L | Published Links | Per platform. **C** copies the selected link, **A** copies all visible links (permanent URLs only) |
| T | Content | Content Inbox stage counts. The classic inbox and profile editor run through `run_classic` |
| S | Settings | OAuth status, media-provider status (local checks only), editable concurrency and retry delay |
| ? | Help | Help modal |
| Ctrl+P | Command palette | Navigation commands |
| X / Ctrl+Q | Exit | Blocked while a batch is publishing (engine workers can't be cancelled mid-post); otherwise a confirmation |

**Review → publish.**
1. A docked action bar (`#actions`) is always visible, with the buttons **▶ PUBLISH NOW**, Next, Back and Cancel.
2. **P** or **Ctrl+Enter** publishes; Esc goes back.
3. There is **one** confirmation modal. The Publishing screen then shows live progress per job: the initial round, "RETRY ROUND COMPLETE ↻ Recovered n", and the final result with the links saved.

**Toasts and modals.** `app.notify` shows success, warning and error toasts. `ConfirmModal`, `MessageModal`, `HelpModal`, `FilePickerModal` and `ConnectModal` (OAuth progress plus the paste field) are the modals.

**Responsive sizes.**
- Every page renders from **80×24** upward. Tests cover 80×24, 100×30, 120×40 and 160×50.
- Below **100 columns** the sidebar hides (`NARROW_WIDTH = 100`).

**Accessibility.**
- Everything works from the keyboard.
- The active page is marked by text (▌ plus bold), not only by color.
- Status is shown with symbols and words (✓ / ✗ / "Ready"), not color alone.

### 7.3 Important UI bugs [HISTORY, fixes CURRENT]

| Bug | Root cause | Fix | Regression tests |
|---|---|---|---|
| **Review "Publish" button missing on small terminals** | The action buttons were inside the scrolling step content. A long review pushed them below the fold. | A docked `#actions` bar outside the scroll area. `check_action` shows Next on steps 1–4 and PUBLISH only on Review. P / Ctrl+Enter bindings. | `test_review_shows_publish_back_cancel_at_every_size`, `test_long_review_never_hides_the_actions`, `test_enter_activates_publish_then_single_confirmation`, `test_back_and_cancel_from_review` |
| **Sidebar active-page bug** | The only "active" signal was the OptionList cursor highlight, which moves with the arrows or mouse. The highlight could sit on a different item than the page shown. A later contrast issue: accent-colored text on the violet highlight was unreadable. | The active page is marked in its own label (`▌` + bold), independent of the cursor. `reset_highlight()` puts the cursor back on the active page on mount, on resume and on blur. Only the `▌` marker uses the accent color. | `test_sidebar_marks_the_active_page` (parametrized over the modes) |
| **OAuth asyncio event-loop bug** | Textual runs an asyncio loop on the main thread. The existing connect path called `asyncio.run(auth.connect_account(...))`, which fails inside a running loop ("asyncio.run() cannot be called from a running event loop"). | `AccountService.connect` / `disconnect` call `require_worker_thread()` and run `asyncio.run(...)` in a **worker thread**, which gets its own loop. The Accounts screen uses `run_worker(thread=True)`. Classic flows run through `run_classic` (suspended app, joined plain thread). The OAuth implementation itself is unchanged and still centralized in `AuthManager.connect_account`. | `tests/unit/test_tui_oauth.py`: `test_connect_runs_on_a_worker_and_returns_to_accounts`, `test_service_refuses_to_run_on_a_running_loop`, `test_run_classic_runs_the_flow_off_the_loop_thread`, `test_disconnect_runs_on_a_worker`, … |
| Paste field not visible (Instagram add-account) | In `ConnectModal`, a `LoadingIndicator` defaulted to `height: 100%` and pushed the paste `Input` off-screen. | Spinner height 1 (DEFAULT_CSS), hidden while pasting. The prompt is handed across threads with a `threading.Event`. | `test_paste_field_is_visible_on_screen`, `test_instagram_paste_mode_through_the_modal` |
| "Connect" couldn't add a *second* Instagram account | Instagram reused the browser's logged-in session and silently re-authorized the same account. | `force_reauth=true` in the authorize URL (`InstagramAuth.create_config`) | `test_instagram_oauth.py` (force_reauth config test) |
| Slow on large videos | The full-file SHA-256 was recomputed on every wizard step; Instagram polling waited 60 s per check. | `@lru_cache` `_checksum(path, size, mtime_ns)`; `POLL_INTERVAL = 15.0` | `test_checksum_is_cached_until_the_file_changes`; the poll test in `test_publishers.py` |

---

## 8. Service layer

**Why it exists [CURRENT].** Before the TUI, `src/cli/publish_menu.py` assembled batch facts directly from engine and media internals, such as `engine.store.*`, `cover_plan`, `delivery_provider` and `max_concurrent_publishes`. A UI built that way would duplicate engine logic. `src/services` is the one facade that any UI uses.

```
UI ─► services ─► PublisherEngine / JobStore / AccountManager / AuthManager / PublishedLinks / ContentIntake
```

| Service | Main methods | Notes |
|---|---|---|
| `PublishingService` | `inspect_video`, `inspect_cover`, `plan(video, caption, cover, account_ids, …) → BatchPlan`, `create_batch(plan, include_already_published) → post_id`, `publish_batch(post_id, on_event)`, `retry_job`, `resume_open`, `batch`, `batches`, `history`, `delete_history`, `queue_counts`, `recent_activity`, `friendly_error` | Events: `batch_started`, `job_*`, `retry_started`, `batch_finished`. `delete_history` deletes only published or failed jobs (their attempts cascade), plus posts left without jobs. Link files are untouched. A deleted published job no longer triggers the "already published" warning. |
| `AccountService` | `list`, `summary`, `is_configured`, `connect(platform, redirect_prompt)`, `disconnect`, `enable` | Token-free `AccountView`. `connect` and `disconnect` must run on a worker thread. |
| `LinkService` | `records`, `counts`, `copy` (permanent URLs only), `open`, `text_file` | |
| `ContentService` | `stage_counts`, `entries`, `root` | |
| `SettingsService` | `value`, `save`, `media_status`, `oauth_status` | Edits only **two** keys: `INSTAGRAM_MAX_CONCURRENT_PUBLISHES` (1..20) and `INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS` (0..300). It writes `.env` only on the user's explicit save and never touches credentials. |

**Rule.** The UI must not implement Instagram API logic, Cloudflare or tunnel logic, the retry engine, the OAuth protocol, worker-pool logic or token encryption. The services must not implement those either: they only **call** the engine.

---

## 9. Account system

**Database [CURRENT].** Table `accounts` (migration 001):

- `platform`: one of `instagram`, `tiktok`, `youtube`.
- `platform_account_id`: the platform's ID. For Instagram this is the IG professional `user_id`; for TikTok it is the `open_id`.
- `username`, `display_name`.
- `access_token_enc`, `refresh_token_enc`: Fernet-encrypted.
- `expires_at`.
- `status`: `active` / `expired` / `revoked` / `disconnected`.
- `meta_json`: holds the **granted scopes**.
- Unique index on `(platform, platform_account_id)`.

| Action | Behavior |
|---|---|
| Connect | OAuth (§10) → identity lookup → `create_account`. Reconnecting an existing `(platform, platform_account_id)` **updates** the row instead of failing. |
| Reconnect | The same Connect flow, which refreshes the tokens and scopes. |
| Disconnect | Revokes where the platform supports it (TikTok, YouTube). Instagram documents no revoke endpoint, so it is disconnected locally only. The row stays with `status = disconnected`. |
| Enable | Sets the status back to `active`. |
| Validation | Before planning and publishing, the engine checks: `status == active`; `missing_scopes()` against the adapter's `REQUIRED_SCOPES`; token expiry, renewing it through AuthManager when it is within `TOKEN_REFRESH_MARGIN` (Instagram: 7 days). |

Real state at the time of writing [HISTORY]: 11 Instagram accounts and 2 YouTube channels connected; no TikTok account. No credentials appear in this file.

---

## 10. OAuth

| | Instagram | YouTube | TikTok |
|---|---|---|---|
| Product | Instagram API with **Instagram Login** (Business Login) | Google OAuth, installed app | Login Kit for Desktop |
| Authorize | `instagram.com/oauth/authorize` + `force_reauth=true` | Google authorize endpoint | TikTok authorize endpoint |
| PKCE | None (not documented by Meta) | S256, base64url | S256, **hex** challenge (TikTok's own format) |
| Redirect | **Fixed, registered** `INSTAGRAM_REDIRECT_URI`. Recommended: the https GitHub Pages page (**paste mode**). A loopback URI on a fixed port also works if Meta accepts it. | Dynamic loopback `http://127.0.0.1:<free port>/callback/youtube` | Dynamic loopback; `http://127.0.0.1:*/callback/tiktok` is registered |
| Scopes | `instagram_business_basic`, `instagram_business_content_publish` | `youtube.upload`, `youtube`, `youtube.readonly` | `user.info.basic`, `video.publish` (ADR-019) |
| Token lifetime | Short-lived → long-lived (`ig_exchange_token`, about 60 days); "refresh" = `ig_refresh_token` re-exchange (the token must be ≥ 24 h old) | Access + refresh token (apps in Testing status: 7-day refresh expiry) | Access 24 h + rotating refresh token (rotations are persisted) |
| Revoke | None documented → local only | Yes | Yes |

**Shared mechanics [CURRENT].**
- **State:** random, single-use, platform-bound, 10-minute TTL (`src/auth/state.py`). A state issued for another platform is rejected before any code exchange.
- **Callback server:** `127.0.0.1` only, exclusive bind, ignores non-callback paths, HTML-escaped errors, 300 s timeout.
- **Paste mode:** only for platforms that declare `REQUIRES_REGISTERED_REDIRECT` (Instagram), and only with an https non-loopback redirect. The user pastes the final address from the GitHub Pages callback page, which gets the same validation (base URI, error, code, state). In the TUI the paste box is part of `ConnectModal`.
- `expires_at` = now (UTC) + `expires_in`.
- Tokens are Fernet-encrypted before they are written.

**TUI integration issue.** See §7.3: Textual's running loop versus `asyncio.run`. Fix: run the **existing** `AuthManager.connect_account` in a worker thread. No OAuth code was duplicated.

**Never include** real tokens, client secrets, encryption keys or authorization codes in docs, logs, tests or commits.

---

## 11. Instagram

`src/platforms/instagram/publisher.py` [CURRENT]. Graph API `https://graph.instagram.com/v25.0`. The token is always sent in the `Authorization: Bearer` header, never in a URL.

```
validate()  →  media: provider.prepare(video, cover)  (shared per batch)
            →  POST /{ig_user_id}/media  media_type=REELS, video_url, caption, [cover_url], [share_to_feed]
            →  state = {"container_id": …, "cover_sent": True?}   ← persisted via on_progress, never URLs
            →  GET /{container_id}?fields=status_code   every 15 s
                  IN_PROGRESS → keep polling
                  FINISHED    → publish
                  PUBLISHED   → already published (lost response) → done
                  EXPIRED     → retryable error
                  ERROR       → processing_failed (not retryable within the job's own retries; the batch retry round may retry it)
            →  state["publish_requested"] = True
            →  POST /{ig_user_id}/media_publish creation_id=container_id → media id
            →  GET /{media_id}?fields=permalink  (best effort; must start with https://www.instagram.com/)
            →  PublishOutcome("published", media_id, state, cover_status="published" if cover)
```

- **Polling window:** `POLL_ATTEMPTS = 5` means **minutes**. Large files get one extra minute per 25 MB above 50 MB, capped by `INSTAGRAM_MAX_POLL_MINUTES` (default 15). Checks = minutes × 4 (every 15 s). Meta recommends checking about once a minute for up to 5 minutes; Soc_bot checks more often inside the same window. If the container is still `IN_PROGRESS` at the end, the job stays `processing` and is re-checked on resume.
- **Recovery and duplicate safety:** a stored `container_id` is checked first. If the container is `PUBLISHED`, the job is marked done with no second publish. If it is `ERROR` or `EXPIRED`, a new container is created with fresh media. `media_publish` is only called after `FINISHED`.
- **Failure mapping (`_check`):** 401 or error code 190 → `unauthorized`; 403 or codes 10 / 200 → `permission_denied`; codes 4 / 9 / 17 / 32 / 613 → `rate_limited` (retryable); 429, 5xx or `is_transient` → retryable. The message includes Meta's text and `fbtrace_id`.
- **Validation:** MP4 or MOV; caption ≤ 2,200 characters, ≤ 30 hashtags, ≤ 20 @-mentions; duration 3 s – 15 min (when ffprobe is available); width ≤ 1920; a media provider must be available for that size and cover.
- **Size cap:** `MAX_SIZE_BYTES = 300 * 1024 * 1024`. Meta's IG User Media reference says "300MB maximum" for Reels. The code uses MiB (314,572,800 bytes), so files between 300,000,000 and 314,572,800 bytes are the only ambiguous range. This is documented and deliberately kept.
- **Rate limit:** 100 API-published posts per account per 24 h (Meta). Soc_bot does not call `content_publishing_limit` during publishing.
- **Debug override:** an explicit `video_url` job option is honored (https only) and can't be combined with a cover.

---

## 12. Instagram cover

- **One cover file for every selected Instagram account.** The same local path goes into each job's `options["cover_path"]`. The file is **never copied**: there are never 50 copies of the same cover.
- **Validation (`validate_cover`):** `.jpg` / `.jpeg` only; a stdlib JPEG structure check (`jpeg_info`); at most 8 MB (Meta's Reels cover spec). An invalid cover **blocks** the destination. A cover the user chose is never silently dropped or converted.
- **Serving:** the shared media session serves the cover on the **same** local server and tunnel as the video, at its own random route `/media/<random>.jpg` with type `image/jpeg`.
- **API:** `cover_url` is sent in the container POST only when a cover exists. Meta documents `cover_url` in the IG User Media reference; the Instagram Login publishing guide doesn't list it. It was **verified to work with Instagram Login by real publishes plus a `thumbnail_url` readback**, in which the thumbnail bytes matched the cover.
- **Provider requirement:** only the Cloudflare Quick Tunnel declares `supports_cover = True`. In AUTO mode, a job with a cover is always routed to the tunnel, even for small videos. Without cloudflared, cover jobs are blocked, not published without the cover.
- `cover_status` / `cover_error` are stored per job (migration 003).

---

## 13. The large-video problem

**Problem.** With Instagram Login, Meta documents Reels video publishing only through `video_url`: a **public HTTPS URL that Meta downloads**. The resumable `rupload.facebook.com` upload is documented for Facebook Login only, and was not used because its support for Instagram Login tokens is unverified.

**The user did NOT want:**
- to type public URLs by hand (ADR-012 required that at first; it was bad UX and led to a YouTube Shorts page URL being entered, which can never work);
- permanent cloud storage;
- YouTube as an intermediary (the YouTube API gives no direct media URL, and scraping violates YouTube's Terms);
- paid hosting.

**Evolution [HISTORY].**
1. ADR-012: the user supplies `video_url` → rejected in practice.
2. ADR-023: a private S3-compatible bucket plus a presigned URL. It works in theory, but the user had no bucket, so no real run was possible.
3. ADR-024: 0x0.st, an opt-in public host → the service had disabled uploads.
4. ADR-025: TempFile.org → **the first real Reel was published**, but the limit is 100 MB.
5. ADR-026: the `auto` router (TempFile ≤ 99 MB, S3 above) → large files were still blocked without S3.
6. Research into free hosts for 131.9 MB (§14) → none worked.
7. **Cloudflare Quick Tunnel.** The file is *served from the PC*, with nothing uploaded → real 131.9 MB Reel published. It became AUTO tier 2 and the only provider that can deliver covers.
8. ADR-027: fan-out with **one tunnel per job** → Cloudflare 429 after about 45 tunnel creations.
9. ADR-028: **one shared tunnel per batch** (LOCKED) plus an automatic retry round.

---

## 14. Media providers

"Remains" means the provider still exists in the codebase.

| Provider | Why considered | What was tested | Observed result | Decision | Remains? |
|---|---|---|---|---|---|
| **S3 / R2 / MinIO** (`s3.py`, `ObjectStorageMediaProvider`) | Private bucket plus a short-lived presigned URL; the most "proper" option | Unit tests (mocked boto3) | **No real run**: the user has no bucket credentials | Kept as the AUTO last resort (only reached when cloudflared is unavailable) and as the explicit `MEDIA_STORAGE_PROVIDER=s3` (the default when the variable is unset). No cover support. | ✅ |
| **TempFile.org** (`tempfile.py`) | No credentials, public temporary host | API verified from its `openapi.json` + real requests. Real Instagram publish (job 6) and an AUTO-mode run (job 7) | Works. 100 MB limit (99 MB in AUTO), 200 uploads per hour, IP blocks possible (403). The response's `url` is an HTML page; the media URL is `/<id>/download`. | AUTO tier 1 for small videos **without** a cover. Public exposure until deleted (default expiry 1 h). Its terms forbid bulk automated uploads. | ✅ |
| **0x0.st** (`zerox0.py`) | Simple public host, 512 MiB | Unit tests; the service's page | **Uploads disabled** by the operator | Opt-in only (`0x0`), never in AUTO | ✅ (dormant) |
| qurl.sh | Free host for 131.9 MB | Real upload attempt (2026-09-25) | **HTTP 413** (too large) | Rejected | ❌ never implemented |
| temp.sh | Free host | Real attempt | GET returned an **HTML page**, not the file | Rejected (Meta needs the raw media) | ❌ |
| file.io | Free host | Real attempt | **HTTP 405** | Rejected | ❌ |
| Litterbox / Catbox | Free host | Real attempt | **HTTP 500, even for 10 MB** | Rejected | ❌ |
| Pixeldrain | Free host | Checked | Hotlinking is **premium-only** | Rejected | ❌ |
| Filebin | Free host | Checked | **Cookie wall** in front of downloads | Rejected | ❌ |
| x0.at | Listed as a candidate | **Not verified from current repository** (no recorded result) | none | none | ❌ |
| **Cloudflare Quick Tunnel** (`cloudflare_tunnel.py`) | Serve the local file; no upload, no account, no size limit | Real direct test (131,925,281 bytes, SHA-256 identical, Range 206, 404 elsewhere, 502 after cleanup) and many real Instagram batches | Works. Quick Tunnels are documented as testing/dev only (no SLA). Creating too many triggers **HTTP 429**. | AUTO tier 2 and the only cover-capable provider. **One tunnel per batch.** | ✅ |

**AUTO order [CURRENT]:** `("tempfile", "cloudflare_tunnel", "s3")`. The router picks the **first** configured tier whose size limit (minus `MEDIA_STORAGE_AUTO_MARGIN_MB`) fits and that supports a cover when one is needed. There is **no fallback after a failure**: media never moves somewhere unexpected.

**Do not repeat the free-host experiments** without a concrete new reason (for example a host that newly documents direct raw GET URLs for more than 100 MB).

---

## 15. Cloudflare Quick Tunnel

**Locked rule: ONE Cloudflare Quick Tunnel per Instagram publishing batch, NOT one per account.**

`src/media_storage/cloudflare_tunnel.py` [CURRENT]:

| Aspect | Implementation |
|---|---|
| Local origin | `http.server.ThreadingHTTPServer` on **127.0.0.1**, port 0 (a free port). `CLOUDFLARE_MEDIA_HOST` must be `127.0.0.1`, otherwise it is a configuration error. |
| Random media paths | `/media/<secrets.token_hex(16)>.mp4`: 128-bit by default (`CLOUDFLARE_MEDIA_TOKEN_BYTES` 16..64). The cover gets its own random `.jpg` route. |
| Routing | Exact path match against the routes dict; anything else is **404**. No directory listing, no traversal. Supports GET, HEAD and single Range requests (206 / 416). Streams in 1 MiB chunks. `Cache-Control: no-store`. Request logging is disabled, because paths contain the token. |
| Tunnel start | `subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:<port>"], stdin=DEVNULL, …, creationflags=CREATE_NO_WINDOW)`. This is an **argument list with no shell**. |
| Hostname parsing | Regex `https://[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com`, which requires hyphenated random words, so `https://api.trycloudflare.com` (also printed by cloudflared) is ignored. |
| DNS readiness | Waits 5 s (`FIRST_LOOKUP_DELAY`), then resolves the host through **public DNS-over-HTTPS**, alternating `cloudflare-dns.com` and `dns.google`, and sends HEAD to that IP with the real hostname as SNI + Host (the certificate is still verified). Ready when the answer is 200 with the right Content-Length. Reason: `trycloudflare.com` has a 60 s negative-cache TTL, and asking the local resolver too early caches "no such host". |
| Startup timeout | `CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS`, default **90** (allowed 5..300; keep it above 60 because of the negative TTL). |
| Error reporting | A reader thread drains the output and keeps the last `ERR` / "failed" line with URLs removed. If cloudflared exited, the error says "exited before the tunnel was ready (exit code N): <line>", for example 429. If it is still running: "did not start within 90s". Both are `retryable=True`. |
| Process management | Children are tracked in `_LIVE`. `_terminate` runs terminate → wait 5 s → kill. There is an `atexit` hook. Ceiling (marked in the code): a hard kill of Python can still orphan cloudflared; a Windows Job Object is the upgrade path. |
| Cleanup | `cleanup(handle)` is idempotent and **never raises**. It stops cloudflared and the server, clears the URLs from the handle, and logs a warning on partial failure. |
| Replacement | Handled by `SharedMediaSession` (§16): a dead tunnel is replaced **once** by the next job that needs it. |
| Health check | `cloudflared --version` only; it never starts a tunnel. |

**The user never runs cloudflared by hand** during normal publishing. Soc_bot starts it after the confirmation and stops it when the batch ends.

**`CLOUDFLARED_PATH`:** the executable name or full path. The default is `cloudflared`, found on `PATH` with `shutil.which`. Soc_bot never downloads executables (install with `winget install --id Cloudflare.cloudflared`). This setting is not a secret.

---

## 16. SharedMediaSession

`src/media_storage/shared.py` [CURRENT]. `InstagramPublisher.batch_media()` returns a new session per batch. `PublisherEngine._batch_sessions()` creates it lazily, so nothing starts until a job needs media.

```
batch starts ──► first job needing media: provider.prepare()   (ONE tunnel / ONE upload)
later jobs   ──► same handle, same video/cover URLs (the lock makes concurrent jobs wait for the one start)
job ends     ──► session.cleanup(handle) is a NO-OP (the media belongs to the batch)
dead media   ──► the next job starts ONE replacement
failed start ──► the same error is returned for FAILED_START_COOLDOWN = 30 s (Cloudflare is not hammered)
retry round  ──► reset_failed_starts() → one fresh real start attempt (see §20)
batch ends   ──► close(): each provider handle cleaned exactly once; never raises
```

- The key is `(resolved video path, content type, resolved cover path)`.
- `starts` counts the underlying prepares. Tests assert **50 jobs → 1 start**.

| Batch-shared | Job-specific |
|---|---|
| Local server, tunnel process, video URL, cover URL | Instagram account, access token, container ID, attempt history, job status, retry flags, permanent Reel URL |

The temporary URLs exist **only in memory**. They are never in the DB, the logs or the link files.

---

## 17. Multi-account fan-out

1 video + 1 cover + 1 caption + N Instagram accounts becomes **N independent jobs**:

1. **Selection:** the TUI Destinations step (searchable multi-select, all / none) or the CLI multi-select (A / N / toggles / ranges).
2. **Plan:** `PublishingService.plan` shows valid and invalid destinations with reasons, the provider, "shared tunnel", the job count, the initial active count, the retry policy and a bandwidth estimate. No network.
3. **One confirmation.**
4. **One batch:** `JobStore.create_post(video, caption, destinations, auto_retry=True)` writes one `posts` row plus one `publish_jobs` row per account. `(post_id, account_id)` is unique. Already-published (video, account) pairs are skipped unless the user explicitly ticks "Also publish to accounts that already have this video".
5. Each job has its own state machine, attempts and provider state, and gets its **own permalink**. The media (video URL, cover URL, tunnel) is shared.
6. YouTube and TikTok jobs of the same post run **sequentially** before the Instagram pool (ADR-011; only `PARALLEL_PLATFORMS = ("instagram",)` run in parallel).

---

## 18. Five-at-a-time queue

```env
INSTAGRAM_MAX_CONCURRENT_PUBLISHES=5      # allowed 1..20; invalid → 5
```

This is the **maximum number of ACTIVE jobs**, not fixed groups of five. `_run_pool` submits every job to a `ThreadPoolExecutor(max_workers=min(limit, len(jobs)))`, so a freed slot is refilled immediately:

```
t0: 5 running, 45 pending
t1: one finishes → 5 running, 44 pending     (NOT "wait for all 5, then start the next 5")
```

There are **no rigid waves**; do not implement waves. On Ctrl+C, queued futures are cancelled (those jobs stay `pending` or `retrying` for a later resume), running jobs finish their current step, and the session closes after them.

---

## 19. Automatic retry

`PublisherEngine.publish_post` [CURRENT]:

1. **The initial round completes first**: the pool is fully drained.
2. `_retry_candidates(post_id)`: only jobs of **this batch** that are
   - on platform `instagram`,
   - with `status == "failed"`,
   - with `auto_retry_used == 0`,
   - and belong to a post with `posts.auto_retry = 1`.
3. **Uncertain failures are not retried automatically.** If the last error has `uncertain: true` (the provider may have received the post), the job's `auto_retry_used` is set without retrying and a warning is logged.
4. Sleep `INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS` (default 5, allowed 0..300).
5. `reset_failed_starts()` on each session (see §20).
6. For each candidate: `mark_auto_retry_used(job.id)` **before** running it, so a crash can never earn a second automatic retry; then `failed → retrying`, and the event "automatic retry".
7. The retry round runs in the **same pool limit on the same media session**. A retry that needs a new container gets fresh media through the session.
8. The session closes once, after the retry round.

**Maximum two automatic rounds per job:** the initial attempt plus one automatic retry. There is no third automatic retry. Inside one round, a job's own retryable errors are still retried with back-off `retry_delays = (30, 60, 120)` s. That is the engine's per-attempt retry, separate from the batch retry round.

- **Old posts:** posts created before migration 004 have `auto_retry = NULL` and are **never** retried automatically.
- **Manual retry** (`retry_job`) marks `auto_retry_used`, so automation never retries that job afterwards.
- Tests (`tests/unit/test_batch.py`) include: `test_four_ok_one_failure_retried_once_and_recovers`, `test_retry_fails_again_stays_failed_exactly_two_attempts`, `test_retry_round_waits_for_the_whole_initial_round`, `test_retry_round_concurrency_is_limited`, `test_uncertain_failures_are_not_retried_automatically`, `test_old_and_legacy_failures_are_never_retried`, `test_manual_retry_takes_over_the_automatic_one`.

---

## 20. Retry bug history

**The tunnel-cooldown bug** (found in the final engine audit, 2026-09-26; severity HIGH):

- The old behavior: a failed tunnel start was cached for `FAILED_START_COOLDOWN = 30 s`, so that waiting jobs didn't all hit Cloudflare. The retry round started after `INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS = 5 s`.
- The problem: if the initial round failed because the tunnel didn't start (for example Cloudflare 429), the retry round began less than 30 s later. Every retried job got the **cached** error instantly and its **one automatic retry was used up without a real attempt**.
- The fix:
  - `SharedMediaSession.reset_failed_starts()` clears the remembered failures.
  - `publish_post` calls it right before the retry round, so the round makes **one fresh shared start**.
- Regression test: `test_retry_round_makes_one_fresh_shared_start_after_a_failed_start` (`tests/unit/test_batch.py`), which fails without the fix.

---

## 21. Resume and crash safety

**What is persisted [CURRENT]:**
- `posts.auto_retry`: the batch's retry entitlement.
- `publish_jobs.status`, `retry_count`, `next_retry_at`, `auto_retry_used`.
- `publish_attempts.response_json`: provider IDs such as `container_id`, `publish_requested`, `media_id`, `permalink`, `cover_sent`. These are saved through `on_progress` **before** the next step (ADR-013).
- Attempt errors, including the `uncertain` flag.

**Resume.** `resume_open_jobs()` / `PublishingService.resume_open()` covers every post with open jobs, **or** with an owed retry round (`retry_owed_post_ids`). It then calls `publish_post` again, which never restarts published or failed jobs.

| Crash point | What happens on resume |
|---|---|
| Before the initial round | Jobs are `pending` → they run normally. |
| During the initial round | `processing` jobs with a `container_id` → the status is re-checked (PUBLISHED → done, no second publish; FINISHED → publish once; ERROR / EXPIRED → new container). `uploading` jobs whose adapter can't restart → failed as **uncertain** ("check the account before retrying") and never retried automatically. |
| Before the retry round | Failed jobs with `auto_retry_used = 0` on an `auto_retry` post are "owed" → only those are retried (`test_crash_before_retry_round_resumes_only_the_owed_retries`). |
| During the retry round | `auto_retry_used` was set before each ran → **no second automatic retry** (`test_crash_during_retry_round_never_gives_a_second_automatic_retry`). A job left mid-flight resumes from its provider state like any other job. |

**How duplicate Reels are avoided:**
- An atomic `claim` (`pending` / `retrying` → `uploading`) means only one worker owns a job.
- Container IDs are persisted, and a container is checked before a new one is created.
- `media_publish` runs only after FINISHED.
- `TRANSITIONS` never move a published job. `_fail` never downgrades a published job.
- `(post_id, account_id)` is unique.
- **The operational rule matters too:** resume publishes **every** open job, including stale ones. See the incident in §29 and the rule in §40.

---

## 22. Failure isolation

- Each job runs inside `run()`, which catches every exception and turns it into a failed job (`code="internal"`), so one bug can't stop the others.
- A failed account doesn't cancel other accounts, pending jobs, the shared tunnel or the retry round.
- Job-level `cleanup()` on the shared session is a no-op; only `close()` at the end of the batch releases the media.
- `close()` swallows cleanup errors (a finished batch must stay finished), and so does the tunnel's `cleanup()`.
- Link recording never raises: a link problem can't fail a published job.
- The cover status is stored separately: a YouTube thumbnail failure never fails the published video.

---

## 23. Published links

Files in `content/published_links/` [CURRENT]:

```
instagram.json  instagram.txt
youtube.json    youtube.txt
tiktok.json     tiktok.txt
.lock           (OS file lock)
```

- **JSON is the source of truth.** Each record is `{"video", "account", "url", "provider_id", "published_at" (UTC ISO)}`.
- **TXT holds permanent URLs only**, one per line, for pasting. It is regenerated from the JSON.
- **When links are saved:** by the engine (`_record_link`), right after a job transitions to `published`, per job. The TUI and CLI show the links saved per platform after a batch.
- **URL sources:**
  - Instagram: the `permalink` returned by the API. No URL is guessed from the media ID; `permalink_missing` is set with a warning when the permalink can't be read.
  - YouTube: `https://www.youtube.com/watch?v=<id>`.
  - TikTok: none, because TikTok documents no public URL for the private posts of unaudited apps.
- **Only permanent URLs:** `ALLOWED_HOSTS` accepts only youtube.com, youtu.be, instagram.com and tiktok.com. Temporary URLs (tempfile, 0x0, S3 presigned, trycloudflare) raise `LinkError`.
- **Duplicate prevention:** `(account, provider_id)` must be unique. The file is re-read under the lock before appending.
- **Concurrent-write locking:** an in-process `threading.RLock`, plus an OS lock (`msvcrt.locking` on Windows) on `.lock` for two processes, for example the TUI and `--scan`.
- **Atomic replacement:** a temp file in the same folder, fsync, then `os.replace`. A malformed JSON file is **quarantined** (renamed) instead of being overwritten silently.
- **Import / backfill:** `engine.import_published_links()`. It is idempotent: YouTube links come from the stored video IDs, Instagram links from the stored permalink or a permalink fetch.
- **Clipboard:** the Windows built-in `clip.exe` (no dependency), ASCII only. The UI copies permanent URLs only.

---

## 24. Content system

The root is `CONTENT_ROOT` (default `<project>/content`). Stages (`STAGES`) [CURRENT]:

```
content/incoming/    new packages (scanned)
content/publishing/  moved here BEFORE the post is created (stored paths stay valid)
content/published/   all destinations done
content/failed/      invalid / blocked / failed
content/archive/
content/published_links/   (link library, §23)
```

**A package is one folder, for example `content/incoming/post_001/`:**

| File | Rule |
|---|---|
| video | Exactly one `.mp4`, `.mov` or `.webm`. Several videos is an error. |
| caption | `caption.txt` (or `caption*.txt`); exactly one; UTF-8; sent as-is |
| cover | Optional `cover.{jpg,jpeg,png,webp}`; at most one. Instagram needs a JPEG. |
| `title.txt` | Optional YouTube title (default: the first line of the caption) |
| partial files | `.part`, `.crdownload`, `.tmp`, … → the package is not ready |

- Symlinks are never followed.
- Unsafe folder names are ignored.
- Files must be stable for `CONTENT_STABILITY_SECONDS` (default 3) before a package is ready.
- `ContentManager` moves packages only inside the root and never deletes one.

**Profiles:**
- **VERIFY**: shows the plan, one confirmation.
- **AUTO**: `--scan` publishes without asking, but still validates everything, and never retries failed jobs automatically. This is separate from the Instagram retry round inside a batch.

**Duplicates:**
- `content_key` = SHA-256 of the video's name, size and first/last 64 KiB (ADR-016).
- The rule is **per video + account** (ADR-021): accounts added to the profile later get jobs; accounts that already have a job never get the video again.
- Manual Create Post uses `JobStore.already_published(video, account)`.

---

## 25. Database

SQLite through SQLAlchemy. The path comes from `DATABASE_URL` (the example uses `sqlite:///data/publisher.db`). Tables [CURRENT, `src/storage/database.py` + `migrations/`]:

| Table | Key fields | Relationships |
|---|---|---|
| `accounts` | platform, platform_account_id (unique pair), username, display_name, `access_token_enc`, `refresh_token_enc`, expires_at, status, meta_json (scopes) | 1–N publish_jobs (cascade) |
| `videos` | filename, path, size_bytes, duration, mime, width, height, **checksum (unique)** | 1–N posts |
| `posts` | video_id, caption, **auto_retry** (004) | = one batch; 1–N publish_jobs (cascade) |
| `publish_jobs` | post_id, account_id (**unique pair**, 002), status (CHECK: pending / uploading / processing / published / failed / retrying), platform_media_id, error_message, retry_count, next_retry_at, published_at, **options_json** (002), **cover_status / cover_error** (003), **auto_retry_used** (004) | 1–N publish_attempts (cascade) |
| `publish_attempts` | job_id, attempt_number (unique per job), status, **response_json** (provider state), error_json, started / completed | none |
| `content_items` (003) | content_key (unique), package paths, status, post_id (SET NULL) | none |
| `publishing_profiles` (003) | name (unique), settings_json | none |
| `schema_version` | version, applied_at, description | none |

**Migrations:**

| # | Change |
|---|---|
| 001 | Initial schema |
| 002 | `options_json` + unique `(post_id, account_id)` |
| 003 | Content intake tables + cover status columns |
| 004 | `posts.auto_retry` (NULL = never auto-retried: all older posts) + `publish_jobs.auto_retry_used` (default 0) |

**Current schema version: 4.**

The runner (ADR-014) strips comment lines, tolerates "duplicate column" when `create_all()` already created a column, and records versions with `merge`. `main.py` runs `create_all()` + `migrate()` on every start.

**Integrity:**
- Foreign keys cascade from post to jobs to attempts.
- The job status CHECK constraint plus `TRANSITIONS` in code.
- A reused video row (same checksum) takes the newly chosen path (a fix, see §29).
- The final audit [HISTORY] reported: `PRAGMA integrity_check` ok, no FK violations, no orphan jobs or attempts, no duplicate jobs, no impossible states.

---

## 26. Security

| Protection | How [CURRENT] |
|---|---|
| Encrypted OAuth tokens | Fernet (`cryptography`) with `ENCRYPTION_KEY` from `.env`. Tokens are decrypted only when needed. Views and reprs are token-free. Note: Fernet is AES-128-**CBC** + HMAC-SHA256. Some docstrings and docs say "AES-128-GCM", which is inaccurate wording; the behavior is Fernet's. |
| Ignored secrets | `.env`, `*.env` (except `.env.example`), `secrets/`, `client_secret*.json`, `data/*.db`, `logs/`, `/content/`, `videos/`, `*.mp4 / *.mov / …` are all git-ignored. |
| Local-only media origin | The media server binds `127.0.0.1` only; any other host is refused as a configuration error. |
| Random paths | 128-bit+ tokens per route; they exist only in memory. |
| Arbitrary-file protection | The server serves only the routes in its dict (exact match). There is no filesystem mapping, so nothing else is reachable. |
| Traversal protection | Exact-match routing (no path joining); the content manager checks `inside_root` and safe names; symlinks are ignored. |
| Subprocess | `cloudflared` is started with an **argument list** and no `shell=True`, so there is no shell injection. `clip` is called the same way. |
| No token logging | Tokens go in the `Authorization` headers. The `httpx` logger is set to WARNING (its INFO lines contain URLs). `redact()` strips secrets from error text. The tunnel server logs no requests. |
| No temporary-URL persistence | Provider state stores only IDs (`container_id` etc.), never `video_url` or `cover_url`. The link library rejects non-permanent hosts. `MediaHandle` hides its URLs and tokens from `repr`. |
| No tracked media | Videos and content are git-ignored. `tools/readme_screenshots.py` uses demo data in a neutral path. |
| OAuth CSRF | State is random, single-use, platform-bound and has a TTL. The loopback callback uses an exclusive bind. |

**What this protects against:**
- leaked tokens in logs, the DB file or commits;
- someone guessing the tunnel URL to read other files;
- path traversal through package names;
- CSRF / code injection in the OAuth callback;
- shell injection through file paths.

**What it does NOT protect against:**
- someone who obtains the random tunnel URL during the short publish window can download that one video (and cover);
- TempFile uploads are public while they exist;
- a local attacker with access to `.env` can decrypt the tokens.

---

## 27. Testing history

**Test counts recorded over time** (all unit tests, `pytest tests/unit`; each count comes from the docs or session reports):

| When | Count | Source |
|---|---|---|
| Phase 1 | 47 | PROJECT_STATUS / CLAUDE_HANDOFF |
| Phase 2 | 100 | same |
| Phase 3A | 138 | same |
| Phase 3B (before / after audit) | 170 / 214 | same |
| Phase 4 | 305 | same |
| Phases 1–5B | 402 | TESTING.md |
| Phase 5C | 425 | PROJECT_STATUS |
| S3 media provider | 466 | same |
| 0x0.st | 522 | same |
| TempFile | 574 | same |
| AUTO router | 602 | same |
| Published links | 639 | CLAUDE_HANDOFF |
| Cloudflare tunnel | 701 | same |
| Fan-out + cover | 746 | same |
| Link finalization | 754 | PROJECT_STATUS |
| Shared tunnel + retry | 777 (session report) / 779 (PROJECT_STATUS), both recorded | |
| Final engine audit | 780 | PROJECT_STATUS |
| TUI | 792 | same |
| **Now (`49ce462`)** | **823 passed** (full run, 2026-09-26, about 200 s); **Ruff: all checks passed** | verified in this session |

**REAL versus MOCKED (critical distinction):**

| Area | Unit tests (mocked / fake) | Real-world verification |
|---|---|---|
| Instagram OAuth | `test_instagram_oauth.py`, `test_platform_auth.py`, `test_auth_*` (httpx MockTransport) | ✅ Real: 11 accounts connected |
| Instagram publishing | `test_publishers.py`, `test_fanout.py`, `test_batch.py` (`FlakyInstagram` fakes, injected failures) | ✅ Real: many Reels (§28) |
| Cloudflare tunnel | `test_cloudflare_tunnel.py` (fake Popen, fake DoH, real loopback server) | ✅ Real direct test + real batches |
| Automatic retry round | `test_batch.py` with **injected** failures | ⚠️ The recovery path is mock-verified. The real 11-account run in the link-finalization phase failed with a Cloudflare 429 before the retry feature existed. **Not verified from current repository** whether a real run ever recovered a job through the automatic retry round. |
| YouTube | `test_publishers.py`, `test_platform_auth.py` | ✅ Real OAuth (2 channels), real uploads, custom thumbnail |
| TikTok | `test_tiktok_5b.py` (full mocked TikTok API) | ❌ **Never run against the real service** |
| TUI | `test_tui.py`, `test_tui_oauth.py` (headless `App.run_test` pilot, fakes) | ✅ Real TUI publish (post 25) |
| Security scans | none automated | Manual greps of the DB, logs and link files for temporary URLs and tokens after real runs: clean [HISTORY] |
| DB integrity | `test_database.py` | Final audit: integrity ok [HISTORY] |
| Lint | none | `ruff check .` clean |

`tests/conftest.py` hides any real cloudflared from tests. No test reads the real `.env` or needs network access.

---

## 28. Real test results

Only URLs already recorded in the repo docs or the session record are listed. All these tests ran on 2026-09-25 / 26 [HISTORY].

| Test | Result |
|---|---|
| First real Instagram Reel (TempFile, local file) | Job 6 → https://www.instagram.com/reel/DdttFw8CqnI/ ; the TempFile copy was deleted |
| Real direct tunnel test | 131,925,281 bytes; SHA-256 identical; Range 206; other paths 404; about 10.5 MB/s; URL returns 502 after cleanup |
| **131.9 MB Instagram publish via the tunnel** | Post 8 / job 9 → https://www.instagram.com/reel/DduAxFSgaZt/ , 84 s end to end, no cloudflared left |
| Custom cover, 10 MB | https://www.instagram.com/reel/DdvCpWvEspn/ , cover verified by `thumbnail_url` readback |
| Custom cover, 131.9 MB | https://www.instagram.com/reel/DdvDAyhCGzR/ , cover verified |
| 11-account fan-out (per-job tunnels at the time) | Post 11, jobs 12–22, 11/11 published in 4 min 10 s, max 5 concurrent, all covers verified |
| 5-account fan-out | Post 12, jobs 23–27, `1114(1).mp4` (40 MB), 5/5 in 1 min 24 s |
| One failure in a 131.9 MB × 11 batch | Post 13 / job 29 (@noxivra_100): container ERROR with no reason from Instagram (see §29) |
| Link finalization | Posts 15 (1 account) and 16 (5 accounts) passed. Post 17 (11 accounts) **failed 0/11 with Cloudflare 429** before any container was created, so no duplicates were possible. |
| **Shared tunnel (LOCKED design)** | Posts 18 (2 accounts), 19 (5), 20 (11), 21 (131.9 MB × 2): **1 tunnel creation per batch** each time, all published, covers verified, links saved |
| Final audit regression | Posts 22 (1 account), 23 (5), 24 (131.9 MB, 2): all published, 1 tunnel per batch |
| TUI publish | Post 25, @noxivra_07 → https://www.instagram.com/reel/Ddvf586AGkE/ (cover verified; the clipboard held only the permanent URL) |
| YouTube | Real OAuth (2 channels). Real private upload `rAGivy-c3DM` with a custom thumbnail (Phase 5A regression). A second publish of the same package was refused (duplicate rule). |
| TikTok | **Not run**: no developer credentials |

**Tests intentionally not run:**
- A real S3 large-file test (no bucket).
- Repeating the >99 MB × 5-account run (bandwidth; the design is covered by the 131.9 MB × 2 run).
- A real TikTok run.
- Unnecessary extra real posts in later phases, by the user's rule.

---

## 29. Bug and mistake history

Chronological. "Test" means a regression test was added (✅), or the table says what exists instead.

| # | Problem | What happened | Root cause | Fix | Test | Lesson |
|---|---|---|---|---|---|---|
| 1 | App crashed on normal start | `main.py` called `run_menu(auth_manager=…)` | Signature mismatch, hidden because tests mocked `run_menu` | Signature fixed | ✅ | Don't mock away the thing you're wiring |
| 2 | "Auth manager not initialized" on Connect | Connected Accounts was opened without the AuthManager | Wiring bug | Passed through | ✅ | none |
| 3 | Identical timestamps on every row | `datetime.now()` default evaluated at import | Python default-argument pitfall | Per-row defaults | ✅ | none |
| 4 | Most of migration 001 never ran | The runner skipped any chunk starting with a comment | Naive SQL splitting, masked by `create_all` | Strip comment lines | ✅ | Test migrations on an empty DB without `create_all` |
| 5 | TikTok PKCE rejected | Used a base64url challenge | TikTok wants **hex** | `encoding` option | ✅ | Read each provider's PKCE spec |
| 6 | Real connect would crash | `expires_in=` passed to `create_account` | Unsupported kwarg | Compute `expires_at` | ✅ | none |
| 7 | Instagram used Facebook Login endpoints | Wrong product | Early design | Migrated to Instagram Login | ✅ | none |
| 8 | Dead duplicate "Exit" in the main menu | The prompt added a second Exit | UI bug | Removed | ✅ | none |
| 9 | `.gitignore` `content/` would hide `src/content/` | Unanchored pattern | gitignore semantics | `/content/` | none | Anchor ignore patterns |
| 10 | Every TikTok connect failed | TikTok v2 returns `"error":{"code":"ok"}` on success | Treated as an error | `_tiktok_error()` | ✅ | none |
| 11 | "Sorry, this page isn't available" (Instagram) | `.env` had the **Meta** app ID/secret | Instagram Login needs the **Instagram** app ID | User fixed `.env`; the error message now explains it | ✅ | Diagnose with read-only calls before changing code |
| 12 | Instagram redirect could never match | A dynamic loopback port versus Meta's exact registered URI | Design | Required `INSTAGRAM_REDIRECT_URI` + paste mode | ✅ | none |
| 13 | Scope parsing would crash | `permissions` returned as a list | Unverified response shape | Normalized | ✅ | none |
| 14 | **Manual Instagram public URL requirement** (ADR-012) | The user had to paste a URL; a YouTube Shorts page URL was entered (real job 4) | Instagram Login publishing needs `video_url` | The media-provider layer (§13) | ✅ | UX requirements matter as much as the API |
| 15 | "Video file not found: video.mp4" for an existing file | A reused video row kept a stale path | Dedup by checksum reused the old row | The reused row takes the new path | ✅ | none |
| 16 | Storage 403 retried as if temporary | AccessDenied was marked retryable | Error classification | 4xx not retryable | ✅ | none |
| 17 | **0x0.st uploads disabled** | The provider was implemented, then found unusable | External service change | TempFile added; 0x0 kept opt-in | ✅ | Probe a service before building on it |
| 18 | **qurl.sh 413 / temp.sh HTML / file.io 405 / Litterbox 500 / Pixeldrain premium / Filebin cookie wall** | No free host for 131.9 MB | External limits | Cloudflare Quick Tunnel | n/a | Don't repeat (§14) |
| 19 | **Wrong Cloudflare hostname** | The parser took `https://api.trycloudflare.com` (HTTP 405) | cloudflared prints service hosts too | Only hyphenated quick-tunnel names | ✅ | Real runs find what mocks can't |
| 20 | **DNS negative-cache issue** | The readiness check failed for about 45–60 s | The local resolver cached "no such host" (60 s TTL) | 5 s delay + public DoH (2 resolvers) + 90 s timeout | ✅ | none |
| 21 | **Token in logs** | During a manual real test, INFO logging made httpx log request URLs, including one tunnel token (the tunnel was already dead) | httpx logs URLs at INFO | The tunnel module forces the `httpx` logger to WARNING | Not verified from current repository whether a dedicated test exists | Check third-party loggers when a URL is a secret |
| 22 | **Process cleanup / orphan cloudflared** | A manual test script crashed before stopping cloudflared, leaving an orphan (it was killed by hand). Cleanup errors must not fail jobs. | No guaranteed teardown | `_LIVE` + `atexit`, `TunnelSession.close` collects errors, `cleanup` never raises; the orphan ceiling is documented | ✅ (cleanup tests) | Always tear down child processes in `finally` |
| 23 | **Misleading "did not start within 90s"** | The real cause was `quick tunnel provisioning failed with status 429` | The exit was checked before the exit code arrived | Wait for the exit code, report the real line | ✅ | Error text must reflect the real cause |
| 24 | **One-tunnel-per-account design → 429** | About 45 tunnel creations in 1.5 h → Cloudflare 429 → the 11-account batch failed 0/11 | Per-job media sessions (ADR-027) | `SharedMediaSession`: 1 tunnel per batch (ADR-028, LOCKED) | ✅ (50 jobs → 1 start) | Count external resource creations per operation |
| 25 | **Stale resume → unintended Reel** | A resume test also resumed a stale RETRYING job from the previous day (post 4 / job 5) → an extra Reel was published: https://www.instagram.com/reel/DdvDNpKDoMk/ | Resume publishes **all** open jobs; the pre-check showed it, but it was resumed anyway | Operational rule + memory note: list open jobs and ask about stale ones before any real resume; the docs warn about it | n/a (behavior is by design) | Real-world side effects need a human check first |
| 26 | **@noxivra_100 processing failure** | Job 29 (131.9 MB, five simultaneous fetches): container ERROR at about 135 s | Instagram-side processing or fetch failure. Ruled out: auth, rate limit (quota 1/100), content (identical bytes succeeded on 10 accounts), Soc_bot state. The exact cause was not provable. | No code change; the automatic retry round was later added for exactly this class of failure | n/a | Not every failure is our bug; collect evidence first |
| 27 | **Concurrent link writes** | Parallel jobs plus two processes (TUI + `--scan`) could interleave read-modify-write | Shared JSON files | RLock + OS file lock + re-read under the lock + atomic replace | ✅ | none |
| 28 | **Test ordering / timing flakes** | Three intermittent test failures are recorded: (a) an ordering assertion in a new `test_batch.py` test (shared-tunnel phase); (b) a test that depended on thread start order (final audit); (c) a concurrency-timing test in `test_fanout.py` | Non-deterministic thread scheduling | (a) and (b) were fixed; the exact code change is **not verified from current repository**. (c) `ProbeInstagram(until_active=…)` holds jobs until N run at once (`tests/unit/test_fanout.py`). | The tests themselves | Never assert on thread start order or wall-clock timing |
| 29 | **Windows socket reset in tests** | An intermittent `ConnectionResetError` on a POST with an unread body (final audit) | Windows resets the connection when a server responds without consuming the request body | Fixed in the test code; the exact change is **not verified from current repository** | The test itself | Windows socket semantics differ from Linux |
| 30 | **Retry cooldown bug** | See §20 | 30 s cached failure > 5 s retry delay | `reset_failed_starts()` | ✅ | Check how timers interact |
| 31 | **TUI Review button hidden** | See §7.3 | Buttons inside the scroll area | Docked action bar | ✅ | Test at 80×24 |
| 32 | **Sidebar active state** | See §7.3 | Cursor highlight used as the "active" marker | Text marker + `reset_highlight` | ✅ | none |
| 33 | **TUI OAuth `asyncio.run`** | See §7.3 | Nested event loop | Worker thread | ✅ | Blocking or loop-owning code must run off the UI loop |
| 34 | Can't add a second Instagram account | Instagram reused the browser session | Missing `force_reauth` | `force_reauth=true` | ✅ | none |
| 35 | Paste field invisible | LoadingIndicator took 100% height | Textual default CSS | Height 1, hidden while pasting | ✅ | none |
| 36 | Connect test hung when an assertion failed | The worker waited forever for the paste answer | Missing teardown | try/finally answers the prompt | ✅ | none |
| 37 | "Tiktok" capitalization in messages | `str.title()` | none | Explicit name map | ✅ | none |
| 38 | Slow wizard on big files | SHA-256 recomputed on each step; 60 s poll interval | none | `lru_cache` checksum; 15 s polling | ✅ | none |
| 39 | README screenshot leaked the Windows username | A temp path under the user's profile appeared in the demo | The screenshot used a temp dir | Neutral `C:/SocBotDemo` path; leak scan | n/a | Scan generated assets for personal data |
| 40 | Docs said "one Soc_bot process at a time (process lock)" | Wrong statement written during the docs update | Only the **link files** have an OS lock; there is no single-instance app lock | Corrected in PROJECT_STATUS.md (this commit) | n/a | Verify docs against code |

---

## 30. Why the failed approaches failed

| Tried | Result | Why rejected | Replaced by |
|---|---|---|---|
| The user supplies `video_url` (ADR-012) | Bad UX; invalid URLs entered | The user shouldn't host files by hand | Automatic media providers |
| A YouTube watch or Shorts URL as the Instagram source | Never a media file | HTML page; no direct media URL in the YouTube API; scraping violates the ToS | Local file served or uploaded temporarily |
| yt-dlp / scraping | Not attempted | ToS violation, brittle | none |
| Private S3 bucket (ADR-023) | Code works (mocked); never real | The user has no bucket and didn't want paid or permanent storage | Kept as an optional fallback |
| 0x0.st (ADR-024) | Uploads disabled | Service decision | TempFile |
| TempFile.org alone (ADR-025) | Works ≤ 100 MB | Size limit; public exposure; terms forbid bulk use | AUTO router + tunnel |
| Free hosts (qurl.sh, temp.sh, file.io, Litterbox, Pixeldrain, Filebin) | 413 / HTML / 405 / 500 / premium / cookie wall | None can serve 131.9 MB as a raw direct GET | Cloudflare Quick Tunnel |
| One Quick Tunnel per job (ADR-027) | 429 after about 45 creations | Cloudflare provisioning limit | One shared tunnel per batch (ADR-028) |
| Sequential Instagram jobs (ADR-011) | Slow for many accounts | Each Reel waits for Meta's processing | Bounded pool (max 5) for Instagram only |
| A web UI | Never built | ADR-001: V1 is terminal-based, and the user asked for a terminal UI | Textual TUI |

---

## 31. Current configuration

From `.env.example` [CURRENT]. **Never copy the real `.env` into docs.**

| Category | Variable | Required? | Default / safe value | Effect |
|---|---|---|---|---|
| Database | `DATABASE_URL` | yes | `sqlite:///data/publisher.db` (the example) | SQLite location |
| Security | `ENCRYPTION_KEY` | **yes** | generate with `Fernet.generate_key()` | Encrypts tokens; losing it means reconnecting every account |
| Instagram | `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | for Instagram | none | The **Instagram** app credentials (not the Meta app's) |
| Instagram | `INSTAGRAM_REDIRECT_URI` | for Instagram | the GitHub Pages callback (paste mode) | Must match the registered URI exactly |
| TikTok | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` | for TikTok | none | none |
| TikTok | `TIKTOK_REDIRECT_URI` | no | empty = dynamic port | none |
| YouTube | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` | for YouTube | none | Desktop-app OAuth client |
| YouTube | `YOUTUBE_REDIRECT_URI` | no | empty | none |
| Media | `MEDIA_STORAGE_PROVIDER` | no | `s3` when unset; **`auto` recommended** | `auto` / `cloudflare_tunnel` / `tempfile` / `s3` / `0x0` |
| Media | `MEDIA_STORAGE_TEMPFILE_EXPIRY_HOURS` | no | 1 (allowed 1, 6, 24, 48) | none |
| Media | `MEDIA_STORAGE_AUTO_MARGIN_MB` | no | 1 (0..50) | Safety margin below provider limits |
| Tunnel | `CLOUDFLARED_PATH` | no | `cloudflared` | Executable name or path |
| Tunnel | `CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS` | no | 90 (5..300; keep > 60) | none |
| Tunnel | `CLOUDFLARE_MEDIA_TOKEN_BYTES` | no | 16 (16..64) | URL token size |
| Tunnel | `CLOUDFLARE_MEDIA_HOST` | no | `127.0.0.1` (must be) | none |
| Instagram engine | `INSTAGRAM_MAX_POLL_MINUTES` | no | 15 | Polling window cap |
| Instagram engine | `INSTAGRAM_MAX_CONCURRENT_PUBLISHES` | no | 5 (1..20) | Max active jobs |
| Instagram engine | `INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS` | no | 5 (0..300) | Pause before the retry round |
| 0x0 | `MEDIA_STORAGE_0X0_URL`, `MEDIA_STORAGE_0X0_EXPIRES_HOURS` | no | `https://0x0.st`, 1 | Dormant |
| S3 | `MEDIA_STORAGE_BUCKET/REGION/ENDPOINT/ACCESS_KEY/SECRET_KEY` | for S3 only | empty | ENDPOINT must be https |
| S3 | `MEDIA_STORAGE_PRESIGNED_URL_TTL` | no | 900 (60..604800) | none |
| S3 | `MEDIA_STORAGE_DELETE_AFTER_PUBLISH` | no | true | none |
| App | `LOG_LEVEL`, `DRY_RUN` | no | INFO, false | none |
| App | `OAUTH_CALLBACK_HOST`, `OAUTH_CALLBACK_PORT` | no | 127.0.0.1, 0 | none |
| Content | `CONTENT_ROOT`, `CONTENT_STABILITY_SECONDS` | no | `<project>/content`, 3 | Read in `src/content/models.py`; not in `.env.example` |

`.env.example` also lists `MAX_CONCURRENT_UPLOADS` and `MAX_VIDEO_SIZE_*` as commented optional values. **Not verified from current repository** that the code reads them; a grep found no reader in `src/`.

---

## 32. File and folder rules

| Path | Status | Notes |
|---|---|---|
| `.env` | **secret, ignored** | Never print, commit or modify it automatically (the TUI's Settings save writes only the two editable keys, on an explicit user action) |
| `.env.example` | tracked | Template, no secrets |
| `secrets/`, `client_secret*.json` | **secret, ignored** | Downloaded OAuth client files |
| `data/*.db` | runtime, ignored | Contains encrypted tokens and history |
| `logs/` | runtime, ignored | `soc_bot.log` in TUI mode; no tokens or temporary URLs |
| `videos/`, `*.mp4 *.mov *.avi *.mkv *.webm` | user media, ignored | none |
| `/content/` | runtime, ignored | Packages + `published_links/` (the user's link records) |
| `docs/images/*.svg` | tracked | Demo-data screenshots only |
| `docs/oauth/instagram-callback.html`, `docs/*.html` | tracked | Served by GitHub Pages (paste-mode callback, privacy, terms) |
| `tools/`, `setup_guide/`, `Start_Soc_bot.bat` | tracked | none |
| `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` | ignored | none |

---

## 33. Git history

Verified with `git log` at the time of writing (dates are author dates):

| Commit | Date | Purpose (commit message) |
|---|---|---|
| `90312a5` | 2026-09-24 | Initial commit |
| `501107f` | 2026-09-24 | Project initialization: structure, documentation, and configuration |
| `8618a2a` | 2026-09-25 | phase 1 (database foundation) |
| `2360f3d` | 2026-09-25 | Implement CLI framework |
| `1434e14` | 2026-09-25 | phase 2A |
| `434adaa` | 2026-09-25 | Implement Phase 3B: Official OAuth Verification + OAuth Infrastructure |
| `ab4b6c3` | 2026-09-25 | Fix Phase 3B OAuth compliance issues |
| `a6a0fd9` | 2026-09-25 | Implement Phase 4 publishing engine |
| `0cb5c05` | 2026-09-25 | Load .env at startup for Phase 5 YouTube configuration |
| `d2070f7` | 2026-09-25 | Implement Phase 5A content intake, publishing profiles and covers |
| `86cacd1` | 2026-09-25 | Prepare Phase 5B TikTok verification (real run pending credentials) |
| `47ca1fd` | 2026-09-25 | all updates (contents not itemized here; by timing probably Phase 5C + media-storage work, not verified) |
| `dc11345` | 2026-09-25 | new thing (by the user; contents not itemized here) |
| `ec6b76f` | 2026-09-25 | Add Cloudflare Tunnel support for large Instagram videos |
| `2c0807d` | 2026-09-26 | Add Instagram fan-out publishing and custom covers |
| `e8f612a` | 2026-09-26 | Add shared Instagram batch tunnel and automatic failure retry |
| `3f554fb` | 2026-09-26 | Finalize Instagram batch publishing workflow |
| `ef43c56` | 2026-09-26 | start up guid how to work with this (TUI + setup guides) |
| `49ce462` | 2026-09-26 | README.md, docs/images/, tools/, Start_Soc_bot.bat |

**State when this file was written:**
- Branch: `main`.
- HEAD: `49ce462c7a81ef648f7659d8b8dd81ce75bd7242`.
- The working tree was clean before this file was added.
- `git ls-remote origin refs/heads/main` returns the **same hash**, so GitHub was current with the local HEAD (0 ahead / 0 behind).
- This handoff file, and the one-line correction in `docs/PROJECT_STATUS.md`, are **uncommitted**. The user commits and pushes personally.

---

## 34. Current project state

| Area | Status | Evidence |
|---|---|---|
| Backend / engine | **READY, FROZEN** (change only for real defects) | Final audit; 823 tests pass |
| Instagram OAuth | **READY** | 11 real accounts; `force_reauth`; paste mode tested |
| Instagram publishing | **READY** | Many real Reels (§28) |
| Large video (up to Instagram's limit) | **READY** via the Cloudflare tunnel | Real 131.9 MB publishes |
| Custom cover | **READY** (tunnel provider only) | Real `thumbnail_url` readbacks |
| Fan-out | **READY** | Real 2 / 5 / 11-account batches |
| Shared tunnel | **READY, LOCKED** | 1 tunnel per batch in all real runs; 50-job unit test |
| Automatic retry | **READY** (mock-verified; see §27 caveat) | `test_batch.py` |
| Published links | **READY** | Real links saved; lock + atomic tests |
| YouTube | **READY** | Real OAuth + upload + thumbnail |
| TikTok | **LIMITED**: code complete, mock-tested, **never run for real** | No developer credentials |
| TUI | **READY** | 823 tests incl. TUI; real TUI publish |
| Classic CLI | READY (`--plain`) | Tests |
| Docs / setup guides / launcher | READY | `setup_guide/`, `Start_Soc_bot.bat`, README |

---

## 35. Known limitations and known bugs

**Known limitations** (by design or external; these are not bugs):
- Cloudflare Quick Tunnels are documented for testing/development: no SLA, no uptime guarantee.
- Cloudflare can rate-limit Quick Tunnel creation (**429**). The limit is undocumented; one past episode lasted about an hour.
- Bandwidth scales with accounts: each Instagram account downloads its own full copy (for example 131.9 MB × 50 ≈ 6.6 GB of upload). The PC must stay online until the batch finishes.
- Covers can only be delivered by the Cloudflare tunnel provider. Without cloudflared, cover jobs are blocked.
- TempFile uploads are public while they exist; the terms forbid bulk automation.
- TikTok:
  - real publishing depends on developer credentials;
  - unaudited apps can only post `SELF_ONLY`;
  - there is no public URL, so no TikTok links are saved;
  - there is no custom cover upload.
- YouTube: apps in "Testing" status need a reconnect every 7 days; there is a daily quota; thumbnails need a phone-verified channel.
- Instagram: 100 API posts per account per 24 h; the 300 MiB versus 300 MB ambiguity (§11); `permalink` is best-effort.
- Instagram login needs one copy/paste step (paste mode). The authorization code passes through GitHub Pages request logs (short-lived, single-use, and useless without the secret).
- Engine workers can't be cancelled mid-post, so quitting is blocked while a batch publishes.
- A hard kill of Python can orphan `cloudflared` (it has no Job Object).
- ffprobe is optional; without it, duration and resolution aren't checked locally.
- TikTok `refresh_expires_in` is not persisted.
- There is no single-instance lock for the app itself. Only the link files are locked across processes. Don't run two publishing sessions against the same DB at once.

**Known bugs:**
- None open at the time of writing. The last full run passed (823 tests, Ruff clean).
- Doc inaccuracy: a few docs and docstrings say Fernet is "AES-128-GCM"; Fernet is AES-128-CBC + HMAC-SHA256.

---

## 36. Future development rules

1. Read this master file first.
2. Inspect the actual repository; the code wins over the docs.
3. Check `git status` (the user may have uncommitted work).
4. Read the relevant tests before changing code.
5. Do not rewrite working architecture.
6. **Preserve one shared tunnel per Instagram batch.**
7. **Preserve max 5 concurrent Instagram jobs** (as the default) unless it is intentionally changed and tested.
8. **Preserve one cover for all selected Instagram accounts** (one file, never copied).
9. **Preserve exactly one automatic retry round** (never a third attempt; never retry old or uncertain jobs automatically).
10. **Preserve permanent-link-only storage.**
11. **Never persist temporary URLs** (DB, logs, link files, docs).
12. **Never expose secrets.**
13. Run the tests before declaring success.
14. Always distinguish mocked tests from real tests in reports.
15. Update this handoff file after major architectural changes.
16. Keep the UI going through `src/services`; add features there, not in `src/cli`.
17. Don't commit or push unless the user explicitly asks (the user's standing rule).

---

## 37. How another AI should start

1. Read `docs/SOC_BOT_MASTER_HANDOFF.md` (this file).
2. Run:
   ```bash
   git status
   git log --oneline -10
   git remote -v
   ```
3. Inspect the relevant source (§5 map).
4. Inspect the relevant tests (`tests/unit/test_<area>.py`).
5. Reproduce the issue (a headless TUI pilot or a unit test first; real posts only with explicit human intent).
6. Make the smallest safe change, in the layer that owns the responsibility (§4.3).
7. Add a regression test that fails without the fix.
8. Run the targeted tests: `python -m pytest tests/unit/test_<area>.py -q`.
9. Run the full suite: `python -m pytest -q` (about 3–4 minutes; expect 823+ passing).
10. Run Ruff: `ruff check .`.
11. Inspect the diff: `git diff`, `git diff --check`.
12. Update the docs (this file, PROJECT_STATUS.md, TROUBLESHOOTING.md as relevant).
13. Ask before committing or pushing.

---

## 38. Quick reference

```
SOC_BOT
Purpose     social-media DISTRIBUTION (one finished video → many accounts)
UI          Textual + Rich TUI (terminal only); classic menu with --plain
Platforms   Instagram (Reels) / YouTube / TikTok, official APIs only
Instagram   one batch · ONE shared Cloudflare Quick Tunnel · one video · one cover
            max 5 ACTIVE jobs (sliding, not waves) · poll every 15 s
            ONE automatic retry round for this batch's failed jobs
Links       content/published_links/*.json = source of truth; *.txt = permanent URLs
Storage     temporary media only while publishing; no media archive; no temp URLs saved
Tokens      Fernet-encrypted in SQLite (data/), key in .env
DB schema   v4 (004 = posts.auto_retry + publish_jobs.auto_retry_used)
Backend     FROZEN unless a real defect is found
Tests       823 passing, Ruff clean (2026-09-26)

Start       python main.py            (or double-click Start_Soc_bot.bat)
Plain       python main.py --plain
Dry run     python main.py --dry-run  (no network, nothing changed)
Scan        python main.py --scan     (AUTO profile publishes; VERIFY lists)
Screenshots python tools/readme_screenshots.py
```

---

## 39. Final architecture diagram

```
                              USER
                               │
                               ▼
               SOC_BOT TUI (Textual + Rich, terminal)
                               │
                               ▼
                 SERVICES (src/services, no tokens/URLs)
                               │
                               ▼
              PUBLISHER ENGINE + JOB STORE (src/core)
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
      INSTAGRAM             YOUTUBE              TIKTOK
          │           (resumable upload,     (Direct Post,
          │            thumbnails.set)        SELF_ONLY pre-audit)
          ▼
   INSTAGRAM BATCH (one post row)
          │
   ┌──────┴────────┐
   ▼               ▼
ONE LOCAL       ONE CLOUDFLARE
MEDIA SERVER    QUICK TUNNEL
(127.0.0.1,     (cloudflared child process,
 random paths)   *.trycloudflare.com)
   │               │
   └──────┬────────┘
          │
   shared video URL + shared cover URL   (memory only)
          │
          ▼
   MAX 5 ACTIVE JOBS  (sliding pool; one container per job)
          │
          ▼
   INITIAL ROUND COMPLETE
          │
          ▼
   FAILED JOBS OF THIS BATCH ONLY (not uncertain, not already retried)
          │
          ▼
   ONE AUTOMATIC RETRY (fresh shared start if the tunnel had failed)
          │
          ▼
   FINAL RESULTS (per job: published / failed)
          │
          ▼
   PERMANENT LINKS SAVED (json + txt, per job as it publishes)
          │
          ▼
   CLEANUP ONCE (tunnel + server stopped)
```

---

## 40. Instructions to the next AI

- **This is a terminal / TUI project.** Do **not** build a web UI unless the user explicitly asks for one.
- **Do not rewrite the engine.** It is frozen. Change it only for a demonstrated defect, with a regression test.
- **Inspect the source before assuming anything.** File names, behavior and numbers in any doc (including this one) can go stale.
- **Preserve the locked architecture:**
  - one shared tunnel per Instagram batch;
  - max 5 active jobs (sliding);
  - one cover file for all accounts;
  - exactly one automatic retry round;
  - permanent links only.
- **Treat tests as important evidence**, and say clearly which results are mocked and which are real.
- **Distinguish verified facts from assumptions** in every report. Write "not verified" when that is the truth.
- **Do not repeat the rejected hosting experiments** (§14, §30) without a concrete new reason.
- **Never request or expose secrets:**
  - don't ask the user to paste credentials;
  - don't print, log or commit `.env` values, tokens or keys;
  - don't modify `.env` automatically.
- **Never publish real social posts for testing** without explicit human intent. Avoid unnecessary real Instagram test publications: they can't be deleted through the API.
- **Inspect stale queue jobs before any resume.** Resume publishes **every** open job. List them and ask about old ones first (see incident #25).
- **Don't commit or push** unless the user asks; the user does it.
- **Update the documentation after major changes:** this file first, then PROJECT_STATUS.md, TROUBLESHOOTING.md and DECISIONS.md (add an ADR for architectural decisions).
