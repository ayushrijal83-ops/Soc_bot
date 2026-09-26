# Content Intake, Publishing Profile & Covers (Phase 5A)

Drop a folder into the content inbox, confirm once, and Soc_bot publishes it to every account in your saved profile.

## Folder structure

```
content/                      # CONTENT_ROOT (default <project>/content; absolute or relative to the project)
├── incoming/                 # put new packages here
├── publishing/               # a package is here while it is being published (or after a crash)
├── published/                # every destination succeeded
├── failed/                   # validation failed or at least one destination failed
└── archive/                  # "after success" target if the profile says archive
```

The folders are created automatically at startup. `content/` is git-ignored because it holds your media.

## Package format

```
content/incoming/post_001/        # folder name = package ID
    video.mp4                     # exactly one video: .mp4 .mov .webm (any file name)
    caption.txt                   # exactly one caption file, UTF-8
    cover.jpg                     # optional, at most one: cover.jpg/.jpeg/.png/.webp
    title.txt                     # optional: YouTube title (default: first non-empty caption line)
```

- Multiple videos, multiple `caption*.txt` files, or multiple `cover.*` files make the package **invalid**. Soc_bot never guesses which one you meant.
- Other files are ignored and listed as a warning.
- The caption is read as UTF-8 and sent exactly as written (emoji, hashtags and line breaks are kept; nothing is trimmed or shortened). Platform limits may reject it.
- The video is never re-encoded or copied. The filesystem is the source of truth.
- An invalid cover image (wrong type, can't be read, bad magic bytes, empty, over 50 MB) is a **warning**: the cover is skipped and the video can still be published.

### Partial-copy protection
A package counts as `COPYING` (not ready) when:
- any file ends in `.part`, `.partial`, `.crdownload`, `.download`, `.tmp` or `.!ut`, or
- its files changed during the `CONTENT_STABILITY_SECONDS` window (default 3). The size and mtime of each file are checked before and after the wait. If every file is already older than the window, there is no wait.

## Publishing profile (Settings → Create/Edit / View / Reset)

One `default` profile is stored in the `publishing_profiles` table. It holds account IDs and non-secret options only; tokens stay with the accounts (encrypted).

| Setting | Values |
|---------|--------|
| Accounts | any number per platform (Instagram / TikTok / YouTube); each one becomes its own job |
| TikTok privacy level | chosen by the user (required by TikTok); unaudited apps: `SELF_ONLY` |
| YouTube privacy / made for kids | `private` (default) / `unlisted` / `public`; yes/no |
| Cover/thumbnail | enabled / disabled |
| After success | move to `published/` or `archive/` |
| Mode | `VERIFY` (default) or `AUTO` |

The profile is checked before every publish. It can't be used if a referenced account no longer exists, belongs to another platform, or isn't `active` ("YouTube X is not currently authorized"), if no accounts are selected, or if the TikTok privacy level is missing.

## Modes

**VERIFY** (Content Inbox): scan → pick a package → the verification screen shows the video, caption, cover, every destination with ✓/✗, and cover handling per destination → **one** confirmation → publish everything → results. Nothing else is asked during that publish.

**AUTO** (Content Inbox, or `python main.py --scan`): every `READY` or `RESUME` package is published with no confirmation. Validation is never skipped: `INVALID` and `BLOCKED` packages are moved to `failed/` with the reason recorded, and nothing is sent for them. In VERIFY mode, `--scan` only lists packages.

**Dry-run** (`python main.py --dry-run`): scans, validates, loads the profile and prints each package's plan and cover handling. It moves nothing, writes no content items or jobs, refreshes no tokens and never contacts a platform.

## Inbox statuses

| Status | Meaning |
|--------|---------|
| `READY` | valid, stable, profile OK, every destination passes validation |
| `COPYING` | still being copied; try again shortly |
| `INVALID` | package problems (missing/multiple files, empty or non-UTF-8 caption, bad video) |
| `NO PROFILE` | create a profile under Settings |
| `BLOCKED` | a profile problem or a destination fails validation (e.g. Instagram media storage not configured, YouTube title > 100 chars) |
| `RESUME` | in `publishing/`: a previous run was interrupted; continuing never republishes finished destinations |
| `FAILED` | in `failed/`: retrying runs **only** the failed destinations |
| `PUBLISHED` | this video was already published (by content key); publishing again is refused |

## Lifecycle and resume

```
incoming/post_001 ──(publish starts)──► publishing/post_001 ──all jobs published──► published/ (or archive/)
                                              │
                                              ├── any job failed ──► failed/post_001 ──(retry)──► publishing/ ...
                                              └── jobs still processing / crash ──► stays in publishing/ (RESUME)
```

- The package is moved into `publishing/` **before** the post and jobs are created, so stored paths point at `publishing/<name>`. A resumed or retried package is moved back there first.
- Each content item links to one post; each (post, account) is a single job (unique index). A profile that gains an account later gets one new job; existing jobs are never duplicated.
- On restart, the existing job states decide what runs: `published` never runs again, `processing`/`uploading`/`pending` continue under the Phase 4 rules, and `failed` jobs run again only when you choose to retry.
- **Content key** = SHA-256 of the video's name, size, and first and last 64 KiB. It deliberately excludes the folder name and the caption, so a renamed folder (a move adds a `__timestamp` suffix if the target exists) or an edited caption is still recognised. The same video placed in a new folder is reported `PUBLISHED`. The unique index on `content_items.content_key` enforces this.
- Packages are never deleted.

## Covers / thumbnails per platform

Adapters declare what they support: `supports_cover_upload()`, `supports_cover_timestamp()`, `validate_cover()`, `cover_plan()`. The content layer only asks the adapter; it has no platform-specific code.

| Platform | Cover image | What happens | Recorded `cover_status` |
|----------|-------------|--------------|--------------------------|
| **YouTube** | ✅ `thumbnails.set` (JPEG/PNG, ≤ 50 MB) | Uploaded **after** the video exists, once. A failure (e.g. `forbidden` for channels without custom-thumbnail rights) doesn't change the video result and never re-uploads the video | `pending` → `published` / `failed` (+ `cover_error`) |
| **TikTok** | ❌ | Direct Post only has `video_cover_timestamp_ms` (a frame of the video); an image can't be uploaded. Not sent | `not_supported` (+ reason) |
| **Instagram** | ✅ `cover_url` (JPEG, ≤ 8 MB) | Served next to the video through the Cloudflare Quick Tunnel and sent with the Reel container; the same `cover.jpg` is used for every selected account. An invalid cover **blocks** the destination (never silently dropped) | `pending` → `published` |

A cover YouTube would reject (e.g. `.webp` or > 50 MB) is `skipped` with the reason, and the video is still published. A cover that isn't a readable image blocks the whole package (it is never silently dropped); for Instagram, a non-JPEG or > 8 MB cover blocks the Instagram destinations.

## History (main menu → History)
Per content item: name, dates, status, every destination's video status, and every destination's cover status. It reads the existing job rows; there is no separate history table.

## Published Links (main menu → 6)
After a destination is confirmed **published**, its permanent public URL is appended to one JSON file per platform:

    content/published_links/youtube.json | instagram.json | tiktok.json

Next to each JSON file, `<platform>.txt` holds only the permanent URLs, one per line (for pasting into other tools). It is rebuilt from the JSON on every write and at startup. The JSON stays the source of truth.

Record: `{"video", "account", "url", "provider_id", "published_at"}` (UTC). The files are created at startup and written atomically (temp file in the same folder, fsync, `os.replace`). Writers are serialized by a thread lock plus an OS file lock (`.lock`, `msvcrt`/`fcntl`), so concurrent fan-out jobs and a second Soc_bot process never lose records (tested: 5 threads and 3 processes writing at once).
After each publish the result shows, per platform, "Permanent links saved: N" and both file paths. The clipboard is never touched automatically; copying is the manual "Copy link" action.
If Instagram publishes but its permalink can't be read, the job stays PUBLISHED, gets `permalink_missing` in its provider state and a warning, and no URL is guessed. Import from publish history adds the link later. A malformed file is renamed to `<platform>.corrupt-<timestamp>.json` and never deleted. Duplicates (same account + provider_id) are skipped, so the same video on two accounts gives two records. Failed jobs never produce a link, and a link problem never fails a job. Nothing is stored in the database.

| Platform | URL | Source |
|---|---|---|
| YouTube | `https://www.youtube.com/watch?v=<id>` | video id returned by `videos.insert` |
| Instagram | the IG Media `permalink` | `GET /{ig-media-id}?fields=permalink` right after `media_publish` (the container/media id is never turned into a URL) |
| TikTok | **not saved** | the Content Posting API documents no post URL, and `publicaly_available_post_id` is only returned for public, moderation-approved posts (never for SELF_ONLY) |

Only `https://` URLs on the platform's own site are accepted (`is_permanent_platform_url`). TempFile, 0x0, S3/R2 presigned and upload-session URLs are rejected, as are signed query strings.
Menu: YouTube / Instagram / TikTok → List links / Copy link (Windows `clip.exe`, "✓ Link copied to clipboard."). **Import from publish history** backfills links for jobs published before this feature existed. It is idempotent.

## Limitations
- No background watcher: scan from Content Inbox or `--scan`. `ContentIntake.scan()`/`publish_ready()` are the hooks a future watcher would call.
- Instagram uses the package's local video automatically, through temporary private storage (`MEDIA_STORAGE_*`, see API_INTEGRATIONS.md). `video_url.txt` is no longer used.
- TikTok: frame-based covers (`video_cover_timestamp_ms`) are not configurable yet.
- One profile (`default`); the table is keyed by name for more later.
- A YouTube thumbnail failure is not retried automatically.
- The same video file can't be published again as a new package (by design); rename or re-encode it if you really want a second post.
