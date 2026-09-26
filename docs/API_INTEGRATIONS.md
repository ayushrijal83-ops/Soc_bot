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

## Instagram Media Delivery (local file → temporary private storage → Instagram)

### Why Instagram needs a URL
With Instagram Login, Meta's Reels publishing takes the video as `video_url`, and **Meta's servers download it**. A Windows path such as `D:\Soc_bot\videos\x.mp4` isn't reachable from Meta. The alternative local-upload host (`rupload.facebook.com`) is documented only for Facebook Login. So the local file has to be exposed briefly over HTTPS.

### Why a YouTube watch URL is not used
The YouTube Data API returns video resources, IDs and page URLs (`youtube.com/watch?v=…`, `/shorts/…`). It has **no endpoint that returns a direct media file**. A watch or Shorts URL is an HTML page, not video bytes, so Meta can't process it; an earlier real job that used a Shorts URL was never going to work. Scraping YouTube, extracting stream URLs, or using yt-dlp to re-download is **prohibited**: YouTube's Terms forbid downloading content except through YouTube's own features, and such hacks break without notice.

### Flow (implemented)
```
local video (unchanged, never deleted)
   ↓  MediaSourceProvider.prepare()      upload to private bucket, key instagram/temp/<random>/video.mp4
   ↓  get_public_url()                   presigned HTTPS GET URL, TTL = MEDIA_STORAGE_PRESIGNED_URL_TTL (900 s)
   ↓  POST /{ig_id}/media  (REELS, video_url)      Meta downloads the object
   ↓  poll status_code (60 s × 5, per Meta)        object kept alive during processing
   ↓  POST /{ig_id}/media_publish
   ↓  cleanup()                          object deleted (success, failure or Ctrl+C)
```
- Code: `src/media_storage/` (`ObjectStorage` → `S3ObjectStorage`; `MediaSourceProvider` → `ObjectStorageMediaProvider`; `create_media_provider()` factory), used only by `InstagramPublisher`. `PublisherEngine`, YouTube and TikTok don't know about storage.
- **Retry/resume:**
  - Media is uploaded only when a new container is needed, and each attempt mints a fresh object and URL (never reused).
  - Resuming a `FINISHED` container publishes with no upload. An `ERROR`/`EXPIRED` container, or a retry after a failure, uploads again.
  - Storage 5xx, 429 and network errors are retryable. 4xx errors (AccessDenied, NoSuchBucket) and missing configuration are not.
- **If polling ends while the container is still `IN_PROGRESS`** (after about 5 minutes), the object is deleted at the end of the attempt anyway. If Meta hadn't finished downloading, the container becomes `ERROR` and the next resume uploads fresh media.
- **Never stored or logged:** the presigned URL (not in provider state, attempt rows or logs; `redact()` strips `X-Amz-*` URLs from error text), storage credentials, and the local path (not in the object key).
- **Explicit `video_url` job option:** kept only as an internal/debug override. The CLI never asks for it and content packages no longer use `video_url.txt`.

### Automatic routing by size (`MEDIA_STORAGE_PROVIDER=auto`)
```
local video ── size (bytes) ──► MediaStorageRouter (src/media_storage/router.py)
                                   ├─ fits TempFile (≤ max_file_size − margin)? ──► TempFile.org (PUBLIC, temporary)
                                   ├─ cloudflared available? ──────────────────────► Cloudflare Quick Tunnel (served locally, nothing uploaded)
                                   └─ otherwise ───────────────────────────────────► S3 (PRIVATE object + presigned URL)
```
- The router **is a `MediaSourceProvider`**, so the Instagram adapter is unchanged. It picks the **first configured** provider in `AUTO_ORDER = ("tempfile", "cloudflare_tunnel", "s3")` whose declared **`max_file_size`** fits the file. The handle remembers its provider, so `get_public_url()` and `cleanup()` go to the one that created the object.
- **Limits come from the providers, not the router:** TempFile `max_file_size = 100_000_000` bytes (the API documents "100MB"; the decimal reading is used because it's the smaller, safe one); S3 `None` (multipart, no application limit; Instagram's own 300 MB Reels limit is enforced by the Instagram adapter); 0x0 `512 MiB` (not in the auto order: uploads disabled by the service).
- **Safety margin:** `MEDIA_STORAGE_AUTO_MARGIN_MB` (default 1 MB, allowed 0–50), because multipart framing adds bytes to the request. TempFile is therefore used up to **99,000,000 bytes**; from 99,000,001 bytes on, S3 is used.
- **No failure fallback:** if TempFile fails (for example 503) for a small video, the job reports that TempFile failure and follows the normal retry rules. The video is **never** silently re-sent to S3. An opt-in fallback was considered and not added, since nothing needs it yet.
- **Large videos:** with cloudflared installed, they go through the Cloudflare Quick Tunnel. S3 is only used when cloudflared is missing. With neither, a large video is **BLOCKED in the plan** before anything is started, with the exact missing piece. "Configured" is a local check (the executable exists); a broken tunnel reports a failure and is never silently replaced by S3.
- **Plans and dry-run** show the size and the chosen provider (`150.1 MB video -> temporary PRIVATE object storage (S3) + presigned HTTPS URL [auto]`). No provider is contacted, no S3 client is built, and no presigned URL is created.
- **Settings → Check Instagram media storage** shows the AUTO mode, each tier's limit, whether it's public or private, and each configured tier's upload-free check.
- **Multiple Instagram accounts:** each Instagram job uploads **its own** temporary object and deletes it after **its own** attempt. One job never deletes media another job uses, so there's no shared-object race; one account failing doesn't affect another. The cost is one upload per account, which is fine within TempFile's 200 uploads/hour. Sharing one object across accounts would need cross-job reference counting and URL-expiry handling, and was rejected for now.
- **Backward compatibility:** `s3`, `tempfile` and `0x0` keep their exact single-provider meaning. With `tempfile`, a video over 100 MB fails validation, as before. Use `auto` for size-based routing.
- **Real tests (2026-09-25):**
  - Small: the 10.1 MB video routed to TempFile and was published to Instagram (job 7, media 18073405427476313); the temporary copy was deleted.
  - Large: a valid 150.06 MB MP4 routed away from TempFile, and was **blocked because S3 isn't configured**, so the real S3 → Instagram run is **NOT DONE**.
  - Two Instagram accounts: **NOT DONE** (only one Instagram account is connected).

### Provider: Cloudflare Quick Tunnel (`MEDIA_STORAGE_PROVIDER=cloudflare_tunnel`, AUTO tier 2)
**Why:** TempFile takes at most 100 MB (99 MB in AUTO). No free public host tested on 2026-09-25 could take a 131.9 MB video with a direct GET URL: qurl.sh (413), temp.sh (GET returns an HTML page), file.io (405), Litterbox (500 even for 10 MB), Pixeldrain (hotlinking premium-only), Filebin (cookie wall). 0x0 uploads are disabled.

**Not storage, a tunnel:** the video is never uploaded. It stays in `D:\Soc_bot\...`; Cloudflare only forwards Instagram's HTTP request to a server on this computer. There's no cloud copy and nothing to delete: cleanup only stops the tunnel and the local server.
```
local MP4 ─► ThreadingHTTPServer on 127.0.0.1:<random free port>  (ONE route: /media/<32 hex chars>.mp4)
          ─► cloudflared tunnel --no-autoupdate --url http://127.0.0.1:<port>   (child process, output parsed)
          ─► https://<random>.trycloudflare.com/media/<token>.mp4 ─► Instagram container (video_url)
             ... poll FINISHED ─► media_publish ─► permalink ─► cleanup: terminate cloudflared, stop the server
```
- **Quick Tunnels** (`cloudflared tunnel --url …`) need no Cloudflare account, API key or domain. Cloudflare documents them as **intended for testing and development, with no uptime guarantee (no SLA)** ([docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)). Use S3 for production reliability.
- **Security model:**
  - The server binds **only to 127.0.0.1**; any other `CLOUDFLARE_MEDIA_HOST` is rejected.
  - It serves exactly one file at exactly one path, with a cryptographically random token (`secrets.token_hex`, `CLOUDFLARE_MEDIA_TOKEN_BYTES`, default 16 = 128 bits). The URL carries no filename.
  - Every other path is 404, including traversal, `.env`, the DB and directory roots. There's no listing. Methods other than GET/HEAD get 501.
  - The server has request logging disabled (paths contain the token). The public URL lives in memory only: never logged, never stored in the DB, `provider_state` or published links.
- **HTTP:** GET and HEAD, `Content-Type` from the video type, an exact `Content-Length`, single `Range` requests (206/416), and streaming from disk in 1 MiB chunks (never loaded into RAM).
- **Start-up:**
  - `cloudflared` is found via `CLOUDFLARED_PATH` (an absolute path or a name on `PATH`); a missing binary gives an actionable error. Soc_bot never downloads executables.
  - Output is drained in a background thread; the first `https://*.trycloudflare.com` URL is used.
  - Only hyphenated quick-tunnel names count (`https://api.trycloudflare.com`, which cloudflared also prints, is ignored).
  - `CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS` (default 90) bounds both the URL appearing and the file being reachable. A failure stops everything and is retryable.
  - Readiness is checked the way Meta will fetch: the hostname is resolved through **public DNS-over-HTTPS** (cloudflare-dns.com / dns.google, alternated, first lookup after 5 s), then `HEAD` goes to that IP with the real hostname as TLS SNI and Host (certificate verified). Why: `trycloudflare.com` has a **60 s negative-cache TTL**. A lookup made before the record exists makes a resolver answer "no such host" for 60 s, and the PC's own resolver can't be flushed from here (real run 2026-09-25: the system resolver failed for ~45 s while 1.1.1.1 already had the record).
- **Lifecycle:** the tunnel is started in `prepare()` (only after you confirm publishing; never in plans or `--dry-run`) and stays up through container creation, Meta's download and processing, and `media_publish`. The Instagram adapter's `ExitStack` closes it on success, failure, container `ERROR`, timeout, exceptions and Ctrl+C. An `atexit` hook terminates any cloudflared still alive at interpreter exit. A cleanup problem is logged as `Tunnel cleanup warning: …` and never turns a published Reel into a failure.
- **Multiple accounts:** each job gets its **own** server, token and tunnel, so cleanup of one never touches another's URL.
- **Limits:** no application size limit (`max_file_size = None`); Instagram's 300 MB Reels limit applies. The PC must stay on and online while Instagram fetches. Quick Tunnels have a concurrent-request limit (documented by Cloudflare as 200 in-flight requests), which is irrelevant for one fetch.
- **Polling window:** Meta downloads the file before processing it, so larger videos get more status polls: 5 + ⌈(size − 50 MB) / 25 MB⌉ polls, one per minute, capped at `INSTAGRAM_MAX_POLL_MINUTES` (default 15). The floor is Meta's documented 5 (131.9 MB → 9 minutes). If Instagram is still `IN_PROGRESS` at the end, the attempt ends and the tunnel closes; the job resumes later by container id, and Meta must already have the file by then.

- **Real test (2026-09-25):**
  - Direct tunnel test ✅: `videos/0612.mp4` (131,925,281 bytes). Public GET 200 `video/mp4` (server: cloudflare), exact Content-Length, SHA-256 identical, Range 206, other paths 404, ~10.5 MB/s. Tunnel ready 19 s after start. After cleanup: no cloudflared process, the URL returns 502, the source is untouched.
  - **Real Instagram publish through the tunnel ✅ (2026-09-25):** Create Post (AUTO) → `videos/0612.mp4` (131.9 MB) → Cloudflare Quick Tunnel → Reel on noxivra_01. Post 8 / job 9, media id 18155048788569324, https://www.instagram.com/reel/DduAxFSgaZt/, 84 s end to end. Link saved to `instagram.json`; no cloudflared left; tunnel URL in no log, DB or link file; source unchanged (same SHA-256).

### Provider: TempFile.org (`MEDIA_STORAGE_PROVIDER=tempfile`): PUBLIC temporary host, **real-tested ✅**
Verified 2026-09-25 from the service's OpenAPI description (`https://tempfile.org/openapi.json`, API v2.2.0) and real requests:

| Item | Verified behaviour |
|------|--------------------|
| Upload | `POST https://tempfile.org/api/upload/local`, `multipart/form-data`, field `files` (+ `expiryHours`); **no authentication** |
| Expiry | `expiryHours` ∈ **{1, 6, 24, 48}** (default 1); the file is deleted automatically afterwards |
| Response | `200 {"success": true, "files": [{"id", "name", "size", "url", "expiryTime"}], "message"}`. `url` (`https://tempfile.org/<id>/`) is an **HTML landing page**, not the file |
| Direct file | `GET https://tempfile.org/<id>/download`: real test served `Content-Type: video/mp4`, correct `Content-Length`, byte-identical bytes. `Accept-Ranges` is advertised but a `Range` request returned the full file (200) |
| Delete | `DELETE https://tempfile.org/api/file/<id>` → `200 {"success": true}`, then 404 (real-tested). **No auth: anyone who knows the id can delete or download**, so the id is treated as a secret |
| Limits | 100 MB per file; uploads 200 requests/hour (`X-RateLimit-*` headers); 20 files per request; downloads 5000 per 15 min |
| Errors | 400 invalid, **403 "Access denied from this IP address"**, **413** too large, **429** rate limit, 500 |
| Rules | no malware, **copyrighted content without permission**, adult material, **automated bulk uploading or spam** |

**What Soc_bot does:**
- **Upload:** user agent `Soc_bot/1.0 (+repo)`; generic filename `video.mp4`; `expiryHours=MEDIA_STORAGE_TEMPFILE_EXPIRY_HOURS` (default 1); the file is streamed.
- **Validation:** the file id must match the documented format (11 Base58 characters or legacy `file_<ts>_<rand>`), and the response's landing URL must be https on `tempfile.org`. The media URL is then **built** as `https://tempfile.org/<id>/download`; nothing from the response is passed to Instagram verbatim. A `HEAD` request checks status 200 and the exact `Content-Length`, and warns if the served type isn't `video/*`.
- **Secrecy:** the id and URL are kept only in `MediaHandle` (hidden from `repr`), never logged or stored. Real test: no `tempfile.org` string in the database.
- **Cleanup:** `DELETE` after the attempt (success, failure, Ctrl+C); a 404 means it's already gone. A failed delete is logged; the file then expires within `expiryHours`.
- **Errors:** 429, 5xx, timeouts and network errors are retryable; 403, 413 and 400 are not. Provider messages are redacted and truncated.
- **Settings check:** `GET /openapi.json` only, so it reports "service reachable; no credentials required" and says plainly that a real upload is only confirmed by publishing.

**Real test (2026-09-25):** Create Post → `videos/test_youtub.mp4` (10,060,970 bytes) → Instagram `noxivra_01` → TempFile upload (5.6 s in the direct test) → container → FINISHED → `media_publish` → TempFile copy deleted. Job 6 is **published**, media id `17981564450901718`, confirmed via Graph API as `REELS`: https://www.instagram.com/reel/DdttFw8CqnI/. No URL prompt; the local file is unchanged.

**Limitations:**
- The video is publicly downloadable while it exists, and the operator's terms apply.
- Not suitable for AUTO or bulk runs: the terms forbid automated bulk uploading, and the rate limit is 200 uploads per hour.
- Max 100 MB (Instagram allows 300 MB).
- No availability guarantee; it may deny IPs (403).

### Provider status
| Provider | Status |
|----------|--------|
| `tempfile` | ✅ working, real Instagram Reel published |
| `0x0` | implemented and unit-tested, but **0x0.st currently has uploads disabled** (reported by the user from a direct curl test, 2026-09-25) |
| `s3` | implemented and unit-tested; real run needs a bucket (the most private and controllable option) |

To switch back to private storage: `MEDIA_STORAGE_PROVIDER=s3` + the `MEDIA_STORAGE_BUCKET/ACCESS_KEY/SECRET_KEY` (+ REGION or ENDPOINT) settings.

### Provider: 0x0.st (`MEDIA_STORAGE_PROVIDER=0x0`): PUBLIC temporary host (uploads currently disabled by the service)
Verified 2026-09-25 from https://0x0.st (the service's own documentation page):

| Item | Verified behaviour |
|------|--------------------|
| Upload | `POST https://0x0.st`, `multipart/form-data`, field `file`; optional `secret` (a longer, hard-to-guess URL) and `expires` (maximum lifetime in **hours**, or ms since the epoch) |
| Response | body = the file URL; header `X-Token` = management token (**only when the file didn't already exist**) |
| Delete | `POST <file URL>` with `token=<X-Token>` and `delete=` |
| Change expiry | `POST <file URL>` with `token`, `expires` |
| Max size | 512 MiB |
| Retention | 30 days to 1 year depending on size (`min_age + (min_age - max_age) * (size/max_size - 1)^3`) unless `expires` is shorter; expired files are removed "within the next minute" |
| Client rules | use a user agent that uniquely identifies the program; browser impersonation is detected and blocked (fetch tools got **HTTP 418**); tell users clearly it's a public host "run by some Internet rando in Germany" with no control or privacy guarantees |
| Terms (not allowed) | piracy, pornography/gore, "AI slop", extremist/terrorist content, malware, crypto, **backups**, CI artifacts, **"other automated mass uploads"**, doxxing, anything illegal under German law. Violations are removed and the IP may be blocked |
| Privacy | stores the uploader's **IP address and user agent** with each file for moderation |
| Rate limits | no numeric limits are published |

**What Soc_bot does:**
- **Upload:** user agent `Soc_bot/1.0 (+https://github.com/ayushrijal83-ops/Soc_bot)`; generic filename `video.mp4` (the local name never leaves the machine); a `secret` URL; `expires=MEDIA_STORAGE_0X0_EXPIRES_HOURS` (default **1**, allowed 1–24). The file is streamed, not loaded into memory.
- **URL check:** the returned URL must be https, on the configured instance host, and not localhost or a private IP. A `HEAD` request then confirms it serves the file; if not, the upload is deleted and the attempt is retryable.
- **Token:** kept only in memory (`MediaHandle`, hidden from `repr`), never logged or stored in the database.
- **Cleanup:** after the attempt (published, failed or Ctrl+C), `POST token+delete`. If 0x0.st returned no token (an identical file already existed), early deletion isn't possible and the file expires on 0x0.st's schedule; Soc_bot logs this. A failed delete is logged and never changes the Instagram result.
- **Errors:** 418/403 mean blocked (not retried); 413 means too large; 5xx, 429, timeouts and network errors are retryable. Response bodies are never echoed.
- **Settings check:** Settings → Check Instagram media storage runs `GET https://0x0.st` only, so it detects outages or blocking but **can't confirm uploads will be accepted**, because the service is anonymous; the check says so.

**Limitations / responsibility:**
- The video **leaves the computer** and is **publicly downloadable** by anyone with the link until deleted or expired. 0x0.st keeps the uploader's IP and user agent. Soc_bot warns about this in Create Post and in every plan.
- Only upload clips you have the right to share. 0x0.st, like Instagram, forbids piracy.
- Its Terms forbid "automated mass uploads", so **don't combine 0x0.st with AUTO mode** or bulk publishing; use `s3` for unattended runs.
- It is a single-operator free service with no availability guarantee; it may block clients or IPs at any time.

**Alternative:** `MEDIA_STORAGE_PROVIDER=s3` (a private bucket you control, presigned URLs, no third party).

### Configuration (`.env`)
| Variable | Meaning |
|----------|---------|
| `MEDIA_STORAGE_PROVIDER` | `auto` (**recommended**: TempFile for small videos, S3 for large ones), `s3` (default when unset: AWS S3, Cloudflare R2, MinIO), `tempfile` or `0x0` (public temporary hosts, see above) |
| `MEDIA_STORAGE_TEMPFILE_EXPIRY_HOURS` | 1, 6, 24 or 48, default 1 (`tempfile` and `auto`) |
| `MEDIA_STORAGE_AUTO_MARGIN_MB` | safety margin below a provider's size limit, default 1 (only for `auto`) |
| `MEDIA_STORAGE_0X0_URL` | 0x0 instance, default `https://0x0.st` (only for `0x0`) |
| `MEDIA_STORAGE_0X0_EXPIRES_HOURS` | maximum lifetime of the upload, 1–24, default 1 (only for `0x0`) |
| `MEDIA_STORAGE_BUCKET` | private bucket (no public access, no ACLs) |
| `MEDIA_STORAGE_REGION` | AWS region (e.g. `us-east-1`); for R2 use `auto` or leave empty with an endpoint |
| `MEDIA_STORAGE_ENDPOINT` | empty for AWS; `https://<account>.r2.cloudflarestorage.com` for R2; MinIO URL (**must be https**, since Meta only fetches HTTPS) |
| `MEDIA_STORAGE_ACCESS_KEY` / `MEDIA_STORAGE_SECRET_KEY` | credentials limited to that bucket (Put/Get/Delete object, and ListBucket for the health check) |
| `MEDIA_STORAGE_PRESIGNED_URL_TTL` | seconds, 60–604800, default 900 (must cover Meta's download/processing) |
| `MEDIA_STORAGE_DELETE_AFTER_PUBLISH` | `true` (default) deletes the object after each attempt |

Recommended: a **bucket lifecycle rule** expiring `instagram/temp/` after 1 day, which catches objects left behind by a crash. Check the setup with **Settings → Check Instagram media storage**, which calls `head_bucket` only and uploads nothing. Without configuration, Instagram destinations are blocked in the plan with a clear message.

### Dry-run
The plan shows "media delivery: <provider> (uploaded only when publishing)". Planning and `--dry-run` make no storage call, create no URL and never contact Instagram.

### YouTube "Soc_bot Media Hub" (optional)
A dedicated YouTube channel can serve as an **archive, backup and debugging hub**. Connect it like any YouTube account (Connected Accounts → Connect YouTube) and add it to the publishing profile or select it in Create Post; it's then just an independent YouTube destination. It is **not** a media source for Instagram (see above). The channel is created manually on YouTube; Soc_bot doesn't create channels and assumes no channel ID. YouTube and Instagram jobs for the same local file stay independent; neither waits for the other.

### Status
- Implemented and unit-tested with a fake S3 client and fake providers: 39 tests.
- **Real Instagram publish: ✅ DONE with `tempfile`** (see the TempFile section). `s3` has not had a real run yet.

## Instagram Setup & Diagnosis (Phase 5C, 2026-09-25)

### Why authorization showed "Sorry, this page isn't available."
**Root cause (verified):** `.env` `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` held the credentials of the **Meta (Facebook) app "Soc_bot"**, not its **Instagram App ID/secret**.
- `GET graph.facebook.com/v25.0/<INSTAGRAM_APP_ID>` with `app_id|app_secret` as the token returned the app `name = "Soc_bot"`, so it is a Meta app ID + secret.
- `POST api.instagram.com/oauth/access_token` with the same ID returned **`Invalid platform app`**, identical to the response for a made-up ID. Instagram doesn't recognise that `client_id`, so `instagram.com/oauth/authorize` shows "this page isn't available".

**Second problem (code):** with `INSTAGRAM_REDIRECT_URI` empty, Soc_bot sent a random-port `http://127.0.0.1:<port>/callback/instagram`. Meta only redirects to a URI registered **exactly** in Business login settings, so this could never work.

**Third problem (code):** the token response's `permissions` can be a JSON list, while the docs show a string. Scope parsing expected a string and would have crashed after a successful exchange.

### Required Meta dashboard configuration (existing app "Soc_bot", use case "Manage messaging & content on Instagram")
1. **App Dashboard → Instagram → API setup with Instagram login.** Copy the **Instagram app ID** and **Instagram app secret** shown there into `.env` as `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET`. (They differ from App settings → Basic "App ID/App secret".)
2. **Permissions:** `instagram_business_basic`, `instagram_business_content_publish`. No Facebook permissions, no Page, no Business Portfolio.
3. **Step "Set up Instagram business login" → Business login settings → OAuth redirect URIs:** add the redirect URI, then put **the identical string** in `.env` as `INSTAGRAM_REDIRECT_URI`:
   - **Recommended (paste mode):** `https://ayushrijal83-ops.github.io/Soc_bot/oauth/instagram-callback.html` (a static page in this repo; it must be pushed and live first)
   - Alternative (local mode, only if the dashboard accepts http): `http://127.0.0.1:8765/callback/instagram`
   - After saving, check the list: the dashboard may add a trailing slash. `.env` must match what's listed.
4. **Account access (Standard Access):** Meta: Standard Access serves "accounts you own/manage that you've added to your app". Add @nopex_12b to the app, via the "Generate access tokens → Add account" step on the same page or **App roles → Roles → Instagram Testers**. If it's added as a tester, accept the invite in Instagram (Settings → Website permissions / Apps and websites → Tester invites). NOT VERIFIED from Meta's docs: the exact UI labels (the pages couldn't be fetched in full).
5. **App mode:** Development mode is expected to work for accounts with a role on the app (Standard Access); Live mode + App Review (Advanced Access) is needed only for accounts you don't own or manage. NOT VERIFIED in Meta's docs for this exact use case; community reports only.

### Uncertainties (explicit)
- Whether Business login settings accept `http://127.0.0.1` redirect URIs is **not stated** in Meta's docs; community reports say https is required. That's why paste mode with an https page exists.
- The Instagram Tester UI path is from Meta's dashboard and community knowledge, not from a fetched official page.

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
- **Redirect URI:** must exactly match a registered OAuth redirect URI (enforced by Soc_bot since Phase 5C: no fixed `INSTAGRAM_REDIRECT_URI` means a clear error before the browser opens). Two modes:
  - **Paste mode** (any registered `https://` non-loopback URI, e.g. the GitHub Pages callback page): Soc_bot opens the browser, you approve, the browser lands on the page, and you paste its full address into Soc_bot. Soc_bot checks the base URI, the `error` parameter, the code, and that the state is single-use, unexpired and issued for Instagram, then exchanges the code (stripping `#_`).
  - **Local mode** (registered `http://127.0.0.1:<port>/callback/instagram`): the existing loopback callback server on that fixed port.
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
- **Upload method:** with Instagram Login, video publishing is documented only via `video_url`. Meta's docs list the resumable `rupload.facebook.com` upload for **Facebook Login only** (NOT VERIFIED whether it works with Instagram Login tokens, so it isn't used). Soc_bot therefore delivers the local file through temporary private storage and a presigned HTTPS URL (see "Instagram Media Delivery" above); the user never enters a URL.
- **Polling:** Meta recommends checking status "once per minute, for no more than 5 minutes". Soc_bot polls every 60 s, 5 times. If the container is still `IN_PROGRESS`, the job stays `processing` and is re-checked later; this is not a failure.
- **Containers** expire after 24 h (`EXPIRED`). Soc_bot then creates a new container on the next attempt.
- **Rate limit:** 100 API-published posts per account per 24-hour moving window (`GET /{ig_user_id}/content_publishing_limit` shows usage; not called by Soc_bot).
- **Caption:** max 2,200 characters, 30 hashtags, 20 @ tags.
- **Reels video spec:** MOV or MP4; HEVC or H.264; 23–60 FPS; max 1920 px wide; aspect ratio 0.01:1 to 10:1 (9:16 recommended); 3 s to 15 min; ≤ 300 MB; AAC audio ≤ 48 kHz. Soc_bot checks size, container and caption locally; it checks duration and width only when ffprobe is installed.
- **Errors:** Graph error JSON `{"error": {"message", "code", "is_transient", "fbtrace_id"}}`. Soc_bot maps `is_transient`, 429 and 5xx to retryable; code 190 / 401 to `unauthorized`; codes 10 and 200 / 403 to `permission_denied`; codes 4, 9, 17, 32 and 613 to `rate_limited` (retryable).
- **Token:** a long-lived token (~60 days) renewed with `ig_refresh_token`. The engine renews it when it expires within 7 days, and continues if renewal is refused but the token is still valid.

### Known Limitations
- Needs `MEDIA_STORAGE_*` configured (a private S3/R2/MinIO bucket). Soc_bot uploads the file temporarily and deletes it after the attempt.
- Personal Instagram accounts are not supported (Business/Creator only)
- Only Reels are implemented (no images, carousels or stories)

### Current Implementation Status
OAuth and publishing implemented. **Mocked tests verified. Real provider OAuth and publishing NOT verified (NOT RUN).**

---

## TikTok

## TikTok Setup (Phase 5B)

1. **Developer app** at https://developers.tiktok.com/ → Manage apps → create an app.
2. **Products:**
   - **Login Kit**, platform **Desktop**. Redirect URI: exactly `http://127.0.0.1:*/callback/tiktok` (wildcard port; Soc_bot picks a free port per login).
   - **Content Posting API** with **Direct Post** enabled.
3. **Scopes:** `user.info.basic`, `video.publish`. Soc_bot requests only these (least privilege; `video.upload` is the separate inbox/draft flow and isn't used).
4. **Credentials:** put the Client key / Client secret in `.env` as `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`. Leave `TIKTOK_REDIRECT_URI` blank.
5. **Test account:** while the app is **unaudited**, TikTok restricts posts to private viewing (`SELF_ONLY`), and the posting account should itself be **private** (error `unaudited_client_can_only_post_to_private_accounts`). Add it as a target/test user if TikTok asks.
6. **Connect:** `python main.py` → Connected Accounts → Connect TikTok. The browser opens TikTok's consent page. Afterwards Soc_bot shows the account ID, name and **granted scopes**, and warns if `video.publish` is missing.
7. **Profile:** Settings → Create/Edit Publishing Profile → select the TikTok account, privacy `SELF_ONLY`, mode VERIFY.
8. **Publish:** Content Inbox → pick the package → the verification screen queries **creator_info** (nickname, allowed privacy levels, max duration) → confirm once.

Audit: to post publicly, TikTok must audit the app (content-sharing UX guidelines: show the creator's nickname and let the user pick from `privacy_level_options` with no default; Soc_bot's VERIFY screen shows the creator info and the chosen privacy). Until then only `SELF_ONLY` works.

### Phase 5B findings (docs re-verified 2026-09-25)
- The docs match the implementation: Login Kit for Desktop (hex S256 PKCE, comma-separated scopes, loopback redirect with wildcard port); Content Posting API creator_info → video/init (FILE_UPLOAD) → sequential PUT chunks (206/201) → status/fetch.
- **Bug fixed:** v2 API responses always carry `"error": {"code": "ok", ...}` on success. The OAuth user-info lookup treated any `error` field as a failure, so **every real TikTok connection would have failed** after the token exchange. Fixed with `_tiktok_error()` (string errors from OAuth endpoints, object errors from v2 endpoints), plus a regression test.
- **creator_info before confirmation:** the VERIFY screen now calls the read-only `preflight()` (TikTok: creator_info) and blocks the destination if the profile's privacy isn't in `privacy_level_options` or the video exceeds `max_video_post_duration_sec`. Dry-run and the inbox list make no calls.
- **Granted scopes** are stored per account (`accounts.meta_json`). An account without `video.publish` is blocked with "missing required permission: video.publish".

### Official API
- **Name:** TikTok Login Kit + Content Posting API
- **Documentation:** https://developers.tiktok.com/doc/login-kit-desktop, https://developers.tiktok.com/doc/oauth-user-access-token-management
- **API Version:** v2
- **Platform Requirements:** TikTok Developer Account; Content Posting API access requires approval

### OAuth Flow — docs verified 2026-09-25 · tested with mocks · real OAuth NOT RUN
- **Product:** Login Kit for Desktop
- **Authorization URL:** `https://www.tiktok.com/v2/auth/authorize/` (`client_key`, `scope`, `response_type=code`, `redirect_uri`, `state`, `code_challenge`, `code_challenge_method=S256`)
- **Scopes (comma-separated):** `user.info.basic`, `video.publish` (Phase 5B: `video.upload` removed; Direct Post doesn't use it)
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
| TikTok | Login Kit for Desktop | tiktok.com/v2/auth/authorize/ | open.tiktokapis.com/v2/oauth/token/ | refresh_token grant, rotation persisted | user.info.basic, video.publish | S256, **hex** | 127.0.0.1, wildcard port | open_id | ✅ 2026-09-25 | ✅ | NOT RUN |
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
| Instagram | IG User `/media` reference: `cover_url` "For Reels only. The path to an image to use as the cover image for the Reels tab" (Meta fetches it from a public server). Reels cover spec: **JPEG, ≤ 8 MB, sRGB, 9:16 recommended**. `cover_url` takes precedence over `thumb_offset`. The Instagram Login publishing guide doesn't list it | **Supported**: sent as `cover_url` on the Reel container, served by the media provider next to the video (Cloudflare Quick Tunnel). Validated before confirmation (JPEG structure, ≤ 8 MB); an invalid cover **blocks** the destination. Works with Instagram Login tokens on graph.instagram.com v25.0: real-verified 2026-09-26 by API readback: the Reel's `thumbnail_url` is the chosen cover (TEST 1 https://www.instagram.com/reel/DdvCpWvEspn/, TEST 2 https://www.instagram.com/reel/DdvDAyhCGzR/, both thumbnails byte-identical); a Reel without `cover_url` shows a video frame |

## Instagram multi-account fan-out (one video + one cover → many accounts)

- **Batch = post.** One post (video, caption, optional `cover_path` in each job's options) has one independent `publish_jobs` row per account. No new table. The aggregate status is computed as pending / running / completed / completed_with_failures / failed.
- **Concurrency:** `PublisherEngine.publish_post` runs a post's Instagram jobs in a thread pool of `INSTAGRAM_MAX_CONCURRENT_PUBLISHES` (default **5**, allowed 1..20). The rest wait and start as workers free up; retry waits keep their slot, so active jobs never exceed the limit. YouTube/TikTok jobs stay sequential.
- **One cover for all:** every job's options point at the **same local file**; it is never copied or converted. Each active job serves it under its **own** random path on its **own** local server + tunnel (the video and cover share that job's single tunnel).
- **Isolation:** own media session, container, retries and result per job; one failure (or an unexpected exception) never stops the others. A cleanup warning never turns a published Reel into a failure.
- **Duplicates:** same video + same account needs an explicit "publish again" (asked once for all such accounts, default No); same video + different accounts is allowed. The DB still forbids the same account twice in one post.
- **Resume:** published and failed jobs are never started again; pending/retrying/processing jobs continue (processing resumes by container id). Ctrl+C: queued jobs stay pending, and running jobs finish their step and clean up.
- **Links:** one `instagram.json` record per successful account (writes are serialized with a lock); temporary video/cover URLs are never stored anywhere.
- **Bandwidth:** Cloudflare Tunnel is a proxy, not storage: each account's fetch transfers the file again (131.9 MB × 50 accounts ≈ 6.6 GB outbound). The confirmation shows the estimate.
- **Routing with a cover:** a provider that can't also serve the cover is never chosen (`supports_cover`; only the Cloudflare Quick Tunnel has it today). So a small video + cover goes through the tunnel, not TempFile. S3 and TempFile covers aren't implemented; with a cover and no cloudflared, the plan is BLOCKED.

## Real Provider Status (updated 2026-09-25)

| Platform | Real OAuth | Real publishing | Real cover |
|----------|------------|-----------------|------------|
| YouTube | ✅ 2 channels | ✅ (user upload + Phase 5A private regression) | ✅ thumbnail published |
| TikTok | NOT RUN (developer app / credentials not yet configured) | NOT RUN | n/a (not supported) |
| Instagram | NOT RUN (Phase 5C: root cause found = Meta App ID used instead of Instagram App ID; code fixed; awaiting correct `.env` + registered redirect) | NOT RUN | n/a (not supported) |
