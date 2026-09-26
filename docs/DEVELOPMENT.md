# Development Setup

## Supported Python Version
- **Python 3.11+** (tested on 3.11, 3.12)
- **Currently running on Python 3.10.11** (CI uses 3.11+)

## Virtual Environment

```bash
# Create
python -m venv .venv

# Activate (Linux/macOS)
source .venv/bin/activate

# Activate (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Activate (Windows CMD)
.venv\Scripts\activate.bat
```

## Dependency Installation

```bash
# Install production dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -r requirements-dev.txt
```

## Environment Variables

Copy template and configure:
```bash
cp .env.example .env
# Edit .env with your credentials
```

Required variables for development:
```env
DATABASE_URL=sqlite:///data/publisher.db
ENCRYPTION_KEY=your_generated_key
LOG_LEVEL=DEBUG
DRY_RUN=true

# Instagram (Instagram API with Instagram Login)
INSTAGRAM_APP_ID=
INSTAGRAM_APP_SECRET=
INSTAGRAM_REDIRECT_URI=        # fixed + registered, e.g. http://127.0.0.1:8765/callback/instagram

# TikTok (Login Kit for Desktop)
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REDIRECT_URI=           # blank = dynamic port

# YouTube (Google, Desktop app client)
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=          # blank = dynamic port

# Optional: callback host/port (default 127.0.0.1, port 0 = dynamic)
# OAUTH_CALLBACK_HOST=127.0.0.1
# OAUTH_CALLBACK_PORT=0
```

Generate encryption key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Optional: cloudflared (Instagram videos over 99 MB)

With `MEDIA_STORAGE_PROVIDER=auto`, Instagram videos too big for TempFile are served from this PC through a Cloudflare Quick Tunnel. No Cloudflare account, API key or domain is needed; Soc_bot never downloads the binary itself:

```powershell
winget install --id Cloudflare.cloudflared
# new terminal:
cloudflared --version
```

Optional `.env` overrides: `CLOUDFLARED_PATH` (full path if not on PATH), `CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS=90`, `CLOUDFLARE_MEDIA_TOKEN_BYTES=16`, `CLOUDFLARE_MEDIA_HOST=127.0.0.1` (must stay loopback), `INSTAGRAM_MAX_POLL_MINUTES=15`, `INSTAGRAM_MAX_CONCURRENT_PUBLISHES=5` (Instagram jobs running at once, 1..20), `INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS=5` (pause before the batch's automatic retry round, 0..300). One Quick Tunnel per batch. Quick Tunnels are a Cloudflare testing/development service with no uptime guarantee. Unit tests never find or start a real cloudflared (`tests/conftest.py` points `CLOUDFLARED_PATH` at a missing file).

## Terminal UI (Textual)

`pip install -r requirements.txt` installs `textual` (plus `rich`). Structure:
- `src/services/`: the thin application layer the UI uses (`PublishingService` plan/create_batch/publish_batch with events, `AccountService`, `LinkService`, `ContentService`, `SettingsService`). It only calls the engine; no platform, tunnel, retry or worker code lives here.
- `src/tui/`: `app.py` (modes, global keys, command palette, quit guard), `theme.py` (THE color/icon/CSS definitions), `widgets.py` (page layout, modals, file picker), `screens/` (dashboard, create_post, publishing, lists).
- Engine callbacks arrive on worker threads; the TUI runs batches in a Textual thread worker and hands events to the UI with `call_from_thread`.
- Tests: `tests/unit/test_tui.py` (headless `App.run_test` pilot + service tests). For visual checks, `app.save_screenshot()` writes an SVG; headless Edge (`msedge --headless=new --screenshot=out.png file.svg`) turns it into a PNG.
- README screenshots: `python tools/readme_screenshots.py` rebuilds `docs/images/*.svg` from demo data only (temp DB in `C:/SocBotDemo`, fake accounts and publishers, no network; the folder is deleted afterwards). Rerun it after visible UI changes and check the pictures for personal data before committing.
- Performance notes: `file_checksum` is cached per (path, size, mtime) so a big video is hashed once; Instagram status polls run every 15 s (`POLL_INTERVAL`).

## Database Setup

```bash
# Initialize database (creates tables, runs migrations)
python -m src.storage.database init

# Run pending migrations only
python -m src.storage.database migrate

# Health check
python -m src.storage.database health
```

## Running the Application

**Windows, no typing:** double-click `Start_Soc_bot.bat` in the project root (or a desktop shortcut to it). It
`cd`s to its own folder, uses `.venv\Scripts\python.exe` when present (else `python`), installs
`requirements.txt` once if `textual/httpx/sqlalchemy/dotenv/cryptography` can't be imported, warns when `.env`
is missing, runs `main.py` (extra arguments are passed through) and keeps the window open on errors.

```bash
# Full-screen terminal UI
python main.py

# Classic text menu
python main.py --plain

# Dry-run: show the plan, publish nothing
python main.py --dry-run

# Help
python main.py --help

# With debug logging
LOG_LEVEL=DEBUG python main.py
```

## Running Tests

```bash
# All tests
pytest

# Unit tests only (170 tests)
pytest tests/unit -v

# Integration tests only
pytest tests/integration -v

# With coverage
pytest --cov=src --cov-report=term-missing

# Specific test file
pytest tests/unit/test_account_manager.py -v
```

## Debugging

### VS Code Launch Configuration (`.vscode/launch.json`)
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Debug Social Publisher",
      "type": "python",
      "request": "launch",
      "module": "main",
      "args": ["--dry-run"],
      "envFile": "${workspaceFolder}/.env",
      "console": "integratedTerminal",
      "justMyCode": true
    }
  ]
}
```

### Logging
- Logs written to `logs/` directory
- Console output controlled by `LOG_LEVEL`
- Structured JSON logging for machine parsing

## Code Style

```bash
# Format
ruff format src tests

# Lint
ruff check src tests

# Type check
mypy src
```

## Project Structure

```
Soc_bot/
├── main.py                 # Entry point (IMPLEMENTED)
├── requirements.txt        # Production deps
├── requirements-dev.txt    # Dev deps
├── .env.example           # Env template
├── .gitignore
├── README.md
├── LICENSE
├── data/
│   └── publisher.db       # SQLite (gitignored)
├── logs/                  # Log files (gitignored)
├── videos/                # User video files (gitignored)
├── src/
│   ├── auth/              # IMPLEMENTED
│   │   ├── __init__.py
│   │   ├── base.py        # OAuth base classes, config, token result
│   │   ├── state.py       # OAuth state management, PKCE
│   │   ├── callback_server.py  # OAuth callback HTTP server
│   │   ├── manager.py     # AuthManager coordinating all platforms
│   │   └── errors.py      # OAuth-specific exceptions
│   ├── cli/               # IMPLEMENTED
│   │   ├── __init__.py
│   │   ├── display.py     # Formatting, tables, status
│   │   ├── menu.py        # Menu navigation & routing
│   │   ├── prompts.py     # Input validation
│   │   └── account_menu.py # Account management submenu
│   ├── core/              # PLANNED
│   │   └── __init__.py
│   ├── accounts/          # IMPLEMENTED
│   │   ├── __init__.py
│   │   └── manager.py     # Account CRUD, listing, status management
│   ├── storage/           # IMPLEMENTED
│   │   ├── __init__.py
│   │   ├── database.py    # SQLAlchemy ORM, migrations
│   │   ├── tokens.py      # Fernet encryption
│   │   └── migrations/
│   │       └── 001_initial_schema.sql
│   └── platforms/         # IMPLEMENTED (auth)
│       ├── __init__.py
│       ├── instagram/
│       │   ├── __init__.py
│       │   └── auth.py    # Instagram/Meta OAuth flow
│       ├── tiktok/
│       │   ├── __init__.py
│       │   └── auth.py    # TikTok OAuth flow
│       └── youtube/
│           ├── __init__.py
│           └── auth.py    # YouTube/Google OAuth flow
├── tests/
│   ├── unit/              # 170 TESTS PASSING
│   │   ├── __init__.py
│   │   ├── test_tokens.py
│   │   ├── test_database.py
│   │   ├── test_cli_display.py
│   │   ├── test_cli_prompts.py
│   │   ├── test_cli_menu.py
│   │   ├── test_main.py
│   │   ├── test_account_manager.py
│   │   ├── test_auth_state.py
│   │   ├── test_auth_callback.py
│   │   └── test_platform_auth.py
│   ├── integration/       # PLANNED
│   └── fixtures/          # PLANNED
└── docs/
    ├── ARCHITECTURE.md
    ├── PROJECT_STATUS.md
    ├── API_INTEGRATIONS.md
    ├── AUTHENTICATION.md
    ├── DATABASE.md
    ├── DEVELOPMENT.md
    ├── TESTING.md
    ├── SECURITY.md
    ├── DECISIONS.md
    ├── TROUBLESHOOTING.md
    └── CLAUDE_HANDOFF.md
```

## Adding Dependencies

```bash
# Add production dependency
pip install package_name
pip freeze > requirements.txt

# Add dev dependency
pip install package_name
pip freeze > requirements-dev.txt
```

## Pre-commit Hooks (Optional)

```bash
pip install pre-commit
pre-commit install
```

`.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.0
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
```