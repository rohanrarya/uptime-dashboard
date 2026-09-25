# 📊 Uptime Dashboard

A lightweight monitoring dashboard built on **Cloudflare Pages** that shows the live status of all your UptimeRobot monitors — uptime %, response time sparklines, and incident history.

---

## 🚀 Deploy to Cloudflare Pages (via Git)

### Step 1 — Push this repo to GitHub
Make sure this project is pushed to your GitHub account.

### Step 2 — Connect to Cloudflare Pages
1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com) → **Pages**
2. Click **"Create a project"** → **"Connect to Git"**
3. Select your GitHub repo
4. Use these build settings:

| Field | Value |
|-------|-------|
| **Framework preset** | `None` |
| **Build command** | *(leave empty)* |
| **Build output directory** | `public` |

### Step 3 — Add Environment Variables ⚠️ Required
In Cloudflare Pages → your project → **Settings** → **Environment Variables** → Add:

| Variable Name | Value | Where to get it |
|---------------|-------|-----------------|
| `UPTIMEROBOT_API_KEY` | `ur...` (your Read-Only API key) | [UptimeRobot → My Settings → API Settings](https://uptimerobot.com/dashboard#mySettings) → **Read-Only API Key** |

> ⚠️ **Never commit your API key to GitHub.** Only add it in the Cloudflare dashboard as an environment variable.

### Step 4 — Deploy
Click **"Save and Deploy"**. Cloudflare will build and deploy automatically.

From now on, every time you push to GitHub → Cloudflare **auto-redeploys**. No manual uploading needed.

---

## 🔑 Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `UPTIMEROBOT_API_KEY` | ✅ Yes | Your UptimeRobot **Read-Only** API key. Found in UptimeRobot → My Settings → API Settings. Starts with `ur`. |

---

## 📁 Project Structure

```
├── _worker.js          # Cloudflare Pages Worker (API proxy + dashboard server)
├── public/
│   └── index.html      # Dashboard HTML (embedded in worker at build time)
├── build-worker.js     # Local build script — run before committing changes
├── wrangler.toml       # Cloudflare config
├── .env.example        # Template for local development
└── README.md           # This file
```

---

## 🛠️ Local Development

1. Copy `.env.example` to `.env` and fill in your API key:
   ```
   UPTIMEROBOT_API_KEY=ur_your_key_here
   PORT=3000
   ```

2. Run the local server:
   ```bash
   node server.js
   ```
   Or double-click `start.bat` on Windows.

3. Open [http://localhost:3000](http://localhost:3000)

---

## ✏️ Making Changes to the Dashboard

If you edit `public/index.html`, you must rebuild the worker before committing:

```bash
node build-worker.js
```

This embeds the updated HTML into `_worker.js`. Then commit and push — Cloudflare auto-redeploys.

---

## 📡 How It Works

```
Browser → Cloudflare Pages (_worker.js)
                │
                ├── GET /          → Serves the dashboard HTML
                └── POST /api/monitors → Proxies to UptimeRobot API
                                         (keeps your API key secret)
```

Your API key **never reaches the browser**. It stays inside the Cloudflare Worker on the server side.
