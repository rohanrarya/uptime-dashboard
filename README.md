# 🚀 Ops & Uptime Dashboard

A unified, real-time command center for your applications. Tracks:
1. **Live Git Deployments**: Real-time build status (Building 🔄, Live 🟢, Failed 🔴), commit messages, branch, and live preview URLs across **multiple Render and Cloudflare accounts**.
2. **Uptime & Latency Monitors**: 24/7 endpoint ping status, latency sparklines, and incident history powered by UptimeRobot.
3. **100% Mobile Responsive**: Clean dark theme optimized for desktop, tablets, and mobile smartphones.

---

## ⚡ Key Highlights

- **Multi-Account Support**: Connect unlimited Render and Cloudflare accounts simultaneously.
- **Zero-Manual-Setup Auto Discovery**: No need to copy-paste service IDs or project names. Give the dashboard an API key, and it auto-discovers all your services and Pages projects automatically!
- **Fast Build Polling**: When any service is actively building, the dashboard switches to 10-second fast polling so you see the exact moment your `git push` goes live.
- **Edge Powered**: Deploys as a lightweight Cloudflare Pages Worker (`_worker.js`) with zero cold starts.

---

## 🛠️ How to Deploy on Cloudflare Pages (via Git)

### 1. Push to GitHub
Commit and push this repo to your GitHub account:
```bash
git add .
git commit -m "feat: add multi-account deployment tracker"
git push origin main
```

### 2. Connect Repo in Cloudflare Pages
1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com) → **Pages** → **Connect to Git**
2. Select your `uptime-dashboard` repo
3. Build Settings:
   - **Framework preset**: `None`
   - **Build command**: *(leave empty)*
   - **Build output directory**: `public`
4. Click **Save and Deploy**.

---

## 🔑 Environment Variables Setup

Go to: **Cloudflare Pages → Your Project → Settings → Environment Variables**.

Add the variables for whichever services you want to track:

### 1. Uptime Monitoring (UptimeRobot)
| Variable Name | Value | Where to get it |
| :--- | :--- | :--- |
| `UPTIMEROBOT_API_KEY` | `ur...` | [UptimeRobot → My Settings → API Settings](https://uptimerobot.com/dashboard#mySettings) → **Read-Only API Key** |

---

### 2. Multiple Render Accounts (Live Git Deployments)
All services on each account are **auto-discovered** automatically!

**Where to get Render API Key:**
Go to [dashboard.render.com](https://dashboard.render.com) → Top right profile icon → **Account Settings** → **API Keys** → **Create API Key**.

#### For 1 Render Account:
```
RENDER_API_KEY = rnd_your_api_key
RENDER_LABEL   = Render - Main
```

#### For Multiple Render Accounts (Account 1, Account 2, Account 3, etc.):
```
RENDER_API_KEY_1 = rnd_first_account_key
RENDER_LABEL_1   = Render - Personal (Aniruddh / PostPulse)

RENDER_API_KEY_2 = rnd_second_account_key
RENDER_LABEL_2   = Render - Client / Studio
```
*(Or use comma-separated: `RENDER_API_KEYS = rnd_key1,rnd_key2`)*

---

### 3. Multiple Cloudflare Accounts (Live Pages Deployments)
All Pages projects on each account are **auto-discovered** automatically!

**Where to get Cloudflare Credentials:**
- **Account ID**: Shown on the right sidebar of [Cloudflare Dashboard](https://dash.cloudflare.com) on any overview page.
- **API Token**: Go to [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens) → **Create Token** → Select template **"Edit Cloudflare Workers"** (grants read access to Pages & Workers).

#### For 1 Cloudflare Account:
```
CF_ACCOUNT_ID = your_account_id
CF_API_TOKEN  = your_api_token
CF_LABEL      = Cloudflare - Main
```

#### For Multiple Cloudflare Accounts (Account 1, Account 2, etc.):
```
CF_ACCOUNT_ID_1 = your_first_account_id
CF_API_TOKEN_1  = your_first_api_token
CF_LABEL_1      = Cloudflare - Personal

CF_ACCOUNT_ID_2 = your_second_account_id
CF_API_TOKEN_2  = your_second_api_token
CF_LABEL_2      = Cloudflare - Studio
```

---

## 💻 Local Development

1. Copy `.env.example` to `.env` and paste your keys:
   ```bash
   cp .env.example .env
   ```

2. Start the local server:
   ```bash
   node server.js
   ```
   Or double-click `start.bat`.

3. Open [http://localhost:3000](http://localhost:3000)

---

## ✏️ Making Changes to the UI

If you edit `public/index.html` or `lib/deployments.js`, rebuild the worker files before committing:

```bash
node build-worker.js
```

Then commit and push:
```bash
git add .
git commit -m "update dashboard"
git push origin main
```
Cloudflare Pages will auto-deploy your updates within seconds!
