# =============================================================================
# Ops Command Center — Python Flask API Backend
# Runs on Vercel (Serverless) and locally with python api/index.py
# Handles: Auth, PostgreSQL DB, Render, Cloudflare, UptimeRobot, TinyURL
# =============================================================================

import os
import sys
import json
import secrets
import requests
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.security import check_password_hash

# Ensure api directory is in python path
sys.path.append(os.path.dirname(__file__))
import db

app = Flask(__name__)
# Enable CORS for all domains so Cloudflare Pages (or custom domain) can fetch data
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Active session tokens (in-memory cache for fast validation)
ACTIVE_TOKENS = {}

# Initialize Database and Auto-sync .env on startup
try:
    db.init_db()
    db.sync_env_to_db()
except Exception as e:
    print(f"[Init Warning] Database init/sync notice: {e}")


# =============================================================================
# Authentication Decorator & Helpers
# =============================================================================

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        token = ''
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1].strip()
        elif request.args.get('token'):
            token = request.args.get('token').strip()

        if not token or token not in ACTIVE_TOKENS:
            return jsonify({'error': 'Unauthorized', 'message': 'Please login to access this panel.'}), 401
        
        # Check token expiration (7 days)
        token_data = ACTIVE_TOKENS[token]
        if datetime.utcnow() > token_data['expires_at']:
            del ACTIVE_TOKENS[token]
            return jsonify({'error': 'Session expired', 'message': 'Please login again.'}), 401
            
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# Auth Routes
# =============================================================================

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get('username', 'admin').strip()
    password = data.get('password', '').strip()

    if not password:
        return jsonify({'error': 'Password is required'}), 400

    user = db.get_user(username)
    if not user:
        # Fallback to check default if DB was not seeded yet
        default_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        if password == default_pass:
            token = secrets.token_hex(24)
            ACTIVE_TOKENS[token] = {
                'username': 'admin',
                'expires_at': datetime.utcnow() + timedelta(days=7)
            }
            return jsonify({'success': True, 'token': token, 'username': 'admin'})
        return jsonify({'error': 'Invalid username or password'}), 401

    if check_password_hash(user['password_hash'], password):
        token = secrets.token_hex(24)
        ACTIVE_TOKENS[token] = {
            'username': user['username'],
            'expires_at': datetime.utcnow() + timedelta(days=7)
        }
        return jsonify({'success': True, 'token': token, 'username': user['username']})
    else:
        return jsonify({'error': 'Invalid username or password'}), 401


@app.route('/api/auth/verify', methods=['GET'])
def verify_token():
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.replace('Bearer ', '').strip() if auth_header.startswith('Bearer ') else ''
    if token and token in ACTIVE_TOKENS:
        return jsonify({'valid': True, 'username': ACTIVE_TOKENS[token]['username']})
    return jsonify({'valid': False}), 401


@app.route('/api/auth/change-password', methods=['POST'])
@require_auth
def change_password():
    data = request.get_json(silent=True) or {}
    new_password = data.get('new_password', '').strip()
    if len(new_password) < 4:
        return jsonify({'error': 'Password must be at least 4 characters long'}), 400
    
    db.update_admin_password(new_password)
    return jsonify({'success': True, 'message': 'Admin password updated successfully!'})


# =============================================================================
# Operational Dashboard Routes (Deployments, Monitors, Projects)
# =============================================================================

@app.route('/api/config', methods=['GET'])
def get_config():
    """Returns basic configuration status without exposing sensitive credentials."""
    uptimerobot_key = db.get_setting('uptimerobot_api_key')
    tinyurl_token = db.get_setting('tinyurl_api_token')
    accounts = db.get_all_accounts()
    
    render_count = len([a for a in accounts if a['provider'] == 'render'])
    cf_count = len([a for a in accounts if a['provider'] == 'cloudflare'])
    
    _, engine = db.get_connection()
    
    return jsonify({
        'uptimeRobotConfigured': bool(uptimerobot_key),
        'tinyUrlConfigured': bool(tinyurl_token),
        'renderAccountsCount': render_count,
        'cfAccountsCount': cf_count,
        'databaseType': engine,
        'databaseConnected': True
    })


@app.route('/api/deployments', methods=['GET'])
@require_auth
def get_deployments():
    """Aggregates all deployments across Render and Cloudflare accounts stored in DB."""
    accounts = db.get_all_accounts()
    all_deployments = []

    # 1. Fetch Render Accounts
    render_accounts = [a for a in accounts if a['provider'] == 'render' and a['is_active']]
    for acc in render_accounts:
        api_key = acc['api_key']
        acc_name = acc['account_name']
        try:
            r = requests.get(
                'https://api.render.com/v1/services?limit=50',
                headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'},
                timeout=10
            )
            if r.status_code == 200:
                services = r.json()
                if isinstance(services, dict):
                    services = services.get('services', [])
                
                for s_item in services:
                    srv = s_item.get('service', s_item)
                    srv_id = srv.get('id')
                    srv_name = srv.get('name', 'Unnamed Service')
                    srv_url = srv.get('serviceDetails', {}).get('url', srv.get('url', ''))
                    branch = srv.get('branch', 'main')

                    # Fetch latest deploy
                    dep_status = 'live'
                    dep_label = 'Live'
                    commit_msg = 'No commit message'
                    commit_hash = ''
                    updated_at = None

                    try:
                        dep_r = requests.get(
                            f'https://api.render.com/v1/services/{srv_id}/deploys?limit=1',
                            headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'},
                            timeout=8
                        )
                        if dep_r.status_code == 200:
                            deploys = dep_r.json()
                            if isinstance(deploys, list) and len(deploys) > 0:
                                d = deploys[0].get('deploy', deploys[0])
                                raw_st = (d.get('status') or '').lower()
                                updated_at = d.get('finishedAt') or d.get('createdAt')
                                
                                if raw_st == 'live':
                                    dep_status = 'live'
                                    dep_label = 'Live'
                                elif any(w in raw_st for w in ['in_progress', 'created', 'queued', 'building']):
                                    dep_status = 'building'
                                    dep_label = 'Building'
                                elif 'failed' in raw_st:
                                    dep_status = 'failed'
                                    dep_label = 'Build Failed'
                                elif raw_st == 'canceled':
                                    dep_status = 'canceled'
                                    dep_label = 'Canceled'

                                c_obj = d.get('commit') or {}
                                commit_msg = (c_obj.get('message') or '').split('\n')[0]
                                commit_hash = (c_obj.get('id') or '')[:7]
                    except Exception:
                        pass

                    all_deployments.append({
                        'id': f"render-{srv_id}",
                        'provider': 'render',
                        'accountName': acc_name,
                        'projectName': srv_name,
                        'serviceType': srv.get('type', 'Web Service'),
                        'liveUrl': srv_url,
                        'dashboardUrl': f"https://dashboard.render.com/web/{srv_id}",
                        'status': dep_status,
                        'statusLabel': dep_label,
                        'commit': {
                            'message': commit_msg,
                            'hash': commit_hash,
                            'branch': branch
                        },
                        'updatedAt': updated_at
                    })
            else:
                all_deployments.append({
                    'id': f"render-err-{acc['id']}",
                    'provider': 'render',
                    'accountName': acc_name,
                    'projectName': 'Render Connection Error',
                    'serviceType': 'API Error',
                    'status': 'failed',
                    'statusLabel': f'HTTP {r.status_code}',
                    'error': f'Render API returned {r.status_code}: {r.text[:120]}'
                })
        except Exception as e:
            all_deployments.append({
                'id': f"render-err-{acc['id']}",
                'provider': 'render',
                'accountName': acc_name,
                'projectName': 'Network Error',
                'serviceType': 'Error',
                'status': 'failed',
                'statusLabel': 'Failed',
                'error': str(e)
            })

    # 2. Fetch Cloudflare Accounts
    cf_accounts = [a for a in accounts if a['provider'] == 'cloudflare' and a['is_active']]
    for acc in cf_accounts:
        acc_id = acc.get('account_id')
        token = acc.get('api_key')
        acc_name = acc.get('account_name', 'Cloudflare')
        
        if not acc_id or not token:
            continue

        try:
            r = requests.get(
                f'https://api.cloudflare.com/client/v4/accounts/{acc_id}/pages/projects',
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                projects = data.get('result') or []
                for p in projects:
                    p_name = p.get('name', 'Unnamed Project')
                    subdomain = p.get('subdomain', '')
                    live_url = f"https://{subdomain}" if subdomain else ""
                    dep = p.get('latest_deployment') or p.get('canonical_deployment') or {}
                    
                    stage = dep.get('latest_stage') or {}
                    raw_st = (stage.get('status') or '').lower()
                    meta = dep.get('deployment_trigger', {}).get('metadata') or {}

                    status = 'live' if raw_st == 'success' else ('building' if any(w in raw_st for w in ['active', 'building', 'queued']) else 'failed')
                    status_label = 'Live' if raw_st == 'success' else ('Building' if status == 'building' else 'Failed')

                    all_deployments.append({
                        'id': f"cf-{p_name}",
                        'provider': 'cloudflare',
                        'accountName': acc_name,
                        'projectName': p_name,
                        'serviceType': 'Pages',
                        'liveUrl': dep.get('url') or live_url,
                        'dashboardUrl': f"https://dash.cloudflare.com/{acc_id}/pages/view/{p_name}",
                        'status': status,
                        'statusLabel': status_label,
                        'commit': {
                            'message': (meta.get('commit_message') or '').split('\n')[0] or 'Pages deployment',
                            'hash': (meta.get('commit_hash') or '')[:7],
                            'branch': meta.get('branch', 'main')
                        },
                        'updatedAt': dep.get('modified_on') or dep.get('created_on')
                    })
            else:
                all_deployments.append({
                    'id': f"cf-err-{acc['id']}",
                    'provider': 'cloudflare',
                    'accountName': acc_name,
                    'projectName': 'Cloudflare Connection Error',
                    'serviceType': 'API Error',
                    'status': 'failed',
                    'statusLabel': f'HTTP {r.status_code}',
                    'error': f"Cloudflare API returned {r.status_code}: {r.text[:120]}"
                })
        except Exception as e:
            all_deployments.append({
                'id': f"cf-err-{acc['id']}",
                'provider': 'cloudflare',
                'accountName': acc_name,
                'projectName': 'Network Error',
                'serviceType': 'Error',
                'status': 'failed',
                'statusLabel': 'Failed',
                'error': str(e)
            })

    return jsonify({
        'deployments': all_deployments,
        'summary': {
            'total': len(all_deployments),
            'live': len([d for d in all_deployments if d.get('status') == 'live']),
            'building': len([d for d in all_deployments if d.get('status') == 'building']),
            'failed': len([d for d in all_deployments if d.get('status') == 'failed'])
        }
    })


@app.route('/api/monitors', methods=['GET'])
@require_auth
def get_monitors():
    """Proxies UptimeRobot API using the key stored in the database."""
    api_key = db.get_setting('uptimerobot_api_key')
    if not api_key:
        return jsonify({'error': 'UptimeRobot API key is not configured in settings.'}), 400

    try:
        r = requests.post(
            'https://api.uptimerobot.com/v2/getMonitors',
            data={
                'api_key': api_key,
                'format': 'json',
                'logs': '1',
                'response_times': '1',
                'custom_uptime_ratios': '1-7-30'
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': 'Failed to contact UptimeRobot API', 'detail': str(e)}), 502


@app.route('/api/projects', methods=['GET'])
@require_auth
def get_projects():
    """Fetches TinyURL links and applies custom titles from database."""
    token = db.get_setting('tinyurl_api_token')
    if not token:
        return jsonify({'configured': False, 'links': [], 'message': 'TinyURL API token not set in settings.'})

    # Fetch custom titles dictionary from DB
    titles_list = db.get_all_project_titles()
    titles_map = {t['alias'].lower().strip(): t['custom_title'] for t in titles_list}

    try:
        r = requests.get(
            'https://api.tinyurl.com/urls/available',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
            timeout=10
        )
        if r.status_code != 200:
            return jsonify({'configured': True, 'links': [], 'error': f'TinyURL API error: {r.text[:140]}'})

        data = r.json()
        raw_list = data.get('data', [])
        if isinstance(raw_list, dict):
            raw_list = [raw_list]

        links = []
        for item in raw_list:
            domain = item.get('domain', 'tinyurl.com')
            alias = item.get('alias', '')
            short_url = item.get('tiny_url') or f"https://{domain}/{alias}"
            target_url = item.get('url', '')

            # Check custom title mapping
            clean_alias = alias.lower().strip()
            if clean_alias in titles_map:
                title = titles_map[clean_alias]
            else:
                title = item.get('description') or item.get('title') or (
                    alias.replace('-', ' ').replace('_', ' ').title() if alias else 'Project Link'
                )

            links.append({
                'id': item.get('id') or alias,
                'title': title,
                'alias': alias,
                'tinyUrl': short_url,
                'displayUrl': short_url.replace('https://', '').replace('http://', ''),
                'targetUrl': target_url
            })

        return jsonify({'configured': True, 'links': links, 'count': len(links)})
    except Exception as e:
        return jsonify({'configured': True, 'links': [], 'error': str(e)})


# =============================================================================
# Interactive Management Panel (Admin API Routes)
# =============================================================================

@app.route('/api/admin/overview', methods=['GET'])
@require_auth
def admin_overview():
    """Returns all settings, accounts, and project titles for the control panel."""
    conn, engine = db.get_connection()
    conn.close()

    return jsonify({
        'database': {
            'engine': engine,
            'url': db.get_setting('database_url', os.environ.get('DATABASE_URL', ''))
        },
        'keys': {
            'uptimerobot_api_key': db.get_setting('uptimerobot_api_key'),
            'tinyurl_api_token': db.get_setting('tinyurl_api_token')
        },
        'accounts': db.get_all_accounts(),
        'titles': db.get_all_project_titles()
    })


@app.route('/api/admin/settings', methods=['POST'])
@require_auth
def update_settings():
    """Updates API keys and settings in the database."""
    data = request.get_json(silent=True) or {}
    
    if 'uptimerobot_api_key' in data:
        db.set_setting('uptimerobot_api_key', data['uptimerobot_api_key'].strip())
    if 'tinyurl_api_token' in data:
        db.set_setting('tinyurl_api_token', data['tinyurl_api_token'].strip())
    if 'database_url' in data:
        db.set_setting('database_url', data['database_url'].strip())

    return jsonify({'success': True, 'message': 'Settings saved to database successfully!'})


@app.route('/api/admin/accounts', methods=['POST'])
@require_auth
def add_account():
    data = request.get_json(silent=True) or {}
    provider = data.get('provider', '').lower().strip()
    account_name = data.get('account_name', '').strip()
    api_key = data.get('api_key', '').strip()
    account_id = data.get('account_id', '').strip() or None

    if not provider or not account_name or not api_key:
        return jsonify({'error': 'Provider, Account Name, and API Key/Token are required.'}), 400

    new_id = db.add_account(provider, account_name, api_key, account_id)
    return jsonify({'success': True, 'id': new_id, 'message': f'{account_name} added successfully!'})


@app.route('/api/admin/accounts/<int:acc_id>', methods=['DELETE'])
@require_auth
def delete_account(acc_id):
    db.delete_account(acc_id)
    return jsonify({'success': True, 'message': 'Account deleted successfully!'})


@app.route('/api/admin/titles', methods=['POST'])
@require_auth
def save_project_title():
    data = request.get_json(silent=True) or {}
    alias = data.get('alias', '').strip().lower()
    custom_title = data.get('custom_title', '').strip()
    target_url = data.get('target_url', '').strip() or None

    if not alias or not custom_title:
        return jsonify({'error': 'TinyURL Alias and Custom Title are required.'}), 400

    db.set_project_title(alias, custom_title, target_url)
    return jsonify({'success': True, 'message': f"Title for '{alias}' set to '{custom_title}'!"})


@app.route('/api/admin/titles/<int:title_id>', methods=['DELETE'])
@require_auth
def delete_project_title(title_id):
    db.delete_project_title(title_id)
    return jsonify({'success': True, 'message': 'Title override removed.'})


@app.route('/api/admin/test-db', methods=['POST'])
@require_auth
def test_db_connection():
    data = request.get_json(silent=True) or {}
    test_url = data.get('database_url', '').strip()
    if not test_url:
        return jsonify({'success': False, 'error': 'Database URL is empty'}), 400

    if test_url.startswith('postgres://'):
        test_url = test_url.replace('postgres://', 'postgresql://', 1)

    try:
        import psycopg2
        conn = psycopg2.connect(test_url)
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.close()
        conn.close()
        return jsonify({'success': True, 'message': 'Successfully connected to PostgreSQL database!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# =============================================================================
# Static Frontend Serving (For Local Development)
# =============================================================================
@app.route('/')
def serve_index():
    public_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'public')
    if os.path.exists(os.path.join(public_dir, 'index.html')):
        return send_from_directory(public_dir, 'index.html')
    return jsonify({'status': 'Ops Command Center API is running!'})

@app.route('/<path:path>')
def serve_static(path):
    public_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'public')
    file_path = os.path.join(public_dir, path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return send_from_directory(public_dir, path)
    if os.path.exists(os.path.join(public_dir, 'index.html')):
        return send_from_directory(public_dir, 'index.html')
    return jsonify({'error': 'Not found'}), 404

# =============================================================================
# Local Runner
# =============================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Ops Command Center on http://127.0.0.1:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)
