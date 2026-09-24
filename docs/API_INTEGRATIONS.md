# API Integrations

## Overview

This document tracks the official API specifications, OAuth flows, and integration requirements for each target platform. All information must be verified against **official documentation** before implementation.

---

## Instagram

### Official API
- **Name:** Instagram Graph API (part of Meta Business Platform)
- **Documentation:** https://developers.facebook.com/docs/instagram-api/
- **API Version:** v21.0 (as of 2024)
- **Platform Requirements:** Meta Business Account, Instagram Business/Creator Account linked to Facebook Page

### OAuth Flow
- **Type:** OAuth 2.0 (Authorization Code Grant with PKCE)
- **Authorization URL:** `https://www.facebook.com/v21.0/dialog/oauth`
- **Token URL:** `https://graph.facebook.com/v21.0/oauth/access_token`
- **Scopes Required:**
  - `instagram_graph_user_profile` — Basic profile
  - `instagram_graph_user_media` — Media publishing
  - `pages_show_list` — List Facebook Pages
  - `pages_read_engagement` — Page insights
- **Redirect URI:** Must match exactly in Meta App Dashboard
- **Account Linking:** User must link Instagram Business/Creator account to Facebook Page

### Required Credentials
| Credential | Source |
|------------|--------|
| App ID | Meta App Dashboard → Settings → Basic |
| App Secret | Meta App Dashboard → Settings → Basic |
| Redirect URI | Configured in Meta App Dashboard → Products → Instagram → Basic Display |

### Publishing Workflow
1. **Container Creation** — POST `/<IG_USER_ID>/media` with `media_type=VIDEO`, `video_url` (publicly accessible URL), `caption`
2. **Status Polling** — GET `/<CONTAINER_ID>?fields=status_code` until `FINISHED`
3. **Publish** — POST `/<IG_USER_ID>/media_publish` with `creation_id=<CONTAINER_ID>`
4. **Result** — Returns media ID on success

### Media Requirements (UNVERIFIED - Verify Before Implementation)
| Property | Requirement |
|----------|-------------|
| Format | MP4, MOV |
| Codec | H.264 |
| Max File Size | 4 GB |
| Max Duration | 60 minutes (Reels: 90 seconds) |
| Aspect Ratio | 9:16 (Reels), 4:5, 1:1, 1.91:1 |
| Frame Rate | ≤ 30 fps |
| Audio | AAC |

### Rate Limits (UNVERIFIED)
- 200 calls/hour per user (Graph API)
- Media publish: ~25/day per account (estimated)

### Known Limitations
- Requires publicly accessible video URL (not direct upload)
- Must use Facebook Page as intermediary
- Personal Instagram accounts not supported (Business/Creator only)
- Reels vs Feed post distinction via media_type

### Current Implementation Status
📋 **PLANNED** — No code written

---

## TikTok

### Official API
- **Name:** TikTok Creator API / TikTok Content Posting API
- **Documentation:** https://developers.tiktok.com/doc/content-posting-api-getting-started/
- **API Version:** v2 (as of 2024)
- **Platform Requirements:** TikTok Developer Account, approved Creator API access

### OAuth Flow
- **Type:** OAuth 2.0 (Authorization Code Grant with PKCE)
- **Authorization URL:** `https://www.tiktok.com/v2/auth/authorize/`
- **Token URL:** `https://open.tiktokapis.com/v2/oauth/token/`
- **Scopes Required:**
  - `video.upload` — Upload videos
  - `video.publish` — Publish videos
  - `user.info.basic` — Basic user info
- **Redirect URI:** Must match exactly in TikTok Developer Portal
- **Client Key / Secret:** From TikTok Developer Portal

### Required Credentials
| Credential | Source |
|------------|--------|
| Client Key | TikTok Developer Portal → App Details |
| Client Secret | TikTok Developer Portal → App Details |
| Redirect URI | Configured in TikTok Developer Portal |

### Publishing Workflow (Content Posting API)
1. **Initialize Upload** — POST `/v2/post/publish/video/init/` with `source_info` → returns `upload_url`
2. **Upload Video** — PUT video binary to `upload_url`
3. **Publish** — POST `/v2/post/publish/video/create/` with `video_id`, `caption`, `privacy_level`
4. **Status Polling** — GET `/v2/post/publish/status/fetch/` with `publish_id`

### Media Requirements (UNVERIFIED - Verify Before Implementation)
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

### Rate Limits (UNVERIFIED)
- 1000 requests/day per app (Creator API)
- Video upload: varies by account tier

### Known Limitations
- Creator API access requires approval (not automatic)
- Personal accounts may have limited API access
- Video must be uploaded to TikTok's URL first (not direct post)
- Draft vs Direct post options
- Commercial Content Library restrictions

### Current Implementation Status
📋 **PLANNED** — No code written

---

## YouTube

### Official API
- **Name:** YouTube Data API v3
- **Documentation:** https://developers.google.com/youtube/v3
- **API Version:** v3
- **Platform Requirements:** Google Cloud Project, YouTube Data API v3 enabled, OAuth consent screen configured

### OAuth Flow
- **Type:** OAuth 2.0 (Authorization Code Grant with PKCE)
- **Authorization URL:** `https://accounts.google.com/o/oauth2/v2/auth`
- **Token URL:** `https://oauth2.googleapis.com/token`
- **Scopes Required:**
  - `https://www.googleapis.com/auth/youtube.upload` — Upload videos
  - `https://www.googleapis.com/auth/youtube` — Full channel management
  - `https://www.googleapis.com/auth/youtube.readonly` — Read access
- **Redirect URI:** Must match exactly in Google Cloud Console
- **Client ID / Secret:** From Google Cloud Console → Credentials

### Required Credentials
| Credential | Source |
|------------|--------|
| Client ID | Google Cloud Console → APIs & Services → Credentials |
| Client Secret | Google Cloud Console → APIs & Services → Credentials |
| Redirect URI | Configured in Google Cloud Console |

### Publishing Workflow
1. **Resumable Upload Init** — POST `/upload/youtube/v3/videos?part=snippet,status` with metadata → returns `upload_url`
2. **Upload Video** — PUT video binary to `upload_url` (resumable, chunked)
3. **Status Polling** — Poll upload URL for completion
4. **Result** — Returns video ID on success

### Media Requirements (UNVERIFIED - Verify Before Implementation)
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

### Rate Limits (UNVERIFIED)
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
📋 **PLANNED** — No code written

---

## Verification Checklist

Before implementing any platform adapter, verify:

- [ ] Official documentation URL is current
- [ ] OAuth flow matches current spec
- [ ] Required scopes are accurate
- [ ] Media requirements are up to date
- [ ] Rate limits are documented
- [ ] Publishing workflow steps are correct
- [ ] Error codes and handling are understood
- [ ] Account type requirements are met (Business/Creator/Personal)

**Mark each item VERIFIED when confirmed against official docs.**