# Architecture

## System Overview

```
                      main.py
                        |
                  Terminal CLI
                        |
           +------------+------------+
           |                         |
    Account Manager   JobStore (src/core/jobs.py)
           |                         |
           +------------+------------+
                        |
                 Publisher Engine
                        |
        +----------------+----------------+
        |                |                |
     Instagram         TikTok          YouTube
      Adapter          Adapter          Adapter
        |                |                |
     Official API     Official API     Official API
```

## Core Components

### 1. Terminal CLI (`src/cli/`) — ✅ **IMPLEMENTED**
- **menu.py** — Main menu loop, navigation, user input handling
- **prompts.py** — Interactive prompts for video selection, caption entry, platform/account selection
- **display.py** — Rich text formatting, tables, progress bars, status display
- **account_menu.py** — Account management submenu (list, details, connect, create dev, update, disconnect, enable)
- **publish_menu.py** — Phase 4 minimal CLI: Create Post (plan → confirm → publish with live status) and Publishing Queue

### 2. Account Manager (`src/accounts/manager.py`) — ✅ **IMPLEMENTED**
- Manages connected accounts per platform
- Stores/retrieves account credentials from database (encrypted)
- Account status tracking (active, expired, revoked, disconnected)
- Account listing with filtering (platform, status)
- Account search by platform + platform_account_id
- Safe display (no tokens in output)
- Internal token access for publishing
- Development/test account creation (explicitly labeled)

### 3. Auth Manager (`src/auth/manager.py`) — ✅ **IMPLEMENTED**
- Coordinates OAuth flows for all platforms
- Manages platform configurations from environment
- Orchestrates OAuth flows: browser launch, callback server, token exchange
- Creates/updates accounts with encrypted token storage
- Manages token refresh and revocation
- Provides account selection for publishing

### 4. Auth Infrastructure (`src/auth/`) — ✅ **IMPLEMENTED**
- **base.py**: `OAuthConfig`, `OAuthTokenResult` (with `expires_at` from `expires_in`), `PlatformAuth` base class that all three adapters subclass, and the `expires_at_from` / `is_token_expiring` helpers
- **state.py**: OAuth state (single-use, platform-bound) and PKCE; `generate_pkce_challenge(verifier, encoding)` supports `base64url` (RFC 7636) and `hex` (TikTok)
- **callback_server.py**: one shared `OAuthCallbackServer` on 127.0.0.1, dynamic port (0) by default, exposing the bound `port` and `redirect_uri(platform)`, with timeout and state/platform validation
- **errors.py** — OAuth-specific exception hierarchy
- **manager.py** — AuthManager coordinating all platform adapters

### 5. Job Store (`src/core/jobs.py`): ✅ **IMPLEMENTED** (Phase 4)
- Creates a post plus one `PublishJob` per destination account (the video is de-duplicated by SHA-256)
- Enforces the job state machine with conditional UPDATEs (`transition`, atomic `claim`)
- Records every provider attempt in `publish_attempts`: provider IDs only, never tokens

### 6. Publisher Engine (`src/core/publisher.py`): ✅ **IMPLEMENTED** (Phase 4)
- Platform-agnostic: no platform HTTP; calls `PlatformPublisher` adapters
- Per job: validate, check the account, renew the token if needed, publish/resume, record the attempt, update state
- Destinations are isolated: an error (even an unexpected exception) in one job never stops or rolls back another
- Bounded retries for retryable errors; resumes processing jobs; dry-run plan without network

### 7. Platform Adapters (`src/platforms/`): ✅ **IMPLEMENTED** (mock-tested; real publishing NOT RUN)
- **base.py**: `PlatformPublisher` interface, `PublishContext`, `PublishOutcome`, `PublishError` (retryable / uncertain), `redact()`
- **`<platform>/auth.py`**: OAuth (Phase 3B)
- **`<platform>/publisher.py`**: platform publishing and platform-specific validation

### 7a. Media Validation (`src/core/validation.py`): ✅ **IMPLEMENTED**
- Generic checks only: exists, regular file, readable, non-empty, MP4/MOV/WebM, size; ffprobe metadata when installed
- Platform limits live in each adapter's `validate()`

### 7b. Content Intake (`src/content/`): ✅ **IMPLEMENTED** (Phase 5A)
```
content/incoming/<package>/  ->  ContentDetector  ->  ContentValidator  ->  PublishingProfile
      -> ContentIntake.plan() (engine.plan_destinations + adapter.cover_plan)  ->  one confirmation (VERIFY) / none (AUTO)
      -> ContentManager.move(publishing/)  ->  JobStore.create_post / add_missing_jobs  ->  existing PublisherEngine
      -> published/ | archive/ | failed/ | stays in publishing/ (still processing / crash)
```
- **models.py**: `ContentPackage`, `content_root()` (`CONTENT_ROOT`), `stability_seconds()` (`CONTENT_STABILITY_SECONDS`)
- **detector.py**: finds packages and their video/caption/cover (ambiguity = invalid; symlinks never followed)
- **validator.py**: video via `core.validation`, UTF-8 caption (unmodified), cover magic bytes, partial-copy stability
- **manager.py**: moves packages between stages, only inside the content root, never deletes
- **profile.py**: `Profile` + `ProfileStore` (`publishing_profiles`, account IDs only)
- **intake.py**: `ContentIntake`: scan/inspect (read-only), plan, publish (one package), publish_ready (AUTO), history
- No platform-specific code: per-platform options come from the profile, and cover handling comes from the adapter's capability methods. See [CONTENT_INTAKE.md](CONTENT_INTAKE.md).

### 7b-2. Audience Strategy (`src/content/audience.py`): ✅ **IMPLEMENTED** (V1, 2026-10-02, migration 005)
**What it is:** reusable *content-strategy metadata* recorded on each post: which countries the post is meant for
(ISO 3166-1 alpha-2 codes), its language (ISO 639), caption locale (e.g. `en-US`) and a posting-time strategy.
**What it is NOT:** geographic targeting. Organic Instagram / TikTok / YouTube publishing has no audience-country
parameter, so nothing from a profile is sent to any platform, and there are no location, VPN or proxy tricks
anywhere. Choosing "United States" does **not** guarantee the post is shown in the United States: the platforms
alone decide distribution. The strategy exists for future caption localisation, audience-aware scheduling and analytics.

```
AudienceStore (audience_profiles) --list()--> TUI Create Post (Caption step: "Audience Strategy" select, default Global)
                                  --list()--> CLI Create Post / publishing profile editor (Content Inbox)
PublishingService.create_batch / run_create_post / ContentIntake.publish
   +- JobStore.create_post (unchanged) --> AudienceStore.attach(post_id, profile_id)
         writes posts.audience_profile_id + posts.audience_json (immutable snapshot)
PublisherEngine / adapters: unchanged, never see the strategy
```
- **Profiles**: `Audience` dataclass + `AudienceStore` (list / get / save / attach / for_post). Countries are validated
  against the full ISO 3166-1 alpha-2 table (`ISO_COUNTRIES`, 249 codes): any valid code works without code or schema
  changes. Rejected: unknown codes (e.g. `UK`, `USA`, `XX`), duplicate codes, an empty country list on a custom
  profile, bad language/locale, unknown time strategy, duplicate names.
- **Built-ins**: Global (no countries, `global` time) + 15 single-country profiles (US, CA, GB, AU, DE, FR, JP, KR, NL,
  SE, NO, DK, CH, SG, NZ, each with its language/locale and `audience_local` time), seeded as rows by migration 005.
  Same code path as custom profiles; they can be edited or disabled, never deleted.
- **Custom profiles**: Settings › Audience Profiles (classic menu; in the TUI: Settings › Advanced › Open classic
  settings): create / edit / enable-disable. Profiles are never deleted (disabling hides them from new posts).
- **`AudienceTimeStrategy`**: `global` | `audience_local` | `manual`. Recorded only; no scheduler reads it yet.
- **Snapshot**: `attach` stores `{"profile_id", "name", "countries", "language", "caption_locale",
  "timezone_strategy"}` in `posts.audience_json` once; a second attach never replaces it. Editing or disabling the
  profile later does not change existing posts. Posts with no snapshot (all posts before migration 005, or "Skip")
  mean "no strategy" and are shown as `none (Global)`.
- **Where it is shown**: Create Post Review, the CLI batch summary, the publishing-profile view, Batch detail.
- **Future direction**: audience-aware scheduling from `timezone_strategy` + countries; caption locale handling;
  analytics comparing the chosen strategy with actual audience countries **from official platform insights APIs
  only**. No metrics are fabricated and no analytics are collected in V1.

### 7b-3. Audience-aware timing suggestions (`src/content/timezones.py`, `src/content/scheduling.py`): ✅ **IMPLEMENTED** (V2.1, 2026-10-02)
**A recommendation engine only.** It suggests publishing times that fall in good *local* hours for the audience's
timezones. It does not schedule anything, creates no jobs, sends nothing to any platform, and does not control or
guarantee geographic distribution: choosing countries is not targeting, and the platforms alone decide who sees a
post. No VPN, proxy or location spoofing exists anywhere.

```
audience snapshot (V1 Audience.snapshot() / posts.audience_json)
  -> zones_for(countries, year): country -> weighted IANA zones (curated table, else tzdata zone.tab)
  -> 15-minute UTC slots, now rounded up after a 15-minute lead, 24-hour horizon
  -> per zone: local time (zoneinfo, that slot's real date = DST-aware) -> posting window weight, quiet-hours penalty
  -> slot score = sum(audience weight x zone score); coverage = audience share inside a window
  -> merge neighbouring equal-score slots -> drop score <= 0 and < 25 % of the best -> >= 2 h apart -> at most 3
  -> Recommendation(slots with UTC range + local views, zones, notes, engine_version, tzdata_version, windows)
```
- **Country -> timezones** (`timezones.py`): a country is never reduced to one zone. `CURATED_ZONES` holds
  representative zones with **approximate population shares** (heuristics, not audience data) for US, CA, AU, RU,
  BR, MX, ID, ES, PT, CN (Shanghai only), NZ, CL, EC. Other countries are derived from tzdata's `zone.tab`, grouped by
  their January/July UTC offsets, equal weights, marked `derived` (a note when there is more than one zone). BV/HM
  have no timezone data and get a note. Each country gets an equal share of a multi-country audience.
- **Posting windows** (`DEFAULT_WINDOWS`, configurable through `TimezoneEngine(windows=...)`): morning 07:00–09:00
  (0.6), midday 12:00–13:30 (0.8), evening 19:00–22:00 (1.0), quiet hours 00:00–06:00 (−0.5 penalty). Heuristic
  defaults, not platform facts. **Weekday/weekend differences are not modelled yet.**
- **Strategies**: `audience_local` -> suggestions; `global` (and no strategy / the Global profile) -> no suggestion
  and the note "Global audience: no audience-local time preference; publish when convenient." (there is deliberately
  no "best worldwide time"); `manual` -> no suggestion; `convert_manual(local, zone, snapshot)` turns a wall-clock time
  in an IANA zone into UTC plus the audience's local views (DST gap rejected, DST overlap = first occurrence). Default
  zone: `SOC_BOT_TIMEZONE` (.env), else UTC; the computer's timezone is never guessed.
- **Time handling**: `now` must be timezone-aware and is always passed in (the engine never reads the clock);
  everything is computed in aware UTC; no fixed offsets: DST comes from the IANA data per date.
- **Reproducibility**: same snapshot + same `now` + same tzdata = same result. Results record `engine_version` and
  `tzdata_version`; a newer tzdata release (a country changing its clocks) can change historical calculations.
  Suggestions are not stored.
- **Where shown** (read-only): TUI Create Post › Review ("Suggested times", strategy only) and the CLI batch summary,
  via `PublishingService.suggest_times(audience_id)` / `convert_manual(...)`. Viewing them never schedules or publishes.
- **Dependency**: `tzdata` (requirements.txt): Windows has no system timezone database for `zoneinfo`.
- **Next (V2.2, separate)**: real scheduling (stored UTC publish time) designed so it cannot collide with resume, which
  publishes every `pending` job; per-profile windows; weekday/weekend windows.

### 7b-4. Scheduling (V2.2): 🚧 **IN PROGRESS** (phases 1–3 done: storage, service, one-pass due scheduler)
Real scheduling lives **above** the publishing engine; adapters, media storage, auth and `PublisherEngine` are
unchanged. A scheduled post is an ordinary post with pending jobs that is **held** at the post level
(`posts.schedule_status`, DATABASE.md "Scheduling").

```
SchedulingService.schedule / schedule_from_suggestion ── create post + jobs, HELD (scheduled) ──┐
SchedulingService.cancel / reschedule / publish_now (conditional UPDATEs)                         │
DueScheduler.run_due(now)  ── one pass, UTC only ─────────────────────────────────────────────────┘
   1. scheduled and scheduled_at < now − 60 min   -> missed        (never published automatically)
   2. oldest scheduled with scheduled_at ≤ now ≤ scheduled_at + 60 min (scheduled_at, id):
        conditional UPDATE scheduled -> released  ── won? ──► PublishingService.publish_batch(post_id)
                                                  └─ lost ─► skipped (someone else changed it first)
   3. repeat step 2 until nothing is due (bounded: 1000 posts per pass)
```
- **Phase 1 (storage + gate)**: migration 006; `JobStore` never claims, resumes or auto-retries jobs of held posts
  (scheduled / missed / cancelled); scheduled posts are created atomically with their jobs.
- **Phase 2 (`src/services/scheduling.py`)**: `schedule`, `schedule_from_suggestion` (uses the suggestion's
  `start_utc` exactly), `cancel`, `reschedule`, `publish_now` (release only; the caller publishes), `view`,
  `scheduled`. Times: explicit zone > audience primary zone (`audience_local`) > `SOC_BOT_TIMEZONE` > UTC; V2.1
  `convert_manual` (DST gap rejected, overlap = first occurrence); allowed range now + 2 min … now + 365 days.
  `PublishingService.publish_batch` refuses held posts.
- **Phase 3 (`src/services/due_scheduler.py`)**: `DueScheduler.run_due(now=None, on_event=None) -> DueRunResult`
  (released / published / failed / missed / skipped / errors / events). Grace 60 minutes: exactly at
  `scheduled_at` and exactly at `scheduled_at + 60 min` the post is due; strictly later it becomes `missed`.
  Release and publish happen one post at a time; publishing goes only through `PublishingService` (job claiming,
  retries, tokens and platforms stay in the existing engine). A publishing failure or exception leaves the post
  `released` (no scheduling retry layer). Two scheduler runs, or a scheduler racing a cancel / reschedule /
  publish-now, can't both win: only the run whose conditional UPDATE released the post publishes it. Malformed
  scheduled rows are reported as errors and never published or repaired. The event callback is informational:
  its exceptions are recorded and never undo a state change. `now` must be timezone-aware (normalised to UTC).
- **Phase 4 (`src/core/publish_lock.py`): process-level publishing lock.** One exclusive, **non-blocking** OS lock
  on `<project>/data/publishing.lock` (anchored to the project root, not the working directory): Windows
  `msvcrt.locking(LK_NBLCK)` on byte 0, elsewhere `fcntl.flock(LOCK_EX | LOCK_NB)`. Busy = `PublishLockBusy`
  immediately (no waiting, polling, sleeping, retrying or stealing; user text `BUSY_MESSAGE`: "Another Soc_bot window
  is publishing. Please try again later."). The OS drops the lock when a process dies, so there are no PID files,
  heartbeats, expiry or stale-file deletion; the file holds no data and its existence means nothing. Inside one
  process the lock belongs to the acquiring **thread** and is re-entrant for it (only depth 0→1 locks and 1→0
  unlocks); other threads of the same process are busy too; releasing without holding raises `RuntimeError`.
  It lives in `src/core` because the Content Inbox (`src/content`) needs it and must not import `src/services`.
  - **Protected publishing entry points**: `PublishingService.publish_batch` / `retry_job` / `resume_open` (whole
    operation; TUI publishing goes through these), `DueScheduler.run_due` (the whole pass: missed marking, releases
    and publishing; nested `publish_batch` re-enters), CLI Create Post (from before the post is created), CLI Queue
    continue / retry (prompts are asked before locking), `ContentIntake.publish` (before any folder move; covers CLI
    Content Inbox, `main.py --scan` and the TUI Content screen). Busy: services raise `PublishLockBusy`; CLI prints
    the message; the inbox skips the package untouched; `run_due` returns `busy=True` with one `busy` event and
    changes nothing (no post is marked missed, released or published).
  - **Order**: publishing lock first, then database work. It is a publishing concurrency boundary, not a scheduling
    lock: scheduling state stays protected by the conditional database updates.
- **Phase 5: callers of `run_due`** (both use the same `DueScheduler`, so grace, atomic release, missed handling,
  lock behaviour and publishing are identical):
  ```
  SchedulingService ─► DueScheduler ─► publishing lock ─► PublishingService ─► PublisherEngine
        ▲                   ▲
        │                   ├── TUI: SocBotApp timer (worker group "scheduler")
   (schedules)              └── CLI: python main.py --run-due   (one pass, then exit)
  ```
  - **TUI** (`src/tui/app.py`): one pass right after mount, then every `SOC_BOT_SCHEDULER_POLL_SECONDS` (whole
    seconds 10–300, default 30; anything else falls back to 30 with a log warning). Passes run in a thread worker
    in their own `"scheduler"` group (manual publishing stays in `"publish"`); a tick while a pass is running is
    skipped, never queued. Each pass first asks the read-only `DueScheduler.has_work()`: when nothing is due or
    overdue the publishing lock is not taken, so the timer never makes a manual publish busy. `publishing_active`
    = manual batch OR scheduler actually publishing a due post (set on `publishing_started`); a pass that only
    checks never blocks quitting. Quit stops the timer and cancels the scheduler group; while a scheduled post is
    publishing, quitting is refused like for a manual batch. Notices: missed (post id + UTC time), published,
    finished with problems, errors (redacted, each distinct message once); a busy lock shows "Another Soc_bot
    window is publishing. Scheduled posts will be checked again on the next run." once until a non-busy run.
    A failing pass is logged and notified; the next tick runs normally. The manual Publishing screen now shows the
    friendly busy message instead of "Publishing stopped: PublishLockBusy".
  - **CLI** (`main.py --run-due`): exactly one `run_due()` and exit, no prompt, no timer. Summary: checked time,
    released / published / failed / missed / skipped / errors, then one line per missed, problem and error.
    Exit codes: **0** = the pass completed (even if a post failed to publish: that is a publishing outcome, shown
    in the summary), **3** = another Soc_bot window holds the publishing lock (nothing changed; "Another Soc_bot
    window is publishing. Scheduled posts were not changed."), **1** = the pass could not run (startup or
    scheduler error).
  - **Windows Task Scheduler**: run `.venv\Scripts\python.exe main.py --run-due` every minute with *Start in* =
    the project folder (setup_guide/00_MASTER_SETUP_GUIDE.md, PART 8). No permanently running process. Timing is
    "first check after the scheduled time" (plus Windows start-up time), not second-exact; nothing runs while the
    PC is off or asleep, and posts later than the 60-minute grace become missed.
  - A pass still uses one fixed `now` (phase 3): posts that become due while a long pass publishes are handled
    by the next pass.
- **Phase 6: scheduling UI** (presentation only; every time, zone, DST and range decision is SchedulingService's):
  - **TUI Create Post → Review**: `▶ PUBLISH NOW` (unchanged) · `⏰ Schedule…` · Cancel. Schedule opens
    `ScheduleModal` (`src/tui/screens/schedule.py`): the V2.1 suggestions (selecting one passes the original slot
    to `schedule_from_suggestion`; stale ones are refused by the service and the list refreshes), or a custom
    date (YYYY-MM-DD) + time (HH:MM) + timezone Select over the full tz database (`all_zones()`, computed once;
    preset to the zone `resolve_zone` returns; leaving the preset lets the service resolve, any other choice is
    passed as explicit). "Check time" calls `resolve_time` and shows the confirmation (date, time, zone, UTC,
    audience, DST notes); SCHEDULE submits. Errors (`dst_gap`, `too_soon`, `too_far`, `invalid_input`,
    `stale_suggestion`, …) stay inline via `friendly_error()` and the form stays open. Nothing is published.
  - **TUI Queue**: a SCHEDULED section (scheduled + missed, soonest first: post, video, local time + zone,
    "in 5h 12m", status) above the batches; Enter opens `ScheduledActionsModal` (details incl. history) with
    Reschedule (same `ScheduleModal`, `reschedule` / `reschedule_from_suggestion`), Publish now (confirmation, then
    `PublishingScreen(release=True)`: takes the publishing lock FIRST, then `publish_now` + `publish_batch`, so a
    busy lock never leaves a released-but-unpublished post), Cancel schedule (confirmation, `cancel`; nothing is
    deleted). `conflict` / `invalid_state` show a friendly notice and the queue refreshes.
  - **Held posts are not "pending"**: `PublishingService.batches()` leaves scheduled / missed / cancelled posts out
    of the batch list (`include_held=True` to include), `batch()` reports their status as the schedule status (also
    on their jobs), `queue_counts()` counts their jobs separately (`scheduled` / `missed` / `cancelled` posts), and
    `history()` / `recent_activity()` show the schedule status. Job semantics (phase 1) are unchanged.
  - **Dashboard**: "Next scheduled: #42 Oct 03 19:00 America/New_York" (or none) via
    `SchedulingService.next_scheduled()`, inside the existing 3-second refresh. **Batch detail**: schedule status,
    time and history (`scheduled → rescheduled → released …`). **History**: Scheduled / Missed / Cancelled filters.
  - **CLI** (`src/cli/schedule_menu.py`): Create Post ends with `Publish: 1 Now / 2 Schedule / 3 Cancel`;
    Schedule = suggestions or custom date / time / IANA zone, confirmation (SCHEDULE / Back / Cancel). Queue shows
    a SCHEDULED list (held posts are left out of the batch list) and "Scheduled posts" actions: reschedule, publish
    now (lock first), cancel schedule.
  - New SchedulingService helpers: `parse_local`, `reschedule_from_suggestion`, `next_scheduled`,
    `friendly_error` / `ERROR_MESSAGES`, `all_zones`. UI/CLI code never writes schedule columns (a test scans them).
- **Not built**: Content Inbox scheduling; choosing the second occurrence of an ambiguous (DST overlap) local
  time (the first is always used and shown).

### 7c. Media Delivery (`src/media_storage/`): ✅ **IMPLEMENTED** (Instagram only; real run pending storage config)
```
InstagramPublisher ──► MediaSourceProvider (prepare / get_public_url / cleanup; max_file_size capability)
                            ├─► MediaStorageRouter (MEDIA_STORAGE_PROVIDER=auto): picks TempFile or S3 by file size
                            ├─► ObjectStorageMediaProvider ──► ObjectStorage ──► S3ObjectStorage (boto3; S3/R2/MinIO)   [MEDIA_STORAGE_PROVIDER=s3]
                            ├─► TempFileMediaStorage (httpx; public TempFile.org upload + id delete)                   [MEDIA_STORAGE_PROVIDER=tempfile]
                            ├─► CloudflareTunnelMediaProvider (127.0.0.1 single-file server + cloudflared Quick Tunnel) [MEDIA_STORAGE_PROVIDER=cloudflare_tunnel]
                            └─► ZeroX0MediaStorage (httpx; public 0x0.st upload + token delete)                        [MEDIA_STORAGE_PROVIDER=0x0]
```
`create_media_provider()` picks the provider from `MEDIA_STORAGE_PROVIDER` (default `s3`; never falls back to a public host). AUTO order: TempFile → Cloudflare Quick Tunnel → S3 (0x0 is never in AUTO). The tunnel provider is not object storage: it serves the local file through a temporary `trycloudflare.com` URL while Instagram fetches it, and cleanup stops the tunnel (see API_INTEGRATIONS.md → Cloudflare Quick Tunnel).
Temporary private object + presigned HTTPS URL for platforms that fetch media from a URL. The provider is created by the Instagram adapter via `create_media_provider()`; the engine only gets `delivery_notes()` text for plans. See API_INTEGRATIONS.md → Instagram Media Delivery.

### 7c-2. Instagram fan-out + cover: ✅ **IMPLEMENTED** (2026-09-26)
```
Create Post: video + optional cover.jpg + caption ─► multi-select accounts (A/N/toggle) ─► plan (no network) ─► ONE confirmation
   └─► post (= batch) with one publish_job per account ─► PublisherEngine.publish_post
          └─► Instagram jobs: ThreadPoolExecutor(max INSTAGRAM_MAX_CONCURRENT_PUBLISHES=5); others sequential
                 ├─► ONE SharedMediaSession per batch ─► MediaStorageRouter.select(size, needs_cover)
                 │      └─► CloudflareTunnelMediaProvider: 127.0.0.1 server {/media/<t1>.mp4, /media/<t2>.jpg} + ONE Quick Tunnel
                 ├─► each job: InstagramPublisher(ctx.media_provider = the session) ─► container(video_url, cover_url) ─► poll ─► publish ─► permalink
                 ├─► initial round drained ─► automatic retry round for the batch's failed jobs (once, same pool, same session)
                 └─► session.close() once
```
The batch is only a coordinator (post id). Jobs keep their own state machine; resume = `publish_post` again (final jobs skipped). See API_INTEGRATIONS.md → Instagram multi-account fan-out.

### 7d. Published-Link Library (`src/core/published_links.py`, `src/cli/links_menu.py`): ✅ **IMPLEMENTED**
`PublisherEngine(links=PublishedLinks())` calls `_record_link` after the `published` transition, and `adapter.published_url(media_id, state)` gives the permanent URL (YouTube watch URL / Instagram permalink / TikTok `None`). Links go to per-platform JSON files under `content/published_links/`, not the DB. See CONTENT_INTAKE.md → Published Links.

### 8. Storage (`src/storage/`) — ✅ **IMPLEMENTED**
- **database.py** — SQLite connection, migrations, ORM/models
- **tokens.py** — Token encryption/decryption, secure storage

## Data Flow

```
User Input (Video + Caption)
          |
          v
    Validation
          |
          v
Platform/Account Selection
          |
          v
    Job Creation (1 job per destination)
          |
          v
    For Each Job (sequential, isolated; see ADR-011):
       Publisher Engine
             |
             v
       Platform Adapter
             |
             v
       Official API
             |
             v
       Result (Success/Failure)
          |
          v
    Aggregate Results → Display (job + attempt rows are the history)
```

## CLI Layer — ✅ **IMPLEMENTED**

```
main.py
  └── CLI Menu (menu.py)
        ├── Create Post → publish_menu.py → validation.py → plan → JobStore → PublisherEngine
        ├── Connected Accounts → account_menu.py → AuthManager → Account Manager
        ├── Publishing Queue → publish_menu.py → list jobs / continue open jobs / retry failed
        ├── History → not implemented
        ├── Settings → not implemented
        └── Exit
```

## Account Manager

Responsibilities:
- List connected accounts per platform
- Store/retrieve account credentials from database (encrypted)
- Account status tracking (active, expired, revoked, disconnected)
- Account listing with filtering (platform, status)
- Account search by platform + platform_account_id
- Safe display (no tokens in output)
- Internal token access for publishing
- Development/test account creation (explicitly labeled)

## Auth Manager — ✅ **IMPLEMENTED**

Responsibilities:
- Coordinate OAuth flows for all platforms
- Manage platform configurations from environment
- Orchestrate OAuth flows: browser launch, callback server, token exchange
- Create/update accounts with encrypted token storage
- Manage token refresh and revocation
- Provide account selection for publishing

## Publishing (Phase 4)

```
Post #15 (video + caption)
   +-- Job #101 -> Instagram account A   (independent)
   +-- Job #102 -> TikTok account B      (independent)
   +-- Job #103 -> YouTube channel C     (independent)
```

### Job state machine (`publish_jobs.status`)

```
pending ──claim──► uploading ──► processing ──► published   (terminal)
   │                  │   │           │  ▲
   │                  │   └───────────┼──┘ (fast providers: uploading ──► published)
   │                  ▼               ▼
   └───(validation)─► failed ◄────────┘
                        │ ▲
      retryable error:  │ │ bounded (3), then failed
   uploading/processing ──► retrying ──claim──► uploading
                        └── manual retry: failed ──► retrying
```

Allowed transitions are listed in `src/core/jobs.TRANSITIONS`. `processing ──► processing` means "still working; checked again later". A job that is still processing when polling ends is **not** failed.

### Engine flow

```
publish_post(post_id)
  for each job (isolated, try/except per job):
    published/failed     -> skip (never republish)
    pending/retrying     -> generic + platform validation (failure = failed, no retry)
                            claim (atomic pending|retrying -> uploading)
    uploading (found on start = crashed process)
                         -> resume only if adapter.can_restart(saved state), else failed "outcome unknown"
    processing           -> resume (poll / finish with saved provider IDs)
    attempt loop:
      start attempt -> check account active -> renew token if expiring -> adapter.publish(ctx, on_progress)
        on_progress: persists provider IDs to the attempt immediately + moves job to uploading/processing
      PublishError retryable & retries left -> retrying, sleep(30/60/120 s), claim, next attempt
      otherwise -> failed (safe error text)
      success -> published (platform_media_id) or processing
```

### Adapter interface (`src/platforms/base.py`)

```python
class PlatformPublisher(ABC):
    PLATFORM: str
    TOKEN_REFRESH_MARGIN: int          # renew when the token expires within this many seconds
    RESTART_SAFE: bool                 # may a crashed mid-upload job be started again?
    def validate(caption, options, media) -> list[str]           # no network
    def publish(ctx, on_progress) -> PublishOutcome              # resumes from ctx.state
    def can_restart(state) -> bool
    # Phase 5A cover capability
    def supports_cover_upload() -> bool      # YouTube: True
    def supports_cover_timestamp() -> bool   # TikTok: True (not configured yet)
    def validate_cover(path) -> list[str]
    def cover_plan(path) -> (status, reason) # none | upload | not_supported | skipped
```

`PublishOutcome.cover_status/cover_error` carry the cover result separately; the engine stores them in `publish_jobs.cover_status/cover_error` without touching the video status.

`publish()` is resumable: given the provider IDs saved by earlier attempts, it continues instead of starting over. A separate `get_status()` isn't needed. `cancel()` was not implemented: none of the three APIs offers a cancel for an in-flight publish (deleting a published video is a different action).

### Idempotency / duplicate protection

| Layer | Mechanism |
|-------|-----------|
| Database | Unique index `(post_id, account_id)`: one job per destination per post |
| Engine | Atomic claim; `published` is terminal; failed jobs only run again on explicit `retry_job` |
| CLI | Warns before publishing the same file (checksum) to an account it was already published to |
| Instagram | Container ID saved before polling. Resume checks `status_code`: `PUBLISHED` means done (no second `media_publish`); `FINISHED` means publish once; `ERROR`/`EXPIRED` means a new container (the old one can never publish) |
| TikTok | `publish_id` saved before the upload, `upload_complete` after it. Resume after the upload only polls status. An interrupted upload is re-initialised; TikTok can't publish an incomplete upload |
| YouTube | `video_id` saved as soon as the upload completes; resume only polls. The session URI is kept in memory only. If the final chunk's response is lost, the session is queried; if that also fails, the job is marked failed with `uncertain` and is **not** auto-retried (the video may exist) |

Unavoidable uncertainty: a crash between the provider accepting a request and the local write of its ID. On Instagram and TikTok that leaves an orphan container or upload that never publishes. On YouTube a crash during the final chunk leaves the outcome unknown, so the job is failed as "outcome unknown; check the channel" rather than re-uploaded.

Single-process assumption: a job found in `uploading` at the start of a run is treated as crashed. Two CLI instances publishing at once are not supported.

### Token handling
The engine loads the account (it must be `active`) and checks `expires_at` against the adapter's `TOKEN_REFRESH_MARGIN` (Instagram 7 days, others 5 min). If the token is expiring it calls `AuthManager.refresh_account_tokens`, which uses the platform's documented mechanism (Instagram `ig_refresh_token`, TikTok/YouTube refresh token, rotation persisted, Fernet-encrypted by AccountManager). If renewal fails and the token has already expired, the job fails with "reconnect the account"; if it is still valid, publishing continues. Tokens exist only in the adapter's `PublishContext` and request headers.

## Platform Adapters — Auth ✅ IMPLEMENTED

```
PlatformAuth (src/auth/base.py)
  PKCE_CHALLENGE_ENCODING = "base64url"   SCOPE_SEPARATOR = " "
  generate_pkce_pair() / get_authorization_url() / validate_configuration()
     |
     +-- YouTubeAuth     (inherits defaults; access_type=offline, prompt=consent)
     +-- TikTokAuth      (PKCE "hex", scopes ",", client_key param)
     +-- InstagramAuth   (no PKCE, scopes ",", REFRESH_USES_ACCESS_TOKEN)

OAuthCallbackServer (shared) --binds 127.0.0.1:0--> actual port --> redirect_uri --> adapter.config
```

Endpoint details and verification status: see [API_INTEGRATIONS.md](API_INTEGRATIONS.md) (verified 2026-09-25).

- **Instagram**: Business Login for Instagram (`instagram.com/oauth/authorize` → `api.instagram.com/oauth/access_token` → `ig_exchange_token`). No Facebook Page. Identity is `/me` `user_id`.
- **TikTok**: Login Kit for Desktop, with a hex PKCE challenge and refresh-token rotation.
- **YouTube**: Google OAuth for installed apps on a dynamic loopback port.

## Storage

SQLite database (`data/publisher.db`):
- Accounts table
- Videos table
- Posts table
- Publish Jobs table
- Publish Attempts table (optional)

Token encryption using Fernet (symmetric encryption) with key from environment.

## Authentication

OAuth 2.0 / 2.1 flows per platform:
- Instagram: Instagram API with Instagram Login: **IMPLEMENTED, mock-tested, real OAuth NOT RUN**
- TikTok: Login Kit for Desktop: **IMPLEMENTED, mock-tested, real OAuth NOT RUN**
- YouTube: Google OAuth 2.0 installed-app flow: **IMPLEMENTED, mock-tested, real OAuth NOT RUN**

Local callback server on `http://127.0.0.1:<dynamic-port>/callback/{platform}` (or a fixed `*_REDIRECT_URI`).

## Error Handling

- Platform API errors → mapped to common error types
- Network errors → retry with backoff
- Token expiry → renewed before publishing (not on a mid-request 401)
- Validation errors → fail fast, no API call
- Errors stored per job/attempt with `redact()` applied; no structured logging yet (ADR-010 still planned)

## Retry Architecture (implemented)

- Max 3 retries per job (`retry_delays = (30, 60, 120)` seconds), run in-process while the CLI waits
- Retryable: network error/timeout, 5xx, 429, provider-flagged transient errors (Instagram `is_transient`), expired Instagram container, TikTok `rate_limit_exceeded`/`internal_error`
- Non-retryable: validation, 401/invalid token, insufficient scope, permission denied, quota exceeded, rejected content, inactive account, **uncertain outcome**
- `retry_count`, `next_retry_at`, `error_message` persisted on the job; each attempt's error in `publish_attempts.error_json`
- Retries resume from saved provider IDs instead of starting over