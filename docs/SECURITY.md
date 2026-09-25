# Security

## Core Principles

1. **Never store social media passwords** — OAuth only
2. **Encrypt tokens at rest** — Fernet (AES-128-GCM) ✅ **IMPLEMENTED**
3. **Protect .env** — Never committed, in .gitignore
4. **No secrets in logs** — Sanitize all output
5. **HTTPS everywhere** — All API calls over TLS
6. **Validate all input** — File paths, user input, API responses
7. **Least privilege** — Request minimal OAuth scopes

## Threat Model

| Threat | Mitigation | Status |
|--------|------------|--------|
| Token theft from DB | Encryption at rest, file permissions | ✅ IMPLEMENTED |
| Token exposure in logs | Structured logging with sanitization | 📋 PLANNED |
| .env committed to git | .gitignore, pre-commit hooks | ✅ IMPLEMENTED |
| MITM on API calls | HTTPS enforcement, cert validation | 📋 PLANNED |
| CSRF in OAuth | PKCE + state parameter | 📋 PLANNED |
| Malicious video upload | Validation, size limits, type checking | 📋 PLANNED |
| Token replay | Short-lived access tokens, refresh rotation | 📋 PLANNED |

## OAuth Security

- **PKCE mandatory** — Prevents authorization code interception
- **State parameter** — Prevents CSRF on callback
- **Redirect URI exact match** — Preconfigured in platform dashboards
- **Minimal scopes** — Only request needed permissions
- **Token refresh** — Proactive, before expiry
- **Revocation handling** — Detect and disable compromised accounts

## Token Protection (IMPLEMENTED)

```python
# Encryption key from environment (32-byte URL-safe base64)
ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"]

# Fernet encryption
fernet = Fernet(ENCRYPTION_KEY)
encrypted = fernet.encrypt(token.encode())
decrypted = fernet.decrypt(encrypted).decode()
```

Implementation: `src/storage/tokens.py`
- `TokenEncryption` class with `encrypt()`, `decrypt()`, `encrypt_optional()`, `decrypt_optional()`
- `generate_key()` utility for key generation
- Key loaded from `ENCRYPTION_KEY` environment variable
- Never hardcoded, never in repo
- Rotate key → re-encrypt all tokens (future feature)

## .env Protection

```
# .gitignore entries
.env
.env.*
*.env
data/*.db
logs/
videos/
```

- `.env.example` committed (template only)
- `.env` never committed
- Pre-commit hook to detect accidental commits (future)

## Secret Management

| Secret | Storage | Rotation |
|--------|---------|----------|
| Platform App ID/Secret | .env | Platform dashboard |
| Encryption Key | .env | Manual (re-encrypt DB) |
| Access/Refresh Tokens | Encrypted DB (Fernet) | Auto via OAuth |
| Database | File (SQLite) | N/A |

## OAuth Security (IMPLEMENTED)

### CSRF Protection
- **State parameter** — Cryptographically random, single-use, expires
- **State validation** — Required on every callback, mismatch = rejection
- **State expiration** — 10 minutes default, cleaned up after use

### PKCE (Proof Key for Code Exchange)
- **Code verifier** — 32-128 char cryptographically random string
- **Code challenge** — S256 (SHA-256) of verifier
- **Verifier storage** — In-memory with authorization state, cleaned up after callback
- **Platform support:** Required for Instagram, TikTok, YouTube (all verified)

### Redirect URI Protection
- **Exact match required** — Must match platform dashboard exactly
- **Localhost only** — Binds to 127.0.0.1, not 0.0.0.0
- **Path validation** — Only accepts configured callback paths

### Token Handling
```python
# Encryption
fernet = Fernet(key)
encrypted = fernet.encrypt(token.encode())

# Decryption
decrypted = fernet.decrypt(encrypted).decode()
```

Implementation: `src/storage/tokens.py`
- `TokenEncryption` class with `encrypt()`, `decrypt()`, `encrypt_optional()`, `decrypt_optional()`
- `generate_key()` utility for key generation
- Key loaded from `ENCRYPTION_KEY` environment variable
- Never hardcoded, never in repo
- Rotate key → re-encrypt all tokens (future feature)

### Authorization State Management
- **In-memory storage** — Single-use, expires after 10 minutes
- **Auto-cleanup** — Removed after callback, timeout, or cancellation
- **Never persisted** to database or disk
- **Platform-specific** — Separate state per authorization attempt

## .env Protection

```
# .gitignore entries
.env
.env.*
*.env
data/*.db
logs/
videos/
```

- `.env.example` committed (template only)
- `.env` never committed
- Pre-commit hook to detect accidental commits (future)

## Secret Management

| Secret | Storage | Rotation |
|--------|---------|----------|
| Platform App ID/Secret | .env | Platform dashboard |
| Encryption Key | .env | Manual (re-encrypt DB) |
| Access/Refresh Tokens | Encrypted DB (Fernet) | Auto via OAuth |
| Database | File (SQLite) | N/A |

## Upload Validation

Before any API call:
- File exists and readable
- File size ≤ platform max (configurable)
- MIME type in allowed list
- Extension matches MIME
- Duration within limits
- Resolution/aspect ratio checked (ffprobe)
- No path traversal (`../`)

## Safe Filenames

- Sanitize user-provided filenames
- Use checksum-based storage names
- Reject null bytes, control characters
- Limit length (255 chars)

## Size Limits

| Platform | Max Size (Config) | Enforced |
|----------|-------------------|----------|
| Instagram | 4 GB | ✅ Pre-upload |
| TikTok | 4 GB | ✅ Pre-upload |
| YouTube | 256 GB | ✅ Pre-upload |

Default conservative limit: **2 GB** (configurable via env)

## Secure Logging

```python
# Sanitized logging
logger.info("Upload started", extra={
    "job_id": job.id,
    "account": account.username,  # Safe: public handle
    "platform": account.platform,
    # NEVER log: tokens, file paths, captions, error details with secrets
})
```

- Structured JSON logs
- No tokens, secrets, PII in logs
- Error logs: generic messages, correlation IDs only
- Debug logs: only in development (LOG_LEVEL=DEBUG)

## HTTPS Requirements

- All platform API calls: HTTPS only
- Certificate validation: Enabled (default)
- Redirect URI: HTTPS in production, localhost HTTP for dev
- No self-signed certs accepted

## API Credential Protection

- Client ID/Secret: .env only
- Never in source code
- Never in documentation
- Never in error messages
- Rotate periodically via platform dashboards

## File System Permissions

```bash
# Recommended (Linux/macOS)
chmod 700 data/
chmod 600 data/publisher.db
chmod 600 .env
```

Windows: Rely on user profile isolation.

## Incident Response

If token compromise suspected:
1. Revoke via platform dashboard
2. Mark account `revoked` in DB
3. Generate new encryption key
4. Re-encrypt all tokens
5. User re-authenticates affected accounts

## Compliance Notes

- No GDPR personal data stored (only public handles)
- No CCPA regulated data
- Platform API terms of service apply
- User responsible for content compliance