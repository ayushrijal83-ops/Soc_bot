# Troubleshooting

## Structure

Each entry follows:
- **Problem** — User-visible symptom
- **Symptoms** — Error messages, behaviors
- **Root Cause** — Why it happens
- **Solution** — Steps to fix
- **Affected Files** — Where to look
- **How to Reproduce** — Steps to trigger
- **How to Verify** — Confirm fix works

---

## OAuth / Authentication Issues

### Problem: "Redirect URI mismatch" during OAuth

**Symptoms:**
- Browser shows platform error page: "Redirect URI does not match"
- Callback server receives no request

**Root Cause:**
- Redirect URI in `.env` doesn't match platform developer dashboard exactly
- Trailing slash difference, http vs https, port mismatch

**Solution:**
1. YouTube: use a "Desktop app" OAuth client and leave `YOUTUBE_REDIRECT_URI` blank (dynamic `http://127.0.0.1:<port>/callback/youtube`)
2. TikTok: register `http://127.0.0.1:*/callback/tiktok` (wildcard port) and leave `TIKTOK_REDIRECT_URI` blank
3. Instagram: set `INSTAGRAM_REDIRECT_URI` to exactly the URI registered in Business login settings (format `http://<host>:<port>/callback/instagram`)
4. Include/exclude trailing slash consistently

**Affected Files:** `.env`, platform developer dashboards

**How to Reproduce:** Start OAuth with mismatched URI

**How to Verify:** OAuth completes, tokens stored, account appears in "Connected Accounts"

---

### Problem: "Invalid state parameter" on callback

**Symptoms:**
- Callback server logs "Invalid state"
- Browser shows error page

**Root Cause:**
- CSRF protection: state parameter generated != state received
- Multiple OAuth attempts overlapping
- Browser cache/cookies

**Solution:**
1. Only one OAuth flow at a time
2. Clear browser cookies for platform
3. Restart CLI, retry

**Affected Files:** `src/platforms/*/auth.py`, callback server

**How to Reproduce:** Start OAuth, don't complete, start again

**How to Verify:** Single OAuth flow completes successfully

---

### Problem: Token refresh fails with "invalid_grant"

**Symptoms:**
- Account status shows `revoked` or `expired`
- Publishing fails with auth error
- Refresh attempt logged as failed

**Root Cause:**
- Refresh token expired (Instagram: 60 days)
- User revoked access in platform settings
- Platform rotated refresh token, old one invalid

**Solution:**
1. User must reconnect account via "Connected Accounts" → "Reconnect"
2. Full OAuth flow required
3. Old tokens automatically cleaned up

**Affected Files:** `src/accounts/manager.py`, `src/platforms/*/auth.py`

**How to Reproduce:** Wait for token expiry, or revoke in platform settings

**How to Verify:** Reconnect works, new tokens stored, publishing succeeds

---

## Publishing Issues

### Problem: "Video too large" / Upload fails

**Symptoms:**
- Validation passes but upload fails
- Platform returns 413 or size error

**Root Cause:**
- Video exceeds platform limit
- Validation used wrong limit
- File size miscalculated

**Solution:**
1. Check platform limits in `API_INTEGRATIONS.md`
2. Compress video or split
3. Update validation limits if platform changed

**Affected Files:** `src/core/validation.py`, platform adapters

**How to Reproduce:** Try uploading >4GB video to Instagram/TikTok

**How to Verify:** Validation rejects oversized video before API call

---

### Problem: Video stuck in "PROCESSING" state

**Symptoms:**
- Job status: `processing` for extended time
- No error, no completion

**Root Cause:**
- Platform processing delay (YouTube: hours)
- Polling interval too aggressive
- Platform internal error

**Solution:**
1. Increase polling interval (YouTube: 5 min)
2. Add max processing timeout (configurable)
3. Allow manual "check status" in CLI

**Affected Files:** `src/core/publisher.py`, platform adapters

**How to Reproduce:** Upload large video to YouTube

**How to Verify:** Job eventually reaches `published` or `failed` with clear error

---

### Problem: Partial publish — some accounts succeed, some fail

**Symptoms:**
- "3 of 4 accounts published"
- One job shows `failed`

**Root Cause:**
- Per-account issues: quota, permissions, token expiry
- Platform-specific limits

**Solution:**
1. Check failed job error message
2. Retry individual job from "Publishing Queue"
3. Fix underlying issue (reconnect account, wait for quota reset)

**Affected Files:** `src/core/jobs.py`, `src/cli/menu.py`

**How to Reproduce:** Publish to accounts where one has exhausted quota

**How to Verify:** Failed job can be retried independently; successes unchanged

---

## Database Issues

### Problem: "Database is locked" / SQLite busy

**Symptoms:**
- `sqlite3.OperationalError: database is locked`
- Occurs on concurrent access

**Root Cause:**
- Multiple processes accessing DB (CLI + background job)
- Long transaction holding lock

**Solution:**
1. Enable WAL mode: `PRAGMA journal_mode=WAL;`
2. Use connection pooling / single connection
3. Short transactions, commit quickly
4. Retry with backoff on busy

**Affected Files:** `src/storage/database.py`

**How to Reproduce:** Run two CLI instances simultaneously

**How to Verify:** Concurrent reads work; writes serialize cleanly

---

### Problem: Migration fails / schema mismatch

**Symptoms:**
- App crashes on startup with schema error
- Column missing, table missing

**Root Cause:**
- Migration not run
- DB corrupted
- Version mismatch

**Solution:**
1. Run migration: `python -m src.storage.database migrate`
2. If corrupted: backup, delete DB, re-run migrations
3. Check `schema_version` table

**Affected Files:** `src/storage/database.py`, `src/storage/migrations/`

**How to Reproduce:** Upgrade code without running migrations

**How to Verify:** App starts, all tables exist, version matches

---

## Configuration Issues

### Problem: "ENCRYPTION_KEY not set" / Decryption fails

**Symptoms:**
- Startup error: missing encryption key
- `InvalidToken` when reading tokens

**Root Cause:**
- `.env` missing `ENCRYPTION_KEY`
- Key changed since tokens encrypted
- Key format invalid (not 32-byte base64)

**Solution:**
1. Generate key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Add to `.env`
3. If key changed: re-authenticate all accounts (old tokens unrecoverable)

**Affected Files:** `.env`, `src/storage/tokens.py`

**How to Reproduce:** Delete ENCRYPTION_KEY from .env

**How to Verify:** App starts, can read/write tokens

---

### Problem: Callback server fails to start

**Symptoms:**
- `OAuthCallbackError: Could not start OAuth callback server on <host>:<port>`
- OAuth never completes

**Root Cause:**
- A fixed `*_REDIRECT_URI` or `OAUTH_CALLBACK_PORT` points at a port another process already holds
- The default dynamic port (0) should not normally conflict

**Solution:**
1. Leave `OAUTH_CALLBACK_PORT` unset (dynamic) and `*_REDIRECT_URI` blank where the platform allows it
2. For a fixed Instagram redirect URI, free that port or register a different one
3. The callback server times out (300 s) and always shuts down after each flow

**Affected Files:** `src/auth/callback_server.py`, `src/auth/manager.py`, `.env`

**How to Reproduce:** Start OAuth, kill CLI, start again

**How to Verify:** OAuth callback completes, server shuts down

---

## Platform-Specific Issues

### Instagram: "Instagram account not linked to Facebook Page"

**Symptoms:**
- OAuth succeeds but no Instagram account found
- "No Instagram Business Account" error

**Root Cause:**
- User has personal Instagram, not Business/Creator
- Instagram not linked to Facebook Page

**Solution:**
1. Convert to Business/Creator account in Instagram app
2. Link to Facebook Page in Meta Business Suite
3. Re-run OAuth

**Affected Files:** `src/platforms/instagram/auth.py`

**How to Reproduce:** Connect personal Instagram account

**How to Verify:** Account appears with `@username` in Connected Accounts

---

### TikTok: "Creator API access not approved"

**Symptoms:**
- OAuth succeeds but video upload returns 403
- "Permission denied" on publish

**Root Cause:**
- TikTok Creator API requires app review/approval
- Personal account not eligible

**Solution:**
1. Apply for Creator API access in TikTok Developer Portal
2. Use approved app credentials
3. Ensure account meets follower/content requirements

**Affected Files:** `src/platforms/tiktok/auth.py`, `src/platforms/tiktok/publisher.py`

**How to Reproduce:** Use unapproved app credentials

**How to Verify:** Upload/publish succeeds with approved app

---

### YouTube: "Quota exceeded"

**Symptoms:**
- Upload fails with 403 "quotaExceeded"
- Daily upload limit reached

**Root Cause:**
- Default quota: 10,000 units/day
- Video insert: ~1,600 units
- ~6 uploads/day

**Solution:**
1. Request quota increase in Google Cloud Console
2. Space uploads across days
3. Monitor quota usage in CLI

**Affected Files:** `src/platforms/youtube/publisher.py`

**How to Reproduce:** Upload >6 videos in one day

**How to Verify:** Quota error handled gracefully, job marked failed with clear message

---

## CLI / UX Issues

### Problem: Menu doesn't display / Rich formatting broken

**Symptoms:**
- Garbled output
- Missing colors, tables
- Terminal width issues

**Root Cause:**
- Terminal doesn't support ANSI/rich
- Narrow terminal (< 80 cols)
- Output redirected to file

**Solution:**
1. Detect terminal capabilities
2. Fallback to plain text if not TTY
3. Set `TERM=xterm-256color`
4. Minimum width check

**Affected Files:** `src/cli/display.py`, `src/cli/menu.py`

**How to Reproduce:** Run in basic terminal or pipe output

**How to Verify:** Menu renders correctly in target terminals

---

## Logging / Debugging

### Problem: No logs / Can't debug failure

**Symptoms:**
- Job fails but no error details
- Log files empty

**Root Cause:**
- Log level too high (WARNING)
- Log directory not writable
- Logger not configured

**Solution:**
1. Set `LOG_LEVEL=DEBUG` in `.env`
2. Check `logs/` directory permissions
3. Verify logging config in `main.py`

**Affected Files:** `main.py`, `.env`, `logs/`

**How to Reproduce:** Set LOG_LEVEL=ERROR, trigger failure

**How to Verify:** Debug logs show full request/response (sanitized)

---

## Adding New Entries

When new issues arise:
1. Document in this format
2. Add to relevant section
3. Link from `CLAUDE_HANDOFF.md` if critical
4. Update `PROJECT_STATUS.md` known issues

---

### Problem: Instagram destination "BLOCKED: temporary media storage is not configured"

**Cause:** Instagram fetches Reels from an HTTPS URL. Soc_bot needs a private S3-compatible bucket to hand it a temporary one.

**Solution:** fill in `MEDIA_STORAGE_BUCKET`, `MEDIA_STORAGE_ACCESS_KEY` and `MEDIA_STORAGE_SECRET_KEY`, plus `MEDIA_STORAGE_REGION` (AWS) or `MEDIA_STORAGE_ENDPOINT` (R2/MinIO, https) in `.env`, restart, then run **Settings → Check Instagram media storage**.

### Problem: "Instagram media preparation failed: Temporary media upload failed: AccessDenied (HTTP 403)"

**Cause:** the credentials can't write to the bucket (or the bucket name, region or endpoint is wrong). This is not retried automatically.

**Solution:** grant `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` (and `s3:ListBucket` for the health check) on that bucket; check the region or endpoint.

### Problem: Instagram container status `ERROR` right after creation

**Cause:** Meta couldn't download or process the media: URL expired (TTL too short), wrong format (Reels: MP4/MOV, H.264/HEVC, ≤ 300 MB, 3 s–15 min), or storage not reachable over HTTPS.

**Solution:** keep TTL ≥ 900; check the video spec. A retry uploads fresh media automatically.

### Problem: "Video file not found: video.mp4" for a file that exists

**Cause (fixed 2026-09-25):** when you published the same video again, the job reused an old video row whose path had moved (e.g. an earlier content package).

**Solution:** fixed in `JobStore.create_post`: a reused row now takes the path you just chose.

### Problem: "Temporary media upload to 0x0.st refused (the user agent or IP may be blocked) (HTTP 418/403)"

**Cause:** 0x0.st blocks clients it considers abusive (browser-like user agents, Tor exits, IPs that broke its Terms).

**Solution:** Soc_bot already sends its own user agent. If the block persists, switch to `MEDIA_STORAGE_PROVIDER=s3` or contact the 0x0.st operator (see its FAQ). Not retried automatically.

### Problem: "0x0.st returned no management token"

**Cause:** an identical file was already on 0x0.st, and the service only returns a token for new files.

**Effect:** Soc_bot can't delete it early; it expires on 0x0.st's own schedule (up to 30 days for small files). Nothing to fix; use `s3` if that matters.

### Problem: TempFile.org "denied from this IP address (HTTP 403)" / "rate limited (HTTP 429)"

**Cause:** the service blocks some IPs, and allows 200 uploads per hour.

**Solution:** 403 is not retried; switch to `s3`. 429 is retried by the engine; the error names the reset time.

### Problem: "TempFile.org accepts files up to 100 MB"

**Solution:** use `s3` (Instagram itself allows up to 300 MB).

### Problem: 0x0.st uploads fail

0x0.st currently has uploads disabled (2026-09-25). Use `MEDIA_STORAGE_PROVIDER=tempfile` or `s3`.

### Problem: Instagram "No media provider can take this N MB video"

**Cause:** with `MEDIA_STORAGE_PROVIDER=auto`, videos over 99 MB need the Cloudflare Quick Tunnel (cloudflared) or S3, and neither is available.

**Solution:** install cloudflared (`winget install --id Cloudflare.cloudflared`, then open a new terminal), or set `CLOUDFLARED_PATH` to the full path of `cloudflared.exe`. Or set `MEDIA_STORAGE_BUCKET`, `MEDIA_STORAGE_ACCESS_KEY` and `MEDIA_STORAGE_SECRET_KEY`, plus `MEDIA_STORAGE_REGION` (AWS) or `MEDIA_STORAGE_ENDPOINT` (R2/MinIO, https). Then run Settings → Check Instagram media storage.

### Problem: "cloudflared not found"

**Cause:** `cloudflared` isn't on `PATH`, or `CLOUDFLARED_PATH` is wrong. Soc_bot never downloads it.

**Solution:** `winget install --id Cloudflare.cloudflared` (or download `cloudflared-windows-amd64.exe` from https://github.com/cloudflare/cloudflared/releases), open a new terminal, and check `cloudflared --version`. Or set `CLOUDFLARED_PATH=C:\full\path\cloudflared.exe`. Then run Settings → Check Instagram media storage.

### Problem: "Cloudflare Quick Tunnel did not start within 90s" / "cloudflared exited before the tunnel was ready"

**Cause:** there's no Internet access to Cloudflare, outbound port 7844 or QUIC is blocked, Cloudflare throttled quick-tunnel creation, or a `~/.cloudflared/config.yaml` interferes (Quick Tunnels refuse to start when a config file is present).

**Solution:** retry (the error is retryable), raise `CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS`, or temporarily rename `%USERPROFILE%\.cloudflared\config.yml`. Test manually with `cloudflared tunnel --url http://127.0.0.1:8000`.

### Problem: "Tunnel URL not reachable within 90s"

**Cause:** the new `*.trycloudflare.com` hostname didn't resolve or answer in time. Possible reasons: DNS propagation; a resolver caching "no such host" for 60 s (trycloudflare.com's negative TTL); `cloudflare-dns.com`/`dns.google` blocked; or a filter blocking trycloudflare.com. The check uses public DNS-over-HTTPS, so a stale answer from the PC's own resolver doesn't matter.

**Solution:** retry; allow `trycloudflare.com` in DNS filters or ad blockers; raise `CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS`.

### Problem: Instagram `ERROR` / still `IN_PROGRESS` with a large tunnel video

**Cause:** Meta fetches the file through your upload bandwidth. The PC must stay on and online, and the tunnel closes when the polling window ends.

**Solution:** keep Soc_bot running until the job finishes; raise `INSTAGRAM_MAX_POLL_MINUTES` for slow connections. Quick Tunnels have no uptime guarantee; use S3 when reliability matters.

### Note: "Tunnel cleanup warning: …"

The Reel's status is unaffected. If a `cloudflared.exe` is still running afterwards, end it in Task Manager (the URL was random and is useless once the local server stopped).
