# Testing Strategy

## Test Framework
- **pytest** — Primary test runner
- **pytest-asyncio** — Async test support
- **pytest-mock** — Mocking utilities
- **respx** — HTTP mocking for API tests
- **faker** — Test data generation

## Test Organization

```
tests/
├── unit/                    # Fast, isolated unit tests
│   ├── test_validation.py
│   ├── test_models.py
│   ├── test_token_encryption.py
│   ├── test_job_state_machine.py
│   └── test_cli_parsing.py
├── integration/             # Slower, cross-component tests
│   ├── test_database.py
│   ├── test_account_manager.py
│   ├── test_publisher_engine.py
│   └── test_oauth_flows.py
├── platform/                # Platform-specific tests (mocked APIs)
│   ├── test_instagram_adapter.py
│   ├── test_tiktok_adapter.py
│   └── test_youtube_adapter.py
├── e2e/                     # End-to-end (manual/ci only)
│   └── test_publish_flow.py
└── fixtures/                # Shared test data
    ├── sample_videos/
    ├── mock_responses/
    └── test_accounts.json
```

## Unit Tests (Implemented: 0)

| Test Module | Coverage Target |
|-------------|-----------------|
| `test_validation.py` | Video validation logic |
| `test_models.py` | Database models, serialization |
| `test_token_encryption.py` | Encrypt/decrypt roundtrip |
| `test_job_state_machine.py` | Status transitions |
| `test_cli_parsing.py` | Argument parsing, menu logic |

## Integration Tests (Implemented: 0)

| Test Module | Coverage Target |
|-------------|-----------------|
| `test_database.py` | CRUD, migrations, constraints |
| `test_account_manager.py` | OAuth flow, token refresh, disconnect |
| `test_publisher_engine.py` | Job creation, execution, dry-run |

## Platform Adapter Tests (Implemented: 0)

Each platform adapter tested against **mocked official API responses**:
- Media validation
- Upload initiation
- Status polling
- Publishing
- Token refresh
- Error handling (rate limit, auth expired, invalid media)

## OAuth Tests (Implemented: 0)

- PKCE code generation/validation
- Authorization URL construction
- State parameter CSRF protection
- Callback server handling
- Token exchange
- Token refresh
- Revoked token detection

## Publishing Tests (Implemented: 0)

- Single destination publish
- Multi-destination publish (independent jobs)
- Partial failure handling
- Retry logic
- Dry-run accuracy

## Failure Tests (Implemented: 0)

- Network timeout
- Platform API errors (4xx, 5xx)
- Invalid media format/size
- Expired tokens
- Revoked permissions
- Rate limiting (429)
- Quota exceeded

## Retry Tests (Implemented: 0)

- Retry on transient failure
- No retry on permanent failure
- Exponential backoff timing
- Max retry limit
- Retry state persistence

## Dry-run Tests (Implemented: 0)

- Plan matches actual execution
- No API calls made
- All destinations listed
- Validation still runs

## Account Selection Tests (Implemented: 0)

- Select subset of connected accounts
- Deselect all → no jobs created
- Platform filter
- Account status filter (only active)

## Running Tests

```bash
# All tests
pytest

# Unit only (fast)
pytest tests/unit -v

# Integration (requires test DB)
pytest tests/integration -v

# Platform mocks
pytest tests/platform -v

# With coverage
pytest --cov=src --cov-report=html

# Parallel (if independent)
pytest -n auto
```

## Test Data

- **No real credentials** in tests
- Mock responses from official API documentation
- Fixtures in `tests/fixtures/`
- Video fixtures: small synthetic MP4 files (< 1MB)

## CI/CD Integration (Future)

```yaml
# .github/workflows/test.yml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements-dev.txt
      - run: pytest --cov=src --cov-fail-under=80
```

## Current Status
📋 **PLANNED** — No tests implemented yet. First tests will be written alongside database layer.