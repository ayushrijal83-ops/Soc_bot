# CLAUDE_HANDOFF.md

> **CRITICAL:** This file is the source of truth when switching from OpenCode to Claude Code. It must contain everything needed to understand the project without asking the user.

---

## Current Objective

**Phase 2 Complete** — CLI Framework implemented with menu navigation, input validation, and comprehensive test coverage. Ready for Phase 3: Account Management.

---

## Current Project State

- **Phase:** 2 (CLI Framework) — **COMPLETE**
- **Repository:** https://github.com/ayushrijal83-ops/Soc_bot
- **Branch:** main
- **Last Commit:** 90312a5 (Initial commit with docs)
- **Working Tree:** Clean (new files only)
- **Git Status:** Synchronized with remote

---

## What Has Been Implemented ✅

### Repository Structure
```
Soc_bot/
├── main.py                 # IMPLEMENTED - Entry point with arg parsing
├── requirements.txt        # Created
├── requirements-dev.txt    # Created
├── .env.example            # Created
├── .env                    # Created (from .env.example, with ENCRYPTION_KEY)
├── .gitignore              # Created
├── README.md               # Comprehensive
├── LICENSE                 # MIT
├── data/
│   └── publisher.db        # SQLite database (CREATED)
├── logs/                   # Empty
├── videos/                 # Empty
├── src/
│   ├── __init__.py
│   ├── cli/                # IMPLEMENTED
│   │   ├── __init__.py
│   │   ├── display.py      # Formatting, tables, status messages
│   │   ├── menu.py         # Menu navigation & routing
│   │   └── prompts.py      # Interactive prompts & validation
│   ├── core/               # Empty
│   │   └── __init__.py
│   ├── accounts/           # Empty
│   │   └── __init__.py
│   ├── storage/            # IMPLEMENTED
│   │   ├── __init__.py
│   │   ├── tokens.py       # Fernet encryption
│   │   ├── database.py     # SQLAlchemy ORM, migrations, models
│   │   └── migrations/
│   │       └── 001_initial_schema.sql
│   └── platforms/
│       ├── __init__.py
│       ├── instagram/      # Empty
│       │   └── __init__.py
│       ├── tiktok/         # Empty
│       │   └── __init__.py
│       └── youtube/        # Empty
│           └── __init__.py
├── tests/
│   ├── __init__.py
│   ├── unit/               # 100 TESTS PASSING
│   │   ├── __init__.py
│   │   ├── test_tokens.py
│   │   ├── test_database.py
│   │   ├── test_cli_display.py
│   │   ├── test_cli_prompts.py
│   │   ├── test_cli_menu.py
│   │   └── test_main.py
│   ├── integration/        # Empty
│   │   └── __init__.py
│   ├── platform/           # Empty
│   │   └── __init__.py
│   ├── e2e/                # Empty
│   │   └── __init__.py
│   └── fixtures/           # Empty
│       └── __init__.py
└── docs/                   # ALL DOCS UPDATED
    ├── ARCHITECTURE.md
    ├── PROJECT_STATUS.md
    ├── API_INTEGRATIONS.md
    ├── AUTHENTICATION.md
    ├── DATABASE.md         # UPDATED - IMPLEMENTED
    ├── DEVELOPMENT.md
    ├── TESTING.md
    ├── SECURITY.md
    ├── DECISIONS.md
    ├── TROUBLESHOOTING.md
    └── CLAUDE_HANDOFF.md   # THIS FILE
```

### Documentation (Updated)
All 11 required documentation files updated to reflect Phase 2 completion:
- README.md — Main entry point with accurate status
- ARCHITECTURE.md — System design with ASCII diagrams
- PROJECT_STATUS.md — Progress tracker (Phase 2 ✅)
- API_INTEGRATIONS.md — Platform APIs (all marked UNVERIFIED)
- AUTHENTICATION.md — OAuth design, token handling
- DATABASE.md — Schema design (**IMPLEMENTED**)
- DEVELOPMENT.md — Dev setup, commands
- TESTING.md — Test strategy
- SECURITY.md — Security practices
- DECISIONS.md — 10 ADRs documented
- TROUBLESHOOTING.md — Common issues structure
- CLAUDE_HANDOFF.md — This file

### Configuration Files
- `.gitignore` — Protects .env, DB, logs, videos, tokens
- `.env.example` — Template with all required variables
- `.env` — Configured with ENCRYPTION_KEY (NOT COMMITTED)
- `requirements.txt` — Production deps
- `requirements-dev.txt` — Dev deps (pytest, ruff, mypy, etc.)

---

## Phase 1 Implementation Summary

### Files Created
1. `src/storage/tokens.py` — Fernet encryption/decryption utilities
2. `src/storage/database.py` — SQLite + SQLAlchemy ORM, migration runner, all models
3. `src/storage/migrations/001_initial_schema.sql` — Initial schema migration
4. `src/storage/__init__.py` — Package exports
5. `tests/unit/test_tokens.py` — 14 token encryption tests
6. `tests/unit/test_database.py` — 33 database layer tests

### Database Models Implemented
- **Account** — Connected social accounts with encrypted tokens
- **Video** — Video file metadata with checksum deduplication
- **Post** — Video + caption combinations
- **PublishJob** — Per-destination publishing jobs (independent)
- **PublishAttempt** — Detailed attempt history per job
- **SchemaVersion** — Migration version tracking

### Security Implementation
- **Token Encryption**: Fernet (AES-128-GCM) via `cryptography.fernet`
- **Key Management**: `ENCRYPTION_KEY` from environment (32-byte URL-safe base64)
- **Storage**: Encrypted tokens as base64-encoded TEXT
- **No Hardcoded Keys**: Key generated at setup, never committed
- **CHECK Constraints**: Enforced at database level (platform, status values)
- **Foreign Keys**: Enforced with CASCADE DELETE (PRAGMA foreign_keys=ON)

### Migration System
- Versioned SQL files in `src/storage/migrations/`
- `schema_version` table tracks applied migrations
- `python -m src.storage.database init` — Creates tables + runs migrations
- `python -m src.storage.database migrate` — Runs pending migrations only
- Safe to run repeatedly, preserves existing data

### Test Results (Phase 1)
```
47 passed in ~2s
- test_tokens.py: 14 tests (encryption, decryption, edge cases)
- test_database.py: 33 tests (init, models, constraints, relationships, indexes, encryption integration)
```

---

## Phase 2 Implementation Summary

### Files Created
1. `src/cli/display.py` — Formatting, tables, headers, status messages
2. `src/cli/prompts.py` — Input validation (text, int, choice, yes/no, menu selection)
3. `src/cli/menu.py` — MenuHandler class with navigation, routing, 6-option menu
4. `main.py` — Entry point with argparse (--help, --dry-run, --version)
5. `tests/unit/test_cli_display.py` — 11 display utility tests
6. `tests/unit/test_cli_prompts.py` — 24 prompt/input validation tests
7. `tests/unit/test_cli_menu.py` — 14 menu navigation tests
8. `tests/unit/test_main.py` — 4 entry point tests

### CLI Architecture
- **display.py**: Pure formatting functions (no state), easy to test/replace
- **prompts.py**: Reusable input validation (text, int, choice, yes/no, menu)
- **menu.py**: MenuHandler class — loop, display, route, handle choices 1-6
- **main.py**: argparse entry point — --help, --dry-run, --version, KeyboardInterrupt handling

### Menu Behavior
```
╔════════════════════════════════════════╗
║         SOCIAL PUBLISHER v1            ║
╠════════════════════════════════════════╣
║  1. Create Post                        ║
║  2. Connected Accounts                 ║
║  3. Publishing Queue                   ║
║  4. History                            ║
║  5. Settings                           ║
║  6. Exit                               ║
╚════════════════════════════════════════╝
```
- Options 1-5: Show "not implemented yet" message, pause, return to menu
- Option 6: Clean exit with "Goodbye!" header
- Invalid input: Re-prompts with error message
- Ctrl+C: Clean exit with "Interrupted. Goodbye!"

### Input Validation
- Menu selection: Validates 1-6 (or 1-7 with exit), rejects non-numeric, out of range
- Text input: Required/optional, default values, empty string handling
- Integer input: Min/max bounds, default values, non-numeric rejection
- Yes/No: y/n, yes/no, case-insensitive, default handling
- EOF/Ctrl+C: Returns None, handled gracefully

### Test Results (Phase 1 + 2)
```
100 passed in ~2s
- test_tokens.py: 14 tests (encryption, decryption, edge cases)
- test_database.py: 33 tests (init, models, constraints, relationships, indexes, encryption integration)
- test_cli_display.py: 11 tests (headers, menus, tables, status messages)
- test_cli_prompts.py: 24 tests (text, int, choice, yes/no, menu selection, EOF, Ctrl+C)
- test_cli_menu.py: 14 tests (init, routing, handlers, run loop, edge cases)
- test_main.py: 4 tests (args parsing, dry-run, KeyboardInterrupt, exceptions)
```

---

## What Has NOT Been Implemented ❌

### Code (Phase 3+)
- **No Account Manager** (`src/accounts/manager.py`)
- **No OAuth callback server**
- **No Instagram/TikTok/YouTube OAuth flows**
- **No Job Manager** (`src/core/jobs.py`)
- **No Publisher Engine** (`src/core/publisher.py`)
- **No Validation** (`src/core/validation.py`)
- **No Platform Adapters** (all three empty)
- **No Integration/E2E Tests**

### Infrastructure
- No OAuth credentials configured
- No platform developer accounts set up
- No logging setup
- Publishing features are placeholders only

---

## Recent Changes

| Date | Change |
|------|--------|
| 2024-01-XX | Repository initialized |
| 2024-01-XX | Project structure created |
| 2024-01-XX | All 11 documentation files created |
| 2024-01-XX | Configuration files created |
| 2024-01-XX | Git commit 90312a5 pushed to origin |
| 2026-09-25 | **Phase 1: Database Foundation implemented** |
| 2026-09-25 | **Phase 2: CLI Framework implemented** |

---

## Architecture Summary

```
main.py → Terminal CLI → Account Manager + Job Manager → Publisher Engine → Platform Adapters → Official APIs
                                          ↓                    ↓
                                    Storage (SQLite)    Validation
```

**Key Decisions (from DECISIONS.md):**
1. Terminal-based V1 (ADR-001)
2. Isolated platform adapters (ADR-002)
3. Official APIs only (ADR-003)
4. Independent publishing jobs (ADR-004)
5. SQLite for V1 (ADR-005)
6. Fernet token encryption (ADR-006)
7. Dry-run required (ADR-007)
8. Local OAuth callback server (ADR-008)
9. No AI/generation features (ADR-009)
10. Structured logging with sanitization (ADR-010)

---

## Important Files

| File | Purpose | Status |
|------|---------|--------|
| `main.py` | Entry point | ✅ IMPLEMENTED |
| `src/cli/menu.py` | Menu navigation & routing | ✅ IMPLEMENTED |
| `src/cli/prompts.py` | Interactive prompts & validation | ✅ IMPLEMENTED |
| `src/cli/display.py` | Formatting, tables, status messages | ✅ IMPLEMENTED |
| `src/storage/database.py` | DB layer, migrations, models | ✅ IMPLEMENTED |
| `src/storage/tokens.py` | Token encryption | ✅ IMPLEMENTED |
| `src/storage/migrations/001_initial_schema.sql` | Initial migration | ✅ IMPLEMENTED |
| `src/accounts/manager.py` | Account/OAuth management | 📋 PLANNED |
| `src/core/jobs.py` | Job lifecycle, retry | 📋 PLANNED |
| `src/core/publisher.py` | Orchestration engine | 📋 PLANNED |
| `src/core/validation.py` | Media validation | 📋 PLANNED |
| `src/platforms/*/auth.py` | Platform OAuth flows | 📋 PLANNED |
| `src/platforms/*/publisher.py` | Platform publishing | 📋 PLANNED |
| `data/publisher.db` | SQLite database | ✅ CREATED |
| `.env` | Runtime config | 🔧 CONFIGURED |

---

## Important Decisions

1. **No passwords ever** — OAuth only, documented in SECURITY.md, AUTHENTICATION.md
2. **Platform adapters isolated** — Core engine knows only Protocol interface
3. **Independent jobs** — One failure ≠ all fail
4. **Dry-run mandatory** — `--dry-run` shows plan without API calls
5. **SQLite + Fernet** — Simple, secure, portable
6. **UNVERIFIED APIs** — All platform specs marked UNVERIFIED in API_INTEGRATIONS.md — must verify before coding

---

## Database State

**Database exists and initialized.** Schema in DATABASE.md (IMPLEMENTED):
- `accounts` — Connected accounts with encrypted tokens
- `videos` — Video file metadata with checksum deduplication
- `posts` — Video + caption combinations
- `publish_jobs` — Per-destination publishing jobs (independent)
- `publish_attempts` — Attempt history per job
- `schema_version` — Migration tracking

Migration system: versioned SQL files in `src/storage/migrations/`

---

## Authentication State

**No OAuth implemented.** Design in AUTHENTICATION.md:
- PKCE + state for all platforms
- Local callback server on `http://localhost:8080/callback/{platform}`
- Token refresh: proactive (24h) + on-demand (401)
- Encrypted storage via Fernet
- Revocation handling → mark account `revoked`, require reconnect

---

## API Integration State

**All UNVERIFIED.** API_INTEGRATIONS.md documents:

| Platform | API | Status |
|----------|-----|--------|
| Instagram | Graph API v21.0 | 📋 PLANNED, UNVERIFIED |
| TikTok | Creator API v2 | 📋 PLANNED, UNVERIFIED |
| YouTube | Data API v3 | 📋 PLANNED, UNVERIFIED |

**Before implementing any adapter:** Verify every spec against official docs, mark VERIFIED in API_INTEGRATIONS.md.

---

## Tests

**100 unit tests passing.** TESTING.md defines strategy:
- Unit: validation, models, encryption, state machine, CLI parsing
- Integration: database, account manager, publisher engine
- Platform: mocked API tests per adapter
- E2E: full publish flow (manual/CI)

Run: `pytest tests/unit/ -v`

---

## Known Issues

| Issue | Impact | Workaround |
|-------|--------|------------|
| API specs unverified | May implement wrong | Verify before coding |
| No platform credentials | Cannot test OAuth | Set up developer accounts |
| No logging setup | No observability | Add in Phase 3 |
| Publishing features placeholders | Menu shows "not implemented" | Implement in Phase 3-6 |

---

## Known Limitations

- Single-user CLI tool (no multi-user)
- No web UI (terminal only)
- No scheduling (publish now only)
- No analytics/insights
- No bulk operations beyond multi-account select
- Platform API limits apply (quotas, rate limits)

---

## Current Blockers

**None.** Ready to begin Phase 3.

---

## Next Recommended Task

### Phase 3: Account Management

**Priority:** HIGH

**Files to Create:**
1. `src/accounts/manager.py` — Account CRUD, listing, status management
2. OAuth callback server (local HTTP on port 8080)
3. `src/platforms/instagram/auth.py` — Instagram/Meta OAuth flow
4. `src/platforms/tiktok/auth.py` — TikTok OAuth flow
5. `src/platforms/youtube/auth.py` — YouTube/Google OAuth flow

**Verification:**
- `python main.py` → "Connected Accounts" → shows empty list
- OAuth flow initiates browser, handles callback, stores encrypted tokens
- Account appears in list with status "active"
- Token refresh works (simulated)
- Account disconnect removes tokens

**Estimated Effort:** 5-8 hours

---

## How To Continue

1. **Read PROJECT_STATUS.md** — Current progress tracker
2. **Read ARCHITECTURE.md** — Understand component boundaries
3. **Read API_INTEGRATIONS.md** — Note UNVERIFIED items
4. **Verify `.env`** has ENCRYPTION_KEY set
5. **Run tests** to confirm baseline: `pytest tests/unit/ -v`
6. **Implement Account Management** (Phase 3 above)
7. **Run tests** after each component
8. **Update PROJECT_STATUS.md** and **this file** after each meaningful change

---

## Things NOT To Break

1. **`.gitignore`** — Must always protect `.env`, `data/*.db`, `logs/`, `videos/`, tokens
2. **Documentation accuracy** — Never mark PLANNED as IMPLEMENTED
3. **API_INTEGRATIONS.md UNVERIFIED tags** — Don't remove without verification
4. **Security principles** — No passwords, no secrets in logs, no .env commits
5. **Architecture boundaries** — Platform code stays in adapters, core stays platform-agnostic
6. **Independent jobs** — Don't couple job execution
7. **Dry-run** — Must remain functional throughout development

---

## Verification Checklist (Run After Each Task)

- [ ] `git status` clean (only intended changes)
- [ ] No secrets in diff (`git diff` check)
- [ ] Tests pass (`pytest tests/unit/`)
- [ ] Lint passes (`ruff check src`)
- [ ] Type check passes (`mypy src`)
- [ ] PROJECT_STATUS.md updated
- [ ] CLAUDE_HANDOFF.md updated
- [ ] Relevant docs updated
- [ ] No regression in existing functionality