# GitHub Pages: Legal Pages for Platform Review

## What they are

TikTok (and other platforms' developer portals) require public **Terms of Service** and **Privacy Policy** URLs for an app. Soc_bot hosts them as a static site from this repository's `docs/` folder:

| File | Page |
|------|------|
| `docs/index.html` | Landing page with links to both documents |
| `docs/terms.html` | Terms of Service |
| `docs/privacy.html` | Privacy Policy |
| `docs/oauth/instagram-callback.html` | Instagram OAuth callback page for paste mode (tells the user to copy the address back into Soc_bot; no JavaScript) |
| `docs/.nojekyll` | Turns off Jekyll so GitHub serves the files as-is. The Markdown docs in `docs/` would otherwise go through Jekyll and could break the build |

Plain HTML with inline CSS: no JavaScript, no external resources, no trackers, relative links. It works on mobile and in dark mode.

The policy text describes the application's current behaviour (local SQLite storage, Fernet-encrypted tokens, official platform APIs only, no analytics, and what disconnecting does and doesn't delete). **Update both pages when that behaviour changes**, and change the effective date.

**Contact:** both pages list `ayushruchal83@gmail.com` (Terms: contact; Privacy: security reports and contact). Keep it a monitored address; reviewers may reject policies without a working contact.

## Enable GitHub Pages (repository owner, once)

1. Commit and push these files to `main`.
2. GitHub → **ayushrijal83-ops/Soc_bot** → **Settings** → **Pages** (under "Code and automation").
3. **Build and deployment → Source:** *Deploy from a branch*.
4. **Branch:** `main`, folder **`/docs`** → **Save**.
5. Wait for the "pages build and deployment" run to finish (**Actions** tab, usually 1–2 minutes). The Pages settings page then shows "Your site is live at …".

The repository must be public, or on a plan that allows Pages for private repositories. As of 2026-09-25 it is public, and Pages was **not** yet enabled.

## URLs to give TikTok

| Field | Expected URL |
|-------|--------------|
| Terms of Service URL | `https://ayushrijal83-ops.github.io/Soc_bot/terms.html` |
| Privacy Policy URL | `https://ayushrijal83-ops.github.io/Soc_bot/privacy.html` |
| (Website, if asked) | `https://ayushrijal83-ops.github.io/Soc_bot/` |

These are the **expected** URLs. **Open each one in a browser after deployment** and check that it loads (not a 404) before entering it in the TikTok developer portal.

## Notes
- Enabling Pages publishes everything under `docs/`, including the Markdown project docs. They are already public in the repository, and no secrets live there. Keep it that way: never put credentials in `docs/`.
- Nothing here changes the application; the pages are static files only.
