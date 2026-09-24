# CLAUDE_HANDOFF.md

> **CRITICAL:** This file is the source of truth when switching from OpenCode to Claude Code. It must contain everything needed to understand the project without asking the user.

---

## Current Objective

**Project Initialization Complete** — Establish clean, documented, agent-friendly foundation for Social Publisher (SOC_BOT). No functional code implemented yet. Ready for Phase 1: Database Layer & Models.

---

## Current Project State

- **Phase:** 0 (Initialization & Documentation) — **COMPLETE**
- **Repository:** https://github.com/ayushrijal83-ops/Soc_bot
- **Branch:** main
- **Last Commit:** 90312a5 (Initial commit with docs)
- **Working Tree:** Clean
- **Git Status:** Synchronized with remote

---

## What Has Been Implemented ✅

### Repository Structure
```
Soc_bot/
├── main.py                 # NOT YET CREATED
├── requirements.txt        # Created (minimal)
├── requirements-dev.txt    # Created
├── .env.example            # Created
├── .gitignore              # Created
├── README.md               # Comprehensive
├── LICENSE                 # MIT
├── data/                   # Empty (publisher.db will go here)
├── logs/                   # Empty
├── videos/                 # Empty
├── src/
│   ├── cli/                # Empty
│   ├── core/               # Empty
│   ├── accounts/           # Empty
│   ├── storage/            # Empty
│   └── platforms/
│       ├── instagram/      # Empty
│       ├── tiktok/         # Empty
│       └── youtube/        # Empty
├── tests/
│   ├── unit/               # Empty
│   ├── integration/        # Empty
│   ├── platform/           # Empty
│   ├── e2e/                # Empty
│   └── fixtures/           # Empty
└── docs/                   # ALL DOCS CREATED
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
    └── CLAUDE_HANDOFF.md   # THIS FILE
```

### Documentation (100% Complete)
All 11 required documentation files created and accurate:
- README.md — Main entry point with accurate status
- ARCHITECTURE.md — System design with ASCII diagrams
- PROJECT_STATUS.md — Progress tracker (this is the source of truth)
- API_INTEGRATIONS.md — Platform APIs (all marked UNVERIFIED)
- AUTHENTICATION.md — OAuth design, token handling
- DATABASE.md — Schema design (marked PLANNED)
- DEVELOPMENT.md — Dev setup, commands
- TESTING.md — Test strategy (all PLANNED)
- SECURITY.md — Security practices
- DECISIONS.md — 10 ADRs documented
- TROUBLESHOOTING.md — Common issues structure
- CLAUDE_HANDOFF.md — This file

### Configuration Files
- `.gitignore` — Protects .env, DB, logs, videos, tokens
- `.env.example` — Template with all required variables
- `requirements.txt` — Production deps (minimal)
- `requirements-dev.txt` — Dev deps (pytest, ruff, mypy, etc.)

---

## What Has NOT Been Implemented ❌

### Code (0% Complete)
- **No `main.py` entry point**
- **No database layer** (`src/storage/database.py`)
- **No token encryption** (`src/storage/tokens.py`)
- **No models** (Account, Video, Post, PublishJob)
- **No CLI** (`src/cli/menu.py`, `prompts.py`, `display.py`)
- **No Account Manager** (`src/accounts/manager.py`)
- **No Job Manager** (`src/core/jobs.py`)
- **No Publisher Engine** (`src/core/publisher.py`)
- **No Validation** (`src/core/validation.py`)
- **No Platform Adapters** (all three empty)
- **No Tests** (all directories empty)

### Infrastructure
- No SQLite database (`data/publisher.db`)
- No encryption key generated
- No OAuth credentials configured
- No platform developer accounts set up

---

## Recent Changes

| Date | Change |
|------|--------|
| 2024-01-XX | Repository initialized |
| 2024-01-XX | Project structure created |
| 2024-01-XX | All 11 documentation files created |
| 2024-01-XX | Configuration files created |
| 2024-01-XX | Git commit 90312a5 pushed to origin |

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
| `main.py` | Entry point | **NOT CREATED** |
| `src/storage/database.py` | DB layer, migrations | **NOT CREATED** |
| `src/storage/tokens.py` | Token encryption | **NOT CREATED** |
| `src/accounts/manager.py` | Account/OAuth management | **NOT CREATED** |
| `src/core/jobs.py` | Job lifecycle, retry | **NOT CREATED** |
| `src/core/publisher.py` | Orchestration engine | **NOT CREATED** |
| `src/core/validation.py` | Media validation | **NOT CREATED** |
| `src/platforms/*/auth.py` | Platform OAuth flows | **NOT CREATED** |
| `src/platforms/*/publisher.py` | Platform publishing | **NOT CREATED** |
| `data/publisher.db` | SQLite database | **NOT CREATED** |
| `.env` | Runtime config | **NOT CREATED** (from .env.example) |

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

**No database exists yet.** Schema defined in DATABASE.md (PLANNED):
- `accounts` — Connected accounts with encrypted tokens
- `videos` — Video file metadata
- `posts` — Video + caption combinations
- `publish_jobs` — Per-destination publishing jobs
- `publish_attempts` — Optional attempt history

Migration system planned: versioned SQL files in `src/storage/migrations/`

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

**Zero tests exist.** TESTING.md defines strategy:
- Unit: validation, models, encryption, state machine, CLI parsing
- Integration: database, account manager, publisher engine
- Platform: mocked API tests per adapter
- E2E: full publish flow (manual/CI)

First tests will accompany database layer implementation.

---

## Known Issues

| Issue | Impact | Workaround |
|-------|--------|------------|
| No code implemented | Cannot run anything | Start Phase 1 |
| API specs unverified | May implement wrong | Verify before coding |
| No encryption key | Cannot store tokens | Generate when needed |
| No platform credentials | Cannot test OAuth | Set up developer accounts |

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

**None.** Ready to begin implementation.

---

## Next Recommended Task

### Phase 1: Database Layer & Models

**Priority:** HIGH

**Files to Create:**
1. `src/storage/tokens.py` — Fernet encryption/decryption
2. `src/storage/database.py` — SQLite connection, migrations, base model
3. `src/storage/migrations/001_initial_schema.sql` — All tables from DATABASE.md
4. `src/storage/__init__.py` — Exports
5. `src/models/` or inline in database.py — Account, Video, Post, PublishJob, PublishAttempt models

**Verification:**
- `python -m src.storage.database init` creates DB with tables
- `python -m src.storage.database migrate` runs migrations
- Token encrypt/decrypt roundtrip works
- CRUD operations for all models work
- Unit tests pass

**Estimated Effort:** 2-4 hours

---

## How To Continue

1. **Read PROJECT_STATUS.md** — Current progress tracker
2. **Read ARCHITECTURE.md** — Understand component boundaries
3. **Read API_INTEGRATIONS.md** — Note UNVERIFIED items
4. **Create `.env`** from `.env.example` with generated ENCRYPTION_KEY
5. **Implement database layer** (Phase 1 above)
6. **Run tests** after each component
7. **Update PROJECT_STATUS.md** and **this file** after each meaningful change

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
- [ ] Tests pass (`pytest`)
- [ ] Lint passes (`ruff check src`)
- [ ] Type check passes (`mypy src`)
- [ ] PROJECT_STATUS.md updated
- [ ] CLAUDE_HANDOFF.md updated
- [ ] Relevant docs updated
- [ ] No regression in existing functionality