# 🚀 Ops Command Center

A secure, unified operations dashboard tracking **Live Git Deployments (Render & Cloudflare)**, **Uptime Monitors (UptimeRobot)**, and **1-Click Quick Launch Links (TinyURL with Custom Titles)**.

- **Main Application**: Python Flask (`app.py`) with built-in template rendering (`templates/index.html`).
- **Database**: **PostgreSQL** (`db.py` - Neon, Render, or Supabase) with automatic schema setup and `.env` seeding.
- **Security**: Protected by Admin Login authentication.
- **Interactive Control Panel**: Manage API keys, accounts, custom titles, and database URLs right from the UI!
- **Hosted on**: **Vercel** with custom domain **`uptime.digitaltask.in`**.
- **100% Mobile & Tablet Responsive**.

---

## 📁 Clean Project Architecture

```text
uptime-dashboard/
├── app.py              # 🐍 Main Flask Application (API & Template Router)
├── db.py               # 🔌 PostgreSQL database connection & table manager
├── templates/
│   └── index.html      # 🌐 Built-in Flask HTML Template (Admin Login & Control Panel)
├── requirements.txt    # Python dependencies for Vercel
├── vercel.json         # Vercel serverless configuration
├── .env.example        # Environment variables reference
├── .gitignore
└── README.md
```

---

## 🚀 Deployment Guide (Vercel)

### Step 1: Deploy to Vercel

1. Log in to [vercel.com](https://vercel.com) using your GitHub account (`rohanrarya`).
2. Click **"Add New Project"** → Import your **`uptime-dashboard`** repository.
3. In **Environment Variables**, add:
   - `ADMIN_PASSWORD` = Your desired admin password (e.g. `MyStrongPassword123`)
   - `DATABASE_URL` = Your PostgreSQL connection string (from Neon, Render, or Supabase)
   - `UPTIMEROBOT_API_KEY` = Your UptimeRobot read-only key
   - `TINYURL_API_TOKEN` = Your TinyURL API token
   - *(Optional)* `RENDER_API_KEY`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`
4. Click **Deploy**. Vercel will build and assign you a live URL.

---

### Step 2: Configure Subdomain `uptime.digitaltask.in`

To point your subdomain **`uptime.digitaltask.in`** to this dashboard:

1. **In Cloudflare DNS**:
   - Go to Cloudflare Dashboard → **`digitaltask.in`** → **DNS** → **Records**.
   - Edit the **`uptime`** CNAME record:
     - **Name**: `uptime`
     - **Target**: `cname.vercel-dns.com`
     - **Proxy status**: DNS only (Grey cloud)
   - *(Delete the unnecessary `api-uptime` record if you created one earlier)*.

2. **In Vercel**:
   - Go to your project in Vercel → **Settings** → **Domains**.
   - Add **`uptime.digitaltask.in`**.
   - Vercel will verify the DNS record and turn **Green**!

---

## 💻 Local Development

Run the entire application locally on your PC with a single command:

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Start the app:
   ```bash
   python app.py
   ```

3. Open your browser at: [http://127.0.0.1:5000](http://127.0.0.1:5000)  
   Log in with **Username**: `admin` | **Password**: your password (default: `admin123`).

---

## ⚙️ Interactive Control Panel Features

Click the **⚙️ Control Panel** button in the dashboard header to:
- **🔌 PostgreSQL Database**: View connection health, update `DATABASE_URL`, and click **Test Connection**.
- **🏢 Multi-Account Manager**: Add/Delete unlimited Render and Cloudflare accounts without redeploying.
- **🏷️ Project Titles & TinyURL**: Assign friendly names to any alias (e.g., `rohanrarya` ➔ `My Portfolio`).
- **🔑 Service Keys**: Update UptimeRobot & TinyURL tokens on the fly.
- **🔒 Security**: Change admin password.
