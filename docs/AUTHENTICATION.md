# Authentication

## OAuth Architecture

Each platform uses **OAuth 2.0 with PKCE (Proof Key for Code Exchange)** for secure authorization without client secrets in the authorization request.

```
User → CLI → Browser (Platform Login) → Platform Consent → Callback Server → Token Exchange → Secure Storage
```

## Browser Authorization Flow

1. **User selects "Connect Account"** in CLI menu
2. **Application generates PKCE code_verifier/code_challenge**
3. **Application opens system browser** to platform authorization URL with:
   - `client_id`
   - `redirect_uri` (http://localhost:8080/callback/{platform})
   - `scope` (space-separated)
   - `response_type=code`
   - `code_challenge` + `code_challenge_method=S256`
   - `state` (CSRF protection)
4. **User logs in** on platform's official site
5. **User grants permissions** (scopes)
6. **Platform redirects** to `redirect_uri?code=AUTH_CODE&state=STATE`
7. **Local callback server** receives request, validates state, extracts code
8. **Application exchanges code for tokens** via token endpoint (with `code_verifier`)
9. **Tokens stored securely** in encrypted database
10. **Account available** for publishing

## Callback Flow

Local HTTP server on `http://localhost:8080/callback/{platform}`:
- Single-use per authorization attempt
- Validates `state` parameter matches generated value
- Returns success/failure HTML to browser
- Shuts down after receiving callback

## Access Tokens

| Platform | Token Type | Typical Lifetime |
|----------|------------|------------------|
| Instagram | User Access Token | 60 days (extendable) |
| TikTok | Access Token | 2 years (refreshable) |
| YouTube | Access Token | 1 hour |

**Never log access tokens.** Store encrypted only.

## Refresh Tokens

| Platform | Refresh Token Lifetime | Refresh Endpoint |
|----------|------------------------|------------------|
| Instagram | 60 days (with token refresh) | `https://graph.facebook.com/v21.0/oauth/access_token` |
| TikTok | Long-lived (revocable) | `https://open.tiktokapis.com/v2/oauth/token/` |
| YouTube | Until revoked | `https://oauth2.googleapis.com/token` |

Refresh logic:
- Proactive refresh: 24h before expiry
- On-demand: when API returns 401/expired token error
- Store new token pair atomically (replace old)

## Expiration Handling

```python
def is_token_expired(expires_at: datetime) -> bool:
    return datetime.utcnow() >= (expires_at - timedelta(hours=1))

def needs_refresh(expires_at: datetime) -> bool:
    return datetime.utcnow() >= (expires_at - timedelta(hours=24))
```

## Token Refresh Flow

```
API Call → 401 Unauthorized / Token Expired
    |
    v
Refresh Token Request (with stored refresh_token)
    |
    v
New Access Token + New Refresh Token (if rotated)
    |
    v
Update Database (atomic)
    |
    v
Retry Original API Call
```

## Account Identification

Store platform-specific identifiers:
- **Instagram:** `instagram_user_id` (IG User ID) + `page_id` (Facebook Page ID)
- **TikTok:** `open_id` / `union_id` + `display_name`
- **YouTube:** `channel_id` + `channel_title`

Display name shown in CLI: `@username` or `Channel Name`

## Disconnecting Accounts

1. User selects "Disconnect" in Connected Accounts menu
2. **Revoke tokens** via platform revoke endpoint (if available):
   - Instagram: `DELETE /<USER_ID>/permissions`
   - TikTok: `POST /v2/oauth/revoke/`
   - YouTube: `POST https://oauth2.googleapis.com/revoke`
3. **Delete local record** from database
4. **Clear any cached data**

## Invalid/Revoked Token Handling

Detection:
- API returns 401 with specific error codes
- Token refresh fails (invalid_grant, revoked_token)

Response:
1. Mark account status = `REVOKED` in database
2. Notify user in CLI ("Account disconnected, please reconnect")
3. Remove from available accounts for publishing
4. Preserve history/jobs for audit

## Secure Token Storage

- **Encryption:** Fernet (AES-128-GCM) via `cryptography` library
- **Key:** `ENCRYPTION_KEY` from environment (32-byte base64)
- **Storage:** Encrypted blob in SQLite `access_token_enc`, `refresh_token_enc` columns
- **Key Rotation:** Not implemented in V1 (document for future)

```python
# Encryption
fernet = Fernet(key)
encrypted = fernet.encrypt(token.encode())

# Decryption
decrypted = fernet.decrypt(encrypted).decode()
```

## Environment Variables

```env
ENCRYPTION_KEY=base64_encoded_32_byte_key
# Generated via: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Never commit encryption key.** Generate unique per deployment.