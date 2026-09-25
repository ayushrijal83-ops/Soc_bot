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
| CSRF in OAuth | State (single-use, platform-bound) + PKCE where supported | ✅ IMPLEMENTED |
| Malicious video upload | Validation, size limits, type checking | 📋 PLANNED |
| Token replay | Provider expiry honoured; rotated TikTok refresh tokens replace old ones | ✅ IMPLEMENTED |
| Auth-code interception on loopback | 127.0.0.1 only, exclusive port bind (no SO_REUSEADDR), dynamic port, state check before exchange | ✅ IMPLEMENTED |

## OAuth Security

- **PKCE** where the provider documents it (YouTube: base64url; TikTok: hex). Instagram Login does not document PKCE.
- **State parameter**: prevents CSRF on callback and is bound to the platform
- **Redirect URI**: dynamic loopback port (YouTube, TikTok wildcard) or an exact registered URI (Instagram)
- **Minimal scopes**: only the scopes needed to identify the account and (later) publish
- **Token renewal**: `AuthManager.refresh_account_tokens`; proactive scheduling is Phase 4
- **Revocation**: TikTok/YouTube revoke endpoints; Instagram has none documented (local disconnect only)

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
- **State parameter**: cryptographically random, single-use, expires after 10 minutes
- **State validation**: required on every callback; unknown, expired or reused state is rejected
- **Platform binding**: the state's platform must equal the callback path's platform (`/callback/<platform>`). A mismatch is rejected and the state consumed **before** any code exchange. `AuthManager` re-checks the platform against the flow it started.

### PKCE (Proof Key for Code Exchange)
- **Code verifier**: 64-char `secrets.token_urlsafe(48)` (RFC 7636 range 43–128)
- **Code challenge**: S256; base64url (RFC 7636) by default, hex for TikTok Login Kit for Desktop
- **Verifier storage**: in memory with the authorization state, removed when the state is consumed
- **Platform support**: YouTube and TikTok send PKCE; Instagram Login does not document it, so it is not sent

### Redirect URI / Callback Server Protection
- **Loopback only**: binds 127.0.0.1 by default, never 0.0.0.0
- **Dynamic port**: port 0; the OS assigns a free port for each flow and the redirect URI uses the actual port
- **Exclusive bind**: `SO_REUSEADDR` is disabled and `SO_EXCLUSIVEADDRUSE` is set on Windows, so another process cannot bind the same port and receive the code
- **Path validation**: only `/callback/<platform>` is handled; other paths get a 404 and do not complete the flow
- **Output escaping**: provider error text is HTML-escaped; codes are never echoed; unexpected exceptions show a generic message

### No Secrets in Logs / Errors
- Instagram token-exchange errors carry only the HTTP status and a bounded error body, never the code, secret or token. `raw_response` is not kept for Instagram token responses.
- The Instagram `/me` call sends the token in the `Authorization` header, not in the URL
- Regression tests check that access/refresh tokens, authorization codes and client secrets do not appear in logs, results or exception messages

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

## Temporary Media Storage (Instagram)

- **Private bucket:** objects are uploaded without ACLs; Meta reads them only through a presigned **GET** URL for that one object (SigV4), HTTPS only, expiring after `MEDIA_STORAGE_PRESIGNED_URL_TTL` (default 900 s, max 7 days).
- **Unguessable keys:** `instagram/temp/<192-bit random>/video.<ext>`, with no filename, local path, account or token in them.
- **Presigned URLs are treated as secrets:** never logged, never stored in the database (provider state holds only the container ID), and stripped from error text by `redact()`.
- **Credentials:** `MEDIA_STORAGE_*` live only in `.env`. Storage errors show only the error code and HTTP status (`_safe_error`), never URLs or keys. Use credentials scoped to this bucket.
- **Cleanup:** each attempt deletes its object (`finally`/`ExitStack`, which also covers Ctrl+C). Cleanup is idempotent, and a failed delete is logged but doesn't fail the job. A bucket lifecycle rule on `instagram/temp/` catches crash leftovers. The local source video is only ever read.
- **Residual risk:** while a URL is valid, anyone who obtains it can download that one video. Keep the TTL short.

### 0x0.st provider (`MEDIA_STORAGE_PROVIDER=0x0`): public, not private
- The video is uploaded to a third-party **public** host. Anyone with the (hard-to-guess, `secret`) URL can download it until Soc_bot deletes it or it expires (`expires` default 1 h). 0x0.st stores the uploader's IP and user agent.
- The management token and the URL exist only in memory, are hidden from `repr`, and are never logged or stored in the database. Error messages carry only HTTP status codes.
- The URL is accepted only if it is https, on the configured instance host, and not localhost or a private IP.
- Use `s3` when privacy matters or when publishing unattended (AUTO): 0x0.st's Terms forbid automated mass uploads.

### TempFile.org provider (`MEDIA_STORAGE_PROVIDER=tempfile`): public, not private
- The video is uploaded to a public third-party host with **no authentication**. The file id alone allows downloading **and deleting** it, so Soc_bot keeps the id and URL only in memory (hidden from `repr`), never logs or stores them, and deletes the file after the attempt. `expiryHours` defaults to 1.
- Instagram only receives a URL that Soc_bot builds from a validated id on `https://tempfile.org`; response URLs are checked (https, same host, not local or private) and never passed on as-is.
- Provider error text is redacted and truncated; the local filename is never sent (`video.mp4`).
- Use `s3` when privacy matters or for unattended runs.

### Automatic routing (`MEDIA_STORAGE_PROVIDER=auto`)
- Small videos (up to 99 MB by default) go to **TempFile.org, a PUBLIC host**; larger ones go to **S3, private** (no ACL, SigV4 presigned GET, TTL 900 s). The plan names the provider **before** the user confirms, and Create Post shows the public-host warning when TempFile will be used.
- No failure fallback: a failed provider is reported, and the video is never re-sent to a different host.
- Each Instagram job has its own temporary object and cleanup (in-memory handles only); nothing is persisted.

