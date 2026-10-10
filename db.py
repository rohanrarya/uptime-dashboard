# =============================================================================
# Database Module for Ops Dashboard
# Supports PostgreSQL (via DATABASE_URL) with safe SQLite fallback for local test
# =============================================================================

import os
import re
from datetime import datetime

# Determine database driver
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()

def get_connection():
    """
    Returns a database connection.
    Uses PostgreSQL if DATABASE_URL is set, otherwise falls back to local SQLite.
    """
    db_url = os.environ.get('DATABASE_URL', '').strip()
    
    if db_url and (db_url.startswith('postgres://') or db_url.startswith('postgresql://')):
        # Fix legacy postgres:// URI for modern drivers
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
            conn.autocommit = True
            return conn, 'postgres'
        except Exception as e:
            try:
                import psycopg
                from psycopg.rows import dict_row
                conn = psycopg.connect(db_url, row_factory=dict_row)
                conn.autocommit = True
                return conn, 'postgres'
            except Exception as inner_e:
                print(f"[DB Warning] Could not connect to PostgreSQL: {e} | {inner_e}. Falling back to SQLite.")
    
    # Fallback to local SQLite database (use /tmp on serverless environments like Vercel)
    import sqlite3
    if os.environ.get('VERCEL') or os.path.exists('/tmp'):
        db_path = '/tmp/local.db'
    else:
        db_path = os.path.join(os.path.dirname(__file__), 'local.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn, 'sqlite'


def init_db():
    """
    Creates necessary tables if they don't exist yet.
    """
    conn, engine = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == 'postgres':
            # PostgreSQL Schema
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key_name VARCHAR(100) PRIMARY KEY,
                    key_value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY,
                    provider VARCHAR(50) NOT NULL,
                    account_name VARCHAR(100) NOT NULL,
                    account_id VARCHAR(100),
                    api_key TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS project_titles (
                    id SERIAL PRIMARY KEY,
                    alias VARCHAR(100) UNIQUE NOT NULL,
                    custom_title VARCHAR(200) NOT NULL,
                    target_url TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
        else:
            # SQLite Schema
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key_name TEXT PRIMARY KEY,
                    key_value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    account_id TEXT,
                    api_key TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS project_titles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alias TEXT UNIQUE NOT NULL,
                    custom_title TEXT NOT NULL,
                    target_url TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
    finally:
        cur.close()
        conn.close()


def sync_env_to_db():
    """
    Auto-syncs credentials from environment variables into the database if missing.
    """
    from werkzeug.security import generate_password_hash
    
    conn, engine = get_connection()
    cur = conn.cursor()
    
    try:
        # 1. Initialize default admin user if no users exist
        cur.execute("SELECT COUNT(*) as cnt FROM users;")
        row = cur.fetchone()
        count = row['cnt'] if isinstance(row, dict) or hasattr(row, '__getitem__') else row[0]
        
        if count == 0:
            default_pass = os.environ.get('ADMIN_PASSWORD', 'admin123').strip()
            hashed = generate_password_hash(default_pass)
            if engine == 'postgres':
                cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s);", ('admin', hashed))
            else:
                cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?);", ('admin', hashed))
                conn.commit()
            print("[DB Sync] Initialized default admin user.")

        # Helper to get setting from DB
        def get_db_setting(key):
            if engine == 'postgres':
                cur.execute("SELECT key_value FROM settings WHERE key_name = %s;", (key,))
            else:
                cur.execute("SELECT key_value FROM settings WHERE key_name = ?;", (key,))
            r = cur.fetchone()
            if r:
                return r['key_value'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0]
            return None

        # Helper to save setting to DB
        def set_db_setting(key, val):
            if engine == 'postgres':
                cur.execute("""
                    INSERT INTO settings (key_name, key_value, updated_at) 
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (key_name) DO UPDATE SET key_value = EXCLUDED.key_value, updated_at = CURRENT_TIMESTAMP;
                """, (key, val))
            else:
                cur.execute("""
                    INSERT INTO settings (key_name, key_value, updated_at) 
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value, updated_at = datetime('now');
                """, (key, val))
                conn.commit()

        # 2. Sync UptimeRobot Key
        uptimerobot_key = os.environ.get('UPTIMEROBOT_API_KEY', '').strip()
        if uptimerobot_key and not get_db_setting('uptimerobot_api_key'):
            set_db_setting('uptimerobot_api_key', uptimerobot_key)
            print("[DB Sync] Synced UPTIMEROBOT_API_KEY to DB.")

        # 3. Sync TinyURL Token
        tinyurl_token = os.environ.get('TINYURL_API_TOKEN', os.environ.get('TINYURL_API_KEY', '')).strip()
        if tinyurl_token and not get_db_setting('tinyurl_api_token'):
            set_db_setting('tinyurl_api_token', tinyurl_token)
            print("[DB Sync] Synced TINYURL_API_TOKEN to DB.")

        # 4. Sync Database URL setting for display in UI
        db_url_env = os.environ.get('DATABASE_URL', '').strip()
        if db_url_env and not get_db_setting('database_url'):
            set_db_setting('database_url', db_url_env)

        # 5. Sync Render Accounts
        # Check single
        render_key = os.environ.get('RENDER_API_KEY', '').strip()
        render_label = os.environ.get('RENDER_LABEL', 'Render - Main').strip()
        if render_key:
            cur.execute("SELECT COUNT(*) as cnt FROM accounts WHERE provider = 'render' AND api_key = %s;" if engine == 'postgres' else "SELECT COUNT(*) as cnt FROM accounts WHERE provider = 'render' AND api_key = ?;", (render_key,))
            r = cur.fetchone()
            cnt = r['cnt'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0]
            if cnt == 0:
                if engine == 'postgres':
                    cur.execute("INSERT INTO accounts (provider, account_name, api_key) VALUES ('render', %s, %s);", (render_label, render_key))
                else:
                    cur.execute("INSERT INTO accounts (provider, account_name, api_key) VALUES ('render', ?, ?);", (render_label, render_key))
                    conn.commit()
                print("[DB Sync] Synced Render account to DB.")

        # Check numbered Render: RENDER_API_KEY_1..10
        for i in range(1, 11):
            k = os.environ.get(f'RENDER_API_KEY_{i}', '').strip()
            lbl = os.environ.get(f'RENDER_LABEL_{i}', f'Render (Account {i})').strip()
            if k:
                cur.execute("SELECT COUNT(*) as cnt FROM accounts WHERE provider = 'render' AND api_key = %s;" if engine == 'postgres' else "SELECT COUNT(*) as cnt FROM accounts WHERE provider = 'render' AND api_key = ?;", (k,))
                r = cur.fetchone()
                cnt = r['cnt'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0]
                if cnt == 0:
                    if engine == 'postgres':
                        cur.execute("INSERT INTO accounts (provider, account_name, api_key) VALUES ('render', %s, %s);", (lbl, k))
                    else:
                        cur.execute("INSERT INTO accounts (provider, account_name, api_key) VALUES ('render', ?, ?);", (lbl, k))
                        conn.commit()

        # 6. Sync Cloudflare Accounts
        cf_acc = os.environ.get('CF_ACCOUNT_ID', os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')).strip()
        cf_tok = os.environ.get('CF_API_TOKEN', os.environ.get('CLOUDFLARE_API_TOKEN', os.environ.get('CF_API_KEY', ''))).strip()
        cf_lbl = os.environ.get('CF_LABEL', 'Cloudflare - Main').strip()
        if cf_acc and cf_tok:
            cur.execute("SELECT COUNT(*) as cnt FROM accounts WHERE provider = 'cloudflare' AND account_id = %s;" if engine == 'postgres' else "SELECT COUNT(*) as cnt FROM accounts WHERE provider = 'cloudflare' AND account_id = ?;", (cf_acc,))
            r = cur.fetchone()
            cnt = r['cnt'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0]
            if cnt == 0:
                if engine == 'postgres':
                    cur.execute("INSERT INTO accounts (provider, account_name, account_id, api_key) VALUES ('cloudflare', %s, %s, %s);", (cf_lbl, cf_acc, cf_tok))
                else:
                    cur.execute("INSERT INTO accounts (provider, account_name, account_id, api_key) VALUES ('cloudflare', ?, ?, ?);", (cf_lbl, cf_acc, cf_tok))
                    conn.commit()
                print("[DB Sync] Synced Cloudflare account to DB.")

        # Check numbered Cloudflare: CF_ACCOUNT_ID_1..10
        for i in range(1, 11):
            acc_i = os.environ.get(f'CF_ACCOUNT_ID_{i}', '').strip()
            tok_i = os.environ.get(f'CF_API_TOKEN_{i}', '').strip()
            lbl_i = os.environ.get(f'CF_LABEL_{i}', f'Cloudflare (Account {i})').strip()
            if acc_i and tok_i:
                cur.execute("SELECT COUNT(*) as cnt FROM accounts WHERE provider = 'cloudflare' AND account_id = %s;" if engine == 'postgres' else "SELECT COUNT(*) as cnt FROM accounts WHERE provider = 'cloudflare' AND account_id = ?;", (acc_i,))
                r = cur.fetchone()
                cnt = r['cnt'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0]
                if cnt == 0:
                    if engine == 'postgres':
                        cur.execute("INSERT INTO accounts (provider, account_name, account_id, api_key) VALUES ('cloudflare', %s, %s, %s);", (lbl_i, acc_i, tok_i))
                    else:
                        cur.execute("INSERT INTO accounts (provider, account_name, account_id, api_key) VALUES ('cloudflare', ?, ?, ?);", (lbl_i, acc_i, tok_i))
                        conn.commit()

        # 7. Sync custom project titles: TINYURL_TITLES="rohanrarya:My Portfolio,predict:AI Model"
        titles_str = os.environ.get('TINYURL_TITLES', '').strip()
        if titles_str:
            pairs = [p.strip() for p in titles_str.split(',') if ':' in p]
            for p in pairs:
                parts = p.split(':', 1)
                alias = parts[0].strip()
                title = parts[1].strip()
                if alias and title:
                    if engine == 'postgres':
                        cur.execute("""
                            INSERT INTO project_titles (alias, custom_title, updated_at) 
                            VALUES (%s, %s, CURRENT_TIMESTAMP)
                            ON CONFLICT (alias) DO NOTHING;
                        """, (alias, title))
                    else:
                        cur.execute("""
                            INSERT OR IGNORE INTO project_titles (alias, custom_title, updated_at) 
                            VALUES (?, ?, datetime('now'));
                        """, (alias, title))
                        conn.commit()
    except Exception as e:
        print(f"[DB Sync Error] {e}")
    finally:
        cur.close()
        conn.close()


# =============================================================================
# Database Query Helpers (Simple & Clean)
# =============================================================================

def get_setting(key, default=''):
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("SELECT key_value FROM settings WHERE key_name = %s;", (key,))
        else:
            cur.execute("SELECT key_value FROM settings WHERE key_name = ?;", (key,))
        row = cur.fetchone()
        if row:
            return row['key_value'] if isinstance(row, dict) or hasattr(row, '__getitem__') else row[0]
        return default
    finally:
        cur.close()
        conn.close()


def set_setting(key, value):
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("""
                INSERT INTO settings (key_name, key_value, updated_at) 
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key_name) DO UPDATE SET key_value = EXCLUDED.key_value, updated_at = CURRENT_TIMESTAMP;
            """, (key, value))
        else:
            cur.execute("""
                INSERT INTO settings (key_name, key_value, updated_at) 
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value, updated_at = datetime('now');
            """, (key, value))
            conn.commit()
    finally:
        cur.close()
        conn.close()


def get_all_accounts():
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, provider, account_name, account_id, api_key, is_active FROM accounts ORDER BY id ASC;")
        rows = cur.fetchall()
        accounts = []
        for r in rows:
            accounts.append({
                'id': r['id'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0],
                'provider': r['provider'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[1],
                'account_name': r['account_name'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[2],
                'account_id': r['account_id'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[3],
                'api_key': r['api_key'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[4],
                'is_active': bool(r['is_active'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[5])
            })
        return accounts
    finally:
        cur.close()
        conn.close()


def add_account(provider, account_name, api_key, account_id=None):
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("""
                INSERT INTO accounts (provider, account_name, account_id, api_key) 
                VALUES (%s, %s, %s, %s) RETURNING id;
            """, (provider, account_name, account_id, api_key))
            new_id = cur.fetchone()['id']
        else:
            cur.execute("""
                INSERT INTO accounts (provider, account_name, account_id, api_key) 
                VALUES (?, ?, ?, ?);
            """, (provider, account_name, account_id, api_key))
            conn.commit()
            new_id = cur.lastrowid
        return new_id
    finally:
        cur.close()
        conn.close()


def delete_account(account_id):
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("DELETE FROM accounts WHERE id = %s;", (account_id,))
        else:
            cur.execute("DELETE FROM accounts WHERE id = ?;", (account_id,))
            conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def get_all_project_titles():
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, alias, custom_title, target_url FROM project_titles ORDER BY id ASC;")
        rows = cur.fetchall()
        titles = []
        for r in rows:
            titles.append({
                'id': r['id'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0],
                'alias': r['alias'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[1],
                'custom_title': r['custom_title'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[2],
                'target_url': r['target_url'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[3]
            })
        return titles
    finally:
        cur.close()
        conn.close()


def set_project_title(alias, custom_title, target_url=None):
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("""
                INSERT INTO project_titles (alias, custom_title, target_url, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (alias) DO UPDATE 
                SET custom_title = EXCLUDED.custom_title, target_url = EXCLUDED.target_url, updated_at = CURRENT_TIMESTAMP;
            """, (alias, custom_title, target_url))
        else:
            cur.execute("""
                INSERT INTO project_titles (alias, custom_title, target_url, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(alias) DO UPDATE 
                SET custom_title = excluded.custom_title, target_url = excluded.target_url, updated_at = datetime('now');
            """, (alias, custom_title, target_url))
            conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def delete_project_title(title_id):
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("DELETE FROM project_titles WHERE id = %s;", (title_id,))
        else:
            cur.execute("DELETE FROM project_titles WHERE id = ?;", (title_id,))
            conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def get_user(username='admin'):
    try:
        conn, engine = get_connection()
        cur = conn.cursor()
        try:
            if engine == 'postgres':
                cur.execute("SELECT id, username, password_hash FROM users WHERE username = %s;", (username,))
            else:
                cur.execute("SELECT id, username, password_hash FROM users WHERE username = ?;", (username,))
            r = cur.fetchone()
            if r:
                return {
                    'id': r['id'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0],
                    'username': r['username'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[1],
                    'password_hash': r['password_hash'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[2]
                }
            return None
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"[DB Warning] Could not query user '{username}': {e}")
        return None


def update_admin_password(new_password):
    from werkzeug.security import generate_password_hash
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        hashed = generate_password_hash(new_password)
        if engine == 'postgres':
            cur.execute("UPDATE users SET password_hash = %s WHERE username = 'admin';", (hashed,))
        else:
            cur.execute("UPDATE users SET password_hash = ? WHERE username = 'admin';", (hashed,))
            conn.commit()
        return True
    finally:
        cur.close()
        conn.close()
