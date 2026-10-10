# 🚀 Ops Command Center

A secure, unified operations dashboard tracking **Live Git Deployments (Render & Cloudflare)**, **Uptime Monitors (UptimeRobot)**, and **1-Click Quick Launch Links (TinyURL with Custom Titles)**.

- **Backend**: Python Flask hosted on **Vercel** (`api/index.py`).
- **Frontend**: Pure Static HTML/CSS/JS hosted on **Cloudflare Pages** (`public/index.html`).
- **Database**: **PostgreSQL** (Neon, Render, or Supabase) with automatic schema setup and `.env` seeding.
- **Security**: Protected by Admin Login authentication.
- **Interactive Control Panel**: Manage API keys, accounts, custom titles, and database URLs right from the UI!
- **100% Mobile & Tablet Responsive**.

---

## 📁 Project Architecture

```text
uptime-dashboard/
├── api/
│   ├── index.py          # 🐍 Python Flask Backend API Server
│   └── db.py             # 🔌 PostgreSQL database connection & table manager
├── public/
│   └── index.html        # 🌐 Pure Static Frontend with Admin Login & Control Panel
├── requirements.txt      # Python dependencies for Vercel
├── vercel.json           # Vercel serverless routing
├── run.py                # 1-Command Local Development Server
├── .env.example          # Environment variables reference
└── README.md
```

---

## 🚀 Deployment Guide

### Step 1: Deploy Backend to Vercel

1. Log in to [vercel.com](https://vercel.com) using your GitHub account (`rohanrarya`).
2. Click **"Add New Project"** → Import your **`uptime-dashboard`** repository.
3. In **Environment Variables**, add:
   - `ADMIN_PASSWORD` = Your desired admin password (e.g. `MyStrongPassword123`)
   - `DATABASE_URL` = Your PostgreSQL connection string (from Neon, Render, or Supabase)
   - `UPTIMEROBOT_API_KEY` = Your UptimeRobot read-only key
   - `TINYURL_API_TOKEN` = Your TinyURL API token
   - *(Optional)* `RENDER_API_KEY`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`
4. Click **Deploy**. Vercel will build and assign you a live backend URL (e.g., `https://uptime-dashboard.vercel.app`).

---

### Step 2: Deploy Frontend to Cloudflare Pages

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com) → **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**.
2. Select repository: **`uptime-dashboard`**.
3. Set Build Configuration:
   - **Framework preset**: `None`
   - **Build command**: *(leave completely empty)*
   - **Build output directory**: `public`
4. Click **Save and Deploy**.

---

### Step 3: Configure Subdomain `uptime.digitaltask.in`

To direct `uptime.digitaltask.in` to this new dashboard:

#### 1. Disable the Old Cloudflare Worker Route:
- Go to Cloudflare Dashboard → Select **`digitaltask.in`** domain.
- Navigate to **Workers Routes** (in the sidebar under Workers & Pages).
- If there is a route matching `uptime.digitaltask.in/*`, click **Disable** or **Delete**.

#### 2. Attach Subdomain to Cloudflare Pages:
- In Cloudflare Dashboard → **Workers & Pages** → Click your **`uptime-dashboard`** Pages project.
- Go to the **Custom Domains** tab → Click **Set up a custom domain**.
- Type: **`uptime.digitaltask.in`** and click **Continue** → **Activate domain**.
- Cloudflare will configure the DNS CNAME automatically!

#### 3. Route Backend API via `api-uptime.digitaltask.in`:
- In Cloudflare Dashboard → **digitaltask.in** → **DNS** → **Records** → Click **Add record**:
  - **Type**: `CNAME`
  - **Name**: `api-uptime`
  - **Target**: `cname.vercel-dns.com`
  - **Proxy status**: DNS only (Grey cloud)
- In Vercel Dashboard → Your project → **Settings** → **Domains** → Add **`api-uptime.digitaltask.in`**.
- Now your frontend and backend run seamlessly together under your own domain!

---

## 💻 Local Development

You can run both frontend and backend locally with a single command:

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the server:
   ```bash
   python run.py
   ```

3. Open your browser at: [http://127.0.0.1:5000](http://127.0.0.1:5000)
   - Default login: Username: `admin` | Password: `admin123` (or whatever you set in `ADMIN_PASSWORD`).

---

## ⚙️ Interactive Control Panel Features

Click the **⚙️ Control Panel** button in the dashboard header to:
- **🔌 PostgreSQL Database**: View connection health, update `DATABASE_URL`, and click **Test Connection**.
- **🏢 Multi-Account Manager**: Add/Delete unlimited Render and Cloudflare accounts without redeploying.
- **🏷️ Project Titles & TinyURL**: Assign friendly names to any alias (e.g., `rohanrarya` ➔ `My Portfolio`).
- **🔑 Service Keys**: Update UptimeRobot & TinyURL tokens on the fly.
- **🔒 Security**: Change admin password.
