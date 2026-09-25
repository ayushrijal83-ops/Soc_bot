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
| Publishing implemented | Phase 4: implemented, mocked tests only |
| Real publishing tested | Content actually posted to a real account: NOT RUN |

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

### Publishing (Reels): VERIFIED 2026-09-25 · implemented · mocked tests only · real publishing NOT RUN

Sources: https://developers.facebook.com/docs/instagram-platform/content-publishing and the IG User `/media` reference.

| Step | Endpoint | Method | Request | Response |
|------|----------|--------|---------|----------|
| 1. Create container | `graph.instagram.com/v25.0/{ig_user_id}/media` | POST | `media_type=REELS`, `video_url` (public URL Meta downloads), `caption`, optional `share_to_feed` | `{"id": container_id}` |
| 2. Processing status | `graph.instagram.com/v25.0/{container_id}?fields=status_code` | GET | none | `status_code`: `IN_PROGRESS`, `FINISHED`, `ERROR`, `EXPIRED`, `PUBLISHED` |
| 3. Publish | `graph.instagram.com/v25.0/{ig_user_id}/media_publish` | POST | `creation_id=container_id` | `{"id": ig_media_id}` |

- **Auth:** Instagram user access token (Instagram Login) with `instagram_business_basic` + `instagram_business_content_publish`. Sent as `Authorization: Bearer`, never in the URL.
- **Account types:** Instagram professional (Business or Creator) accounts. An account connected to a Page that requires Page Publishing Authorization can't be published to until PPA is complete.
- **Upload method:** with Instagram Login, video publishing is documented only via `video_url`. Meta's docs list the resumable `rupload.facebook.com` upload for **Facebook Login only**. Soc_bot therefore needs a public `https://` URL for the video and **cannot upload a local file to Instagram**. NOT VERIFIED: whether rupload works with Instagram Login tokens, so it isn't used.
- **Polling:** Meta recommends checking status "once per minute, for no more than 5 minutes". Soc_bot polls every 60 s, 5 times. If the container is still `IN_PROGRESS`, the job stays `processing` and is re-checked later; this is not a failure.
- **Containers** expire after 24 h (`EXPIRED`). Soc_bot then creates a new container on the next attempt.
- **Rate limit:** 100 API-published posts per account per 24-hour moving window (`GET /{ig_user_id}/content_publishing_limit` shows usage; not called by Soc_bot).
- **Caption:** max 2,200 characters, 30 hashtags, 20 @ tags.
- **Reels video spec:** MOV or MP4; HEVC or H.264; 23–60 FPS; max 1920 px wide; aspect ratio 0.01:1 to 10:1 (9:16 recommended); 3 s to 15 min; ≤ 300 MB; AAC audio ≤ 48 kHz. Soc_bot checks size, container and caption locally; it checks duration and width only when ffprobe is installed.
- **Errors:** Graph error JSON `{"error": {"message", "code", "is_transient", "fbtrace_id"}}`. Soc_bot maps `is_transient`, 429 and 5xx to retryable; code 190 / 401 to `unauthorized`; codes 10 and 200 / 403 to `permission_denied`; codes 4, 9, 17, 32 and 613 to `rate_limited` (retryable).
- **Token:** a long-lived token (~60 days) renewed with `ig_refresh_token`. The engine renews it when it expires within 7 days, and continues if renewal is refused but the token is still valid.

### Known Limitations
- Needs a publicly reachable `video_url`. Soc_bot does not host files.
- Personal Instagram accounts are not supported (Business/Creator only)
- Only Reels are implemented (no images, carousels or stories)

### Current Implementation Status
OAuth and publishing implemented. **Mocked tests verified. Real provider OAuth and publishing NOT verified (NOT RUN).**

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

### Publishing (Direct Post, FILE_UPLOAD): VERIFIED 2026-09-25 · implemented · mocked tests only · real publishing NOT RUN

Sources: content-posting-api-reference-direct-post, -query-creator-info, -get-video-status, content-posting-api-media-transfer-guide.

| Step | Endpoint | Method | Request | Response |
|------|----------|--------|---------|----------|
| 1. Creator info | `open.tiktokapis.com/v2/post/publish/creator_info/query/` | POST | none | `privacy_level_options`, `max_video_post_duration_sec`, `comment_disabled`, `duet_disabled`, `stitch_disabled`, `creator_nickname` |
| 2. Init | `open.tiktokapis.com/v2/post/publish/video/init/` | POST (JSON) | `post_info{title, privacy_level, disable_*, is_aigc}`, `source_info{source: FILE_UPLOAD, video_size, chunk_size, total_chunk_count}` | `publish_id`, `upload_url` (valid 1 h) |
| 3. Upload | `upload_url` | PUT per chunk | `Content-Type`, `Content-Range: bytes a-b/total`, sequential | `206` per chunk, `201` when complete |
| 4. Status | `open.tiktokapis.com/v2/post/publish/status/fetch/` | POST | `{"publish_id"}` | `status`: `PROCESSING_UPLOAD`, `PROCESSING_DOWNLOAD`, `SEND_TO_USER_INBOX`, `PUBLISH_COMPLETE`, `FAILED`; `fail_reason`; `publicaly_available_post_id` (sic) |

- **Auth:** `Authorization: Bearer` user token with scope `video.publish`. OAuth success does **not** grant posting rights: the app needs Content Posting API access. **Unaudited clients can only post to private accounts / `SELF_ONLY`.**
- **Privacy:** `privacy_level` is required, and must be one of the creator's `privacy_level_options`. TikTok's UX rules require the user to choose it (no default). Soc_bot asks the user and checks it against creator info before init.
- **Chunks:** 5–64 MB per chunk (last chunk ≤ 128 MB), files < 5 MB go in one chunk, max 1000 chunks, `total_chunk_count = floor(video_size / chunk_size)`. Soc_bot uses 10 MB chunks and reads one chunk at a time.
- **Video:** MP4 (recommended), WebM, MOV; H.264/H.265/VP8/VP9; 23–60 FPS; 360–4096 px; ≤ 10 min via API (the per-creator max may be lower); ≤ 4 GB.
- **Caption (`title`):** ≤ 2,200 UTF-16 code units.
- **Rate limits:** init 6 requests/min per token; status fetch 30 requests/min per token (Soc_bot polls every 5 s).
- **Errors:** envelope `{"data":{}, "error":{"code","message","log_id"}}`. `access_token_invalid` becomes `unauthorized` and `scope_not_authorized` becomes `insufficient_scope` (both non-retryable); `spam_risk_*`, `privacy_level_option_mismatch` and `unaudited_client_can_only_post_to_private_accounts` are non-retryable; `rate_limit_exceeded`, `internal_error` and 5xx are retryable.
- **Post ID:** `publicaly_available_post_id` appears only for public posts after moderation; otherwise Soc_bot records the `publish_id`.
- NOT VERIFIED: what `SEND_TO_USER_INBOX` means for Direct Post (it belongs to the inbox/draft flow). Soc_bot treats it as still processing.

### Known Limitations
- Unaudited apps: private (`SELF_ONLY`) posts only
- Soc_bot shows the privacy options as a fixed list and checks them against creator info at publish time. It does not yet render the full creator-info screen that TikTok's app review expects (nickname, per-creator options, interaction toggles).
- Upload URL expires after 1 hour. An interrupted upload is restarted with a new init; the unfinished one never publishes.

### Current Implementation Status
OAuth and publishing implemented. **Mocked tests verified. Real provider OAuth and publishing NOT verified (NOT RUN).**

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

### Publishing (resumable upload): VERIFIED 2026-09-25 · implemented · mocked tests only · real publishing NOT RUN

Sources: https://developers.google.com/youtube/v3/docs/videos/insert, /guides/using_resumable_upload_protocol, /docs/videos (resource).

| Step | Endpoint | Method | Request | Response |
|------|----------|--------|---------|----------|
| 1. Start session | `www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status` | POST | JSON `{snippet{title, description, categoryId?}, status{privacyStatus, selfDeclaredMadeForKids?}}`, `X-Upload-Content-Length`, `X-Upload-Content-Type` | `200` + `Location` (session URI) |
| 2. Upload | session URI | PUT per chunk | `Content-Range: bytes a-b/total`, chunks a multiple of 256 KiB | `308` + `Range` while incomplete; `200/201` + video resource |
| 2b. Resume | session URI | PUT | `Content-Range: bytes */total`, empty body | `308` + `Range: bytes=0-N`, or `200/201` if complete |
| 3. Processing | `www.googleapis.com/youtube/v3/videos?part=status,processingDetails&id=…` | GET | none | `status.uploadStatus`: `uploaded`, `processed`, `failed`, `rejected`, `deleted` |

- **Auth:** `Authorization: Bearer` with `youtube.upload` (or `youtube`, `youtubepartner`, `youtube.force-ssl`).
- **Metadata:** title ≤ 100 characters, description ≤ 5,000 bytes, neither may contain `<` or `>`; `privacyStatus` is `private`, `unlisted` or `public`. Soc_bot requires a title, defaults privacy to `private`, and asks about `selfDeclaredMadeForKids`. NOT VERIFIED: whether `categoryId` is mandatory. Soc_bot sends it only if the job's `category_id` option is set (the CLI never sets it).
- **Unverified API projects** (created after 2020-07-28) have uploads **forced to private**.
- **Quota:** `videos.insert` costs 1 unit in the Video Uploads bucket, limited to 100 calls/day. `videos.list` (processing polling) costs 1 unit per call, and Soc_bot polls at most 20 times.
- **Retries:** Google says to retry 500, 502, 503 and 504 with exponential backoff. Soc_bot resumes from the server's reported offset (at most 3 times per attempt), then leaves further retries to the engine.
- **Errors:** `{"error": {"code", "message", "errors": [{"reason"}]}}`. `quotaExceeded` and `uploadLimitExceeded` become `quota_exceeded` (non-retryable); 401 becomes `unauthorized`; other 4xx are non-retryable; 5xx and 429 are retryable.
- **Limits:** ≤ 256 GB; `video/*` or `application/octet-stream`.
- **Token:** access tokens last about 1 h and are refreshed with the refresh token before publishing. A single upload that runs longer than the token's life will get a 401 (not handled).

### Known Limitations
- Unverified Google Cloud projects: videos are private
- Daily upload quota
- Processing can take longer than the polling window; the job then stays `processing` and is re-checked from the Publishing Queue

### Current Implementation Status
OAuth and publishing implemented. **Mocked tests verified. Real provider OAuth and publishing NOT verified (NOT RUN).**

---

## OAuth Verification Status (2026-09-25)

| Platform | OAuth product | Auth URL | Code exchange | Long-lived / refresh | Scopes | PKCE | Redirect | Account ID | Docs verified | Mock tests | Real OAuth |
|----------|---------------|----------|---------------|----------------------|--------|------|----------|------------|---------------|------------|------------|
| Instagram | Business Login for Instagram | instagram.com/oauth/authorize | api.instagram.com/oauth/access_token | ig_exchange_token / ig_refresh_token (graph.instagram.com) | instagram_business_basic, instagram_business_content_publish | Not documented, not sent | Fixed, registered | `/me` `user_id` | ✅ 2026-09-25 | ✅ | NOT RUN |
| TikTok | Login Kit for Desktop | tiktok.com/v2/auth/authorize/ | open.tiktokapis.com/v2/oauth/token/ | refresh_token grant, rotation persisted | video.upload, video.publish, user.info.basic | S256, **hex** | 127.0.0.1, wildcard port | open_id | ✅ 2026-09-25 | ✅ | NOT RUN |
| YouTube | Google OAuth 2.0 (Desktop app) | accounts.google.com/o/oauth2/v2/auth | oauth2.googleapis.com/token | refresh_token grant | youtube.upload, youtube, youtube.readonly | S256, base64url | 127.0.0.1, dynamic port | channel id | ✅ 2026-09-25 | ✅ | NOT RUN |

Publishing endpoints were verified 2026-09-25 (Phase 4). See each platform's Publishing section.

## Publishing Verification Status (2026-09-25)

| Platform | Flow | Docs verified | Implemented | Mock tests | Real publishing |
|----------|------|---------------|-------------|------------|-----------------|
| Instagram | Reels: container (`video_url`) → status → `media_publish` | ✅ | ✅ | ✅ | NOT RUN |
| TikTok | Direct Post: creator_info → init → chunked PUT → status | ✅ | ✅ | ✅ | NOT RUN |
| YouTube | Resumable `videos.insert` → chunked PUT → `videos.list` status | ✅ | ✅ | ✅ | NOT RUN |

## Covers / Thumbnails (verified 2026-09-25)

| Platform | Official mechanism | Soc_bot |
|----------|--------------------|---------|
| YouTube | `POST https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId=…&uploadType=media`; body = image; `image/jpeg`, `image/png` (or octet-stream); **≤ 50 MB**; **~50 quota units**; scopes `youtube.upload` / `youtube` / `youtube.force-ssl` / `youtubepartner`; errors `invalidImage` 400, `mediaBodyRequired` 400, `forbidden` 403 (no permission, e.g. a channel without custom-thumbnail rights), `videoNotFound` 404, `uploadRateLimitExceeded` 429 | ✅ Implemented: called once after the video upload succeeds, with the existing OAuth token. The result is stored separately (`cover_status`); a failure never marks the video failed or re-uploads it. **Real run: ✅ published** on private test video `rAGivy-c3DM` |
| TikTok | Direct Post `post_info.video_cover_timestamp_ms` (a frame of the video). No cover image upload | Cover image **not supported** (reported, not sent). Frame selection is not implemented yet |
| Instagram | `cover_url` (a public image URL Meta downloads) and `thumb_offset` exist in the IG User `/media` reference, which is written for **Facebook Login / graph.facebook.com**. The Instagram Login content-publishing docs don't mention them | Cover image **not supported** (reported, not sent). NOT VERIFIED whether `cover_url` works with Instagram Login tokens |

## Real Provider Status (updated 2026-09-25)

| Platform | Real OAuth | Real publishing | Real cover |
|----------|------------|-----------------|------------|
| YouTube | ✅ 2 channels | ✅ (user upload + Phase 5A private regression) | ✅ thumbnail published |
| TikTok | NOT RUN | NOT RUN | n/a (not supported) |
| Instagram | NOT RUN | NOT RUN | n/a (not supported) |
