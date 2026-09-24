# Security

## Core Principles

1. **Never store social media passwords** — OAuth only
2. **Encrypt tokens at rest** — Fernet (AES-128-GCM)
3. **Protect .env** — Never committed, in .gitignore
4. **No secrets in logs** — Sanitize all output
5. **HTTPS everywhere** — All API calls over TLS
6. **Validate all input** — File paths, user input, API responses
7. **Least privilege** — Request minimal OAuth scopes

## Threat Model

| Threat | Mitigation |
|--------|------------|
| Token theft from DB | Encryption at rest, file permissions |
| Token exposure in logs | Structured logging with sanitization |
| .env committed to git | .gitignore, pre-commit hooks |
| MITM on API calls | HTTPS enforcement, cert validation |
| CSRF in OAuth | PKCE + state parameter |
| Malicious video upload | Validation, size limits, type checking |
| Token replay | Short-lived access tokens, refresh rotation |

## OAuth Security

- **PKCE mandatory** — Prevents authorization code interception
- **State parameter** — Prevents CSRF on callback
- **Redirect URI exact match** — Preconfigured in platform dashboards
- **Minimal scopes** — Only request needed permissions
- **Token refresh** — Proactive, before expiry
- **Revocation handling** — Detect and disable compromised accounts

## Token Protection

```python
# Encryption key from environment (32-byte base64)
ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"]

# Fernet encryption
fernet = Fernet(ENCRYPTION_KEY)
encrypted = fernet.encrypt(token.encode())
decrypted = fernet.decrypt(encrypted).decode()
```

- Key generated once per deployment
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
- Pre-commit hook to detect accidental commits

## Secret Management

| Secret | Storage | Rotation |
|--------|---------|----------|
| Platform App ID/Secret | .env | Platform dashboard |
| Encryption Key | .env | Manual (re-encrypt DB) |
| Access/Refresh Tokens | Encrypted DB | Auto via OAuth |
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