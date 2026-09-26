# YOUTUBE SETUP — ultra detailed

Goal: create a Google Cloud project with the YouTube API once, then connect **every** YouTube channel.
Do `00_MASTER_SETUP_GUIDE.md` PARTS 1–5 first.

> Google renamed parts of its console in 2025 ("OAuth consent screen" is now **Google Auth Platform**
> with the pages *Branding*, *Audience*, *Clients*, *Data access*). If you see the old names, the
> fields are the same.

What you will end up with in `.env`:
```
YOUTUBE_CLIENT_ID=<something>.apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=<secret>
YOUTUBE_REDIRECT_URI=            # leave EMPTY (Soc_bot uses a free local port automatically)
```

---

## STEP 1 — Have the channels ready

1. Each channel belongs to a Google account (Gmail). Log in at https://www.youtube.com/ .
2. No channel yet? Click your profile picture → **Create a channel** → confirm.
3. **For custom thumbnails (covers)** the channel must be verified: open
   https://www.youtube.com/verify , choose your country, get the code by SMS, enter it.
   Without this, videos still publish; only the thumbnail is skipped (Soc_bot reports "cover failed").
4. Write down the **Gmail address** of each channel's owner (needed in STEP 4).

---

## STEP 2 — Create the Google Cloud project (only once)

1. Open https://console.cloud.google.com/ and log in with the Google account that will **own the app**
   (it can be one of your channel accounts).
2. First time: accept the Terms of Service (country, agree) → **Agree and continue**.
3. Top bar: click the **project picker** (next to "Google Cloud") → **New Project**.
4. Project name `Soc_bot` → Location: "No organization" is fine → **Create**.
5. Wait for the notification, then select the new project in the project picker (the top bar must show
   `Soc_bot`).

No billing account / credit card is needed for the YouTube Data API.

---

## STEP 3 — Enable the YouTube Data API v3

1. Left menu (☰) → **APIs & Services** → **Library**.
2. Search `YouTube Data API v3` → click it → **Enable**.
3. You land on the API page showing "API enabled". Done.

---

## STEP 4 — Configure the login screen (Google Auth Platform)

1. Left menu → **APIs & Services** → **OAuth consent screen** (opens **Google Auth Platform**).
   If you see **Get started**, click it.
2. **App information:** App name `Soc_bot`, User support email = your Gmail → **Next**.
3. **Audience:** choose **External** → **Next**.
4. **Contact information:** your email → **Next**.
5. **Finish:** tick "I agree to the Google API Services: User Data Policy" → **Continue** → **Create**.
6. Left menu **Audience** → section **Test users** → **+ Add users** → type the **Gmail address of every
   channel owner** from STEP 1 (one per line) → **Save**.
   > While the app's "Publishing status" is **Testing**, only these test users can log in. Anyone else
   > sees "Access blocked: Soc_bot has not completed the Google verification process".
7. (Optional) **Data access** → **Add or remove scopes** → search "YouTube" → tick
   `.../auth/youtube.upload`, `.../auth/youtube`, `.../auth/youtube.readonly` → **Update** → **Save**.
   Soc_bot asks for exactly these three when you connect.

### Important: "Testing" status = reconnect every 7 days
Google expires logins of apps in **Testing** status after **7 days**. Two choices:
- **Keep Testing** (simplest): when a YouTube account shows ⚠ / fails with "token expired", press
  **Reconnect** (1 minute).
- **Publish the app:** **Audience** → **Publish app** → **Confirm**. Logins then don't expire weekly.
  Because YouTube scopes are "sensitive", unverified apps show a warning screen and have user limits;
  for your own channels you can click through the warning (STEP 6, point 4). Full Google verification is
  only needed if other people will use your app.

---

## STEP 5 — Create the OAuth client (Desktop app) and copy the keys

1. Left menu (Google Auth Platform) → **Clients** → **+ Create client**
   (old UI: APIs & Services → **Credentials** → **+ Create credentials** → **OAuth client ID**).
2. **Application type:** **Desktop app**. (Not "Web application": that type needs registered redirect
   URLs and won't work with Soc_bot's automatic local port.)
3. Name: `Soc_bot desktop` → **Create**.
4. A dialog shows **Client ID** and **Client secret**. Copy them into `.env`:
   ```
   YOUTUBE_CLIENT_ID=123456789-abc.apps.googleusercontent.com
   YOUTUBE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
   YOUTUBE_REDIRECT_URI=
   ```
   (Also click **Download JSON** and keep it private as a backup. Soc_bot doesn't need the file.)
5. Save `.env` and **restart Soc_bot**.

---

## STEP 6 — Connect a YouTube channel in Soc_bot (repeat for every channel)

1. `python main.py` → press **A** → tab **YouTube** → **Connect**.
2. The window **CONNECTING YOUTUBE** appears and your browser opens Google's login page.
3. Choose the Google account of the channel (or **Use another account**).
4. If you see **"Google hasn't verified this app"**: click **Continue**
   (sometimes: **Advanced** → **Go to Soc_bot (unsafe)**). This is expected for your own app.
5. If the account has several channels/brand accounts, **pick the channel** to publish to.
6. Tick **all** requested permissions (manage your YouTube account, upload videos, view) → **Continue**.
7. The browser shows **"Authorization Successful — You can now return to Soc_bot."**
   Close the tab. (YouTube needs **no pasting**: Soc_bot receives the answer on a temporary local
   address `http://127.0.0.1:<random port>/callback/youtube` by itself.)
8. Soc_bot shows **✓ YouTube account connected** and the channel appears as **● Ready**.

Repeat Connect for the next channel (choose the other Google account in step 3).

If nothing happens for 5 minutes the attempt ends by itself; just click **Connect** again.

---

## STEP 7 — Test publishing

Create Post → tick one YouTube channel → **Options** step: set the **Title** (max 100 characters),
**Privacy** (private / unlisted / public; start with **private**), **Made for kids** (tick only if it
really is for children) → Review → **PUBLISH NOW**.
The link `https://www.youtube.com/watch?v=...` is saved in `content\published_links\youtube.json`.

---

## YouTube rules you must know

| Rule | Value |
|---|---|
| OAuth client type | **Desktop app** |
| Redirect URI | none to register; leave `YOUTUBE_REDIRECT_URI` empty |
| Cover / thumbnail | JPEG or PNG, up to 50 MB, channel must be phone-verified; uploaded after the video |
| Privacy | chosen per post (private/unlisted/public) |
| Daily limit | YouTube API has a daily **quota** per project (Cloud Console → APIs & Services → YouTube Data API v3 → **Quotas**). Uploads use a large part of it; if you hit it, publishing fails with "quota exceeded" until the next day (Pacific time). |
| Login lifetime | access renewed automatically; in **Testing** status the login ends after 7 days → Reconnect |

---

## YouTube troubleshooting

| Problem | Fix |
|---|---|
| "Access blocked: … has not completed the Google verification process" | Add that Gmail as **Test user** (STEP 4.6). |
| "Error 400: redirect_uri_mismatch" | The client isn't **Desktop app**, or `YOUTUBE_REDIRECT_URI` is set: create a Desktop client and leave it empty. |
| "YouTube Data API v3 has not been used in project … or it is disabled" | Enable the API (STEP 3) and wait 2 minutes. |
| Worked for a week, now "token expired/invalid_grant" | App in Testing → **Reconnect** (or publish the app, STEP 4). |
| Video published but "cover failed" | Verify the channel by phone (STEP 1.3); use JPG/PNG ≤ 50 MB. |
| "quotaExceeded" | Daily quota used up: wait until tomorrow, or request more quota in the Quotas page. |
| Wrong channel got the video | Reconnect and pick the right channel in STEP 6.5. |
