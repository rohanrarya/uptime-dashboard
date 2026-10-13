# =============================================================================
# Ops Command Center — Pure HTTP Router & Controller
# Manages HTTP requests and responses. Business logic lives in separate modules:
# - auth_service.py : User verification, stateless tokens & 2FA
# - services.py     : Render, Cloudflare, UptimeRobot, TinyURL & DB Health
# - db.py           : PostgreSQL pooling (PgBouncer) & persistence
# =============================================================================

import os
from functools import wraps
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

import db
import auth_service
import services

# =============================================================================
# Application Initialization
# =============================================================================

app = Flask(__name__, template_folder='templates', static_folder='static', static_url_path='/static')
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Auto-initialize database schema and sync .env variables on boot
try:
    db.init_db()
    db.sync_env_to_db()
except Exception as e:
    print(f"[Init Notice] Database init/sync: {e}")


# =============================================================================
# Auth Guard Decorator
# =============================================================================

def get_client_info():
    """Extracts User-Agent and client IP from HTTP request."""
    ua = request.headers.get('User-Agent', '')
    forwarded = request.headers.get('X-Forwarded-For', '')
    ip = forwarded.split(',')[0].strip() if forwarded else (request.remote_addr or 'Unknown IP')
    return ua, ip


def get_current_token():
    """Extracts Bearer token from request."""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header.split(' ', 1)[1].strip()
    return request.args.get('token', '').strip()


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_current_token()
        username = auth_service.validate_token(token)
        if not username:
            return jsonify({'error': 'Unauthorized', 'message': 'Please login to access this panel.'}), 401
            
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# HTTP Routes: Authentication & 2FA
# =============================================================================

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Step 1 Login: verifies password, tracks device, and checks 2FA status."""
    data = request.get_json(silent=True) or {}
    username = data.get('username', 'admin').strip()
    password = data.get('password', '').strip()
    ua, ip = get_client_info()
    result, code = auth_service.authenticate_user(username, password, ua, ip)
    return jsonify(result), code


@app.route('/api/auth/login-2fa', methods=['POST'])
def login_2fa():
    """Step 2 Login: verifies 6-digit Authenticator code and registers active device."""
    data = request.get_json(silent=True) or {}
    temp_token = data.get('temp_token', '').strip()
    code = data.get('code', '').strip()
    ua, ip = get_client_info()
    result, status_code = auth_service.verify_2fa_and_login(temp_token, code, ua, ip)
    return jsonify(result), status_code


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """1-Click Logout: revokes the active device session."""
    token = get_current_token()
    result, code = auth_service.logout_session(token)
    return jsonify(result), code


@app.route('/api/auth/verify', methods=['GET'])
def verify_token():
    """Validates session token on page load."""
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.replace('Bearer ', '').strip() if auth_header.startswith('Bearer ') else ''
    username = auth_service.validate_token(token)
    if username:
        return jsonify({'valid': True, 'username': username})
    return jsonify({'valid': False}), 401


@app.route('/api/auth/change-password', methods=['POST'])
@require_auth
def change_password():
    """Updates admin login password."""
    data = request.get_json(silent=True) or {}
    new_password = data.get('new_password', '').strip()
    if len(new_password) < 4:
        return jsonify({'error': 'Password must be at least 4 characters long'}), 400
    
    db.update_admin_password(new_password)
    return jsonify({'success': True, 'message': 'Admin password updated successfully!'})


@app.route('/api/auth/2fa/status', methods=['GET'])
@require_auth
def get_2fa_status():
    """Returns whether 2FA is currently active."""
    result, code = auth_service.get_2fa_status()
    return jsonify(result), code


@app.route('/api/auth/2fa/setup', methods=['POST'])
@require_auth
def setup_2fa():
    """Generates new TOTP secret and QR code."""
    result, code = auth_service.initialize_2fa_setup()
    return jsonify(result), code


@app.route('/api/auth/2fa/verify-enable', methods=['POST'])
@require_auth
def verify_enable_2fa():
    """Verifies test code and activates 2FA."""
    data = request.get_json(silent=True) or {}
    code = data.get('code', '').strip()
    result, status_code = auth_service.confirm_and_enable_2fa(code)
    return jsonify(result), status_code


@app.route('/api/auth/2fa/disable', methods=['POST'])
@require_auth
def disable_2fa():
    """Disables 2FA."""
    data = request.get_json(silent=True) or {}
    code = data.get('code', '').strip()
    result, status_code = auth_service.disable_2fa(code)
    return jsonify(result), status_code


# =============================================================================
# HTTP Routes: Operational Dashboard & Health
# =============================================================================

@app.route('/api/config', methods=['GET'])
def get_config():
    """Returns basic configuration flags for dashboard badges."""
    accounts = db.get_all_accounts()
    _, engine = db.get_connection()
    return jsonify({
        'uptimeRobotConfigured': bool(db.get_setting('uptimerobot_api_key')),
        'dubConfigured': bool(db.get_setting('dub_api_key') or db.get_setting('dub_api_token')),
        'tinyUrlConfigured': bool(db.get_setting('tinyurl_api_token')),
        'renderAccountsCount': len([a for a in accounts if a['provider'] == 'render']),
        'cfAccountsCount': len([a for a in accounts if a['provider'] == 'cloudflare']),
        'vercelAccountsCount': len([a for a in accounts if a['provider'] == 'vercel']),
        'databaseType': engine,
        'databaseConnected': True
    })


@app.route('/api/deployments', methods=['GET'])
@require_auth
def get_deployments():
    """Aggregates all deployments across Render, Cloudflare, and Vercel accounts."""
    force = request.args.get('force') in ['1', 'true', 'yes']
    return jsonify(services.get_all_deployments(force=force))


@app.route('/api/monitors', methods=['GET'])
@require_auth
def get_monitors():
    """Proxies UptimeRobot monitors."""
    force = request.args.get('force') in ['1', 'true', 'yes']
    result, code = services.fetch_uptimerobot_monitors(force=force)
    return jsonify(result), code


@app.route('/api/projects', methods=['GET'])
@require_auth
def get_projects():
    """Fetches Dub.co and TinyURL projects with custom titles."""
    force = request.args.get('force') in ['1', 'true', 'yes']
    result, code = services.fetch_tinyurl_projects(force=force)
    return jsonify(result), code


@app.route('/api/health', methods=['GET', 'HEAD'])
def health_check():
    """Lightweight database keepalive endpoint for UptimeRobot / cron."""
    result, code = services.check_database_keepalive()
    return jsonify(result), code


# =============================================================================
# HTTP Routes: Interactive Control Panel APIs
# =============================================================================

@app.route('/api/admin/overview', methods=['GET'])
@require_auth
def admin_overview():
    """Returns all settings, accounts, and project titles for the Control Panel."""
    conn, engine = db.get_connection()
    conn.close()

    return jsonify({
        'database': {
            'engine': engine,
            'url': db.get_setting('database_url', os.environ.get('DATABASE_URL', '')),
            'pooling': 'pgbouncer-active' if engine == 'postgres' else 'none'
        },
        'keys': {
            'uptimerobot_api_key': db.get_setting('uptimerobot_api_key'),
            'dub_api_key': db.get_setting('dub_api_key') or db.get_setting('dub_api_token'),
            'tinyurl_api_token': db.get_setting('tinyurl_api_token')
        },
        'accounts': db.get_all_accounts(),
        'titles': db.get_all_project_titles()
    })


@app.route('/api/admin/settings', methods=['POST'])
@require_auth
def update_settings():
    """Updates API keys and database settings."""
    data = request.get_json(silent=True) or {}
    if 'uptimerobot_api_key' in data:
        db.set_setting('uptimerobot_api_key', data['uptimerobot_api_key'].strip())
    if 'dub_api_key' in data:
        db.set_setting('dub_api_key', data['dub_api_key'].strip())
    if 'tinyurl_api_token' in data:
        db.set_setting('tinyurl_api_token', data['tinyurl_api_token'].strip())
    if 'database_url' in data:
        db.set_setting('database_url', data['database_url'].strip())

    services.invalidate_services_cache()
    return jsonify({'success': True, 'message': 'Settings saved to database successfully!'})


@app.route('/api/admin/accounts', methods=['POST'])
@require_auth
def add_account():
    """Adds a new deployment provider account."""
    data = request.get_json(silent=True) or {}
    provider = data.get('provider', '').lower().strip()
    account_name = data.get('account_name', '').strip()
    api_key = data.get('api_key', '').strip()
    account_id = data.get('account_id', '').strip() or None

    if not provider or not account_name or not api_key:
        return jsonify({'error': 'Provider, Account Name, and API Key/Token are required.'}), 400

    new_id = db.add_account(provider, account_name, api_key, account_id)
    services.invalidate_services_cache()
    return jsonify({'success': True, 'id': new_id, 'message': f'{account_name} added successfully!'})


@app.route('/api/admin/accounts/<int:acc_id>', methods=['DELETE'])
@require_auth
def delete_account(acc_id):
    """Deletes an account."""
    db.delete_account(acc_id)
    services.invalidate_services_cache()
    return jsonify({'success': True, 'message': 'Account deleted successfully!'})


@app.route('/api/admin/titles', methods=['POST'])
@require_auth
def save_project_title():
    """Sets a custom title override for a slug/alias or creates/updates a custom direct link."""
    import re, time
    data = request.get_json(silent=True) or {}
    title_id = data.get('id')
    link_id = data.get('linkId') or data.get('link_id')
    alias = data.get('alias', '').strip().lower()
    custom_title = data.get('custom_title', '').strip()
    target_url = data.get('target_url', '').strip()

    if not custom_title:
        return jsonify({'error': 'Display Title is required.'}), 400

    if target_url and target_url != '#' and not (target_url.startswith('http://') or target_url.startswith('https://')):
        target_url = f"https://{target_url}"

    # If title_id is provided, try updating by ID
    if title_id:
        try:
            db.update_project_title_by_id(int(title_id), custom_title, target_url or None)
        except Exception:
            pass

    # Auto-generate alias if not provided
    if not alias:
        alias = re.sub(r'[^a-z0-9]+', '-', custom_title.lower()).strip('-')
        if not alias:
            alias = f"link-{int(time.time())}"
        all_titles = db.get_all_project_titles()
        if any(t['alias'].lower() == alias for t in all_titles):
            alias = f"{alias}-{int(time.time()) % 10000}"

    # 1. Sync destination URL & title to Dub.co API if configured
    dub_token = db.get_setting('dub_api_key') or db.get_setting('dub_api_token')
    dub_synced = False
    if dub_token and target_url:
        target_link_id = link_id
        if not target_link_id or not str(target_link_id).startswith('link_'):
            try:
                for dl in services.fetch_dub_links():
                    if (dl.get('key') or '').lower() == alias or dl.get('id') == link_id:
                        target_link_id = dl.get('id')
                        break
            except Exception:
                pass
        if target_link_id and str(target_link_id).startswith('link_'):
            try:
                services.update_dub_link(target_link_id, new_target_url=target_url, new_title=custom_title)
                dub_synced = True
            except Exception:
                pass

    # 2. Sync to TinyURL if not Dub.co
    if not dub_synced and target_url:
        try:
            services.change_tinyurl_destination(alias, target_url)
        except Exception:
            pass

    db.set_project_title(alias, custom_title, target_url or None)
    services.invalidate_services_cache()
    return jsonify({'success': True, 'message': f"Saved '{custom_title}' successfully!"})


@app.route('/api/admin/dub/create', methods=['POST'])
@require_auth
def create_dub_link():
    """Creates a new short link directly on Dub.co."""
    data = request.get_json(silent=True) or {}
    url = data.get('url', '').strip()
    key = data.get('key', '').strip()
    title = data.get('title', '').strip()
    result, code = services.create_dub_link(url, key, title)
    return jsonify(result), code


@app.route('/api/admin/titles/<int:title_id>', methods=['DELETE'])
@require_auth
def delete_project_title(title_id):
    """Deletes a custom title override."""
    db.delete_project_title(title_id)
    services.invalidate_services_cache()
    return jsonify({'success': True, 'message': 'Title override removed.'})


@app.route('/api/admin/links/<path:identifier>', methods=['DELETE'])
@app.route('/api/admin/dub/<path:identifier>', methods=['DELETE'])
@app.route('/api/admin/tinyurl/<path:identifier>', methods=['DELETE'])
@require_auth
def delete_link(identifier):
    """Deletes or removes a link from Dub.co, TinyURL, or database."""
    alias = request.args.get('alias', '').strip()
    db_id = request.args.get('db_id', '').strip()
    result = services.delete_project_link(identifier.strip(), alias=alias, db_id=db_id)
    return jsonify(result), 200


@app.route('/api/admin/links/purge-legacy', methods=['POST'])
@require_auth
def purge_legacy_links():
    """Purges all orphaned TinyURL/custom links from database and clears deactivated TinyURL token."""
    result = services.purge_deactivated_tinyurl_links()
    return jsonify(result), 200


@app.route('/api/admin/test-db', methods=['POST'])
@require_auth
def test_db_connection():
    """Tests database connectivity with Neon connection pooling."""
    data = request.get_json(silent=True) or {}
    test_url = data.get('database_url', '').strip()
    result, code = services.test_postgresql_connection(test_url)
    return jsonify(result), code


# =============================================================================
# HTTP Routes: User Administration & Active Devices
# =============================================================================

@app.route('/api/admin/users', methods=['GET'])
@require_auth
def get_users():
    """Returns all registered users stored in database."""
    result, code = auth_service.list_users()
    return jsonify(result), code


@app.route('/api/admin/users', methods=['POST'])
@require_auth
def add_user():
    """Adds a new user with administrative or viewer privileges."""
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role = data.get('role', 'viewer').strip()
    result, code = auth_service.add_user(username, password, role)
    return jsonify(result), code


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@require_auth
def delete_user(user_id):
    """Deletes a user account (prevents deleting current user or last admin)."""
    current_token = get_current_token()
    current_username = auth_service.validate_token(current_token) or 'admin'
    result, code = auth_service.remove_user(user_id, current_username)
    return jsonify(result), code


@app.route('/api/admin/sessions', methods=['GET'])
@require_auth
def get_sessions():
    """Returns active logged-in devices and sessions."""
    current_token = get_current_token()
    result, code = auth_service.list_active_sessions(current_token)
    return jsonify(result), code


@app.route('/api/admin/sessions/<int:session_id>', methods=['DELETE'])
@require_auth
def delete_session(session_id):
    """Revokes a specific device session (1-click logout)."""
    result, code = auth_service.logout_device(session_id)
    return jsonify(result), code


@app.route('/api/admin/sessions/logout-others', methods=['POST'])
@require_auth
def logout_other_sessions():
    """1-Click Logout for all other devices."""
    current_token = get_current_token()
    current_username = auth_service.validate_token(current_token) or 'admin'
    result, code = auth_service.logout_all_other_devices(current_username, current_token)
    return jsonify(result), code


# =============================================================================
# Frontend Template & Static Asset Serving
# =============================================================================

@app.route('/static/<path:filename>')
def serve_static(filename):
    static_dirs = [
        os.path.join(app.root_path, 'static'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'),
        'static'
    ]
    for s_dir in static_dirs:
        if os.path.exists(os.path.join(s_dir, filename)):
            return send_from_directory(s_dir, filename)
    return jsonify({'error': 'Static file not found'}), 404


@app.route('/')
@app.route('/dashboard')
def serve_index():
    try:
        return render_template('index.html')
    except Exception:
        for p in ['templates/index.html', 'api/templates/index.html', '../templates/index.html']:
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
        return jsonify({'status': 'Ops Command Center API is running!'})


@app.route('/<path:path>')
def catch_all(path):
    if path.startswith('api/') or path.startswith('static/'):
        return jsonify({'error': 'Endpoint not found'}), 404
    return serve_index()


# =============================================================================
# Local Development Runner
# =============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Ops Command Center on http://127.0.0.1:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)
