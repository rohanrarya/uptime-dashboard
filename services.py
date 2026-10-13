# =============================================================================
# Services Module — High Performance Async / Parallel Operations
# Business logic for Render, Cloudflare, UptimeRobot, TinyURL & DB Health
# Features: HTTP connection pooling, parallel threaded queries & in-memory caching
# =============================================================================

import os
import time
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import db

# =============================================================================
# Shared HTTP Client with Connection Pooling & Keep-Alive
# =============================================================================
_http_session = None

def get_http_session():
    """Returns a shared requests.Session with connection pooling to eliminate TLS handshakes."""
    global _http_session
    if _http_session is None:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        _http_session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
        )
        _http_session.mount('https://', adapter)
        _http_session.mount('http://', adapter)
    return _http_session


# =============================================================================
# In-Memory Response Caches (Ultra-Fast Sub-10ms Serving)
# =============================================================================
_deployments_cache = None
_deployments_cache_ts = 0

_monitors_cache = None
_monitors_cache_ts = 0

_tinyurl_cache = None
_tinyurl_cache_ts = 0


def invalidate_services_cache():
    """Clears all in-memory caches when settings, titles, or accounts change."""
    global _deployments_cache, _monitors_cache, _tinyurl_cache
    _deployments_cache = None
    _monitors_cache = None
    _tinyurl_cache = None


# =============================================================================
# Render Deployments Fetcher (Parallelized per Service)
# =============================================================================

def _fetch_single_render_service(s_item, acc_name, api_key):
    """Fetches latest deploy for an individual Render service in a thread worker."""
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
        session = get_http_session()
        dep_r = session.get(
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

    return {
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
    }


def fetch_render_deployments(accounts):
    """Fetches services and latest commit/deploy status from Render accounts concurrently."""
    render_accounts = [a for a in accounts if a['provider'] == 'render' and a['is_active']]
    deployments = []
    session = get_http_session()

    for acc in render_accounts:
        api_key = acc['api_key']
        acc_name = acc['account_name']
        try:
            r = session.get(
                'https://api.render.com/v1/services?limit=50',
                headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'},
                timeout=10
            )
            if r.status_code == 200:
                services = r.json()
                if isinstance(services, dict):
                    services = services.get('services', [])

                if services:
                    # Query all service deploys in parallel
                    workers = min(12, max(len(services), 1))
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        futures = [executor.submit(_fetch_single_render_service, s, acc_name, api_key) for s in services]
                        for f in as_completed(futures):
                            try:
                                deployments.append(f.result())
                            except Exception:
                                pass
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
    session = get_http_session()

    for acc in cf_accounts:
        acc_id = acc.get('account_id')
        token = acc.get('api_key')
        acc_name = acc.get('account_name', 'Cloudflare')
        
        if not acc_id or not token:
            continue

        try:
            r = session.get(
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


# =============================================================================
# Vercel Projects & Deployments Fetcher
# =============================================================================

def fetch_vercel_deployments(accounts):
    """Fetches projects and latest production deployments from Vercel accounts."""
    vercel_accounts = [a for a in accounts if a['provider'] == 'vercel' and a['is_active']]
    deployments = []
    session = get_http_session()

    for acc in vercel_accounts:
        token = acc.get('api_key')
        team_id = (acc.get('account_id') or '').strip()
        acc_name = acc.get('account_name', 'Vercel')

        if not token:
            continue

        try:
            url = 'https://api.vercel.com/v9/projects?limit=50'
            if team_id:
                url += f'&teamId={team_id}'

            r = session.get(
                url,
                headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                timeout=10
            )

            if r.status_code == 200:
                data = r.json()
                projects = data.get('projects') or []
                for p in projects:
                    p_name = p.get('name', 'Unnamed Project')
                    
                    targets = p.get('targets') or {}
                    prod_dep = targets.get('production')
                    latest_deps = p.get('latestDeployments') or []
                    dep = prod_dep or (latest_deps[0] if latest_deps else {})

                    raw_st = (dep.get('readyState') or dep.get('state') or 'READY').upper()
                    if raw_st == 'READY':
                        status = 'live'
                        status_label = 'Live'
                    elif any(w in raw_st for w in ['BUILD', 'INIT', 'QUEUE']):
                        status = 'building'
                        status_label = 'Building'
                    elif any(w in raw_st for w in ['ERR', 'FAIL', 'CANCEL']):
                        status = 'failed'
                        status_label = 'Failed'
                    else:
                        status = 'live'
                        status_label = raw_st.capitalize()

                    # Domain / URL resolution
                    dep_url = dep.get('url') or ''
                    live_url = ''
                    aliases = dep.get('alias') or p.get('alias') or []
                    if isinstance(aliases, list) and aliases:
                        first_alias = aliases[0]
                        if isinstance(first_alias, str):
                            live_url = f"https://{first_alias}"
                        elif isinstance(first_alias, dict) and first_alias.get('domain'):
                            live_url = f"https://{first_alias['domain']}"

                    if not live_url and dep_url:
                        live_url = f"https://{dep_url}"
                    elif not live_url:
                        live_url = f"https://{p_name}.vercel.app"

                    # Console URL
                    if team_id:
                        dash_url = f"https://vercel.com/{team_id}/{p_name}"
                    else:
                        dash_url = f"https://vercel.com/~/projects/{p_name}"

                    # Framework / Service Type
                    framework = p.get('framework')
                    service_type = 'Next.js' if framework == 'nextjs' else (framework.capitalize() if framework else 'Vercel')

                    # Commit metadata
                    meta = dep.get('meta') or {}
                    commit_msg = (
                        meta.get('githubCommitMessage') or 
                        meta.get('gitlabCommitMessage') or 
                        meta.get('bitbucketCommitMessage') or 
                        meta.get('commitMessage') or 
                        'Production deployment'
                    ).split('\n')[0]

                    commit_hash = (
                        meta.get('githubCommitSha') or 
                        meta.get('gitlabCommitSha') or 
                        meta.get('bitbucketCommitSha') or 
                        meta.get('commitSha') or 
                        meta.get('sha') or 
                        ''
                    )[:7]

                    branch = (
                        meta.get('githubCommitRef') or 
                        meta.get('gitlabCommitRef') or 
                        meta.get('bitbucketCommitRef') or 
                        meta.get('branch') or 
                        'main'
                    )

                    # Updated At
                    raw_ts = dep.get('ready') or dep.get('createdAt') or p.get('updatedAt')
                    updated_at = raw_ts
                    if isinstance(raw_ts, (int, float)):
                        if raw_ts > 10000000000:
                            updated_at = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc).isoformat()
                        else:
                            updated_at = datetime.fromtimestamp(raw_ts, tz=timezone.utc).isoformat()

                    deployments.append({
                        'id': f"vercel-{p_name}",
                        'provider': 'vercel',
                        'accountName': acc_name,
                        'projectName': p_name,
                        'serviceType': service_type,
                        'liveUrl': live_url,
                        'dashboardUrl': dash_url,
                        'status': status,
                        'statusLabel': status_label,
                        'commit': {
                            'message': commit_msg,
                            'hash': commit_hash,
                            'branch': branch
                        },
                        'updatedAt': updated_at
                    })
            else:
                deployments.append({
                    'id': f"vercel-err-{acc['id']}",
                    'provider': 'vercel',
                    'accountName': acc_name,
                    'projectName': 'Vercel Connection Error',
                    'serviceType': 'API Error',
                    'status': 'failed',
                    'statusLabel': f'HTTP {r.status_code}',
                    'error': f"Vercel API returned {r.status_code}: {r.text[:120]}"
                })
        except Exception as e:
            deployments.append({
                'id': f"vercel-err-{acc['id']}",
                'provider': 'vercel',
                'accountName': acc_name,
                'projectName': 'Network Error',
                'serviceType': 'Error',
                'status': 'failed',
                'statusLabel': 'Failed',
                'error': str(e)
            })

    return deployments


def get_all_deployments(force=False):
    """Aggregates deployments from all configured Render, Cloudflare, and Vercel accounts in parallel with caching."""
    global _deployments_cache, _deployments_cache_ts
    now = time.time()
    if not force and _deployments_cache and (now - _deployments_cache_ts < 20):
        return _deployments_cache

    accounts = db.get_all_accounts()

    # Query Render, Cloudflare, and Vercel concurrently
    with ThreadPoolExecutor(max_workers=3) as executor:
        f_render = executor.submit(fetch_render_deployments, accounts)
        f_cf = executor.submit(fetch_cloudflare_deployments, accounts)
        f_vercel = executor.submit(fetch_vercel_deployments, accounts)
        render_deps = f_render.result()
        cf_deps = f_cf.result()
        vercel_deps = f_vercel.result()

    all_deps = render_deps + cf_deps + vercel_deps
    result = {
        'deployments': all_deps,
        'summary': {
            'total': len(all_deps),
            'live': len([d for d in all_deps if d.get('status') == 'live']),
            'building': len([d for d in all_deps if d.get('status') == 'building']),
            'failed': len([d for d in all_deps if d.get('status') == 'failed'])
        }
    }
    _deployments_cache = result
    _deployments_cache_ts = now
    return result


# =============================================================================
# UptimeRobot Monitors Fetcher
# =============================================================================

def fetch_uptimerobot_monitors(force=False):
    """Queries UptimeRobot v2 getMonitors API with keep-alive and in-memory cache."""
    global _monitors_cache, _monitors_cache_ts
    now = time.time()
    if not force and _monitors_cache and (now - _monitors_cache_ts < 20):
        return _monitors_cache

    api_key = db.get_setting('uptimerobot_api_key')
    if not api_key:
        return {'error': 'UptimeRobot API key not configured in settings.'}, 400

    try:
        session = get_http_session()
        r = session.post(
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
        res = r.json(), r.status_code
        if r.status_code == 200:
            _monitors_cache = res
            _monitors_cache_ts = now
        return res
    except Exception as e:
        return {'error': 'Failed to contact UptimeRobot API', 'detail': str(e)}, 502


# =============================================================================
# Short Links & Quick Launch: TinyURL & Custom Direct Links
# =============================================================================

def create_tinyurl_link(url, alias=None, title=None, domain='tinyurl.com'):
    """Creates a new short link directly on TinyURL API."""
    token = db.get_setting('tinyurl_api_token')
    if not token:
        return {'success': False, 'error': 'TinyURL API token is not configured. Add it in Control Panel Settings.'}, 400

    if not url or url == '#':
        return {'success': False, 'error': 'A valid destination URL is required.'}, 400

    url = url.strip()
    if not (url.startswith('http://') or url.startswith('https://')):
        url = f"https://{url}"

    clean_alias = str(alias or '').strip().lower().replace('https://', '').replace('http://', '').replace('tinyurl.com/', '').strip('/')

    payload = {
        'url': url,
        'domain': domain
    }
    if clean_alias:
        payload['alias'] = clean_alias
    if title and title.strip():
        payload['description'] = title.strip()

    try:
        session = get_http_session()
        r = session.post(
            'https://api.tinyurl.com/create',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'Accept': 'application/json'},
            json=payload,
            timeout=10
        )
        data = r.json() if r.text else {}
        if r.status_code in (200, 201):
            link_data = data.get('data', {}) if isinstance(data, dict) else {}
            created_alias = link_data.get('alias') or clean_alias
            short_url = link_data.get('tiny_url') or f"https://{domain}/{created_alias}"
            if created_alias:
                db.set_project_title(created_alias, title or created_alias, url)
                hidden = db.get_setting('hidden_project_aliases', db.get_setting('hidden_tinyurl_aliases', ''))
                hidden_set = set(h.strip().lower() for h in hidden.split(',') if h.strip())
                if created_alias in hidden_set:
                    hidden_set.remove(created_alias)
                    db.set_setting('hidden_project_aliases', ','.join(sorted(hidden_set)))
                    db.set_setting('hidden_tinyurl_aliases', ','.join(sorted(hidden_set)))

            invalidate_services_cache()
            return {
                'success': True,
                'link': link_data,
                'alias': created_alias,
                'shortUrl': short_url,
                'message': f"Created short link https://{domain}/{created_alias} successfully!"
            }, 200
        else:
            errors = data.get('errors', [])
            err_msg = (errors[0] if errors else data.get('message')) or f"HTTP {r.status_code}"
            return {'success': False, 'error': f"TinyURL error: {err_msg}"}, r.status_code
    except Exception as e:
        return {'success': False, 'error': f"Failed to connect to TinyURL API: {str(e)}"}, 500


def delete_project_link(identifier, alias=None, db_id=None):
    """
    Unified delete dispatcher for TinyURL or database custom links.
    Handles 'custom-<id>', numeric ID, alias, slug, or full URL.
    Guarantees deletion from local database, marks as hidden, and attempts remote removal.
    """
    raw_id = str(identifier or '').strip()
    raw_alias = str(alias or '').strip() if alias else ''
    raw_db_id = str(db_id or '').strip() if db_id else ''

    # Build set of all candidate identifiers and aliases
    candidates = set()
    if raw_id:
        candidates.add(raw_id)
        candidates.add(raw_id.lower())
    if raw_alias:
        candidates.add(raw_alias)
        candidates.add(raw_alias.lower())

    # Extract numeric database ID if present
    num_id = None
    if raw_id.lower().startswith('custom-'):
        num_str = raw_id.lower().replace('custom-', '').strip()
        if num_str.isdigit():
            num_id = int(num_str)
            candidates.add(num_str)
            candidates.add(f"custom-{num_str}")
    elif raw_id.isdigit():
        num_id = int(raw_id)
    if num_id is None and raw_db_id and raw_db_id.isdigit():
        num_id = int(raw_db_id)
        candidates.add(raw_db_id)
        candidates.add(f"custom-{raw_db_id}")

    # Extract clean slug / key from URL or domain-style inputs
    for s in list(candidates):
        clean_s = s.replace('https://', '').replace('http://', '').strip('/')
        candidates.add(clean_s)
        candidates.add(clean_s.lower())
        if '/' in clean_s:
            slug = clean_s.split('/')[-1].strip()
            if slug:
                candidates.add(slug)
                candidates.add(slug.lower())

    # 1. Delete all matching records from local database (project_titles)
    deleted_titles = []
    try:
        all_titles = db.get_all_project_titles()
        for t in all_titles:
            t_id = t.get('id')
            t_alias = (t.get('alias') or '').strip().lower()
            t_slug = t_alias.replace('https://', '').replace('http://', '').strip('/').split('/')[-1]

            match = False
            if num_id is not None and t_id == num_id:
                match = True
            elif str(t_id) in candidates:
                match = True
            elif t_alias in candidates or t_slug in candidates:
                match = True
            elif any(c in t_alias for c in candidates if len(c) >= 3 and not c.startswith('custom-')):
                match = True

            if match:
                db.delete_project_title(t_id)
                deleted_titles.append(t)
                candidates.add(t_alias)
                candidates.add(t_slug)
                if t_id is not None:
                    candidates.add(str(t_id))
                    candidates.add(f"custom-{t_id}")
    except Exception as e:
        print(f"[DeleteLink DB Error] {e}")

    # 2. Also try direct alias SQL deletion for any candidate
    for c in list(candidates):
        if not c.startswith('custom-') and not c.isdigit() and len(c) >= 2:
            try:
                db.delete_project_title_by_alias(c)
            except Exception:
                pass

    # 3. Add all candidate strings to hidden_project_aliases and hidden_tinyurl_aliases
    hidden = db.get_setting('hidden_project_aliases', db.get_setting('hidden_tinyurl_aliases', ''))
    hidden_set = set(h.strip().lower() for h in hidden.split(',') if h.strip())
    for c in candidates:
        if c:
            hidden_set.add(c.lower())
    db.set_setting('hidden_project_aliases', ','.join(sorted(hidden_set)))
    db.set_setting('hidden_tinyurl_aliases', ','.join(sorted(hidden_set)))

    # 4. If TinyURL token is configured, attempt remote deletion (non-blocking if deactivated)
    tiny_token = db.get_setting('tinyurl_api_token')
    if tiny_token:
        try:
            for c in list(candidates):
                if not c.startswith('custom-') and not c.isdigit() and '.' not in c and len(c) >= 2:
                    clean_slug = c.replace('tinyurl.com', '').strip('/')
                    if clean_slug:
                        delete_tinyurl_link(clean_slug)
                        break
        except Exception:
            pass

    invalidate_services_cache()
    display_name = raw_alias or (deleted_titles[0].get('custom_title') if deleted_titles else raw_id)
    return {
        'success': True,
        'message': f"Link '{display_name}' deleted successfully and removed from dashboard."
    }


def purge_deactivated_tinyurl_links():
    """
    Cleans up all legacy TinyURL / orphaned custom links when TinyURL account is deactivated,
    clears tinyurl_api_token setting, and removes unneeded records.
    """
    # 1. Clear TinyURL token so no further requests stall
    db.set_setting('tinyurl_api_token', '')

    # 2. Delete non-persisted titles from database
    all_titles = db.get_all_project_titles()
    deleted_count = len(all_titles)
    hidden_set = set()

    for t in all_titles:
        alias_clean = (t.get('alias') or '').lower().strip()
        db.delete_project_title(t['id'])
        hidden_set.add(alias_clean)
        hidden_set.add(f"custom-{t['id']}")

    db.set_setting('hidden_project_aliases', ','.join(sorted(hidden_set)))
    db.set_setting('hidden_tinyurl_aliases', ','.join(sorted(hidden_set)))

    invalidate_services_cache()
    return {
        'success': True,
        'deleted_count': deleted_count,
        'message': f"Purged {deleted_count} link{'s' if deleted_count != 1 else ''} and cleared TinyURL settings."
    }


def fetch_tinyurl_projects(force=False):
    """Fetches links from TinyURL API, merges custom titles and direct custom links."""
    global _tinyurl_cache, _tinyurl_cache_ts
    now = time.time()
    if not force and _tinyurl_cache and (now - _tinyurl_cache_ts < 30):
        return _tinyurl_cache

    tiny_token = db.get_setting('tinyurl_api_token')

    titles_list = db.get_all_project_titles()
    titles_map = {t['alias'].lower().strip(): t for t in titles_list}
    matched_aliases = set()
    links = []

    hidden = db.get_setting('hidden_project_aliases', db.get_setting('hidden_tinyurl_aliases', ''))
    hidden_set = set(h.strip().lower() for h in hidden.split(',') if h.strip())

    # 1. Fetch from TinyURL (legacy and modern TinyURL accounts)
    if tiny_token:
        try:
            session = get_http_session()
            r = session.get(
                'https://api.tinyurl.com/urls/available',
                headers={'Authorization': f'Bearer {tiny_token}', 'Accept': 'application/json'},
                timeout=5
            )
            if r.status_code == 200:
                data = r.json()
                raw_list = data.get('data', [])
                if isinstance(raw_list, dict):
                    raw_list = [raw_list]

                for item in raw_list:
                    domain = item.get('domain', 'tinyurl.com')
                    alias = item.get('alias', '')
                    clean_alias = alias.lower().strip()
                    if clean_alias in hidden_set or clean_alias in matched_aliases:
                        continue

                    short_url = item.get('tiny_url') or f"https://{domain}/{alias}"
                    target_url = item.get('url', '')
                    matched_aliases.add(clean_alias)

                    if clean_alias in titles_map:
                        t_item = titles_map[clean_alias]
                        title = t_item.get('custom_title') or item.get('description') or alias
                        db_id = t_item.get('id')
                        if t_item.get('target_url'):
                            target_url = t_item.get('target_url')
                    else:
                        title = item.get('description') or item.get('title') or (
                            alias.replace('-', ' ').replace('_', ' ').title() if alias else 'Project Link'
                        )
                        db_id = None

                    links.append({
                        'id': item.get('id') or alias,
                        'linkId': item.get('id') or alias,
                        'db_id': db_id,
                        'title': title,
                        'alias': alias,
                        'domain': domain,
                        'provider': 'tinyurl',
                        'providerLabel': 'TinyURL',
                        'shortUrl': short_url,
                        'tinyUrl': short_url,
                        'displayUrl': short_url.replace('https://', '').replace('http://', ''),
                        'targetUrl': target_url,
                        'clicks': 0,
                        'isCustomLink': False
                    })
        except Exception as e:
            print(f"[Fetch TinyURL Notice] {e}")

    # 2. Add all custom direct links from database that are not TinyURL short links
    for t in titles_list:
        c_alias = (t.get('alias') or '').lower().strip()
        if c_alias in hidden_set:
            continue
        if c_alias not in matched_aliases:
            target = (t.get('target_url') or '').strip()
            if not target or target == '#':
                if c_alias.startswith('http://') or c_alias.startswith('https://'):
                    target = c_alias
                elif '.' in c_alias:
                    target = f"https://{c_alias}"
                else:
                    target = '#'

            clean_display = target.replace('https://', '').replace('http://', '') if target and target != '#' else (t.get('alias') or 'Custom Link')

            links.append({
                'id': f"custom-{t['id']}",
                'linkId': f"custom-{t['id']}",
                'db_id': t['id'],
                'title': t.get('custom_title') or t.get('alias'),
                'alias': t.get('alias'),
                'domain': 'custom',
                'provider': 'custom',
                'providerLabel': 'Custom Link',
                'shortUrl': target if target else '#',
                'tinyUrl': target if target else '#',
                'displayUrl': clean_display,
                'targetUrl': target if target != '#' else '',
                'clicks': 0,
                'isCustomLink': True
            })

    res = {
        'configured': bool(tiny_token or links),
        'tinyurl_configured': bool(tiny_token),
        'links': links,
        'count': len(links)
    }, 200
    _tinyurl_cache = res
    _tinyurl_cache_ts = now
    return res


def delete_tinyurl_link(alias, domain='tinyurl.com'):
    """Deletes short link from TinyURL API, removes local title overrides, and hides from dashboard."""
    token = db.get_setting('tinyurl_api_token')
    raw_alias = str(alias or '').strip()
    clean_alias = raw_alias.lower().replace('https://', '').replace('http://', '').replace('tinyurl.com/', '').strip('/')
    api_deleted = False
    api_message = ''

    if token and clean_alias and not clean_alias.startswith('custom-') and not clean_alias.isdigit():
        try:
            session = get_http_session()
            r = session.delete(
                f'https://api.tinyurl.com/alias/{domain}/{clean_alias}',
                headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                timeout=5
            )
            if r.status_code in (200, 204):
                api_deleted = True
                api_message = 'Deleted from TinyURL server.'
            elif r.status_code in (401, 403):
                api_message = 'TinyURL note: Account deactivated or invalid token.'
            else:
                try:
                    err_json = r.json()
                    errors = err_json.get('errors', [])
                    msg = err_json.get('message') or (errors[0] if errors else f"HTTP {r.status_code}")
                except Exception:
                    msg = f"HTTP {r.status_code}"
                api_message = f"TinyURL note: {msg}"
        except Exception as e:
            api_message = f"TinyURL API unreachable: {str(e)}"

    for t in db.get_all_project_titles():
        t_alias = (t.get('alias') or '').lower().strip()
        t_slug = t_alias.replace('https://', '').replace('http://', '').replace('tinyurl.com/', '').strip('/')
        if t_alias == clean_alias or t_slug == clean_alias or t_alias == raw_alias.lower():
            db.delete_project_title(t['id'])

    hidden = db.get_setting('hidden_project_aliases', db.get_setting('hidden_tinyurl_aliases', ''))
    hidden_set = set(h.strip().lower() for h in hidden.split(',') if h.strip())
    hidden_set.add(clean_alias)
    if raw_alias:
        hidden_set.add(raw_alias.lower())
    db.set_setting('hidden_project_aliases', ','.join(sorted(hidden_set)))
    db.set_setting('hidden_tinyurl_aliases', ','.join(sorted(hidden_set)))

    invalidate_services_cache()
    return {
        'success': True,
        'api_deleted': api_deleted,
        'message': f"Link '{clean_alias}' removed from dashboard. {api_message}".strip()
    }


def change_tinyurl_destination(alias, new_target_url, domain='tinyurl.com'):
    """Updates target destination on TinyURL API servers and stores local override."""
    token = db.get_setting('tinyurl_api_token')
    clean_alias = alias.lower().strip()
    api_updated = False
    api_message = ''

    if token and new_target_url and new_target_url != '#':
        try:
            session = get_http_session()
            payload = {
                'domain': domain,
                'alias': clean_alias,
                'url': new_target_url
            }
            r = session.patch(
                'https://api.tinyurl.com/change',
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'Accept': 'application/json'},
                json=payload,
                timeout=10
            )
            if r.status_code in (200, 204):
                api_updated = True
                api_message = 'Updated on TinyURL server.'
            else:
                try:
                    err_json = r.json()
                    errors = err_json.get('errors', [])
                    msg = err_json.get('message') or (errors[0] if errors else f"HTTP {r.status_code}")
                except Exception:
                    msg = f"HTTP {r.status_code}"
                api_message = f"TinyURL note: {msg}"
        except Exception as e:
            api_message = f"TinyURL API note: {str(e)}"

    hidden = db.get_setting('hidden_project_aliases', db.get_setting('hidden_tinyurl_aliases', ''))
    hidden_set = set(h.strip().lower() for h in hidden.split(',') if h.strip())
    if clean_alias in hidden_set:
        hidden_set.remove(clean_alias)
        db.set_setting('hidden_project_aliases', ','.join(sorted(hidden_set)))
        db.set_setting('hidden_tinyurl_aliases', ','.join(sorted(hidden_set)))

    invalidate_services_cache()
    return {
        'success': True,
        'api_updated': api_updated,
        'message': api_message
    }


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
