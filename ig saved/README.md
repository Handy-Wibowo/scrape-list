# Instagram Saved Posts Scraper

Download all your Instagram **saved posts**, organized by collection.

For every saved post the script writes:

- the image(s) / video(s)
- `caption.txt` — the post caption
- `meta.json` — Instagram link, caption, author, upload date, save date, collection

At the end it rebuilds `index.csv` (one row per post) in the output folder.

> This tool logs into **your own** account and downloads **only posts you saved**.
> It uses the unofficial `instagrapi` library, not an official Instagram API.

## Requirements

- Python 3.9+
- [`instagrapi`](https://github.com/subzeroid/instagrapi)

```bash
pip install instagrapi
```

## Usage

```bash
python scrape_saved.py                          # download everything
python scrape_saved.py --max 5                  # first 5 posts per collection (test run)
python scrape_saved.py --collections "Recipes,Travel Ideas"
python scrape_saved.py --output my_backup
```

| Flag | Description | Default |
| --- | --- | --- |
| `--output` | Output folder | `saved_posts` |
| `--max` | Max posts per collection (`0` = all) | `0` |
| `--collections` | Comma-separated collection names to scrape | all |
| `--sessionid` | Browser `sessionid` cookie (see below) | — |

Output layout:

```
saved_posts/
  index.csv
  <Collection Name>/
    <post-code>/
      <media files>
      caption.txt
      meta.json
```

Already-downloaded posts are skipped, so you can safely re-run the script to resume.

## Authentication (important)

Instagram has no public login endpoint for this kind of scraping, so the script
needs a **session**. There are three ways to provide it, plus a password fallback.

### Option 1 — Browser session id (recommended)

Reuses the session from your real browser. No new login, so Instagram does not
trigger a security challenge.

1. Log in to <https://www.instagram.com> in your browser.
2. Press `F12` → **Application** → **Cookies** → `https://www.instagram.com`.
3. Copy the value of the `sessionid` cookie.
4. Provide it to the script using **one** of these:

   ```bash
   # a) command line
   python scrape_saved.py --sessionid "PASTE_VALUE_HERE"

   # b) environment variable
   export IG_SESSIONID="PASTE_VALUE_HERE"     # Windows (PowerShell): $env:IG_SESSIONID="..."

   # c) text file named ig_sessionid.txt next to the script
   ```
5. Run the script.

The script validates the session and caches it in `ig_session.json`.

### Option 2 — Username + password

Just run `python scrape_saved.py` with no session id. It will prompt for your
username and password (password input is hidden). Credentials are sent **only**
to Instagram.

If Instagram triggers a security check (`ChallengeRequired`), the script prints
instructions — the quickest fix is to use Option 1 instead. A two-factor code is
also supported if 2FA is enabled.

### Cached session

After a successful login the session is saved to `ig_session.json` next to the
script, so you only authenticate once. Delete the file to log in again.

## ⚠️ Security

`ig_session.json` and the `sessionid` cookie are **login credentials** — anyone
who has them can control your Instagram account. Never commit, upload, or share
them.

Add this `.gitignore` before using git:

```gitignore
ig_session.json
ig_sessionid.txt
.env
saved_posts/
__pycache__/
*.pyc
```

`saved_posts/` is excluded because it contains your personal saved content.

## Notes & limitations

- **Rate limits:** the script sleeps 2–5 s between requests. If Instagram
  rate-limits you it waits 5 minutes and continues. Large collections take a
  while.
- **`saved_at` is usually empty:** Instagram's API does not expose the time you
  saved a post. Save dates are only available via Instagram's official data
  export (*Settings → Accounts Center → Download your information*).
- **Account risk:** automated scraping can violate Instagram's Terms of Service.
  Use at your own discretion.
