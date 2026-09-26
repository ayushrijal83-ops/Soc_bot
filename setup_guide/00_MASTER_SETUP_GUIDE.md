# SOC_BOT — MASTER SETUP GUIDE (new computer, from zero)

This guide takes you from a brand-new computer to publishing your first video with Soc_bot.
Follow it **top to bottom, in order**. Every step says what to click, what to type, and what you
should see. Nothing is skipped.

There are 4 files in this folder:

| File | What it covers |
|---|---|
| `00_MASTER_SETUP_GUIDE.md` (this file) | Installing everything, the `.env` file, Cloudflare, first start, daily use, moving to a new PC |
| `01_INSTAGRAM_SETUP.md` | Meta developer app, Instagram accounts, connecting every Instagram account |
| `02_YOUTUBE_SETUP.md` | Google Cloud project, YouTube API, connecting every YouTube channel |
| `03_TIKTOK_SETUP.md` | TikTok developer app, connecting TikTok accounts |

> **Time needed:** about 30–45 minutes for this file, plus 20–40 minutes per platform.
> **Cost:** everything here is free. No credit card is needed anywhere.

---

## PART 0 — What Soc_bot is (read this once)

Soc_bot is a program that runs **in a terminal window** on your computer. You give it:

- **one** finished video,
- **one** caption,
- **one** optional cover image (`cover.jpg`),
- and you tick **many** accounts (Instagram, YouTube, TikTok).

It then publishes that video to every selected account using the **official** APIs of each platform
(no bots clicking in a browser, no scraping). For Instagram it runs at most **5 accounts at the same
time**, retries failures **once**, and saves every published link in `content/published_links/`.

Your passwords are **never** stored. Each platform gives Soc_bot a "token" after you log in and
approve; tokens are saved **encrypted** in a local database file on your computer.

---

## PART 1 — Checklist: accounts you need BEFORE you start

Create these first (all free). Keep the logins in a password manager.

| # | Account | Why | Where |
|---|---|---|---|
| 1 | **GitHub account** (optional but recommended) | To download the project code | https://github.com/signup |
| 2 | **Meta for Developers account** (uses your Facebook login) | To create the Instagram app | https://developers.facebook.com/ |
| 3 | **Instagram professional accounts** (Business or Creator) | The accounts you publish to | Instagram app → Settings (see `01_INSTAGRAM_SETUP.md`) |
| 4 | **Google account** | To create the YouTube API project | https://accounts.google.com/signup |
| 5 | **YouTube channel(s)** | The channels you publish to | https://www.youtube.com/ → Create a channel |
| 6 | **TikTok for Developers account** | To create the TikTok app | https://developers.tiktok.com/ |
| 7 | **TikTok account(s)** | The accounts you publish to | TikTok app |

You do **not** need a Cloudflare account, an AWS account, or any paid service.

> If you only use Instagram, you can skip the YouTube and TikTok parts. Soc_bot works with any
> combination of platforms.

---

## PART 2 — Install the programs (Windows 10/11)

Open **PowerShell**: press the **Windows key**, type `PowerShell`, press **Enter**.
You will type commands into this window. After each command press **Enter**.

### 2.1 Install Python (version 3.10 or newer; 3.11/3.12 recommended)

1. Open https://www.python.org/downloads/ and click the big **Download Python 3.x** button.
2. Run the downloaded installer.
3. **VERY IMPORTANT:** on the first installer screen, tick **"Add python.exe to PATH"** at the bottom.
4. Click **Install Now**. Wait until it says **Setup was successful**. Click **Close**.
5. **Close PowerShell and open a new one** (so it sees the new program).
6. Check it worked:
   ```powershell
   python --version
   ```
   You should see something like `Python 3.12.4`. If you see "Python was not found", repeat the
   install and make sure step 3 was ticked.

### 2.2 Install Git (to download the code)

1. Open https://git-scm.com/download/win and download **64-bit Git for Windows Setup**.
2. Run it and click **Next** on every screen (the defaults are fine), then **Install**, then **Finish**.
3. Close and reopen PowerShell, then check:
   ```powershell
   git --version
   ```
   You should see `git version 2.x.x`.

### 2.3 Install Cloudflare's tool `cloudflared` (needed for Instagram)

**What is it and why?** Instagram does not accept a video file directly: it downloads the video from a
**public web address**. `cloudflared` creates a temporary, private-looking public address that points
to the video **on your own computer** while Instagram downloads it, then shuts it off. No Cloudflare
account, no website, no payment. Soc_bot starts and stops it automatically; you never run it by hand.

1. In PowerShell type:
   ```powershell
   winget install --id Cloudflare.cloudflared
   ```
   - If asked "Do you agree to all the source agreements terms?", type `Y` and press Enter.
   - Wait for **Successfully installed**.
   - If `winget` is not found: download `cloudflared-windows-amd64.msi` from
     https://github.com/cloudflare/cloudflared/releases/latest and double-click it to install.
2. **Close PowerShell and open a new one.**
3. Check:
   ```powershell
   cloudflared --version
   ```
   You should see `cloudflared version 2026.x.x ...`.
4. If step 3 says "not recognized", the tool is installed but not on PATH. It is usually here:
   `C:\Program Files (x86)\cloudflared\cloudflared.exe`. You will write that full path into the
   `.env` file in PART 4 (`CLOUDFLARED_PATH=`). Soc_bot then finds it even if PATH is wrong.

> **Important Cloudflare facts**
> - Cloudflare calls these "Quick Tunnels" and says they are meant for testing/development, with no
>   uptime guarantee. Soc_bot is built around that: it opens **one tunnel per publishing batch**
>   (not one per account) and closes it when the batch is done.
> - If Cloudflare is asked for too many new tunnels in a short time it answers **HTTP 429**
>   ("too many requests") for roughly an hour. Soc_bot then shows
>   `quick tunnel provisioning failed with status 429`. Just wait and retry later.
> - Never keep a file called `config.yaml` or `config.yml` in `C:\Users\<you>\.cloudflared\`:
>   Quick Tunnels refuse to start when it exists. (Rename it if you ever created one.)
> - Small videos without a cover can go to TempFile.org instead (automatic). Videos with a cover
>   **always** use the Cloudflare tunnel, because only the tunnel can serve the cover image too.

### 2.4 (Optional) Install ffmpeg / ffprobe

Soc_bot works without it. With it, Soc_bot can also show a video's duration and resolution.
```powershell
winget install --id Gyan.FFmpeg
```

---

## PART 3 — Download Soc_bot and install its libraries

1. Choose a folder for the project, for example `D:\`:
   ```powershell
   cd D:\
   git clone https://github.com/ayushrijal83-ops/Soc_bot.git
   cd Soc_bot
   ```
   You now have `D:\Soc_bot`. (If you received the project as a ZIP instead, unzip it to `D:\Soc_bot`
   and `cd D:\Soc_bot`.)

2. Create a private Python environment for the project (keeps its libraries separate):
   ```powershell
   python -m venv .venv
   ```

3. Activate it (do this **every time** you open a new PowerShell to use Soc_bot):
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   Your prompt now starts with `(.venv)`.
   - If you see "running scripts is disabled on this system", run this once, then try again:
     ```powershell
     Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
     ```
     (type `Y` if asked.)

4. Install the libraries:
   ```powershell
   pip install -r requirements.txt
   ```
   Wait until it ends with `Successfully installed ...` (1–3 minutes).

5. Quick check:
   ```powershell
   python main.py --version
   ```
   You should see `SOC_BOT v1.0.0`.

---

## PART 4 — Create and fill the `.env` settings file

The `.env` file holds your app keys and settings. **It is secret. Never share it, never upload it,
never commit it to GitHub** (the project already ignores it).

### 4.1 Create the file from the template

```powershell
Copy-Item .env.example .env
notepad .env
```
Notepad opens `.env`. You will edit it now and save with **Ctrl+S**.

### 4.2 Generate the encryption key (required)

This key encrypts all account tokens. In PowerShell (with `(.venv)` active):
```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
It prints one line of 44 random characters ending in `=` (for example `AbCd...xyz=`; yours will be different). Copy it and set:
```
ENCRYPTION_KEY=<paste the printed key here>
```
> **Keep a safe copy of this key** (password manager). If you lose it, every connected account must
> be connected again. If you move to a new PC and want to keep your accounts, you need the **same**
> key (see PART 9).

### 4.3 Media delivery for Instagram (required for Instagram)

Find `MEDIA_STORAGE_PROVIDER=s3` and change it to:
```
MEDIA_STORAGE_PROVIDER=auto
```
`auto` means: small videos (under 99 MB, no cover) go to TempFile.org; big videos and all videos with
a cover go through the Cloudflare tunnel. You don't need any S3 settings.

If `cloudflared --version` did **not** work in PART 2.3, set the full path:
```
CLOUDFLARED_PATH=C:\Program Files (x86)\cloudflared\cloudflared.exe
```

### 4.4 Platform keys (fill these while following each platform guide)

| Lines in `.env` | Filled in | Guide |
|---|---|---|
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, `INSTAGRAM_REDIRECT_URI` | Instagram | `01_INSTAGRAM_SETUP.md` |
| `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` (leave `YOUTUBE_REDIRECT_URI` empty) | YouTube | `02_YOUTUBE_SETUP.md` |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` (leave `TIKTOK_REDIRECT_URI` empty) | TikTok | `03_TIKTOK_SETUP.md` |

Leave a platform's lines empty if you don't use it.

### 4.5 Optional Instagram batch settings (defaults are good)

```
INSTAGRAM_MAX_CONCURRENT_PUBLISHES=5        # accounts published at the same time (1–20)
INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS=5     # pause before the one automatic retry of failed accounts
INSTAGRAM_MAX_POLL_MINUTES=15               # longest wait for Instagram to process a big video
```
You can also change the first two later inside Soc_bot: **Settings → Instagram**.

### 4.6 Save

Press **Ctrl+S** in Notepad and close it. Rules for `.env`:
- one setting per line, `NAME=value`, **no spaces around `=`**, no quotes needed;
- after changing `.env`, **restart Soc_bot** so it reads the new values.

---

## PART 5 — First start

```powershell
cd D:\Soc_bot
.\.venv\Scripts\Activate.ps1
python main.py
```

What happens:
1. On the very first start Soc_bot **creates its database automatically** (`data\publisher.db`) and the
   folders `content\incoming`, `content\published`, `content\published_links`, … Nothing to do.
2. The full-screen app opens on the **Dashboard**:
   - three cards: **Instagram / YouTube / TikTok** with how many accounts are connected
     (all 0 at first, and "Not set up" until the keys are in `.env`);
   - publishing counters, current batch, system status, recent activity.

Tips:
- Make the window **at least 80 × 24** characters. Wider (100+) also shows the left menu.
- Use **Windows Terminal** (built into Windows 11) for the best colors.
- If the full-screen app cannot start (for example in a plain console), Soc_bot automatically uses
  the classic text menu. You can also choose it on purpose: `python main.py --plain`.

### Keyboard shortcuts (shown at the bottom of the screen too)

| Key | Opens | | Key | Does |
|---|---|---|---|---|
| **D** | Dashboard | | **/** | Search in lists |
| **C** | Create Post | | **Space** | Tick / untick |
| **Q** | Queue | | **Enter** | Open / confirm |
| **H** | History | | **Esc** | Back / close |
| **A** | Accounts | | **Ctrl+P** | Command palette (type what you want) |
| **L** | Published Links | | **?** | Help screen |
| **T** | Content | | **X** or **Ctrl+Q** | Exit |
| **S** | Settings | | | |

Letters don't trigger shortcuts while you are typing inside a text box.

---

## PART 6 — Connect your accounts

Do the platform guides now, in any order:

1. `01_INSTAGRAM_SETUP.md` → then in Soc_bot: **A** → tab **Instagram** → **Connect** (repeat per account)
2. `02_YOUTUBE_SETUP.md` → **A** → tab **YouTube** → **Connect** (repeat per channel)
3. `03_TIKTOK_SETUP.md` → **A** → tab **TikTok** → **Connect**

After each connection the account appears in the list as **● Ready**.

---

## PART 7 — Your first publish (safe test)

Always test with **one** account and a short test video first.

1. Press **C** (Create Post). The top shows 5 steps:
   `1 Media › 2 Caption › 3 Destinations › 4 Options › 5 Review`.
2. **Media:** click **Browse…** next to VIDEO and pick an `.mp4` file (or type its full path and press
   Enter). A card shows the name, size and **✓ Valid**.
   Optional: click **Browse…** next to COVER and pick a `.jpg` (JPEG, max 8 MB, portrait 1080×1920 is
   ideal). **One cover is used for every selected Instagram account.**
   Click **Next →** (or Ctrl+N).
3. **Caption:** type your caption (multiple lines and hashtags are fine). **Next →**.
4. **Destinations:** click the **Instagram** card (and/or YouTube/TikTok). The account list appears.
   - **Space** ticks an account, **A** ticks all, **N** unticks all, **/** searches.
   - "Selected: 1 / 11" shows how many are ticked. **Next →**.
5. **Options:** only what your platforms need: YouTube title, privacy, "made for kids"; TikTok privacy.
   **Next →**.
6. **Review:** read the summary: video, cover, number of accounts, "1 shared tunnel for this batch",
   "failed Instagram jobs retried once", approximate upload traffic. Accounts that already have this
   exact video are skipped unless you tick the box. Blocked accounts are listed with the reason.
7. Press **▶ PUBLISH NOW** (or **P**), then confirm with **Publish** (or **Y**).
8. The live screen shows every account: `○ Pending → ↑ Uploading → → Processing → ✓ Published`.
   A small Instagram video normally takes 20–60 seconds per account; up to 5 run at once.
9. At the end: **PUBLISH COMPLETE** with Published / Failed / Links saved.
   Click **View links** (or press **L** later) → select a row → **C** copies the link.

Where links are saved: `content\published_links\instagram.json` (+ `instagram.txt`, one link per line),
same for `youtube` and `tiktok`.

---

## PART 8 — Everyday use

```powershell
cd D:\Soc_bot
.\.venv\Scripts\Activate.ps1
python main.py
```

- **Queue (Q):** every batch with progress. Enter opens a batch; **o** opens a post, **c** copies its
  link, **r** retries a failed account.
- **History (H):** everything ever published; filter by platform/status, search with **/**.
  **Delete** removes a finished entry (the post stays online and its link stays saved).
- **Published Links (L):** all permanent links; **C** copy one, **A** copy all shown.
- **Content (T):** drop-folder publishing: put a folder with `video.mp4` + `caption.txt` (+ `cover.jpg`)
  into `content\incoming\`, then review it there.
- **Settings (S):** Instagram concurrency/retry delay, media status (**Run online checks**),
  OAuth status per platform.
- Keep the computer **on and online** while a batch runs (the video is served from your PC).
  You cannot quit while a batch is publishing; wait for **PUBLISH COMPLETE**.

### Updating Soc_bot later
```powershell
cd D:\Soc_bot
git pull
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Your `.env`, database and links are not touched by updates.

---

## PART 9 — Moving to a NEW computer and keeping your accounts

You have two choices.

**Option A: start fresh (simplest).** Follow this guide on the new PC and connect every account again.
Your posts stay online; only the local history/links list starts empty.

**Option B: carry everything over.**
1. Do PARTS 2 and 3 on the new PC (install programs, download code, install libraries).
2. From the old PC copy these to the same places on the new PC (use a USB stick or private cloud
   folder, **never** a public place, never email):
   - `.env` (contains the **same ENCRYPTION_KEY**: without it the copied tokens can't be read),
   - the whole `data\` folder (the database with your accounts and history),
   - the whole `content\` folder (published links, content packages).
3. On the new PC check/adjust in `.env`: `CLOUDFLARED_PATH` (if the path differs).
4. Start `python main.py`. All accounts appear. Then:
   - **YouTube:** if your Google app is still in "Testing", logins older than 7 days need a reconnect
     (see `02_YOUTUBE_SETUP.md`).
   - **Instagram:** tokens last ~60 days and are renewed automatically while you keep publishing.
5. Delete the copies from the USB stick / cloud folder afterwards.

---

## PART 10 — Security rules (please follow)

- Never share or upload `.env`, `data\publisher.db`, or your ENCRYPTION_KEY.
- Never paste tokens, app secrets or the Instagram "code" address into chats, emails or screenshots.
- The Instagram callback address you paste into Soc_bot contains a one-time code that expires within
  an hour; it only works together with your app secret, but still don't share it.
- Soc_bot never logs tokens or temporary video links; its log file is `logs\soc_bot.log`.
- To remove Soc_bot's access to an account: **A** → select it → **Disconnect** (and, for Instagram,
  also Instagram → Settings → Apps and websites → remove the app).

---

## PART 11 — Quick troubleshooting

| You see | Do this |
|---|---|
| `python` not found | Reinstall Python with **Add python.exe to PATH** ticked; reopen PowerShell |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again |
| Platform card says **Not set up** | Its keys are missing in `.env`; add them and restart Soc_bot |
| `cloudflared not found` | Install it (PART 2.3) or set `CLOUDFLARED_PATH` to the full `.exe` path |
| `quick tunnel provisioning failed with status 429` | Cloudflare rate limit: wait ~1 hour, then Queue → retry |
| Instagram: "Sorry, this page isn't available" | Wrong app ID: use the **Instagram** app ID (see Instagram guide) |
| Instagram connects the same account again | Log out of Instagram in the browser, or just log in with the other account when asked |
| YouTube: "Access blocked: app has not completed verification" | Add the Gmail as **Test user** (YouTube guide) |
| Publishing is blocked "account is missing required permission" | Reconnect the account and tick all permissions |
| Window looks broken / too small | Make it at least 80×24; use Windows Terminal |
| Anything else | Look at `logs\soc_bot.log` and `docs\TROUBLESHOOTING.md` |

---

## PART 12 — Command summary (copy/paste)

```powershell
# every time
cd D:\Soc_bot
.\.venv\Scripts\Activate.ps1
python main.py                 # full-screen app
python main.py --plain         # classic text menu
python main.py --dry-run       # show what would be published, publishes nothing
python main.py --scan          # process content\incoming (AUTO profile publishes, VERIFY only lists)
python main.py --help          # all options
```
