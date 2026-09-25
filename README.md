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
| OAuth authentication (Instagram, TikTok, YouTube) | ✅ **IMPLEMENTED** |
| OAuth callback server & PKCE | ✅ **IMPLEMENTED** |
| Job management & queue | 📋 **PLANNED** |
| Instagram publishing adapter | 📋 **PLANNED** |
| TikTok publishing adapter | 📋 **PLANNED** |
| YouTube publishing adapter | 📋 **PLANNED** |
| SQLite database layer | ✅ **IMPLEMENTED** |
| Video validation | 📋 **PLANNED** |
| Dry-run mode | 📋 **PLANNED** (placeholder) |
| Unit/integration tests | ✅ **IMPLEMENTED** (170 unit tests) |

**Legend:** ✅ IMPLEMENTED | 🔄 IN PROGRESS | 📋 PLANNED | 🚫 BLOCKED

## Supported Platforms

| Platform | API | Status |
|----------|-----|--------|
| Instagram | Instagram Graph API / Meta Business API | ✅ VERIFIED |
| TikTok | TikTok Creator API / TikTok Shop API | ✅ VERIFIED |
| YouTube | YouTube Data API v3 | ✅ VERIFIED |

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

# Instagram (Meta)
INSTAGRAM_APP_ID=your_app_id
INSTAGRAM_APP_SECRET=your_app_secret
INSTAGRAM_REDIRECT_URI=http://localhost:8080/callback/instagram

# TikTok
TIKTOK_CLIENT_KEY=your_client_key
TIKTOK_CLIENT_SECRET=your_client_secret
TIKTOK_REDIRECT_URI=http://localhost:8080/callback/tiktok

# YouTube (Google)
YOUTUBE_CLIENT_ID=your_client_id
YOUTUBE_CLIENT_SECRET=your_client_secret
YOUTUBE_REDIRECT_URI=http://localhost:8080/callback/youtube

# Application
LOG_LEVEL=INFO
DRY_RUN=false

# Optional: Override callback host/port (default 127.0.0.1:8080)
# OAUTH_CALLBACK_HOST=127.0.0.1
# OAUTH_CALLBACK_PORT=8080
```

**⚠️ Never commit `.env` to version control.** It is protected by `.gitignore`.

## Running the Application

```bash
# Normal mode
python main.py

# Dry-run mode (shows plan without publishing)
python main.py --dry-run

# Help
python main.py --help
```

## CLI Workflow (Implemented V1 Menu)

```
SOCIAL PUBLISHER v1

1. Create Post
2. Connected Accounts
3. Publishing Queue
4. History
5. Settings
6. Exit
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
- Application receives **access tokens** and **refresh tokens**
- Tokens stored securely in encrypted SQLite database
- Automatic token refresh before expiration
- No passwords ever requested or stored

## OAuth Implementation Details

- **PKCE (S256)**: Required for all platforms (Instagram, TikTok, YouTube)
- **State Parameter**: CSRF protection, cryptographically random, single-use
- **Callback Server**: Local HTTP server on `http://localhost:8080/callback/{platform}`
- **Token Storage**: Fernet (AES-128-GCM) encryption at rest
- **Token Refresh**: Automatic proactive refresh (24h before expiry) + on-demand
- **Token Revocation**: Platform-specific revocation endpoints

### Platform-Specific OAuth

| Platform | Auth Product | Auth Flow | Scopes |
|----------|--------------|-----------|--------|
| Instagram | Facebook Login for Instagram | Auth Code + PKCE | instagram_graph_user_profile, instagram_graph_user_media, pages_show_list, pages_read_engagement |
| TikTok | TikTok Login Kit | Auth Code + PKCE | video.upload, video.publish, user.info.basic |
| YouTube | Google OAuth 2.0 (Installed App) | Auth Code + PKCE | youtube.upload, youtube, youtube.readonly |

## Security Rules

- ✅ Official OAuth only
- ✅ Tokens encrypted at rest (Fernet AES-128-GCM)
- ✅ `.env` in `.gitignore`
- ✅ No credentials in logs
- ✅ HTTPS for all API calls
- ✅ Input validation on all user data
- ✅ File size/type validation before upload

## Development Status

This project is in **Phase 3B (Official OAuth Verification + OAuth Infrastructure) — COMPLETE**. The repository contains:
- ✅ SQLite database layer with migrations and token encryption
- ✅ Terminal CLI menu system with input validation
- ✅ Account management core (CRUD, listing, status, filtering)
- ✅ OAuth authentication for Instagram, TikTok, YouTube
- ✅ OAuth callback server with PKCE (S256) and CSRF protection
- ✅ Connected Accounts CLI submenu (list, details, connect, update, disconnect, enable)
- ✅ 170 unit tests passing
- Project structure and documentation

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