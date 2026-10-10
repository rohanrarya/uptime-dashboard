# =============================================================================
# Services Module
# Business logic for Render, Cloudflare, UptimeRobot, TinyURL & DB Health
# =============================================================================

import os
import requests
from datetime import datetime
import db

# =============================================================================
# Render Deployments Fetcher
# =============================================================================

def fetch_render_deployments(accounts):
    """Fetches services and latest commit/deploy status from Render accounts."""
    render_accounts = [a for a in accounts if a['provider'] == 'render' and a['is_active']]
    deployments = []

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

                    deployments.append({
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
                deployments.append({
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
            deployments.append({
                'id': f"render-err-{acc['id']}",
                'provider': 'render',
                'accountName': acc_name,
                'projectName': 'Network Error',
                'serviceType': 'Error',
                'status': 'failed',
                'statusLabel': 'Failed',
                'error': str(e)
            })

    return deployments


# =============================================================================
# Cloudflare Pages Fetcher
# =============================================================================

def fetch_cloudflare_deployments(accounts):
    """Fetches Pages projects and recent deployments from Cloudflare accounts."""
    cf_accounts = [a for a in accounts if a['provider'] == 'cloudflare' and a['is_active']]
    deployments = []

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

                    deployments.append({
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
                deployments.append({
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
            deployments.append({
                'id': f"cf-err-{acc['id']}",
                'provider': 'cloudflare',
                'accountName': acc_name,
                'projectName': 'Network Error',
                'serviceType': 'Error',
                'status': 'failed',
                'statusLabel': 'Failed',
                'error': str(e)
            })

    return deployments


def get_all_deployments():
    """Aggregates deployments from all configured Render and Cloudflare accounts."""
    accounts = db.get_all_accounts()
    render_deps = fetch_render_deployments(accounts)
    cf_deps = fetch_cloudflare_deployments(accounts)
    all_deps = render_deps + cf_deps

    return {
        'deployments': all_deps,
        'summary': {
            'total': len(all_deps),
            'live': len([d for d in all_deps if d.get('status') == 'live']),
            'building': len([d for d in all_deps if d.get('status') == 'building']),
            'failed': len([d for d in all_deps if d.get('status') == 'failed'])
        }
    }


# =============================================================================
# UptimeRobot Monitors Fetcher
# =============================================================================

def fetch_uptimerobot_monitors():
    """Queries UptimeRobot v2 getMonitors API."""
    api_key = db.get_setting('uptimerobot_api_key')
    if not api_key:
        return {'error': 'UptimeRobot API key not configured in settings.'}, 400

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
        return r.json(), r.status_code
    except Exception as e:
        return {'error': 'Failed to contact UptimeRobot API', 'detail': str(e)}, 502


# =============================================================================
# TinyURL Quick Launch Fetcher
# =============================================================================

def fetch_tinyurl_projects():
    """Fetches TinyURL links and merges custom title overrides."""
    token = db.get_setting('tinyurl_api_token')
    if not token:
        return {'configured': False, 'links': [], 'message': 'TinyURL API token not configured.'}, 200

    titles_list = db.get_all_project_titles()
    titles_map = {t['alias'].lower().strip(): t['custom_title'] for t in titles_list}

    try:
        r = requests.get(
            'https://api.tinyurl.com/urls/available',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
            timeout=10
        )
        if r.status_code != 200:
            return {'configured': True, 'links': [], 'error': f'TinyURL API error: {r.text[:140]}'}, 200

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

        return {'configured': True, 'links': links, 'count': len(links)}, 200
    except Exception as e:
        return {'configured': True, 'links': [], 'error': str(e)}, 200


# =============================================================================
# Database Health & Connection Tester
# =============================================================================

def check_database_keepalive():
    """Performs a lightweight ping against PostgreSQL to keep Neon awake 24/7."""
    db_ok = False
    engine_name = 'unknown'
    try:
        conn, engine_name = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.close()
        conn.close()
        db_ok = True
    except Exception as e:
        print(f"[Keepalive Ping Notice] {e}")

    return {
        'status': 'healthy' if db_ok else 'degraded',
        'database': 'connected' if db_ok else 'disconnected',
        'engine': engine_name,
        'pooling': 'pgbouncer-active' if engine_name == 'postgres' else 'none',
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }, (200 if db_ok else 503)


def test_postgresql_connection(raw_url):
    """Tests an arbitrary PostgreSQL connection string."""
    if not raw_url:
        return {'success': False, 'error': 'Database URL is empty'}, 400

    test_url = db.get_pooled_db_url(raw_url)
    try:
        import psycopg2
        conn = psycopg2.connect(test_url, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.close()
        conn.close()
        return {'success': True, 'message': 'Successfully connected to PostgreSQL (Neon PgBouncer Pooling Active)!'}, 200
    except Exception as e:
        return {'success': False, 'error': str(e)}, 400
