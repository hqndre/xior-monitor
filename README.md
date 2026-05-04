# Xior Groningen Studio Monitor

Checks the Xior booking site every 5 minutes and emails you when a new
studio appears in Groningen. Runs free on GitHub Actions.

> **Note on timing:** 5 minutes is the *minimum* interval GitHub Actions
> allows. Scheduled workflows are also "best effort" — during peak hours
> they can start 5–15 minutes late. Real-world check frequency is
> typically every 7–10 minutes. Studios stay locked for 20 minutes once
> someone clicks them, so this is fast enough in practice.

## How it works

1. A GitHub Actions workflow runs every 5 minutes.
2. It launches a headless Chromium browser, opens `xior-booking.com`,
   and scans the rendered page for studios mentioning Groningen.
3. It compares what it finds against `state.json` (saved from the
   previous run) and emails you only when **new** listings appear.
4. The updated state is committed back to the repo so the next run
   knows what was already there.

You'll get an email with the studio details + a direct link, and you
should book within minutes — Xior locks a studio for 20 minutes once
someone clicks it, so speed matters.

## Setup (≈ 10 minutes)

### 1. Create a Gmail app password

(If your email is not Gmail, see "Other email providers" below.)

1. Make sure 2-Step Verification is on for your Google account:
   <https://myaccount.google.com/security>
2. Generate an app password:
   <https://myaccount.google.com/apppasswords>
   Name it "Xior monitor". You'll get a 16-character password — copy it.

### 2. Create a new GitHub repo

1. Go to <https://github.com/new>
2. Name it whatever you like (e.g. `xior-monitor`)
3. Make it **Public** — public repos get unlimited free Actions minutes.
   (If you prefer private, the workflow still works but you have a
   ~2000 min/month free quota; see "Reducing frequency" below.)
4. Click **Create repository**

### 3. Upload these files to the repo

Either drag-and-drop the whole folder into the GitHub web UI, or:

```bash
cd xior-monitor
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/xior-monitor.git
git push -u origin main
```

### 4. Add three secrets to the repo

In your repo on GitHub: **Settings → Secrets and variables → Actions → New repository secret**

Add three secrets:

| Name        | Value                                                     |
|-------------|-----------------------------------------------------------|
| `SMTP_USER` | your Gmail address (e.g. `[email protected]`)           |
| `SMTP_PASS` | the 16-character app password from step 1                 |
| `TO_EMAIL`  | the address that should receive alerts (can be same)      |

### 5. Enable Actions and run once

1. Go to the **Actions** tab.
2. If GitHub asks to enable workflows, click **I understand, enable them**.
3. Click on **Xior Groningen Monitor** in the left sidebar.
4. Click **Run workflow → Run workflow** to test it now.
5. Watch the run — it should finish green in 1-2 minutes.

After that, it'll run automatically every 15 minutes. You'll get one
email the first time it sees Groningen studios, then more emails only
when **new** listings appear.

## Other email providers

The script uses standard SMTP. To use something other than Gmail, change
the values of `SMTP_HOST` and `SMTP_PORT` (set them as additional repo
secrets and the script will pick them up). Common settings:

| Provider | SMTP_HOST              | SMTP_PORT |
|----------|------------------------|-----------|
| Gmail    | smtp.gmail.com         | 587       |
| Outlook  | smtp-mail.outlook.com  | 587       |
| iCloud   | smtp.mail.me.com       | 587       |
| Yahoo    | smtp.mail.yahoo.com    | 587       |

## Reducing frequency (private repos)

The default is every 5 minutes, which on a private repo will exceed the
free quota of ~2000 minutes/month. If your repo is private, edit
`.github/workflows/check.yml` and change the cron line to one of:

```yaml
- cron: "*/15 * * * *"   # every 15 minutes — fits in private free tier
- cron: "*/30 * * * *"   # every 30 minutes — very comfortable
```

Public repos get unlimited free Actions minutes, so leave it at `*/5`.

## Local testing

Want to test the script before pushing to GitHub?

```bash
pip install -r requirements.txt
python -m playwright install chromium
export SMTP_USER="[email protected]"
export SMTP_PASS="your-app-password"
export TO_EMAIL="[email protected]"
python monitor.py
```

## Troubleshooting

- **Workflow shows red X**: click the run, expand "Run monitor", read
  the error. Most common cause is wrong SMTP credentials.
- **No emails despite available studios**: check your spam folder.
  Also: the script only emails on *new* listings, so if it picked them
  up on an earlier run, the state file already knows about them.
- **"Bot-protection challenge" error**: Xior may have added stricter
  protection. Open an issue and we'll add a stealth plugin.
- **GitHub schedules can lag**: cron jobs may run a few minutes late
  during peak times. This is a known GitHub Actions limitation.

## Notes

- Don't trust the script blindly: keep checking the booking site
  manually too, especially Monday mornings.
- Consider also emailing `[email protected]` directly to ask
  about their official future-offer waiting list.
