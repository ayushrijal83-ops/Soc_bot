# Social Publisher (SOC_BOT)

A terminal-based multi-platform social media publishing bot for distributing finished videos with captions to Instagram, TikTok, and YouTube using official platform APIs.

## What This Bot Does

- Publishes **finished videos** with **user-provided captions** to multiple social media platforms
- Supports **multiple connected accounts** per platform (e.g., multiple Instagram pages, TikTok accounts, YouTube channels)
- Uses **official OAuth/authorization flows** — never asks for or stores platform passwords
- Creates **independent publishing jobs** per destination — one failure doesn't block others
- Provides a **terminal-based menu interface** for selecting video, caption, platforms, and accounts
- Tracks publishing **history** and **job status** (PENDING → UPLOADING → PROCESSING → PUBLISHED / FAILED → RETRYING)

## What This Bot Does NOT Do

- ❌ AI video generation
- ❌ AI caption generation
- ❌ Web scraping or browser automation
- ❌ Fake engagement (likes, views, follows)
- ❌ Automated account creation
- ❌ Password-based social media login
- ❌ Unofficial or private APIs

## Current Implementation Status

| Component | Status |
|-----------|--------|
| Project structure & documentation | ✅ **IMPLEMENTED** |
| Terminal CLI menu system | ✅ **IMPLEMENTED** |
| Account management core (CRUD, listing, status) | ✅ **IMPLEMENTED** |
| OAuth authentication (Instagram, TikTok, YouTube) | ✅ **IMPLEMENTED**; real OAuth done for Instagram (11 accounts) and YouTube (2 accounts); TikTok real run pending app credentials |
| OAuth callback server (dynamic loopback port) & PKCE | ✅ **IMPLEMENTED** |
| Job management, queue, resume, one automatic retry round per batch | ✅ **IMPLEMENTED**, real-tested |
| Instagram publishing (Reels + custom cover) with multi-account batches: ONE shared Cloudflare Quick Tunnel per batch, max 5 concurrent jobs | ✅ **IMPLEMENTED**, real-tested (up to 11 accounts, 131.9 MB videos) |
| Published-link library (`content/published_links/*.json` + `.txt`) | ✅ **IMPLEMENTED**, real-tested |
| TikTok publishing adapter (Direct Post) | ✅ **IMPLEMENTED**, mock-tested (real run pending TikTok app credentials) |
| YouTube publishing adapter (resumable upload) | ✅ **IMPLEMENTED**, real-tested |
| SQLite database layer | ✅ **IMPLEMENTED** |
| Video validation | ✅ **IMPLEMENTED** |
| Dry-run mode | ✅ **IMPLEMENTED** (plan only, no API calls) |
| Content Inbox (drop folders in `content/incoming/`) | ✅ **IMPLEMENTED** (Phase 5A) |
| Publishing profile (VERIFY / AUTO) | ✅ **IMPLEMENTED** (Phase 5A) |
| Cover/thumbnail (YouTube `thumbnails.set`) | ✅ **IMPLEMENTED**, real-tested on YouTube |
| Unit/integration tests | ✅ **IMPLEMENTED** (780 unit tests, Ruff clean) |

**Legend:** ✅ IMPLEMENTED | 🔄 IN PROGRESS | 📋 PLANNED | 🚫 BLOCKED

## Supported Platforms

| Platform | API | Status |
|----------|-----|--------|
| Instagram | Instagram API with Instagram Login | OAuth docs verified 2026-09-25 |
| TikTok | Login Kit for Desktop + Content Posting API | OAuth docs verified 2026-09-25 |
| YouTube | YouTube Data API v3 (Google OAuth installed app) | OAuth docs verified 2026-09-25 |

## Requirements

- Python 3.11+
- Virtual environment (recommended)
- Platform developer accounts:
  - Meta Developer Account (for Instagram)
  - TikTok Developer Account
  - Google Cloud Project (for YouTube)

## Installation

```bash
# Clone the repository
git clone https://github.com/ayushrijal83-ops/Soc_bot.git
cd Soc_bot

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your API credentials
```

## Environment Configuration

Create a `.env` file from `.env.example` with the following variables:

```env
# Database
DATABASE_URL=sqlite:///data/publisher.db

# Encryption key for token storage (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
ENCRYPTION_KEY=

# Instagram (Instagram API with Instagram Login; use the Instagram app ID/secret)
INSTAGRAM_APP_ID=your_instagram_app_id
INSTAGRAM_APP_SECRET=your_instagram_app_secret
INSTAGRAM_REDIRECT_URI=http://127.0.0.1:8765/callback/instagram   # fixed; must be registered

# TikTok (Login Kit for Desktop; register http://127.0.0.1:*/callback/tiktok)
TIKTOK_CLIENT_KEY=your_client_key
TIKTOK_CLIENT_SECRET=your_client_secret
TIKTOK_REDIRECT_URI=          # blank = dynamic port

# YouTube (Google OAuth client type "Desktop app")
YOUTUBE_CLIENT_ID=your_client_id
YOUTUBE_CLIENT_SECRET=your_client_secret
YOUTUBE_REDIRECT_URI=         # blank = dynamic port

# Application
LOG_LEVEL=INFO
DRY_RUN=false

# Optional: callback host/port (default 127.0.0.1, port 0 = OS picks a free port per flow)
# OAUTH_CALLBACK_HOST=127.0.0.1
# OAUTH_CALLBACK_PORT=0
```

**⚠️ Never commit `.env` to version control.** It is protected by `.gitignore`.

Note: `main.py` does not load `.env` automatically yet. Export these variables in your shell before running.

## Running the Application

```bash
# Normal mode
python main.py

# Dry-run: validate unpublished posts and show the plan (never contacts a platform)
python main.py --dry-run

# Help
python main.py --help
```

## Content Inbox (Phase 5A)

```
content/incoming/post_001/
    video.mp4        # one video (.mp4 / .mov / .webm)
    caption.txt      # UTF-8 caption, sent as-is
    cover.jpg        # optional (YouTube thumbnail; Instagram Reel cover, JPEG <= 8 MB; TikTok: not supported)
    title.txt        # optional YouTube title
```

1. **Settings → Create/Edit Publishing Profile** (once): choose accounts, privacy, cover, VERIFY/AUTO
2. **Content Inbox**: pick a package, review the plan, confirm **once**. It moves to `published/` (or `failed/`)
3. `python main.py --scan`: AUTO profiles publish ready packages; `--dry-run` previews without doing anything

Full guide: [docs/CONTENT_INTAKE.md](docs/CONTENT_INTAKE.md).

## Terminal UI

`python main.py` opens the full-screen terminal UI (Textual + Rich). It is still a terminal app: no browser, no web server.

| Key | Page | | Key | Action |
|---|---|---|---|---|
| D | Dashboard | | / | Search (lists) |
| C | Create Post | | Space | Toggle selection |
| Q | Queue | | Enter | Open / confirm |
| H | History | | Esc | Back / close |
| A | Accounts | | Ctrl+P | Command palette |
| L | Published Links | | ? | Help |
| T | Content | | Ctrl+Q / X | Exit |
| S | Settings | | | |

- **Create Post:** Media (video + one optional cover) → Caption → Destinations (platform cards, searchable account list, A all / N none) → Options (only what the chosen platforms need) → Review → one confirmation → live progress (initial round, automatic retry round, final result).
- **Queue:** batches with progress; Enter opens a batch (o open post, c copy link, r retry a failed job, Enter details). **Links:** C copy selected, A copy all visible (permanent URLs only).
- Terminals under 100 columns hide the sidebar. Every page works down to 80×24.
- `python main.py --plain` starts the classic text menu. It is also used automatically when there is no interactive terminal. `--dry-run`, `--scan` and `--version` are unchanged.
- In the TUI, log lines go to `logs/soc_bot.log` (no tokens or temporary URLs are ever logged).

## Classic menu (`--plain`)

```
SOCIAL PUBLISHER v1

1. Create Post
2. Connected Accounts
3. Publishing Queue
4. History
5. Content Inbox
6. Published Links
7. Settings
8. Exit
```

**Connected Accounts Submenu:**
```
CONNECTED ACCOUNTS

1. List Accounts
2. Account Details
3. Connect Instagram
4. Connect TikTok
5. Connect YouTube
6. Create Development Account
7. Update Account
8. Disconnect Account
9. Enable Account
10. Back to Main Menu
```

## Authentication Concept

- Uses **official OAuth 2.0 / OAuth 2.1 flows** for each platform
- User authorizes via platform's official login page
- Application receives access tokens, plus refresh tokens where the platform issues them (TikTok, YouTube). Instagram issues long-lived access tokens that are re-exchanged instead.
- Tokens stored securely in encrypted SQLite database
- Token expiry recorded from the provider's `expires_in`; the publishing engine renews expiring tokens before each job
- No passwords ever requested or stored

## OAuth Implementation Details

- **PKCE (S256)**: YouTube (RFC 7636 base64url), TikTok (hex, per TikTok docs). Instagram Login does not document PKCE.
- **State Parameter**: CSRF protection, cryptographically random, single-use, bound to the platform
- **Callback Server**: `http://127.0.0.1:<dynamic-port>/callback/{platform}` (or a fixed `*_REDIRECT_URI`)
- **Token Storage**: Fernet encryption at rest
- **Token Renewal**: TikTok/YouTube refresh token (rotated TikTok tokens are persisted); Instagram `ig_refresh_token` re-exchange
- **Token Revocation**: TikTok and YouTube revoke endpoints; Instagram has none documented (local disconnect only)

### Platform-Specific OAuth

| Platform | Auth Product | Auth Flow | Scopes |
|----------|--------------|-----------|--------|
| Instagram | Business Login for Instagram | Auth Code (no PKCE) → long-lived token | instagram_business_basic, instagram_business_content_publish |
| TikTok | Login Kit for Desktop | Auth Code + PKCE (hex) | video.upload, video.publish, user.info.basic |
| YouTube | Google OAuth 2.0 (Installed App) | Auth Code + PKCE (base64url), loopback | youtube.upload, youtube, youtube.readonly |

## Security Rules

- ✅ Official OAuth only
- ✅ Tokens encrypted at rest (Fernet AES-128-GCM)
- ✅ `.env` in `.gitignore`
- ✅ No credentials in logs
- ✅ HTTPS for all API calls
- ✅ Input validation on all user data
- ✅ File size/type validation before upload

## Development Status

The publishing engine is **complete and frozen for UI development (2026-09-26 audit)**. The repository contains:
- ✅ SQLite database layer with migrations (schema v4) and encrypted tokens
- ✅ Terminal CLI (Create Post with one cover + multi-account selection, Publishing Queue, History, Content Inbox, Published Links, Settings)
- ✅ Official-API OAuth for Instagram, TikTok and YouTube (loopback callback, PKCE where documented)
- ✅ Publishing engine: independent per-account jobs, retries, resume, dry-run; Instagram batches with ONE shared Cloudflare Quick Tunnel, at most 5 active jobs, and one automatic retry round for the batch's failures
- ✅ Instagram Reels (incl. custom cover via `cover_url`) and YouTube: real-tested; TikTok: mock-tested, real run pending app credentials
- ✅ Automatic permanent-link library (JSON + TXT); temporary media URLs are never stored
- ✅ 780 unit tests passing; `ruff check .` clean

See [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for detailed progress tracking.

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, components, data flow |
| [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | Current progress, completed/planned/blocked work |
| [API_INTEGRATIONS.md](docs/API_INTEGRATIONS.md) | Platform API details, OAuth flows, publishing workflows (VERIFIED) |
| [AUTHENTICATION.md](docs/AUTHENTICATION.md) | OAuth architecture, token management, security |
| [DATABASE.md](docs/DATABASE.md) | Schema design, models, relationships (**IMPLEMENTED**) |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Developer setup, commands, debugging |
| [TESTING.md](docs/TESTING.md) | Test strategy, frameworks, test types |
| [SECURITY.md](docs/SECURITY.md) | Security practices, threat model |
| [DECISIONS.md](docs/DECISIONS.md) | Architecture Decision Records (ADRs) |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [CLAUDE_HANDOFF.md](docs/CLAUDE_HANDOFF.md) | Agent handoff context for AI assistants |

## License

MIT License — see [LICENSE](LICENSE) for details.