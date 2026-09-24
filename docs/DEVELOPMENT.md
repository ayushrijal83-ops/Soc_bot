# Development Setup

## Supported Python Version
- **Python 3.11+** (tested on 3.11, 3.12)

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
```

Generate encryption key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Database Setup

```bash
# Run migrations (when implemented)
python -m src.storage.database migrate

# Or initialize fresh
python -m src.storage.database init
```

## Running the Application

```bash
# Normal mode
python main.py

# Dry-run mode
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

# Unit tests only
pytest tests/unit

# Integration tests only
pytest tests/integration

# With coverage
pytest --cov=src --cov-report=term-missing

# Specific test file
pytest tests/unit/test_validation.py -v
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
├── main.py                 # Entry point
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
│   ├── cli/
│   ├── core/
│   ├── accounts/
│   ├── storage/
│   └── platforms/
│       ├── instagram/
│       ├── tiktok/
│       └── youtube/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
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