# Authentication

Official endpoint details and doc links live in [API_INTEGRATIONS.md](API_INTEGRATIONS.md). This file covers how Soc_bot runs OAuth. Last verified against official docs: **2026-09-25**.

Status: OAuth is implemented and tested with mocks. **Real provider OAuth: NOT RUN** (no credentials configured). Publishing is not implemented.

## Browser Authorization Flow

```
Connect Account (CLI)
  → start loopback callback server on 127.0.0.1:<port>   (port 0 = OS picks a free port)
  → redirect_uri = http://127.0.0.1:<actual-port>/callback/<platform>   (or fixed *_REDIRECT_URI)
  → create state (bound to platform) + PKCE verifier
  → open browser at the platform authorization URL
  → callback: /callback/<platform>?code=…&state=…
       state must exist, be unexpired, be unused, and be issued for <platform>
  → exchange code (same redirect_uri) → identity lookup → create/update account (tokens encrypted)
  → stop callback server
```

- `OAuthCallbackServer` is shared by all platforms. It binds `127.0.0.1` by default (never `0.0.0.0`), with exclusive binding so another process cannot share the port.
- Only `/callback/<platform>` paths are handled. Other paths (e.g. `/favicon.ico`) get a 404 and do not end the flow.
- State is single-use: it is consumed on the first callback, even when rejected. If the callback path's platform differs from the state's platform, the callback is rejected **before** any code exchange.
- The flow times out after 300 s by default. Bind or startup failures raise `OAuthCallbackError` without credentials in the message.
- Provider error text shown on the result page is HTML-escaped. Authorization codes are never echoed.

## PKCE

| Platform | PKCE | Challenge encoding |
|----------|------|--------------------|
| YouTube | S256 | RFC 7636 base64url, no padding |
| TikTok (Login Kit for Desktop) | S256 | **hex** SHA-256 digest (TikTok-specific) |
| Instagram (Instagram Login) | not documented, not sent | — |

`generate_pkce_challenge(verifier, encoding="base64url" | "hex")` in `src/auth/state.py` is the single implementation. An adapter chooses its encoding with the `PKCE_CHALLENGE_ENCODING` class attribute (`PlatformAuth` defaults to `base64url`; `TikTokAuth` sets `hex`).

## Token Lifetimes & Expiry Model

Soc_bot never assumes a lifetime. `expires_at = now(UTC) + expires_in`, using the `expires_in` the provider returned (`OAuthTokenResult.expires_at`, `src.auth.base.expires_at_from`). If the provider returned no `expires_in`, `expires_at` stays `None`. `is_token_expiring(expires_at, margin_seconds)` reports whether a token is expired or close to expiry. SQLite returns naive datetimes, which are treated as UTC.

| Platform | Access token (documented) | Renewal mechanism |
|----------|---------------------------|-------------------|
| Instagram | Short-lived 1 h → exchanged immediately for long-lived ~60 days | **Token re-exchange** (`ig_refresh_token`) of the long-lived access token. There is no refresh token. Allowed when the token is ≥ 24 h old and unexpired, and returns a **new** token that replaces the stored one. |
| TikTok | 24 h (`expires_in`) | Refresh token (365 days, `refresh_expires_in`). The refresh token **may rotate**: the returned refresh token is always encrypted and stored in place of the old one. |
| YouTube | ~1 h (`expires_in`) | Refresh token (until revoked). Google usually omits `refresh_token` on refresh, so the stored one is kept. |

`AuthManager.refresh_account_tokens(account_id)` performs the renewal. It uses the stored access token for Instagram (`REFRESH_USES_ACCESS_TOKEN`) and the refresh token for the other platforms, then stores the new access token, any new refresh token, and the new `expires_at`. Scheduling proactive refresh (e.g. before a publish) is a Phase 4 concern and is not implemented.

`refresh_expires_in` (TikTok) is returned on `OAuthTokenResult` but not persisted; the schema has no column for it. When a TikTok refresh fails with an expired refresh token, the user must reconnect.

## Account Identification

| Platform | `platform_account_id` | Source |
|----------|-----------------------|--------|
| Instagram | Instagram professional account ID (`user_id`) | `GET graph.instagram.com/v25.0/me?fields=user_id,username,…` |
| TikTok | `open_id` | `GET open.tiktokapis.com/v2/user/info/` |
| YouTube | channel `id` | `GET youtube/v3/channels?mine=true` |

Reconnecting an account that already exists (same platform + `platform_account_id`) updates its tokens, expiry and identity. It does not fail as a duplicate.

## Disconnecting Accounts

1. Revoke on the platform where supported:
   - TikTok: `POST https://open.tiktokapis.com/v2/oauth/revoke/`
   - YouTube: `POST https://oauth2.googleapis.com/revoke`
   - Instagram: **no documented revocation endpoint**, so no request is made. The user removes access in Instagram → Settings → Apps and websites.
2. Revocation errors never block the local disconnect.
3. Account status is set to `disconnected`; history is preserved.

## Secure Token Storage

- Fernet encryption (`cryptography`), key from `ENCRYPTION_KEY`
- Encrypted columns `access_token_enc`, `refresh_token_enc`
- Tokens, authorization codes and client secrets are never logged, never put in exception messages, and never echoed to the browser (covered by regression tests)

## Environment Variables

```env
ENCRYPTION_KEY=

INSTAGRAM_APP_ID=          # Instagram app ID (Instagram Login), not the Facebook app ID
INSTAGRAM_APP_SECRET=
INSTAGRAM_REDIRECT_URI=    # fixed, must be registered, e.g. http://127.0.0.1:8765/callback/instagram

TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REDIRECT_URI=       # optional; register http://127.0.0.1:*/callback/tiktok and leave blank

YOUTUBE_CLIENT_ID=         # Desktop-app OAuth client
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=      # leave blank: dynamic loopback port

OAUTH_CALLBACK_HOST=127.0.0.1
OAUTH_CALLBACK_PORT=0      # 0 = dynamic (default)
```

A fixed `*_REDIRECT_URI` must have the form `http://<host>:<port>/callback/<platform>`. The callback server binds exactly that host and port.
