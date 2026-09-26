# INSTAGRAM SETUP — ultra detailed

Goal: create the Meta (Instagram) app once, then connect **every** Instagram account you want to
publish to. Do `00_MASTER_SETUP_GUIDE.md` PARTS 1–5 first (Python, Soc_bot, `.env`, cloudflared).

> Meta changes its dashboard wording from time to time. If a button has a slightly different name,
> look for the closest match: the **order** of the steps stays the same.

What you will end up with in `.env`:
```
INSTAGRAM_APP_ID=<Instagram app ID>
INSTAGRAM_APP_SECRET=<Instagram app secret>
INSTAGRAM_REDIRECT_URI=https://ayushrijal83-ops.github.io/Soc_bot/oauth/instagram-callback.html
MEDIA_STORAGE_PROVIDER=auto
```

---

## STEP 1 — Make every Instagram account a PROFESSIONAL account

Soc_bot publishes through Instagram's official API, which only works for **professional** accounts
(type **Creator** or **Business**). Personal accounts can't be used. **No Facebook Page is needed.**

For **each** Instagram account, on your phone:
1. Open the **Instagram** app and log in to that account.
2. Tap your **profile picture** (bottom right).
3. Tap the **☰ menu** (top right) → **Settings and activity**.
4. Scroll to **For professionals** → **Account type and tools**.
5. Tap **Switch to professional account** → **Continue** through the info screens.
6. Pick any **category** (e.g. "Digital creator") → **Done**.
7. Choose **Creator** or **Business** → **Next** → you can skip contact info → **Done**.
8. Check: the profile now shows a "Professional dashboard" button.

Repeat for every account. Write down each **username** (without `@`); you'll need it in STEP 4.

---

## STEP 2 — Create the Meta developer app (only once)

1. On a computer, open https://developers.facebook.com/ and click **Log in** (use your Facebook login).
   - First time: click **Get Started**, accept the terms, verify your phone/email, choose a role
     (e.g. "Developer") → **Complete registration**.
2. Top right: **My Apps** → **Create App**.
3. **App details:** App name `Soc_bot` (any name; it's shown on the login screen), your contact email
   → **Next**.
4. **Use cases:** select **"Manage messaging & content on Instagram"** → **Next**.
5. **Business:** choose **"I don't want to connect a business portfolio yet."** → **Next**.
6. **Requirements / Overview:** click **Next** / **Go to dashboard** until the app dashboard opens.
7. You may be asked to re-enter your Facebook password. Do it.

You now have a Meta app in **Development mode** (top bar shows "App Mode: Development"). That's
correct: Development mode works for accounts you add as testers (STEP 4). You do NOT need App Review
to publish to your own accounts.

---

## STEP 3 — Configure "API setup with Instagram login"

1. In the app dashboard left menu: **Use cases** → next to "Manage messaging & content on Instagram"
   click **Customize**.
2. Open **API setup with Instagram login** (NOT "with Facebook login").

### 3.1 Copy the Instagram app ID and secret  ← the #1 mistake happens here
At the top of this page you see **Instagram app name**, **Instagram app ID** and
**Instagram app secret** (click **Show**, enter your password).

- Copy **Instagram app ID** → `.env` `INSTAGRAM_APP_ID=`
- Copy **Instagram app secret** → `.env` `INSTAGRAM_APP_SECRET=`

> ❌ Do **not** use "App ID / App secret" from **App settings → Basic**. Those are the *Facebook*
> app's ID. With them Instagram shows **"Sorry, this page isn't available"** and the token step fails
> with **"Invalid platform app"**.

### 3.2 Permissions
On the same page, make sure these permissions are listed/added:
- `instagram_business_basic`
- `instagram_business_content_publish`

(If there is an "Add required permissions" button, click it. No Facebook/Page permissions are needed.)

### 3.3 Redirect URI (where Instagram sends you after you approve)
1. Find the section **"Set up Instagram business login"** → click **Business login settings**.
2. In **OAuth redirect URIs** paste exactly:
   ```
   https://ayushrijal83-ops.github.io/Soc_bot/oauth/instagram-callback.html
   ```
3. Click **Save**.
4. Look at the saved list. If Meta changed it (for example added a `/` at the end), copy it **exactly
   as displayed**.
5. Put the **identical** text in `.env`:
   ```
   INSTAGRAM_REDIRECT_URI=https://ayushrijal83-ops.github.io/Soc_bot/oauth/instagram-callback.html
   ```
   Even one different character (http vs https, a missing `/`) breaks the login.

That address is a small static page in this project (`docs/oauth/instagram-callback.html`) hosted on
GitHub Pages. It only says "copy this address back into Soc_bot". It's already online for this repo.

> **Using your own copy (fork) of the project?** Host the page yourself:
> GitHub → your repo → **Settings** → **Pages** → Source: **Deploy from a branch** → Branch `main`,
> folder `/docs` → **Save** → wait 1–2 minutes → open
> `https://<your-github-username>.github.io/<repo-name>/oauth/instagram-callback.html` in a browser
> (it must load, not 404) → use THAT address in the Meta dashboard and in `.env`.

### 3.4 Privacy / terms links (recommended)
Left menu **App settings → Basic**:
- **Privacy policy URL:** `https://ayushrijal83-ops.github.io/Soc_bot/privacy.html`
- **Terms of Service URL:** `https://ayushrijal83-ops.github.io/Soc_bot/terms.html`
- **User data deletion:** choose "Data deletion instructions URL" and use the privacy URL.
- **Save changes.**

---

## STEP 4 — Give every Instagram account access to the app (Instagram Testers)

While the app is in Development mode, only Instagram accounts with a **role** on the app can log in.
Do this for **each** account from STEP 1:

1. App dashboard left menu: **App roles** → **Roles**.
2. Click **Add People** (or "Add Instagram Testers").
3. Choose **Instagram Tester**, type the Instagram **username** → **Add** / **Submit**.
4. Now accept the invite **with that Instagram account**:
   - On a computer: open https://www.instagram.com/ and log in **as that account**,
   - click **More (☰)** → **Settings** → **Apps and websites** (sometimes under "Website permissions")
     → tab **Tester invites** → **Accept**.
5. Back in the Meta dashboard the account's status changes from "Pending" to accepted.

Alternative on the "API setup with Instagram login" page: **Generate access tokens → Add account**,
log in with the Instagram account. Soc_bot does not need the token shown there. This is only another
way to give the account a role.

Repeat for all accounts. (Tip: log out of instagram.com between accounts.)

---

## STEP 5 — Restart Soc_bot so it reads the keys

Save `.env`, then:
```powershell
cd D:\Soc_bot
.\.venv\Scripts\Activate.ps1
python main.py
```
The Dashboard's **Instagram** card should no longer say "Not set up".

---

## STEP 6 — Connect an Instagram account in Soc_bot (repeat for every account)

1. Press **A** (Accounts). Click the **Instagram** tab.
2. Click **Connect** (or **Reconnect** for an account that's already listed: same flow).
3. A window **CONNECTING INSTAGRAM** appears, and your **web browser opens** Instagram's official
   login page.
4. In the browser: **log in with the account you want to add** (Soc_bot always asks Instagram to show
   the login screen, so you can pick any account, even if another one is logged in).
5. Instagram shows what Soc_bot may do. **Keep both permissions ON** (basic info + content
   publishing) and click **Allow** / **Continue**.
6. The browser lands on a page titled **"Instagram authorization received"**.
7. Copy the **full address** from the browser's address bar:
   click the address bar (or press **Ctrl+L**), then **Ctrl+C**. It looks like
   `https://ayushrijal83-ops.github.io/Soc_bot/oauth/instagram-callback.html?code=AQ...#_`
8. Go back to Soc_bot. The window now shows a box
   **"Paste the full address from the browser here"**. Click it, press **Ctrl+V**, then **Continue**
   (or Enter).
9. Soc_bot shows **✓ Instagram account connected: @username** and the account appears in the list as
   **● Ready**, with "Login valid until" about 60 days ahead.
10. Close the browser tab.

**To add the next account:** click **Connect** again and log in with the next account in step 4.
If the browser keeps using the previous account, click "Not you?/Switch accounts" on Instagram's page,
or log out at instagram.com first.

Notes:
- The code in the address works **once** and expires within **1 hour**. If you wait too long or paste
  it twice, just click Connect again.
- If you paste an address containing `error=`, Soc_bot shows why (e.g. you pressed Cancel).
- Esc while the paste box is shown cancels safely.

---

## STEP 7 — Test publishing to ONE account

Follow `00_MASTER_SETUP_GUIDE.md` PART 7 with one Instagram account and a short MP4.
Expected: live screen → **✓ Published** in about 20–60 s → link saved in
`content\published_links\instagram.json`.

---

## Instagram rules you must know

| Rule | Value |
|---|---|
| Account type | Professional (Creator/Business) |
| Video format | MP4 or MOV |
| Length | 3 seconds to 15 minutes |
| File size | up to 300 MB |
| Width | up to 1920 px (9:16 portrait recommended) |
| Cover image | JPEG only, up to 8 MB, 9:16 recommended; the same cover for every selected account |
| Caption | up to 2,200 characters, up to 30 hashtags, up to 20 @mentions |
| Posts per account | Instagram allows 100 API posts per 24 hours per account |
| Login lifetime | about 60 days; Soc_bot renews it automatically when you publish in the last 7 days; if it fully expires → **Reconnect** |

How big videos are sent: Soc_bot serves the file from your PC through **one** Cloudflare tunnel per
batch (see Master guide PART 2.3). Keep the PC online until the batch finishes. With 50 accounts,
each account downloads its own copy (e.g. 131.9 MB × 50 ≈ 6.6 GB upload).

Batch behavior: max **5** accounts at the same time, the next starts as soon as one finishes; when all
are done, failed accounts are retried **once** automatically; a second failure stays Failed.

---

## Instagram troubleshooting

| Problem | Cause → Fix |
|---|---|
| "Sorry, this page isn't available" when the browser opens | You used the Facebook App ID. Use **Instagram app ID/secret** from "API setup with Instagram login" (STEP 3.1). Restart Soc_bot. |
| "Invalid redirect_uri" / "URL blocked" | `.env` `INSTAGRAM_REDIRECT_URI` isn't **exactly** the saved Business-login redirect URI (STEP 3.3). |
| "Invalid platform app" | Same as the first row. |
| Login works but "user is not a tester"/"insufficient developer role" | The account isn't an accepted **Instagram Tester** (STEP 4). |
| Same account gets connected again | Log in with the other account on Instagram's login page; log out at instagram.com if needed. |
| "Missing required permission: instagram_business_content_publish" | You turned a permission off. **Reconnect** and keep both ON. |
| Paste window says the address is wrong | Paste the **full** address from the callback page, not the Instagram login page. |
| "Instagram media processing failed (status ERROR)" | Instagram couldn't process that video for that account (it gets one automatic retry). Check the video rules above. |
| `cloudflared not found` / `status 429` | See Master guide PART 2.3. |
| Account shows **⚠ Needs attention** | Reconnect it. |

Remove access completely: Soc_bot **A** → Instagram → select → **Disconnect**; then Instagram →
Settings → Apps and websites → remove the app.
