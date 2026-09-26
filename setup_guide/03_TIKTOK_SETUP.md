# TIKTOK SETUP — ultra detailed

Goal: create a TikTok developer app once, then connect your TikTok account(s).
Do `00_MASTER_SETUP_GUIDE.md` PARTS 1–5 first.

> **Honest status:** Soc_bot's TikTok code follows TikTok's official Login Kit + Content Posting API and
> is fully tested with simulated TikTok responses, but it has **not yet been run against the real
> TikTok service** (no TikTok developer app existed yet). Follow this guide, then do the test in STEP 7
> and keep an eye on the result.

> TikTok's developer portal wording changes often. If a button name differs, look for the closest one.

What you will end up with in `.env`:
```
TIKTOK_CLIENT_KEY=<client key>
TIKTOK_CLIENT_SECRET=<client secret>
TIKTOK_REDIRECT_URI=              # leave EMPTY
```

---

## Before you start: what TikTok allows for new apps

- A new ("unaudited") TikTok app can only post videos as **private** (`SELF_ONLY`: only you see
  them). The TikTok account you post to should itself be set to **private** while testing.
- Public posting needs TikTok's **app audit** (a review of your app). Until then use SELF_ONLY.
- TikTok returns **no public link** for private posts, so Soc_bot can't save TikTok links (the TikTok
  tab in Published Links stays empty; that's expected).

---

## STEP 1 — Prepare the TikTok account

1. In the TikTok app: **Profile** → **☰** → **Settings and privacy** → **Privacy** →
   turn **Private account** ON (recommended while the app is unaudited).
2. Write down the account's username.

---

## STEP 2 — Create the TikTok developer account and app (only once)

1. Open https://developers.tiktok.com/ → **Log in** (you can log in with your TikTok account) and accept
   the developer terms.
2. Top menu: **Manage apps** (or your avatar → **My apps**) → **Connect an app** / **Create app**.
3. Choose an **individual** developer (or organization if you have one), enter the **App name**
   (e.g. `Soc_bot`), and an **app icon** if asked.
4. Fill the app details:
   - **Category:** e.g. "Social" / "Productivity".
   - **Description:** e.g. "Desktop tool that publishes my own videos to my own TikTok account."
   - **Terms of Service URL:** `https://ayushrijal83-ops.github.io/Soc_bot/terms.html`
   - **Privacy Policy URL:** `https://ayushrijal83-ops.github.io/Soc_bot/privacy.html`
   - **Platforms:** tick **Desktop**.
   (Open both URLs in your browser first: they must load. Using your own fork? Enable GitHub Pages for
   it as explained in `01_INSTAGRAM_SETUP.md` STEP 3.3 and use your own URLs.)

---

## STEP 3 — Add the two products

In the app's page → **Products** / **Add products**:

### 3.1 Login Kit
1. Add **Login Kit**.
2. Platform: **Desktop**.
3. **Redirect URI:** enter exactly
   ```
   http://127.0.0.1:*/callback/tiktok
   ```
   The `*` means "any port". Soc_bot picks a free port for every login. (TikTok's desktop Login Kit
   allows `localhost`/`127.0.0.1` redirect addresses with a wildcard port.)

### 3.2 Content Posting API
1. Add **Content Posting API**.
2. Turn on **Direct Post** (publishing directly to the account).

### 3.3 Scopes
Make sure these scopes are added/approved for the app:
- `user.info.basic`
- `video.publish`

(Soc_bot asks for exactly these two. `video.upload` is not needed.)

---

## STEP 4 — Copy the keys

On the app page (**App details** / **Credentials**) copy:
- **Client key** → `.env` `TIKTOK_CLIENT_KEY=`
- **Client secret** → `.env` `TIKTOK_CLIENT_SECRET=`
- Leave `TIKTOK_REDIRECT_URI=` empty.

Save `.env` and **restart Soc_bot**. The Dashboard's TikTok card should no longer say "Not set up".

---

## STEP 5 — Sandbox / test users (if your portal shows it)

Newer TikTok portals let you create a **Sandbox** for unaudited apps and add **target/test users**.
If you see **Sandbox** or **Target users**:
1. Create a sandbox (any name) and add the Login Kit + Content Posting API products there too.
2. Add your TikTok account(s) as target users and accept any invitation in the TikTok app.
3. Use the **sandbox** Client key/secret in `.env` if the portal gives separate ones.
If your portal has no such section, skip this step.

When everything is configured you may **Submit for review** (audit) later to allow public posting.

---

## STEP 6 — Connect the TikTok account in Soc_bot

1. `python main.py` → **A** → tab **TikTok** → **Connect**.
2. The window **CONNECTING TIKTOK** appears and the browser opens TikTok's official authorization page.
3. Log in with the TikTok account and click **Authorize** / **Continue**. Keep both permissions on.
4. The browser shows **"Authorization Successful — You can now return to Soc_bot."** (no pasting needed,
   like YouTube) → close the tab.
5. Soc_bot shows **✓ TikTok account connected**; the account appears as **● Ready**.
   If it warns "missing permission video.publish", reconnect and allow it.

Repeat for other TikTok accounts (log out of tiktok.com in the browser between accounts if it reuses
the previous login).

---

## STEP 7 — Test publishing (private)

1. Create Post → tick your TikTok account → **Options** step: **Privacy = SELF_ONLY** → Review →
   **PUBLISH NOW**.
2. Before sending, Soc_bot checks with TikTok which privacy levels and video length your account allows;
   if SELF_ONLY isn't allowed or the video is too long, the destination is blocked with the reason.
3. Result: ✓ Published. Open the TikTok app → your profile → the video is there, visible only to you.
4. If anything fails, note the exact message (Queue → open the batch → Enter on the job →
   Technical details) and see the table below.

---

## TikTok rules you must know

| Rule | Value |
|---|---|
| Products | Login Kit (Desktop) + Content Posting API (Direct Post) |
| Redirect URI | `http://127.0.0.1:*/callback/tiktok` in the portal; empty in `.env` |
| Scopes | `user.info.basic`, `video.publish` |
| Privacy before audit | SELF_ONLY only; account should be private |
| Covers | TikTok's API has no cover-image upload; Soc_bot doesn't send one (reported as "not supported") |
| Links | none saved (TikTok gives no public URL for private posts) |
| Login lifetime | access 24 h, renewed automatically with a 1-year refresh token |

---

## TikTok troubleshooting

| Problem | Fix |
|---|---|
| TikTok card says "Not set up" | Client key/secret missing in `.env`; restart Soc_bot. |
| "redirect_uri" error on TikTok's page | Login Kit redirect URI must be exactly `http://127.0.0.1:*/callback/tiktok`, platform Desktop; `.env` TIKTOK_REDIRECT_URI empty. |
| "scope not authorized" | Add `user.info.basic` and `video.publish` to the app (STEP 3.3), wait for approval, reconnect. |
| `unaudited_client_can_only_post_to_private_accounts` | Make the TikTok account private (STEP 1) and use SELF_ONLY. |
| Destination blocked "privacy level not allowed" | Choose a privacy that TikTok lists for your account (SELF_ONLY before audit). |
| Nothing in Published Links → TikTok | Expected: TikTok gives no public link for private posts. |
