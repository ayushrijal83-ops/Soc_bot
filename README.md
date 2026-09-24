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
| Terminal CLI menu system | 🔄 **PLANNED** |
| Account management (OAuth) | 🔄 **PLANNED** |
| Job management & queue | 🔄 **PLANNED** |
| Instagram publishing adapter | 🔄 **PLANNED** |
| TikTok publishing adapter | 🔄 **PLANNED** |
| YouTube publishing adapter | 🔄 **PLANNED** |
| SQLite database layer | 🔄 **PLANNED** |
| Video validation | 🔄 **PLANNED** |
| Dry-run mode | 🔄 **PLANNED** |
| Unit/integration tests | 🔄 **PLANNED** |

**Legend:** ✅ IMPLEMENTED | 🔄 IN PROGRESS | 📋 PLANNED | 🚫 BLOCKED

## Supported Platforms

| Platform | API | Status |
|----------|-----|--------|
| Instagram | Instagram Graph API / Meta Business API | 📋 PLANNED |
| TikTok | TikTok Creator API / TikTok Shop API | 📋 PLANNED |
| YouTube | YouTube Data API v3 | 📋 PLANNED |

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

## CLI Workflow (Planned V1 Menu)

```
SOCIAL PUBLISHER v1

1. Create Post
2. Connected Accounts
3. Publishing Queue
4. History
5. Settings
6. Exit
```

**Create Post Flow:**
1. Choose video file
2. Enter caption
3. Select platforms (Instagram, TikTok, YouTube)
4. Select connected accounts/channels per platform
5. Validate media against platform requirements
6. Show publishing plan
7. Confirm → Create independent publishing jobs
8. Publish → Show results
9. Save to history

## Authentication Concept

- Uses **official OAuth 2.0 / OAuth 2.1 flows** for each platform
- User authorizes via platform's official login page
- Application receives **access tokens** and **refresh tokens**
- Tokens stored securely in encrypted SQLite database
- Automatic token refresh before expiration
- No passwords ever requested or stored

## Security Rules

- ✅ Official OAuth only
- ✅ Tokens encrypted at rest
- ✅ `.env` in `.gitignore`
- ✅ No credentials in logs
- ✅ HTTPS for all API calls
- ✅ Input validation on all user data
- ✅ File size/type validation before upload

## Development Status

This project is in **early initialization phase**. The repository contains:
- Project structure and documentation
- No functional code yet

See [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for detailed progress tracking.

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, components, data flow |
| [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | Current progress, completed/planned/blocked work |
| [API_INTEGRATIONS.md](docs/API_INTEGRATIONS.md) | Platform API details, OAuth flows, publishing workflows |
| [AUTHENTICATION.md](docs/AUTHENTICATION.md) | OAuth architecture, token management, security |
| [DATABASE.md](docs/DATABASE.md) | Schema design, models, relationships (PLANNED) |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Developer setup, commands, debugging |
| [TESTING.md](docs/TESTING.md) | Test strategy, frameworks, test types |
| [SECURITY.md](docs/SECURITY.md) | Security practices, threat model |
| [DECISIONS.md](docs/DECISIONS.md) | Architecture Decision Records (ADRs) |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [CLAUDE_HANDOFF.md](docs/CLAUDE_HANDOFF.md) | Agent handoff context for AI assistants |

## License

MIT License — see [LICENSE](LICENSE) for details.