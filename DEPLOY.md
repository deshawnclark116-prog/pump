# Run it 24/7 for free

This guide puts the radar on a free cloud computer. It runs day and night, restarts itself if it
crashes, and sends alerts to your phone. You don't have to keep anything open.

**Best free option: Oracle Cloud "Always Free".** Oracle gives a small server free forever. Signing
up asks for a card to prove you're a real person. Always Free servers don't charge it.

Takes about 20 minutes, one time.

---

## Step 1: Set up phone alerts (pick one)

**Discord (easiest)**
1. Make a Discord server (the **+** button), or use one you own.
2. Open Server Settings → Integrations → Webhooks → New Webhook → **Copy Webhook URL**.
3. Save that link. You'll paste it in Step 4.

**Telegram**
1. Message **@BotFather**, send `/newbot`, and follow the steps. It gives you a **bot token**.
2. Send your new bot any message.
3. Open `https://api.telegram.org/bot<YOUR TOKEN>/getUpdates` in a browser and copy the number
   after `"chat":{"id":`. That's your **chat id**.

## Step 2: Make the free server

1. Sign up at **oracle.com/cloud/free**.
2. In the menu go to **Compute → Instances → Create instance**.
3. **Image:** click *Edit* and pick **Ubuntu** (22.04 or 24.04).
4. **Shape:** pick one marked **"Always Free eligible"**:
   - `VM.Standard.A1.Flex` (give it 1 OCPU and 6 GB), or
   - `VM.Standard.E2.1.Micro`

   If it says *"out of capacity"*, try the other shape or try again later.
5. **SSH keys:** choose **"Generate a key pair for me"** and **download the private key**. Keep it safe.
6. Click **Create**. When it's running, copy its **Public IP address**.

## Step 3: Connect to it

You can skip the key file entirely: open the instance in the Oracle website, click
**Console connection → Launch Cloud Shell connection**, and you get a terminal in the browser.

Or use your own computer's terminal, with the key you downloaded:

```bash
chmod 600 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@YOUR_PUBLIC_IP
```

## Step 4: Install the radar (one command)

Paste this into the server's terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/deshawnclark116-prog/pump/main/deploy/install.sh | bash
```

> Until the code is merged into `main`, use this version instead:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/deshawnclark116-prog/pump/ccr-bbdb2d78-8yqlgn/deploy/install.sh | BRANCH=ccr-bbdb2d78-8yqlgn bash
> ```

It asks for your Discord link or Telegram token and chat id. Paste them in, or press Enter to skip.

When it finishes, your phone gets **"✅ Meme radar started"**. That's it. You can close the window.

---

## Everyday use

Run these on the server (connect again as in Step 3):

| I want to… | Type this |
|---|---|
| See it working live | `journalctl -u memeradar -f` (Ctrl+C to stop watching; the radar keeps running) |
| See every alert and how it did | `cd ~/pump && set -a && . ./.env && set +a && .venv/bin/python -m memeradar report` |
| Change where alerts go | `nano ~/pump/.env` then `sudo systemctl restart memeradar` |
| Change the settings (thresholds, take-profit %, etc.) | `nano ~/pump/config.toml` then `sudo systemctl restart memeradar` |
| Get the latest code | paste the Step 4 command again (keeps your settings and data) |
| Stop it | `sudo systemctl stop memeradar` |
| Start it again | `sudo systemctl start memeradar` |

## How you know it's still alive

- You get "✅ Meme radar started" every time it starts, including after a server reboot or a crash restart.
- If you haven't had any alerts in a long time, check it with `journalctl -u memeradar -f`.
  You should see a `cycle: tracking … tokens` line every 20 seconds.

## Other free options

- **Google Cloud free tier:** one `e2-micro` server (in `us-west1`, `us-central1` or `us-east1`)
  is free each month. Pick Ubuntu, then do Steps 3–4 the same way.
- **An old laptop or a computer that stays on:** if it runs Ubuntu, do Step 4. On Mac or Windows,
  see the Quick start in README.md. It only runs while that computer is on and online.

## Good to know

- It uses very little: about 100 MB of memory and a little internet traffic, well inside the free limits.
- Old score history is deleted after 14 days so the disk never fills up. Alerts and their results
  are kept forever.
- Oracle sometimes reclaims Always Free servers that sit **almost completely idle** for a week.
  The radar is busy all day, so this shouldn't happen.
