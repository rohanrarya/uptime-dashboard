# =============================================================================
# Database Module for Ops Dashboard
# Supports PostgreSQL (via DATABASE_URL) with safe SQLite fallback for local test
# =============================================================================

import os
import re
from datetime import datetime

# Determine database driver
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()

def get_pooled_db_url(raw_url):
    """
    Ensures connection pooling for Neon PostgreSQL (via PgBouncer -pooler endpoint).
    Also normalizes legacy postgres:// scheme to postgresql://.
    """
    if not raw_url:
        return raw_url
    url = raw_url.strip()
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    
    # Automatically enable Neon Connection Pooling (PgBouncer) if not already using -pooler
    if 'neon.tech' in url and '-pooler' not in url:
        url = re.sub(r'(@[a-zA-Z0-9-]+)(\.[a-zA-Z0-9-]+\.aws\.neon\.tech)', r'\1-pooler\2', url)
    
    return url


# =============================================================================
# Persistent Connection Pool / Reuse for Serverless (Fast Latency)
# =============================================================================
_persistent_pg_conn = None
_persistent_sqlite_conn = None
_db_initialized = False
_env_synced = False


class PooledConnectionWrapper:
    """
    Transparent connection wrapper that keeps the underlying TCP/SSL socket
    alive across requests in warm serverless instances while allowing caller
    code to call `conn.close()` without tearing down the connection.
    """
    def __init__(self, raw_conn, engine):
        self._raw_conn = raw_conn
        self.engine = engine

    def cursor(self, *args, **kwargs):
        return self._raw_conn.cursor(*args, **kwargs)

    def commit(self):
        return self._raw_conn.commit()

    def rollback(self):
        return self._raw_conn.rollback()

    def close(self):
        # NO-OP: Keep underlying connection open and warm!
        pass

    def real_close(self):
        try:
            self._raw_conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()

    def __getattr__(self, name):
        return getattr(self._raw_conn, name)


def reset_persistent_connection():
    global _persistent_pg_conn, _persistent_sqlite_conn
    try:
        if _persistent_pg_conn:
            _persistent_pg_conn.close()
    except Exception:
        pass
    _persistent_pg_conn = None
    _persistent_sqlite_conn = None


def get_connection():
    """
    Returns a database connection with auto-retry and persistent connection reuse.
    Reuses open connections across warm serverless requests to eliminate 300-500ms
    TLS handshakes on every query.
    """
    global _persistent_pg_conn, _persistent_sqlite_conn
    raw_url = os.environ.get('DATABASE_URL', '').strip()
    
    if raw_url and (raw_url.startswith('postgres://') or raw_url.startswith('postgresql://')):
        # Check if persistent connection is still valid and alive
        if _persistent_pg_conn is not None:
            try:
                if hasattr(_persistent_pg_conn, 'closed') and _persistent_pg_conn.closed == 0:
                    return PooledConnectionWrapper(_persistent_pg_conn, 'postgres'), 'postgres'
            except Exception:
                _persistent_pg_conn = None

        db_url = get_pooled_db_url(raw_url)
        
        # Retry up to 3 times to allow suspended Neon databases to wake up smoothly
        for attempt in range(3):
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                conn = psycopg2.connect(
                    db_url,
                    cursor_factory=RealDictCursor,
                    connect_timeout=15,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5
                )
                conn.autocommit = True
                _persistent_pg_conn = conn
                return PooledConnectionWrapper(_persistent_pg_conn, 'postgres'), 'postgres'
            except Exception as e:
                if attempt < 2:
                    import time
                    time.sleep(1.0)
                    continue
                try:
                    import psycopg
                    from psycopg.rows import dict_row
                    conn = psycopg.connect(db_url, row_factory=dict_row)
                    conn.autocommit = True
                    _persistent_pg_conn = conn
                    return PooledConnectionWrapper(_persistent_pg_conn, 'postgres'), 'postgres'
                except Exception as inner_e:
                    print(f"[DB Warning] Could not connect to PostgreSQL: {e} | {inner_e}. Falling back to SQLite.")
    
    # Fallback to local SQLite database (reused within warm serverless container)
    if _persistent_sqlite_conn is not None:
        try:
            return PooledConnectionWrapper(_persistent_sqlite_conn, 'sqlite'), 'sqlite'
        except Exception:
            _persistent_sqlite_conn = None

    import sqlite3
    if os.environ.get('VERCEL') or os.path.exists('/tmp'):
        db_path = '/tmp/local.db'
    else:
        db_path = os.path.join(os.path.dirname(__file__), 'local.db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _persistent_sqlite_conn = conn
    return PooledConnectionWrapper(_persistent_sqlite_conn, 'sqlite'), 'sqlite'


def init_db(force=False):
    """
    Creates necessary tables if they don't exist yet.
    Fast check avoids re-running DDL statements on every serverless invocation.
    """
    global _db_initialized
    if _db_initialized and not force:
        return

    conn, engine = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == 'postgres':
            # Fast check: If project_titles table already exists, schema is already intact
            try:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_schema = 'public' AND table_name = 'project_titles'
                    );
                """)
                row = cur.fetchone()
                exists = row['exists'] if isinstance(row, dict) else (row[0] if row else False)
                if exists and not force:
                    _db_initialized = True
                    return
            except Exception:
                pass

            # PostgreSQL Schema
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role VARCHAR(20) DEFAULT 'admin',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            try:
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'admin';")
            except Exception:
                pass

            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) NOT NULL,
                    token_hash VARCHAR(64) UNIQUE NOT NULL,
                    device_info TEXT,
                    ip_address VARCHAR(50),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                    role TEXT DEFAULT 'admin',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            try:
                cur.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'admin';")
                conn.commit()
            except Exception:
                pass

            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL,
                    device_info TEXT,
                    ip_address TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_active DATETIME DEFAULT CURRENT_TIMESTAMP
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


def sync_env_to_db(force=False):
    """
    Auto-syncs credentials from environment variables into the database if missing.
    Skips redundant work across warm serverless requests.
    """
    global _env_synced
    if _env_synced and not force:
        return

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
                cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s);", ('admin', hashed, 'admin'))
            else:
                cur.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?);", ('admin', hashed, 'admin'))
                conn.commit()
            print("[DB Sync] Initialized default admin user with administrative privilege.")

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
        _env_synced = True
    except Exception as e:
        print(f"[DB Sync Error] {e}")
    finally:
        cur.close()
        conn.close()


# =============================================================================
# In-Memory Cache (Instant sub-millisecond responses)
# =============================================================================
import time

_settings_cache = {}
_settings_cache_ts = {}

_accounts_cache = None
_accounts_cache_ts = 0

_titles_cache = None
_titles_cache_ts = 0


def get_setting(key, default=''):
    now = time.time()
    if key in _settings_cache and (now - _settings_cache_ts.get(key, 0) < 60):
        return _settings_cache[key]

    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("SELECT key_value FROM settings WHERE key_name = %s;", (key,))
        else:
            cur.execute("SELECT key_value FROM settings WHERE key_name = ?;", (key,))
        row = cur.fetchone()
        val = (row['key_value'] if isinstance(row, dict) or hasattr(row, '__getitem__') else row[0]) if row else default
        _settings_cache[key] = val
        _settings_cache_ts[key] = now
        return val
    finally:
        cur.close()
        conn.close()


def set_setting(key, value):
    _settings_cache[key] = value
    _settings_cache_ts[key] = time.time()

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
    global _accounts_cache, _accounts_cache_ts
    now = time.time()
    if _accounts_cache is not None and (now - _accounts_cache_ts < 30):
        return _accounts_cache

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
        _accounts_cache = accounts
        _accounts_cache_ts = now
        return accounts
    finally:
        cur.close()
        conn.close()


def add_account(provider, account_name, api_key, account_id=None):
    global _accounts_cache
    _accounts_cache = None

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
    global _accounts_cache
    _accounts_cache = None

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
    global _titles_cache, _titles_cache_ts
    now = time.time()
    if _titles_cache is not None and (now - _titles_cache_ts < 30):
        return _titles_cache

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
        _titles_cache = titles
        _titles_cache_ts = now
        return titles
    finally:
        cur.close()
        conn.close()


def set_project_title(alias, custom_title, target_url=None):
    global _titles_cache
    _titles_cache = None

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
    global _titles_cache
    _titles_cache = None

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
                cur.execute("SELECT id, username, password_hash, COALESCE(role, 'admin') as role FROM users WHERE username = %s;", (username,))
            else:
                cur.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?;", (username,))
            r = cur.fetchone()
            if r:
                return {
                    'id': r['id'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0],
                    'username': r['username'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[1],
                    'password_hash': r['password_hash'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[2],
                    'role': (r['role'] if isinstance(r, dict) or hasattr(r, '__getitem__') else (r[3] if len(r) > 3 else 'admin')) or 'admin'
                }
            return None
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"[DB Warning] Could not query user '{username}': {e}")
        return None


def get_all_users():
    """Returns all registered users with administrative or viewer roles."""
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("SELECT id, username, COALESCE(role, 'admin') as role, created_at FROM users ORDER BY id ASC;")
        else:
            cur.execute("SELECT id, username, role, created_at FROM users ORDER BY id ASC;")
        rows = cur.fetchall()
        users = []
        for r in rows:
            users.append({
                'id': r['id'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0],
                'username': r['username'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[1],
                'role': (r['role'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[2]) or 'admin',
                'created_at': str(r['created_at'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[3])
            })
        return users
    finally:
        cur.close()
        conn.close()


def create_user(username, password, role='viewer'):
    """Creates a new user in the database with administrative or viewer role."""
    from werkzeug.security import generate_password_hash
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        hashed = generate_password_hash(password)
        if engine == 'postgres':
            cur.execute("""
                INSERT INTO users (username, password_hash, role) 
                VALUES (%s, %s, %s) RETURNING id;
            """, (username, hashed, role))
            new_id = cur.fetchone()['id']
        else:
            cur.execute("""
                INSERT INTO users (username, password_hash, role) 
                VALUES (?, ?, ?);
            """, (username, hashed, role))
            conn.commit()
            new_id = cur.lastrowid
        return new_id
    finally:
        cur.close()
        conn.close()


def delete_user(user_id):
    """Deletes a user by ID."""
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("DELETE FROM users WHERE id = %s;", (user_id,))
        else:
            cur.execute("DELETE FROM users WHERE id = ?;", (user_id,))
            conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


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


# =============================================================================
# Device & Session Tracking (1-Click Logout)
# =============================================================================

def create_session(username, token_hash, device_info, ip_address):
    """Logs active device session."""
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("""
                INSERT INTO sessions (username, token_hash, device_info, ip_address, is_active) 
                VALUES (%s, %s, %s, %s, TRUE) RETURNING id;
            """, (username, token_hash, device_info, ip_address))
            sid = cur.fetchone()['id']
        else:
            cur.execute("""
                INSERT INTO sessions (username, token_hash, device_info, ip_address, is_active) 
                VALUES (?, ?, ?, ?, 1);
            """, (username, token_hash, device_info, ip_address))
            conn.commit()
            sid = cur.lastrowid
        return sid
    finally:
        cur.close()
        conn.close()


def get_active_sessions(username=None):
    """Returns active device sessions with info and timestamps."""
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        query = "SELECT id, username, token_hash, device_info, ip_address, is_active, created_at, last_active FROM sessions WHERE is_active = TRUE" if engine == 'postgres' else "SELECT id, username, token_hash, device_info, ip_address, is_active, created_at, last_active FROM sessions WHERE is_active = 1"
        params = ()
        if username:
            query += " AND username = %s" if engine == 'postgres' else " AND username = ?"
            params = (username,)
        query += " ORDER BY last_active DESC;"
        
        cur.execute(query, params)
        rows = cur.fetchall()
        sessions = []
        for r in rows:
            sessions.append({
                'id': r['id'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0],
                'username': r['username'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[1],
                'token_hash': r['token_hash'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[2],
                'device_info': r['device_info'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[3],
                'ip_address': r['ip_address'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[4],
                'created_at': str(r['created_at'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[6]),
                'last_active': str(r['last_active'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[7])
            })
        return sessions
    finally:
        cur.close()
        conn.close()


def revoke_session(session_id):
    """Revokes a specific session (1-click logout for a device)."""
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if engine == 'postgres':
            cur.execute("UPDATE sessions SET is_active = FALSE WHERE id = %s;", (session_id,))
        else:
            cur.execute("UPDATE sessions SET is_active = 0 WHERE id = ?;", (session_id,))
            conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def revoke_all_other_sessions(username, current_token_hash=None):
    """Revokes all sessions for a user except the current one."""
    conn, engine = get_connection()
    cur = conn.cursor()
    try:
        if current_token_hash:
            if engine == 'postgres':
                cur.execute("UPDATE sessions SET is_active = FALSE WHERE username = %s AND token_hash != %s;", (username, current_token_hash))
            else:
                cur.execute("UPDATE sessions SET is_active = 0 WHERE username = ? AND token_hash != ?;", (username, current_token_hash))
                conn.commit()
        else:
            if engine == 'postgres':
                cur.execute("UPDATE sessions SET is_active = FALSE WHERE username = %s;", (username,))
            else:
                cur.execute("UPDATE sessions SET is_active = 0 WHERE username = ?;", (username,))
                conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def is_session_active(token_hash):
    """Checks if a session token is still active and has not been logged out."""
    if not token_hash:
        return False
    try:
        conn, engine = get_connection()
        cur = conn.cursor()
        try:
            if engine == 'postgres':
                cur.execute("SELECT is_active FROM sessions WHERE token_hash = %s;", (token_hash,))
            else:
                cur.execute("SELECT is_active FROM sessions WHERE token_hash = ?;", (token_hash,))
            r = cur.fetchone()
            if r:
                val = r['is_active'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0]
                return bool(val)
            return True
        finally:
            cur.close()
            conn.close()
    except Exception:
        return True


def update_session_activity(token_hash):
    """Updates the last_active timestamp for a session."""
    if not token_hash:
        return
    try:
        conn, engine = get_connection()
        cur = conn.cursor()
        try:
            if engine == 'postgres':
                cur.execute("UPDATE sessions SET last_active = CURRENT_TIMESTAMP WHERE token_hash = %s;", (token_hash,))
            else:
                cur.execute("UPDATE sessions SET last_active = datetime('now') WHERE token_hash = ?;", (token_hash,))
                conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception:
        pass

