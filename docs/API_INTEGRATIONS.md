# API Integrations

## Overview

This document tracks the official API specifications, OAuth flows, and integration requirements for each target platform. All information must be verified against **official documentation** before implementation.

Status terms used here are separate claims:

| Term | Meaning |
|------|---------|
| Docs verified | Endpoints and parameters checked against the provider's current official docs |
| OAuth implemented | Code exists in `src/platforms/<platform>/auth.py` |
| Tested with mocks | Automated tests with mocked HTTP (no provider contact) |
| Real OAuth tested | A human completed the flow against the real provider with real credentials |
| Publishing implemented | Phase 4, not started |

---

## Instagram

### Official API
- **Name:** Instagram API with Instagram Login (Business Login for Instagram)
- **Documentation:** https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login
- **Graph API version used for `/me`:** v25.0 (checked 2026-09-25)
- **Platform Requirements:** Instagram Business or Creator (professional) account. **No Facebook Page required.**

### OAuth Flow — docs verified 2026-09-25 · tested with mocks · real OAuth NOT RUN
- **Authorization URL:** `https://www.instagram.com/oauth/authorize` (`client_id`, `redirect_uri`, `response_type=code`, `scope`, `state`)
- **Scopes (comma-separated):**
  - `instagram_business_basic`: required for every Instagram Login flow and for token refresh
  - `instagram_business_content_publish`: required to publish content (Phase 4)
  - Legacy `instagram_basic`, `instagram_graph_user_*` and `pages_*` scopes belong to Facebook Login / Basic Display and are **not** used
- **Code → short-lived token:** `POST https://api.instagram.com/oauth/access_token` (form: `client_id`, `client_secret`, `grant_type=authorization_code`, `redirect_uri`, `code`). Response `{"data":[{access_token, user_id, permissions}]}`. The token is valid for 1 hour. The authorization code is valid for 1 hour and can be used once.
  - The earlier audit named `graph.instagram.com/access_token` as the code-exchange endpoint. Meta's docs use `api.instagram.com/oauth/access_token` for the code exchange; `graph.instagram.com/access_token` is the long-lived exchange below.
- **Short → long-lived token:** `GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=…&access_token=<short>` → `{access_token, token_type, expires_in}` (~60 days). If this step fails, the connection fails; the 1-hour token is never stored.
- **"Refresh" is a token re-exchange, not an OAuth refresh token:** `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<long-lived>` → a **new** long-lived token plus `expires_in`. Allowed only when the current token is ≥ 24 h old and not expired, and `instagram_business_basic` was granted. Instagram issues no refresh token. The new token replaces the stored one.
- **Account identity:** `GET https://graph.instagram.com/v25.0/me?fields=user_id,username,name,profile_picture_url`, with the token sent in the `Authorization` header. `user_id` is the Instagram professional account ID and is stored as `platform_account_id`; `id` is only app-scoped.
- **PKCE:** not documented for Instagram Login, so it is not sent.
- **Revocation:** Instagram Login documents no token-revocation endpoint. Soc_bot disconnects locally only (`revoke_tokens()` returns `False` without a request). Users remove access in Instagram → Settings → Apps and websites. Meta's Deauthorize Callback URL is an app-dashboard setting that needs a public HTTPS endpoint, so it is not implemented.
- **Redirect URI:** must exactly match a registered OAuth redirect URI. Instagram does not document a wildcard-port loopback rule, so set a fixed `INSTAGRAM_REDIRECT_URI`. Meta may also require HTTPS here. Confirm in the dashboard before the first real login.
- **State parameter:** sent and validated (CSRF protection, bound to platform)

### Required Credentials
| Credential | Source |
|------------|--------|
| Instagram App ID | App Dashboard → Instagram → API setup with Instagram login (this is **not** the Facebook App ID) |
| Instagram App Secret | Same page |
| Redirect URI | Business login settings → OAuth redirect URIs |

### Publishing Workflow (not re-verified; Phase 4)
1. **Container Creation:** POST `/{IG_USER_ID}/media` with `media_type=VIDEO`/`REELS`, `video_url` (publicly accessible URL), `caption`
2. **Status Polling:** GET `/{CONTAINER_ID}?fields=status_code` until `FINISHED`
3. **Publish:** POST `/{IG_USER_ID}/media_publish` with `creation_id=<CONTAINER_ID>`
4. **Result:** Returns the media ID on success

### Media Requirements
| Property | Requirement |
|----------|-------------|
| Format | MP4, MOV |
| Codec | H.264 |
| Max File Size | 4 GB |
| Max Duration | 60 minutes (Reels: 90 seconds) |
| Aspect Ratio | 9:16 (Reels), 4:5, 1:1, 1.91:1 |
| Frame Rate | ≤ 30 fps |
| Audio | AAC |

### Rate Limits
- 200 calls/hour per user (Graph API)
- Media publish: ~25/day per account (estimated)

### Known Limitations
- Requires a publicly accessible video URL (no direct upload)
- Personal Instagram accounts not supported (Business/Creator only)
- Reels vs Feed post distinction via media_type

### Current Implementation Status
OAuth implemented and tested with mocks. Real OAuth: NOT RUN. Publishing: NOT IMPLEMENTED.

---

## TikTok

### Official API
- **Name:** TikTok Login Kit + Content Posting API
- **Documentation:** https://developers.tiktok.com/doc/login-kit-desktop, https://developers.tiktok.com/doc/oauth-user-access-token-management
- **API Version:** v2
- **Platform Requirements:** TikTok Developer Account; Content Posting API access requires approval

### OAuth Flow — docs verified 2026-09-25 · tested with mocks · real OAuth NOT RUN
- **Product:** Login Kit for Desktop
- **Authorization URL:** `https://www.tiktok.com/v2/auth/authorize/` (`client_key`, `scope`, `response_type=code`, `redirect_uri`, `state`, `code_challenge`, `code_challenge_method=S256`)
- **Scopes (comma-separated):** `video.upload`, `video.publish`, `user.info.basic`
- **PKCE differs from RFC 7636:** `code_challenge = hex(SHA256(code_verifier))`, a **hex** digest, not base64url. `code_challenge_method=S256`.
- **Token URL:** `POST https://open.tiktokapis.com/v2/oauth/token/` → `open_id, scope, access_token, expires_in, refresh_token, refresh_expires_in, token_type`
- **Lifetimes:** access token 24 h, refresh token 365 days. Soc_bot computes `expires_at` from the returned `expires_in` and does not hard-code 24 h.
- **Refresh token rotation:** "The returned `refresh_token` may be different than the one passed in the payload. You must use the newly-returned token." After every refresh Soc_bot encrypts and stores the returned refresh token in place of the old one.
- **Revocation:** `POST https://open.tiktokapis.com/v2/oauth/revoke/` (`client_key`, `client_secret`, `token`)
- **Redirect URI (desktop):** host must be `localhost` or `127.0.0.1` with a port; wildcard ports are supported. Register `http://127.0.0.1:*/callback/tiktok` so the dynamic port works.
- **State parameter:** sent and validated (CSRF protection, bound to platform)

### Required Credentials
| Credential | Source |
|------------|--------|
| Client Key | TikTok Developer Portal → App Details |
| Client Secret | TikTok Developer Portal → App Details |
| Redirect URI | Configured in TikTok Developer Portal (Login Kit → Desktop) |

### Publishing Workflow (Content Posting API; not re-verified, Phase 4)
1. **Initialize Upload:** POST `/v2/post/publish/video/init/` with `source_info` → returns `upload_url`
2. **Upload Video:** PUT the video binary to `upload_url`
3. **Publish:** POST `/v2/post/publish/video/create/` with `video_id`, `caption`, `privacy_level`
4. **Status Polling:** GET `/v2/post/publish/status/fetch/` with `publish_id`

### Media Requirements
| Property | Requirement |
|----------|-------------|
| Format | MP4, MOV, MPEG, 3GP, AVI |
| Codec | H.264, H.265 |
| Max File Size | 4 GB |
| Max Duration | 10 minutes (up to 60 min for some accounts) |
| Aspect Ratio | 9:16 (vertical), 1:1, 16:9 |
| Resolution | Min 720x1280, Max 1080x1920 |
| Frame Rate | ≤ 60 fps |
| Audio | AAC, MP3 |

### Rate Limits
- 1000 requests/day per app (Creator API)
- Video upload: varies by account tier

### Known Limitations
- Content Posting API access requires approval (not automatic)
- Personal accounts may have limited API access
- Draft vs Direct post options
- Commercial Content Library restrictions

### Current Implementation Status
OAuth implemented and tested with mocks. Real OAuth: NOT RUN. Publishing: NOT IMPLEMENTED.

---

## YouTube

### Official API
- **Name:** YouTube Data API v3
- **Documentation:** https://developers.google.com/youtube/v3, https://developers.google.com/identity/protocols/oauth2/native-app
- **API Version:** v3
- **Platform Requirements:** Google Cloud Project, YouTube Data API v3 enabled, OAuth consent screen configured, OAuth client of type **Desktop app**

### OAuth Flow — docs verified 2026-09-25 · tested with mocks · real OAuth NOT RUN
- **Authorization URL:** `https://accounts.google.com/o/oauth2/v2/auth`
- **Token URL:** `https://oauth2.googleapis.com/token`
- **Scopes:** `https://www.googleapis.com/auth/youtube.upload`, `.../youtube`, `.../youtube.readonly`
- **PKCE:** S256, RFC 7636 base64url without padding
- **Redirect URI (loopback IP):** Google's guidance is to "start an HTTP listener on a random available port". Soc_bot binds `127.0.0.1:0`, reads the port the OS assigned, and sends `http://127.0.0.1:<port>/callback/youtube`. Port 8080 is **not** assumed. Desktop-app clients need no per-port registration.
- **Token response:** `access_token`, `expires_in` (used to compute `expires_at`), `refresh_token` (first consent only; the stored one is kept when a refresh omits it)
- **Access type / prompt:** `offline` / `consent`, so a refresh token is issued
- **Revocation:** `POST https://oauth2.googleapis.com/revoke` with `token`
- **State parameter:** sent and validated (CSRF protection, bound to platform)

### Required Credentials
| Credential | Source |
|------------|--------|
| Client ID | Google Cloud Console → APIs & Services → Credentials (Desktop app) |
| Client Secret | Same |
| Redirect URI | Not needed for Desktop-app clients (dynamic loopback) |

### Publishing Workflow (not re-verified; Phase 4)
1. **Resumable Upload Init:** POST `/upload/youtube/v3/videos?part=snippet,status` with metadata → returns `upload_url`
2. **Upload Video:** PUT the video binary to `upload_url` (resumable, chunked)
3. **Status Polling:** Poll the upload URL until complete
4. **Result:** Returns the video ID on success

### Media Requirements
| Property | Requirement |
|----------|-------------|
| Format | MP4, MOV, MPEG, AVI, WMV, FLV, 3GP, WebM |
| Codec | H.264, H.265, VP9, AV1 |
| Max File Size | 256 GB (or 12 hours) |
| Max Duration | 12 hours |
| Aspect Ratio | Any (16:9 recommended) |
| Resolution | Up to 8K (7680x4320) |
| Frame Rate | ≤ 60 fps |
| Audio | AAC, MP3, FLAC |

### Rate Limits
- 10,000 units/day per project (default quota)
- Video insert: ~1,600 units per upload
- ~6 uploads/day default (quota increase request possible)

### Known Limitations
- Requires Google Cloud Project setup
- OAuth consent screen may need verification for sensitive scopes
- Quota limits restrict daily uploads
- Resumable upload required for large files
- Processing time after upload (minutes to hours)

### Current Implementation Status
OAuth implemented and tested with mocks. Real OAuth: NOT RUN. Publishing: NOT IMPLEMENTED.

---

## OAuth Verification Status (2026-09-25)

| Platform | OAuth product | Auth URL | Code exchange | Long-lived / refresh | Scopes | PKCE | Redirect | Account ID | Docs verified | Mock tests | Real OAuth |
|----------|---------------|----------|---------------|----------------------|--------|------|----------|------------|---------------|------------|------------|
| Instagram | Business Login for Instagram | instagram.com/oauth/authorize | api.instagram.com/oauth/access_token | ig_exchange_token / ig_refresh_token (graph.instagram.com) | instagram_business_basic, instagram_business_content_publish | Not documented, not sent | Fixed, registered | `/me` `user_id` | ✅ 2026-09-25 | ✅ | NOT RUN |
| TikTok | Login Kit for Desktop | tiktok.com/v2/auth/authorize/ | open.tiktokapis.com/v2/oauth/token/ | refresh_token grant, rotation persisted | video.upload, video.publish, user.info.basic | S256, **hex** | 127.0.0.1, wildcard port | open_id | ✅ 2026-09-25 | ✅ | NOT RUN |
| YouTube | Google OAuth 2.0 (Desktop app) | accounts.google.com/o/oauth2/v2/auth | oauth2.googleapis.com/token | refresh_token grant | youtube.upload, youtube, youtube.readonly | S256, base64url | 127.0.0.1, dynamic port | channel id | ✅ 2026-09-25 | ✅ | NOT RUN |

Publishing workflows, media requirements and rate limits in this file were **not** re-verified in this pass; that is Phase 4 work.
