// =============================================================================
// Cloudflare Worker / Pages - Ops & Uptime Dashboard
// Real-time Git Deployments (Render & Cloudflare) + UptimeRobot Monitors
// =============================================================================

const UPTIMEROBOT_API_BASE = 'https://api.uptimerobot.com/v2';

// =============================================================================
// Deployments Provider Aggregator
// Fetches and unifies deployment status across multiple Render and Cloudflare accounts
// =============================================================================

function getDeploymentsConfig(env) {
  const config = {
    render: [],
    cloudflare: []
  };

  if (!env) return config;

  // 1. JSON Configuration: DEPLOYMENTS_CONFIG
  if (env.DEPLOYMENTS_CONFIG) {
    try {
      const parsed = typeof env.DEPLOYMENTS_CONFIG === 'string'
        ? JSON.parse(env.DEPLOYMENTS_CONFIG)
        : env.DEPLOYMENTS_CONFIG;
      if (Array.isArray(parsed.render)) config.render.push(...parsed.render);
      if (Array.isArray(parsed.cloudflare)) config.cloudflare.push(...parsed.cloudflare);
    } catch (e) {
      console.error('Error parsing DEPLOYMENTS_CONFIG JSON:', e.message);
    }
  }

  // 2. Render Single Account: RENDER_API_KEY
  if (env.RENDER_API_KEY && env.RENDER_API_KEY.trim()) {
    const key = env.RENDER_API_KEY.trim();
    if (!config.render.some(r => r.apiKey === key)) {
      config.render.push({
        name: (env.RENDER_LABEL && env.RENDER_LABEL.trim()) || 'Render (Account 1)',
        apiKey: key
      });
    }
  }

  // 3. Render Comma-separated: RENDER_API_KEYS="rnd_key1,rnd_key2"
  if (env.RENDER_API_KEYS && env.RENDER_API_KEYS.trim()) {
    const keys = env.RENDER_API_KEYS.split(',').map(k => k.trim()).filter(Boolean);
    keys.forEach((k, idx) => {
      if (!config.render.some(r => r.apiKey === k)) {
        config.render.push({
          name: `Render (Account ${config.render.length + 1})`,
          apiKey: k
        });
      }
    });
  }

  // 4. Render Numbered: RENDER_API_KEY_1, RENDER_LABEL_1, RENDER_API_KEY_2, etc.
  for (let i = 1; i <= 20; i++) {
    const key = env[`RENDER_API_KEY_${i}`];
    if (key && key.trim()) {
      const name = (env[`RENDER_LABEL_${i}`] && env[`RENDER_LABEL_${i}`].trim()) || `Render (Account ${i})`;
      if (!config.render.some(r => r.apiKey === key.trim())) {
        config.render.push({ name, apiKey: key.trim() });
      }
    }
  }

  // 5. Cloudflare Single Account: CF_ACCOUNT_ID and CF_API_TOKEN
  if (env.CF_ACCOUNT_ID && env.CF_API_TOKEN && env.CF_ACCOUNT_ID.trim() && env.CF_API_TOKEN.trim()) {
    const accId = env.CF_ACCOUNT_ID.trim();
    const token = env.CF_API_TOKEN.trim();
    if (!config.cloudflare.some(c => c.accountId === accId)) {
      config.cloudflare.push({
        name: (env.CF_LABEL && env.CF_LABEL.trim()) || 'Cloudflare (Account 1)',
        accountId: accId,
        apiToken: token
      });
    }
  }

  // 6. Cloudflare Comma-separated: CF_ACCOUNTS="accId1:token1:Label1,accId2:token2:Label2"
  if (env.CF_ACCOUNTS && env.CF_ACCOUNTS.trim()) {
    const items = env.CF_ACCOUNTS.split(',').map(s => s.trim()).filter(Boolean);
    items.forEach((item, idx) => {
      const parts = item.split(':').map(p => p.trim());
      if (parts.length >= 2) {
        const [accId, token, label] = parts;
        if (!config.cloudflare.some(c => c.accountId === accId)) {
          config.cloudflare.push({
            name: label || `Cloudflare (Account ${config.cloudflare.length + 1})`,
            accountId: accId,
            apiToken: token
          });
        }
      }
    });
  }

  // 7. Cloudflare Numbered: CF_ACCOUNT_ID_1, CF_API_TOKEN_1, CF_LABEL_1, etc.
  for (let i = 1; i <= 20; i++) {
    const accId = env[`CF_ACCOUNT_ID_${i}`];
    const token = env[`CF_API_TOKEN_${i}`];
    if (accId && token && accId.trim() && token.trim()) {
      const name = (env[`CF_LABEL_${i}`] && env[`CF_LABEL_${i}`].trim()) || `Cloudflare (Account ${i})`;
      if (!config.cloudflare.some(c => c.accountId === accId.trim())) {
        config.cloudflare.push({
          name,
          accountId: accId.trim(),
          apiToken: token.trim()
        });
      }
    }
  }

  return config;
}

async function fetchRenderAccountDeployments(account) {
  const { apiKey, name = 'Render' } = account;
  if (!apiKey) return [];

  try {
    const srvRes = await fetch('https://api.render.com/v1/services?limit=50', {
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Accept': 'application/json'
      }
    });

    if (!srvRes.ok) {
      const errText = await srvRes.text();
      return [{
        id: `render-err-${Date.now()}`,
        provider: 'render',
        accountName: name,
        projectName: 'Render Connection Error',
        serviceType: 'Render API',
        status: 'failed',
        statusLabel: `Error (${srvRes.status})`,
        error: `HTTP ${srvRes.status}: ${errText.slice(0, 120)}`
      }];
    }

    const srvData = await srvRes.json();
    let rawList = Array.isArray(srvData) ? srvData : (srvData.services || []);
    const services = rawList.map(item => item.service || item);

    if (!services.length) return [];

    const deployPromises = services.map(async (srv) => {
      const srvId = srv.id;
      const srvName = srv.name || srv.slug || 'Unnamed Service';
      const srvUrl = (srv.serviceDetails && srv.serviceDetails.url) || srv.url || '';
      const repoUrl = srv.repo || '';
      const branch = srv.branch || 'main';

      try {
        const depRes = await fetch(`https://api.render.com/v1/services/${srvId}/deploys?limit=1`, {
          headers: {
            'Authorization': `Bearer ${apiKey}`,
            'Accept': 'application/json'
          }
        });

        if (!depRes.ok) {
          return {
            id: srvId,
            serviceId: srvId,
            provider: 'render',
            accountName: name,
            projectName: srvName,
            serviceType: srv.type || 'Web Service',
            liveUrl: srvUrl,
            repoUrl: repoUrl,
            dashboardUrl: `https://dashboard.render.com/web/${srvId}`,
            status: 'unknown',
            statusLabel: 'No Deploys Found',
            commit: { branch }
          };
        }

        const depData = await depRes.json();
        const deploys = Array.isArray(depData) ? depData : (depData.deploys || []);
        const latest = deploys[0] ? (deploys[0].deploy || deploys[0]) : null;

        if (!latest) {
          return {
            id: srvId,
            serviceId: srvId,
            provider: 'render',
            accountName: name,
            projectName: srvName,
            serviceType: srv.type || 'Web Service',
            liveUrl: srvUrl,
            repoUrl: repoUrl,
            dashboardUrl: `https://dashboard.render.com/web/${srvId}`,
            status: 'unknown',
            statusLabel: 'No Deploys Yet',
            commit: { branch }
          };
        }

        let status = 'unknown';
        let statusLabel = latest.status || 'Unknown';
        const rawStatus = (latest.status || '').toLowerCase();

        if (rawStatus === 'live') {
          status = 'live';
          statusLabel = 'Live';
        } else if (['build_in_progress', 'created', 'pre_deploy_in_progress', 'update_in_progress'].includes(rawStatus)) {
          status = 'building';
          statusLabel = rawStatus === 'build_in_progress' ? 'Building' : 'Deploying';
        } else if (['build_failed', 'update_failed'].includes(rawStatus)) {
          status = 'failed';
          statusLabel = 'Build Failed';
        } else if (rawStatus === 'canceled') {
          status = 'canceled';
          statusLabel = 'Canceled';
        } else if (rawStatus === 'deactivated') {
          status = 'deactivated';
          statusLabel = 'Deactivated';
        }

        const commitObj = latest.commit || {};
        const commitHash = commitObj.id ? commitObj.id.slice(0, 7) : '';
        const commitMessage = commitObj.message ? commitObj.message.split('\n')[0] : '';

        return {
          id: latest.id || srvId,
          serviceId: srvId,
          provider: 'render',
          accountName: name,
          projectName: srvName,
          serviceType: srv.type || 'Web Service',
          liveUrl: srvUrl,
          repoUrl: repoUrl,
          dashboardUrl: `https://dashboard.render.com/web/${srvId}`,
          status: status,
          statusLabel: statusLabel,
          rawStatus: rawStatus,
          commit: {
            message: commitMessage,
            hash: commitHash,
            fullHash: commitObj.id || '',
            branch: branch
          },
          trigger: latest.trigger || 'git',
          createdAt: latest.createdAt || null,
          finishedAt: latest.finishedAt || null,
          updatedAt: latest.updatedAt || null
        };
      } catch (e) {
        return {
          id: srvId,
          serviceId: srvId,
          provider: 'render',
          accountName: name,
          projectName: srvName,
          serviceType: srv.type || 'Web Service',
          liveUrl: srvUrl,
          dashboardUrl: `https://dashboard.render.com/web/${srvId}`,
          status: 'failed',
          statusLabel: 'Fetch Error',
          error: e.message
        };
      }
    });

    return await Promise.all(deployPromises);
  } catch (err) {
    return [{
      id: `render-net-err-${Date.now()}`,
      provider: 'render',
      accountName: name,
      projectName: 'Render Network Error',
      serviceType: 'Render API',
      status: 'failed',
      statusLabel: 'Network Failed',
      error: err.message
    }];
  }
}

async function fetchCloudflareAccountDeployments(account) {
  const { accountId, apiToken, name = 'Cloudflare' } = account;
  if (!accountId || !apiToken) return [];

  try {
    const res = await fetch(`https://api.cloudflare.com/client/v4/accounts/${accountId}/pages/projects`, {
      headers: {
        'Authorization': `Bearer ${apiToken}`,
        'Content-Type': 'application/json'
      }
    });

    if (!res.ok) {
      const errText = await res.text();
      return [{
        id: `cf-err-${Date.now()}`,
        provider: 'cloudflare',
        accountName: name,
        projectName: 'Cloudflare Pages Connection Error',
        serviceType: 'Pages',
        status: 'failed',
        statusLabel: `Error (${res.status})`,
        error: `HTTP ${res.status}: ${errText.slice(0, 120)}`
      }];
    }

    const data = await res.json();
    if (!data.success) {
      const msg = (data.errors && data.errors[0] && data.errors[0].message) || 'Unknown error';
      return [{
        id: `cf-err-${Date.now()}`,
        provider: 'cloudflare',
        accountName: name,
        projectName: 'Cloudflare Pages Error',
        serviceType: 'Pages',
        status: 'failed',
        statusLabel: 'API Error',
        error: msg
      }];
    }

    const projects = data.result || [];
    if (!projects.length) return [];

    const deployList = projects.map(proj => {
      const projName = proj.name || 'Unnamed Project';
      const subdomain = proj.subdomain || '';
      const defaultUrl = subdomain ? `https://${subdomain}` : '';
      const prodBranch = (proj.source && proj.source.config && proj.source.config.production_branch) || 'main';

      const dep = proj.latest_deployment || proj.canonical_deployment || null;

      if (!dep) {
        return {
          id: `cf-proj-${proj.name}`,
          provider: 'cloudflare',
          accountName: name,
          projectName: projName,
          serviceType: 'Pages',
          liveUrl: defaultUrl,
          dashboardUrl: `https://dash.cloudflare.com/${accountId}/pages/view/${projName}`,
          status: 'unknown',
          statusLabel: 'No Deployments',
          commit: { branch: prodBranch }
        };
      }

      const stage = dep.latest_stage || {};
      const rawStageStatus = (stage.status || '').toLowerCase();
      const trigger = dep.deployment_trigger || {};
      const meta = trigger.metadata || {};

      let status = 'unknown';
      let statusLabel = stage.name ? `${stage.name}: ${rawStageStatus}` : rawStageStatus;

      if (rawStageStatus === 'success') {
        status = 'live';
        statusLabel = 'Live';
      } else if (['active', 'building', 'queued', 'initialize', 'clone_repo'].includes(rawStageStatus) || rawStageStatus === 'idle') {
        status = 'building';
        statusLabel = rawStageStatus === 'active' ? 'Building' : 'Deploying';
      } else if (rawStageStatus === 'failure' || rawStageStatus === 'failed') {
        status = 'failed';
        statusLabel = 'Build Failed';
      } else if (rawStageStatus === 'canceled') {
        status = 'canceled';
        statusLabel = 'Canceled';
      }

      const commitHash = meta.commit_hash ? meta.commit_hash.slice(0, 7) : '';
      const commitMessage = meta.commit_message ? meta.commit_message.split('\n')[0] : '';
      const liveUrl = dep.url || defaultUrl;

      return {
        id: dep.id || `cf-dep-${proj.name}`,
        provider: 'cloudflare',
        accountName: name,
        projectName: projName,
        serviceType: 'Pages',
        liveUrl: liveUrl,
        dashboardUrl: `https://dash.cloudflare.com/${accountId}/pages/view/${projName}`,
        status: status,
        statusLabel: statusLabel,
        rawStatus: rawStageStatus,
        commit: {
          message: commitMessage,
          hash: commitHash,
          fullHash: meta.commit_hash || '',
          branch: meta.branch || prodBranch
        },
        trigger: trigger.type || 'git',
        createdAt: dep.created_on || null,
        finishedAt: dep.modified_on || null
      };
    });

    return deployList;
  } catch (err) {
    return [{
      id: `cf-net-err-${Date.now()}`,
      provider: 'cloudflare',
      accountName: name,
      projectName: 'Cloudflare Network Error',
      serviceType: 'Pages',
      status: 'failed',
      statusLabel: 'Network Failed',
      error: err.message
    }];
  }
}

async function fetchAllDeployments(env) {
  const config = getDeploymentsConfig(env);
  const tasks = [];

  // Fetch each Render account in parallel
  config.render.forEach(acc => {
    tasks.push(fetchRenderAccountDeployments(acc));
  });

  // Fetch each Cloudflare account in parallel
  config.cloudflare.forEach(acc => {
    tasks.push(fetchCloudflareAccountDeployments(acc));
  });

  const settled = await Promise.allSettled(tasks);
  const allDeployments = [];

  settled.forEach(item => {
    if (item.status === 'fulfilled' && Array.isArray(item.value)) {
      allDeployments.push(...item.value);
    }
  });

  // Sort: Building first, then most recently updated / created
  allDeployments.sort((a, b) => {
    if (a.status === 'building' && b.status !== 'building') return -1;
    if (b.status === 'building' && a.status !== 'building') return 1;
    const timeA = new Date(a.finishedAt || a.createdAt || 0).getTime();
    const timeB = new Date(b.finishedAt || b.createdAt || 0).getTime();
    return timeB - timeA;
  });

  return {
    deployments: allDeployments,
    summary: {
      total: allDeployments.length,
      live: allDeployments.filter(d => d.status === 'live').length,
      building: allDeployments.filter(d => d.status === 'building').length,
      failed: allDeployments.filter(d => d.status === 'failed').length
    },
    accounts: {
      renderCount: config.render.length,
      cloudflareCount: config.cloudflare.length
    }
  };
}



export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const pathname = url.pathname;
    const apiKey = (env.UPTIMEROBOT_API_KEY || env.CRONJOB_API_KEY || '').trim();

    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type'
        }
      });
    }

    // 1. API: Configuration & accounts check
    if (pathname === '/api/config') {
      const depConfig = getDeploymentsConfig(env);
      return jsonResponse({
        configured: Boolean(apiKey && apiKey.length > 0),
        deploymentsConfigured: Boolean(depConfig.render.length > 0 || depConfig.cloudflare.length > 0),
        renderAccountsCount: depConfig.render.length,
        cfAccountsCount: depConfig.cloudflare.length
      });
    }

    // 2. API: Fetch all monitors from UptimeRobot
    if (pathname === '/api/monitors') {
      if (!apiKey) {
        return jsonResponse({
          error: 'UPTIMEROBOT_API_KEY is not set in Cloudflare Variables.',
          detail: 'Please go to Settings -> Environment Variables -> Add UPTIMEROBOT_API_KEY.'
        }, 400);
      }

      try {
        const bodyParams = new URLSearchParams({
          api_key: apiKey,
          format: 'json',
          logs: '1',
          response_times: '1',
          custom_uptime_ratios: '1-7-30'
        });

        const upstreamRes = await fetch(UPTIMEROBOT_API_BASE + '/getMonitors', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'Cache-Control': 'no-cache'
          },
          body: bodyParams.toString()
        });

        const data = await upstreamRes.json();

        if (data.stat !== 'ok') {
          return jsonResponse({
            error: data.message || (data.error && data.error.message) || 'UptimeRobot API error',
            raw: data
          }, 400);
        }

        return jsonResponse(data, 200);
      } catch (err) {
        return jsonResponse({ error: 'Failed to contact UptimeRobot API', detail: err.message }, 502);
      }
    }

    // 3. API: Fetch live deployments across multiple Render & Cloudflare accounts
    if (pathname === '/api/deployments') {
      const depConfig = getDeploymentsConfig(env);

      if (!depConfig.render.length && !depConfig.cloudflare.length) {
        return jsonResponse({
          configured: false,
          deployments: [],
          summary: { total: 0, live: 0, building: 0, failed: 0 },
          accounts: { renderCount: 0, cloudflareCount: 0 },
          message: 'No Render or Cloudflare accounts configured'
        });
      }

      try {
        const data = await fetchAllDeployments(env);
        return jsonResponse({ configured: true, ...data }, 200);
      } catch (err) {
        return jsonResponse({ error: 'Failed to fetch deployments', detail: err.message }, 500);
      }
    }

    // 4. Default: Serve the single-page dashboard HTML
    return new Response(DASHBOARD_HTML, {
      headers: {
        'Content-Type': 'text/html; charset=UTF-8',
        'Cache-Control': 'no-cache'
      }
    });
  }
};

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*'
    }
  });
}

const DASHBOARD_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
  <title>Ops & Uptime Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #090d16;
      --surface: #101623;
      --surface2: #182032;
      --surface3: #222d46;
      --border: #232e46;
      --border-subtle: #192237;
      --accent: #6366f1;
      --accent-light: #818cf8;
      --accent-glow: rgba(99, 102, 241, 0.2);
      --success: #10b981;
      --success-glow: rgba(16, 185, 129, 0.16);
      --warning: #f59e0b;
      --warning-glow: rgba(245, 158, 11, 0.18);
      --danger: #ef4444;
      --danger-glow: rgba(239, 68, 68, 0.16);
      --render-color: #46e3b7;
      --cf-color: #f6821f;
      --muted: #64748b;
      --text: #f8fafc;
      --text2: #94a3b8;
      --text3: #64748b;
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 14px;
      --radius-xl: 18px;
      --shadow-sm: 0 2px 6px rgba(0,0,0,0.3);
      --shadow-md: 0 8px 24px rgba(0,0,0,0.4);
      --shadow-lg: 0 16px 40px rgba(0,0,0,0.6);
    }

    body {
      font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      line-height: 1.5;
      -webkit-tap-highlight-color: transparent;
    }

    /* Header */
    header {
      background: rgba(16, 22, 35, 0.92);
      border-bottom: 1px solid var(--border);
      padding: 0.85rem 1.75rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
      backdrop-filter: blur(14px);
    }
    .brand { display: flex; align-items: center; gap: 0.85rem; }
    .brand-icon {
      width: 38px; height: 38px; border-radius: var(--radius-md);
      background: linear-gradient(135deg, #6366f1, #06b6d4);
      display: flex; align-items: center; justify-content: center;
      color: white; box-shadow: 0 0 18px var(--accent-glow); flex-shrink: 0;
    }
    .brand-text h1 { font-size: 1.05rem; font-weight: 700; color: #fff; letter-spacing: -0.01em; }
    .brand-text span { font-size: 0.72rem; color: var(--text3); font-weight: 500; display: block; }

    .header-actions { display: flex; align-items: center; gap: 0.75rem; }
    .refresh-btn {
      padding: 0.48rem 0.9rem; border-radius: var(--radius-sm); font-weight: 600;
      font-size: 0.8rem; border: 1px solid var(--border); cursor: pointer;
      display: inline-flex; align-items: center; gap: 0.45rem; transition: all 0.2s ease;
      background: var(--surface2); color: var(--text2); font-family: inherit;
    }
    .refresh-btn:hover { background: var(--surface3); color: var(--text); border-color: var(--accent-light); }
    .spin { animation: spin 0.8s linear infinite; }
    @keyframes spin { 100% { transform: rotate(360deg); } }

    /* Main Container */
    main { padding: 1.75rem 2rem 4rem; max-width: 1440px; margin: 0 auto; }

    /* Top Navigation Tabs */
    .view-tabs-container {
      display: flex; align-items: center; justify-content: space-between;
      gap: 1rem; margin-bottom: 1.75rem; flex-wrap: wrap;
    }
    .view-tabs {
      display: inline-flex; background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius-lg); padding: 0.3rem; gap: 0.3rem;
    }
    .view-tab {
      padding: 0.55rem 1.25rem; border-radius: var(--radius-md); font-weight: 600;
      font-size: 0.84rem; cursor: pointer; border: none; background: transparent;
      color: var(--text2); transition: all 0.2s ease; display: inline-flex;
      align-items: center; gap: 0.55rem; font-family: inherit;
    }
    .view-tab:hover { color: var(--text); background: rgba(255, 255, 255, 0.04); }
    .view-tab.active {
      background: var(--surface2); color: #fff;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35); border: 1px solid var(--border);
    }
    .tab-badge {
      padding: 0.12rem 0.55rem; border-radius: 999px; font-size: 0.7rem;
      font-weight: 700; background: var(--surface3); color: var(--text2);
    }
    .tab-badge.building {
      background: rgba(245, 158, 11, 0.2); color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.4); animation: pulse 1.8s infinite;
    }

    .live-indicator {
      display: inline-flex; align-items: center; gap: 0.45rem;
      font-size: 0.75rem; color: var(--text3); font-weight: 500;
    }
    .pulse-dot {
      width: 7px; height: 7px; border-radius: 50%;
      background: var(--success); box-shadow: 0 0 8px var(--success);
      animation: pulse 2s infinite;
    }
    .pulse-dot.warning { background: var(--warning); box-shadow: 0 0 8px var(--warning); }
    @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; } }

    /* Summary Stats Grid */
    .stats-grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1rem; margin-bottom: 2rem;
    }
    .stat-card {
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
      padding: 1.15rem 1.35rem; position: relative; overflow: hidden;
      transition: transform 0.2s ease, border-color 0.2s ease;
    }
    .stat-card:hover { transform: translateY(-2px); border-color: var(--surface3); }
    .stat-card::after {
      content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 2px;
      background: var(--stat-color, var(--accent));
    }
    .stat-card.total    { --stat-color: var(--accent); }
    .stat-card.live     { --stat-color: var(--success); }
    .stat-card.building { --stat-color: var(--warning); }
    .stat-card.failed   { --stat-color: var(--danger); }
    .stat-card.accounts { --stat-color: #38bdf8; }

    .stat-label {
      font-size: 0.72rem; font-weight: 700; color: var(--text3);
      text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.35rem;
    }
    .stat-value { font-size: 2rem; font-weight: 800; line-height: 1; color: var(--text); }
    .stat-sub { font-size: 0.72rem; color: var(--text3); margin-top: 0.45rem; }

    /* Controls Strip */
    .controls-strip {
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
      padding: 0.85rem 1.25rem; margin-bottom: 1.75rem; display: flex; align-items: center;
      justify-content: space-between; gap: 1rem; flex-wrap: wrap;
    }
    .filter-group { display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap; }
    .filter-chip {
      padding: 0.35rem 0.85rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
      cursor: pointer; border: 1px solid var(--border); background: var(--surface2);
      color: var(--text2); transition: all 0.15s ease; display: inline-flex; align-items: center; gap: 0.4rem;
    }
    .filter-chip:hover { background: var(--surface3); color: var(--text); }
    .filter-chip.active { background: var(--accent-glow); border-color: var(--accent-light); color: #c7d2fe; }
    .chip-count { background: rgba(255,255,255,0.08); padding: 0.05rem 0.4rem; border-radius: 999px; font-size: 0.7rem; }

    .search-box { position: relative; min-width: 260px; }
    .search-box input {
      width: 100%; background: var(--surface2); border: 1px solid var(--border);
      border-radius: var(--radius-md); padding: 0.5rem 1rem 0.5rem 2.25rem; color: var(--text);
      font-size: 0.82rem; font-family: inherit; outline: none; transition: border-color 0.2s ease;
    }
    .search-box input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }
    .search-icon { position: absolute; left: 0.75rem; top: 50%; transform: translateY(-50%); color: var(--muted); pointer-events: none; }

    /* Deployments Grid */
    .deployments-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 1.25rem;
    }

    .deployment-card {
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
      padding: 1.35rem; display: flex; flex-direction: column; gap: 1rem;
      position: relative; transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1); box-shadow: var(--shadow-sm);
    }
    .deployment-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-md); border-color: var(--accent-light); }
    .deployment-card.is-building {
      border-color: rgba(245, 158, 11, 0.5);
      box-shadow: 0 0 20px rgba(245, 158, 11, 0.15);
    }

    .card-header {
      display: flex; align-items: flex-start; justify-content: space-between; gap: 0.75rem;
    }
    .project-info { display: flex; flex-direction: column; gap: 0.2rem; min-width: 0; }
    .project-name-wrap { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
    .project-title { font-size: 1.05rem; font-weight: 700; color: var(--text); word-break: break-word; }
    
    .platform-pill {
      display: inline-flex; align-items: center; gap: 0.35rem;
      font-size: 0.7rem; font-weight: 700; padding: 0.18rem 0.55rem;
      border-radius: var(--radius-sm); letter-spacing: 0.03em; text-transform: uppercase;
    }
    .platform-pill.render { background: rgba(70, 227, 183, 0.12); color: #46e3b7; border: 1px solid rgba(70, 227, 183, 0.3); }
    .platform-pill.cloudflare { background: rgba(246, 130, 31, 0.12); color: #f6821f; border: 1px solid rgba(246, 130, 31, 0.3); }

    .account-badge {
      display: inline-flex; align-items: center; gap: 0.35rem;
      font-size: 0.72rem; color: var(--text3); font-weight: 500;
    }

    /* Status Badges */
    .status-badge {
      display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.3rem 0.75rem;
      border-radius: 999px; font-size: 0.74rem; font-weight: 700; letter-spacing: 0.02em; white-space: nowrap; flex-shrink: 0;
    }
    .badge-live { background: var(--success-glow); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-building {
      background: var(--warning-glow); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4);
      animation: pulse 1.8s infinite;
    }
    .badge-failed { background: var(--danger-glow); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
    .badge-canceled { background: rgba(100, 116, 139, 0.15); color: var(--muted); border: 1px solid rgba(100, 116, 139, 0.25); }

    /* Commit Box */
    .commit-box {
      background: rgba(24, 32, 50, 0.65); border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md); padding: 0.85rem; display: flex; flex-direction: column; gap: 0.5rem;
    }
    .commit-meta {
      display: flex; align-items: center; justify-content: space-between; gap: 0.5rem;
      font-size: 0.72rem;
    }
    .branch-pill {
      display: inline-flex; align-items: center; gap: 0.35rem;
      font-family: 'JetBrains Mono', monospace; color: #818cf8;
      background: rgba(99, 102, 241, 0.12); padding: 0.15rem 0.5rem; border-radius: 4px;
    }
    .commit-hash {
      font-family: 'JetBrains Mono', monospace; color: var(--text3);
    }
    .commit-message {
      font-size: 0.84rem; color: var(--text); font-weight: 500;
      line-height: 1.35; word-break: break-word;
    }
    .commit-message.empty { color: var(--text3); font-style: italic; }

    /* URL Strip */
    .site-url-bar {
      background: var(--surface2); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm);
      padding: 0.45rem 0.75rem; font-size: 0.78rem; font-family: 'JetBrains Mono', monospace;
      color: var(--text2); display: flex; align-items: center; gap: 0.5rem; overflow: hidden;
    }
    .site-url-link {
      color: var(--text2); text-decoration: none; white-space: nowrap; overflow: hidden;
      text-overflow: ellipsis; flex: 1; transition: color 0.15s ease;
    }
    .site-url-link:hover { color: var(--accent-light); text-decoration: underline; }

    /* Card Footer Actions */
    .card-footer-actions {
      display: flex; align-items: center; justify-content: space-between; gap: 0.75rem;
      padding-top: 0.5rem; border-top: 1px solid var(--border-subtle); font-size: 0.75rem;
    }
    .time-stamp { color: var(--text3); display: flex; align-items: center; gap: 0.35rem; }
    .action-links { display: flex; align-items: center; gap: 0.85rem; }
    .action-link {
      color: var(--accent-light); text-decoration: none; font-weight: 600;
      display: inline-flex; align-items: center; gap: 0.25rem; transition: color 0.15s ease;
    }
    .action-link:hover { color: #fff; text-decoration: underline; }

    /* Setup Guide Card (When no accounts configured) */
    .setup-guide-card {
      background: linear-gradient(135deg, rgba(99, 102, 241, 0.08), rgba(16, 22, 35, 0.95));
      border: 1px solid rgba(99, 102, 241, 0.3); border-radius: var(--radius-xl);
      padding: 2rem; margin-bottom: 2rem; box-shadow: var(--shadow-md);
    }
    .setup-guide-header {
      display: flex; align-items: center; gap: 1rem; margin-bottom: 1.25rem;
    }
    .setup-icon {
      width: 44px; height: 44px; border-radius: var(--radius-md);
      background: rgba(99, 102, 241, 0.15); display: flex; align-items: center;
      justify-content: center; color: var(--accent-light); flex-shrink: 0;
    }
    .setup-guide-header h3 { font-size: 1.15rem; font-weight: 700; color: #fff; }
    .setup-guide-header p { font-size: 0.85rem; color: var(--text2); }
    .setup-tabs {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 1.25rem; margin-top: 1.25rem;
    }
    .setup-box {
      background: var(--surface2); border: 1px solid var(--border); border-radius: var(--radius-md);
      padding: 1.25rem;
    }
    .setup-box h4 { font-size: 0.95rem; font-weight: 700; color: #fff; margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.45rem; }
    .setup-box p { font-size: 0.8rem; color: var(--text2); margin-bottom: 0.75rem; }
    .code-block {
      background: #090b10; border: 1px solid var(--border); border-radius: var(--radius-sm);
      padding: 0.75rem 1rem; font-family: 'JetBrains Mono', monospace; font-size: 0.76rem;
      color: #38bdf8; overflow-x: auto; white-space: pre; line-height: 1.5; margin-bottom: 0.5rem;
    }

    /* Monitors Section Styles (from existing dashboard) */
    .system-health-banner {
      background: linear-gradient(135deg, rgba(16, 185, 129, 0.12), rgba(16, 22, 35, 0.95));
      border: 1px solid rgba(16, 185, 129, 0.3); border-radius: var(--radius-lg);
      padding: 1.25rem 1.75rem; margin-bottom: 2rem; display: flex; align-items: center;
      justify-content: space-between; gap: 1rem; box-shadow: var(--shadow-sm);
    }
    .system-health-banner.degraded {
      background: linear-gradient(135deg, rgba(239, 68, 68, 0.12), rgba(16, 22, 35, 0.95));
      border-color: rgba(239, 68, 68, 0.3);
    }
    .health-status-text { display: flex; align-items: center; gap: 0.85rem; }
    .health-status-icon {
      width: 38px; height: 38px; border-radius: 50%;
      background: var(--success); display: flex; align-items: center;
      justify-content: center; color: white; box-shadow: 0 0 12px var(--success-glow); flex-shrink: 0;
    }
    .system-health-banner.degraded .health-status-icon {
      background: var(--danger); box-shadow: 0 0 12px var(--danger-glow);
    }
    .health-title { font-size: 1.05rem; font-weight: 700; color: #fff; }
    .health-sub { font-size: 0.78rem; color: var(--text2); }

    .monitors-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 1.25rem;
    }
    .monitor-card {
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
      padding: 1.4rem; display: flex; flex-direction: column; gap: 1rem; cursor: pointer;
      position: relative; transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1); box-shadow: var(--shadow-sm);
    }
    .monitor-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-md); border-color: var(--accent-light); }

    .meta-metrics {
      display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;
      background: rgba(24, 32, 50, 0.5); border-radius: var(--radius-md); padding: 0.85rem;
    }
    .metric-item { display: flex; flex-direction: column; gap: 0.15rem; }
    .metric-title { font-size: 0.68rem; color: var(--text3); text-transform: uppercase; font-weight: 600; letter-spacing: 0.04em; }
    .metric-value { font-size: 0.84rem; font-weight: 600; color: var(--text); }
    .metric-value.mono { font-family: 'JetBrains Mono', monospace; font-size: 0.78rem; color: #38bdf8; }

    .sparkline-wrap { display: flex; align-items: flex-end; gap: 3px; height: 26px; padding: 2px 0; }
    .spark-bar { flex: 1; background: rgba(56, 189, 248, 0.35); border-radius: 2px; min-height: 4px; }
    .spark-bar:hover { background: #38bdf8; }
    .uptime-pill { font-weight: 700; color: #34d399; font-family: 'JetBrains Mono', monospace; }

    /* Modal */
    .modal-backdrop {
      display: none; position: fixed; inset: 0; background: rgba(5, 7, 12, 0.85);
      z-index: 200; align-items: center; justify-content: center; padding: 1.25rem; backdrop-filter: blur(8px);
    }
    .modal-backdrop.open { display: flex; }
    .modal-box {
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
      width: 100%; max-width: 820px; max-height: 88vh; overflow-y: auto; box-shadow: var(--shadow-lg);
      animation: modalIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    }
    @keyframes modalIn { from { opacity: 0; transform: scale(0.97) translateY(12px); } to { opacity: 1; transform: scale(1) translateY(0); } }
    .modal-head {
      position: sticky; top: 0; background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 1.25rem 1.75rem; display: flex; align-items: center; justify-content: space-between; z-index: 10;
    }
    .modal-head h2 { font-size: 1.15rem; font-weight: 700; color: #fff; }
    .modal-close-btn {
      background: var(--surface2); border: 1px solid var(--border); color: var(--text2);
      width: 32px; height: 32px; border-radius: var(--radius-sm); display: flex; align-items: center;
      justify-content: center; cursor: pointer; font-size: 1rem;
    }
    .modal-close-btn:hover { background: var(--surface3); color: #fff; }
    .modal-content { padding: 1.75rem; display: flex; flex-direction: column; gap: 1.5rem; }

    .section-header {
      font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
      color: var(--text3); margin-bottom: 0.75rem; display: flex; align-items: center; gap: 0.6rem;
    }
    .section-header::after { content: ''; flex: 1; height: 1px; background: var(--border-subtle); }
    .specs-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 0.85rem; }
    .spec-item {
      background: var(--surface2); border-radius: var(--radius-md); padding: 0.75rem 1rem;
      border: 1px solid var(--border-subtle);
    }
    .spec-name { font-size: 0.68rem; color: var(--text3); text-transform: uppercase; font-weight: 600; letter-spacing: 0.04em; }
    .spec-val { font-size: 0.88rem; font-weight: 600; color: var(--text); margin-top: 0.25rem; word-break: break-all; }
    .spec-val.mono { font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; color: #38bdf8; }

    /* Tables */
    .history-card { background: var(--surface2); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    th, td { text-align: left; padding: 0.7rem 1rem; border-bottom: 1px solid var(--border-subtle); }
    th { background: var(--surface3); color: var(--text2); font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.02); }
    .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; margin-right: 0.4rem; }
    .dot-green { background: var(--success); box-shadow: 0 0 6px var(--success); }
    .dot-red { background: var(--danger); box-shadow: 0 0 6px var(--danger); }

    .empty-state { text-align: center; padding: 4rem 1.5rem; color: var(--text2); }
    .empty-state h3 { font-size: 1.15rem; color: var(--text); font-weight: 700; margin-bottom: 0.35rem; }
    .empty-state p { font-size: 0.85rem; color: var(--text3); max-width: 480px; margin: 0 auto; line-height: 1.5; }
    .loading-spinner {
      width: 36px; height: 36px; border: 3px solid var(--border); border-top-color: var(--accent-light);
      border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 1rem;
    }

    .toast-tray { position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 500; display: flex; flex-direction: column; gap: 0.5rem; max-width: 90vw; }
    .toast-msg {
      background: var(--surface2); border: 1px solid var(--border); border-radius: var(--radius-md);
      padding: 0.75rem 1.25rem; font-size: 0.85rem; color: var(--text); box-shadow: var(--shadow-md);
    }
    .toast-msg.error { border-color: rgba(239, 68, 68, 0.4); color: #fca5a5; }
    .toast-msg.success { border-color: rgba(16, 185, 129, 0.4); color: #6ee7b7; }

    /* Mobile Responsive Optimizations */
    @media (max-width: 768px) {
      header { padding: 0.75rem 1rem; }
      main { padding: 1.25rem 1rem 3rem; }
      .brand-text span { display: none; }
      .brand-text h1 { font-size: 0.95rem; }

      .view-tabs-container { flex-direction: column; align-items: stretch; gap: 0.75rem; }
      .view-tabs { width: 100%; display: flex; }
      .view-tab { flex: 1; justify-content: center; padding: 0.6rem 0.75rem; font-size: 0.8rem; }
      .live-indicator { justify-content: center; }

      .stats-grid { grid-template-columns: repeat(2, 1fr); gap: 0.75rem; }
      .stat-card { padding: 1rem; }
      .stat-value { font-size: 1.6rem; }

      .controls-strip { flex-direction: column; align-items: stretch; gap: 0.85rem; padding: 1rem; }
      .filter-group { overflow-x: auto; flex-wrap: nowrap; padding-bottom: 0.25rem; -webkit-overflow-scrolling: touch; }
      .filter-chip { flex-shrink: 0; }
      .search-box { min-width: 100%; }

      .deployments-grid, .monitors-grid { grid-template-columns: 1fr; gap: 1rem; }
      .deployment-card, .monitor-card { padding: 1.15rem; }
      .setup-tabs { grid-template-columns: 1fr; }
      .modal-box { max-height: 92vh; }
      .modal-content { padding: 1.25rem; }
    }
  </style>
</head>
<body>

<header>
  <div class="brand">
    <div class="brand-icon">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
      </svg>
    </div>
    <div class="brand-text">
      <h1>Ops Command Center</h1>
      <span>Deployments & Uptime Monitor</span>
    </div>
  </div>

  <div class="header-actions">
    <button id="refreshBtn" class="refresh-btn" title="Refresh Live Status">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
      </svg>
      <span>Refresh</span>
    </button>
  </div>
</header>

<main>
  <!-- Navigation Tabs -->
  <div class="view-tabs-container">
    <div class="view-tabs">
      <button class="view-tab active" data-tab="deployments">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/>
          <path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/>
        </svg>
        <span>Git Deployments</span>
        <span class="tab-badge" id="deployTabBadge">0</span>
      </button>

      <button class="view-tab" data-tab="monitors">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
        </svg>
        <span>Uptime Monitors</span>
        <span class="tab-badge" id="monitorTabBadge">0</span>
      </button>
    </div>

    <div class="live-indicator">
      <span class="pulse-dot" id="liveDot"></span>
      <span id="autoRefreshText">Live sync active (30s)</span>
    </div>
  </div>

  <!-- ===================================================================== -->
  <!-- TAB 1: DEPLOYMENTS VIEW -->
  <!-- ===================================================================== -->
  <div id="deploymentsView">
    <!-- Deployments Stats -->
    <div class="stats-grid">
      <div class="stat-card total">
        <div class="stat-label">Total Services</div>
        <div class="stat-value" id="depStatTotal">—</div>
        <div class="stat-sub">Across all accounts</div>
      </div>
      <div class="stat-card live">
        <div class="stat-label">Live & Active</div>
        <div class="stat-value" id="depStatLive">—</div>
        <div class="stat-sub">Healthy latest build</div>
      </div>
      <div class="stat-card building">
        <div class="stat-label">In Progress</div>
        <div class="stat-value" id="depStatBuilding">—</div>
        <div class="stat-sub">Currently deploying</div>
      </div>
      <div class="stat-card failed">
        <div class="stat-label">Failed Builds</div>
        <div class="stat-value" id="depStatFailed">—</div>
        <div class="stat-sub">Needs attention</div>
      </div>
      <div class="stat-card accounts">
        <div class="stat-label">Accounts Synced</div>
        <div class="stat-value" id="depStatAccounts">—</div>
        <div class="stat-sub" id="depStatAccountsSub">Render & Cloudflare</div>
      </div>
    </div>

    <!-- Setup Guide (Hidden when configured) -->
    <div id="depSetupCard" class="setup-guide-card" style="display:none;">
      <div class="setup-guide-header">
        <div class="setup-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
          </svg>
        </div>
        <div>
          <h3>Track Your Render & Cloudflare Deployments</h3>
          <p>Connect your Render and Cloudflare accounts to see live build statuses right after every Git push.</p>
        </div>
      </div>

      <div class="setup-tabs">
        <div class="setup-box">
          <h4>🟠 Render Accounts (Any number of accounts)</h4>
          <p>Get your API key at: <strong>Render Dashboard → Account Settings → API Keys</strong>.</p>
          <div class="code-block"># Option A: Numbered variables (Easiest in Cloudflare Pages)
RENDER_API_KEY_1=rnd_yourKey1
RENDER_LABEL_1=Render - Personal

RENDER_API_KEY_2=rnd_yourKey2
RENDER_LABEL_2=Render - Client Project

# Option B: Comma-separated keys
RENDER_API_KEYS=rnd_key1,rnd_key2</div>
          <p style="font-size:0.75rem;color:var(--text3);">Services are auto-discovered automatically. No service IDs needed!</p>
        </div>

        <div class="setup-box">
          <h4>🔵 Cloudflare Accounts (Any number of accounts)</h4>
          <p>Get Account ID from sidebar and API Token at: <strong>My Profile → API Tokens → Edit Cloudflare Workers</strong>.</p>
          <div class="code-block"># Option A: Numbered variables (Easiest in Cloudflare Pages)
CF_ACCOUNT_ID_1=your_cf_account_id_1
CF_API_TOKEN_1=your_cf_api_token_1
CF_LABEL_1=Cloudflare - Main

CF_ACCOUNT_ID_2=your_cf_account_id_2
CF_API_TOKEN_2=your_cf_api_token_2
CF_LABEL_2=Cloudflare - Studio</div>
          <p style="font-size:0.75rem;color:var(--text3);">Pages projects are auto-discovered automatically!</p>
        </div>
      </div>
    </div>

    <!-- Filters & Search -->
    <div class="controls-strip">
      <div class="filter-group" id="depFilterGroup">
        <button class="filter-chip active" data-dep-filter="all">All <span class="chip-count" id="depCountAll">0</span></button>
        <button class="filter-chip" data-dep-filter="render">🟠 Render <span class="chip-count" id="depCountRender">0</span></button>
        <button class="filter-chip" data-dep-filter="cloudflare">🔵 Cloudflare <span class="chip-count" id="depCountCF">0</span></button>
        <button class="filter-chip" data-dep-filter="building">🔄 Building <span class="chip-count" id="depCountBuilding">0</span></button>
        <button class="filter-chip" data-dep-filter="live">🟢 Live <span class="chip-count" id="depCountLive">0</span></button>
        <button class="filter-chip" data-dep-filter="failed">🔴 Failed <span class="chip-count" id="depCountFailed">0</span></button>
      </div>

      <div class="search-box">
        <svg class="search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input type="text" id="depSearchInput" placeholder="Search service, commit, branch..." />
      </div>
    </div>

    <!-- Deployments Cards Container -->
    <div id="deploymentsContainer">
      <div class="empty-state">
        <div class="loading-spinner"></div>
        <h3>Connecting to deployment providers...</h3>
        <p>Fetching real-time build and commit status across Render and Cloudflare accounts.</p>
      </div>
    </div>
  </div>

  <!-- ===================================================================== -->
  <!-- TAB 2: UPTIME MONITORS VIEW -->
  <!-- ===================================================================== -->
  <div id="monitorsView" style="display:none;">
    <!-- System Health Banner -->
    <div id="healthBanner" class="system-health-banner">
      <div class="health-status-text">
        <div class="health-status-icon" id="healthIcon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
        </div>
        <div>
          <div class="health-title" id="healthTitle">All Systems Operational</div>
          <div class="health-sub" id="healthSub">All monitored endpoints are responding normally.</div>
        </div>
      </div>
      <div style="font-size:0.75rem;color:var(--text3);text-align:right;">
        Powered by UptimeRobot
      </div>
    </div>

    <!-- Monitors Summary Stats -->
    <div class="stats-grid">
      <div class="stat-card total"><div class="stat-label">Total Monitors</div><div class="stat-value" id="statTotal">—</div><div class="stat-sub">Tracked endpoints</div></div>
      <div class="stat-card live"><div class="stat-label">Operational (Up)</div><div class="stat-value" id="statUp">—</div><div class="stat-sub">Responding healthy</div></div>
      <div class="stat-card failed"><div class="stat-label">Downtime / Outage</div><div class="stat-value" id="statDown">—</div><div class="stat-sub">Currently failing</div></div>
      <div class="stat-card accounts"><div class="stat-label">Avg Response Time</div><div class="stat-value" id="statLatency">—</div><div class="stat-sub">Last 24 hours</div></div>
      <div class="stat-card total"><div class="stat-label">30-Day Uptime</div><div class="stat-value" id="statRatio">—</div><div class="stat-sub">Overall reliability</div></div>
    </div>

    <!-- Filters & Search -->
    <div class="controls-strip">
      <div class="filter-group" id="monitorFilterGroup">
        <button class="filter-chip active" data-filter="all">All <span class="chip-count" id="countAll">0</span></button>
        <button class="filter-chip" data-filter="up">🟢 Up <span class="chip-count" id="countUp">0</span></button>
        <button class="filter-chip" data-filter="down">🔴 Down <span class="chip-count" id="countDown">0</span></button>
        <button class="filter-chip" data-filter="paused">⏸ Paused <span class="chip-count" id="countPaused">0</span></button>
      </div>
      <div class="search-box">
        <svg class="search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input type="text" id="searchInput" placeholder="Search by name or URL..." />
      </div>
    </div>

    <!-- Monitors Grid -->
    <div id="monitorsContainer">
      <div class="empty-state">
        <div class="loading-spinner"></div>
        <h3>Connecting to UptimeRobot...</h3>
        <p>Fetching real-time uptime status and latency metrics.</p>
      </div>
    </div>
  </div>
</main>

<!-- Detail Modal for Uptime Monitor -->
<div class="modal-backdrop" id="monitorModal">
  <div class="modal-box">
    <div class="modal-head">
      <div>
        <h2 id="modalTitle">Monitor Details</h2>
        <span id="modalSubtitle" style="font-size:0.75rem;color:var(--text3);font-family:'JetBrains Mono',monospace;"></span>
      </div>
      <button class="modal-close-btn" id="modalCloseBtn">✕</button>
    </div>
    <div class="modal-content" id="modalContent"></div>
  </div>
</div>

<div class="toast-tray" id="toastTray"></div>

<script>
  let activeTab = 'deployments';
  let allDeployments = [];
  let depFilter = 'all';
  let allMonitors = [];
  let monitorFilter = 'all';
  let isBuildingActive = false;
  let refreshTimer = null;

  const $ = id => document.getElementById(id);

  function toast(msg, type = 'info') {
    const el = document.createElement('div');
    el.className = 'toast-msg ' + type;
    el.textContent = msg;
    $('toastTray').appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  function esc(s) {
    if (!s) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function timeAgo(dateString) {
    if (!dateString) return 'Unknown';
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return dateString;
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);

    if (diff < 15) return 'Just now';
    if (diff < 60) return diff + 's ago';
    const m = Math.floor(diff / 60);
    if (m < 60) return m + 'm ago';
    const h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    const d = Math.floor(h / 24);
    if (d < 30) return d + 'd ago';
    return date.toLocaleDateString();
  }

  function formatTime(ts) {
    if (!ts || ts === 0) return 'Never';
    return new Date(ts * 1000).toLocaleString();
  }

  function formatDuration(seconds) {
    if (!seconds && seconds !== 0) return '—';
    if (seconds < 60) return seconds + 's';
    const m = Math.floor(seconds / 60);
    if (m < 60) return m + 'm';
    const h = Math.floor(m / 60);
    return h + 'h ' + (m % 60) + 'm';
  }

  // =========================================================================
  // VIEW TABS CONTROLLER
  // =========================================================================
  document.querySelectorAll('.view-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.view-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeTab = btn.dataset.tab;

      if (activeTab === 'deployments') {
        $('deploymentsView').style.display = 'block';
        $('monitorsView').style.display = 'none';
      } else {
        $('deploymentsView').style.display = 'none';
        $('monitorsView').style.display = 'block';
      }
    });
  });

  // =========================================================================
  // DEPLOYMENTS ENGINE
  // =========================================================================
  async function loadDeployments(isSilent = false) {
    try {
      const res = await fetch('/api/deployments');
      const data = await res.json();

      if (!res.ok) throw new Error(data.error || 'Failed to fetch deployments');

      if (!data.configured) {
        $('depSetupCard').style.display = 'block';
      } else {
        $('depSetupCard').style.display = 'none';
      }

      allDeployments = data.deployments || [];
      const summary = data.summary || {
        total: allDeployments.length,
        live: allDeployments.filter(d => d.status === 'live').length,
        building: allDeployments.filter(d => d.status === 'building').length,
        failed: allDeployments.filter(d => d.status === 'failed').length
      };

      const accounts = data.accounts || { renderCount: 0, cloudflareCount: 0 };

      // Update Deployments KPI stats
      $('depStatTotal').textContent = summary.total;
      $('depStatLive').textContent = summary.live;
      $('depStatBuilding').textContent = summary.building;
      $('depStatFailed').textContent = summary.failed;

      const totalAcc = accounts.renderCount + accounts.cloudflareCount;
      $('depStatAccounts').textContent = totalAcc > 0 ? totalAcc : '0';
      $('depStatAccountsSub').textContent = \`\${accounts.renderCount} Render · \${accounts.cloudflareCount} Cloudflare\`;

      // Update Filter counts
      $('depCountAll').textContent = allDeployments.length;
      $('depCountRender').textContent = allDeployments.filter(d => d.provider === 'render').length;
      $('depCountCF').textContent = allDeployments.filter(d => d.provider === 'cloudflare').length;
      $('depCountBuilding').textContent = summary.building;
      $('depCountLive').textContent = summary.live;
      $('depCountFailed').textContent = summary.failed;

      // Update Tab badge
      $('deployTabBadge').textContent = allDeployments.length;
      if (summary.building > 0) {
        $('deployTabBadge').className = 'tab-badge building';
        $('deployTabBadge').textContent = \`\${summary.building} Building\`;
        isBuildingActive = true;
        $('liveDot').className = 'pulse-dot warning';
        $('autoRefreshText').textContent = 'Fast sync active (10s) — Build in progress';
      } else {
        $('deployTabBadge').className = 'tab-badge';
        isBuildingActive = false;
        $('liveDot').className = 'pulse-dot';
        $('autoRefreshText').textContent = 'Live sync active (30s)';
      }

      renderDeploymentsList();
    } catch (err) {
      $('deploymentsContainer').innerHTML = \`<div class="empty-state"><h3>Unable to load deployments</h3><p>\${esc(err.message)}</p></div>\`;
    }
  }

  function getFilteredDeployments() {
    const q = ($('depSearchInput').value || '').trim().toLowerCase();
    return allDeployments.filter(d => {
      const matchFilter =
        depFilter === 'all'        ? true :
        depFilter === 'render'     ? d.provider === 'render' :
        depFilter === 'cloudflare' ? d.provider === 'cloudflare' :
        depFilter === 'building'   ? d.status === 'building' :
        depFilter === 'live'       ? d.status === 'live' :
        depFilter === 'failed'     ? d.status === 'failed' : true;

      const matchSearch = !q ||
        (d.projectName || '').toLowerCase().includes(q) ||
        (d.accountName || '').toLowerCase().includes(q) ||
        (d.commit && d.commit.message && d.commit.message.toLowerCase().includes(q)) ||
        (d.commit && d.commit.branch && d.commit.branch.toLowerCase().includes(q)) ||
        (d.commit && d.commit.hash && d.commit.hash.toLowerCase().includes(q));

      return matchFilter && matchSearch;
    });
  }

  function renderDeploymentsList() {
    const list = getFilteredDeployments();
    if (!list.length) {
      if (!allDeployments.length) {
        $('deploymentsContainer').innerHTML = \`
          <div class="empty-state">
            <h3>No Deployments Connected</h3>
            <p>Add your Render API key and Cloudflare tokens in environment variables to track live build statuses.</p>
          </div>\`;
      } else {
        $('deploymentsContainer').innerHTML = \`
          <div class="empty-state">
            <h3>No matching deployments</h3>
            <p>Try adjusting your search query or filter chip.</p>
          </div>\`;
      }
      return;
    }

    const html = list.map(d => {
      const isRender = d.provider === 'render';
      const isBuilding = d.status === 'building';
      const isLive = d.status === 'live';
      const isFailed = d.status === 'failed';

      let badgeClass = 'badge-canceled';
      let badgeIcon = '⚪';
      if (isLive) {
        badgeClass = 'badge-live';
        badgeIcon = '🟢';
      } else if (isBuilding) {
        badgeClass = 'badge-building';
        badgeIcon = '🔄';
      } else if (isFailed) {
        badgeClass = 'badge-failed';
        badgeIcon = '🔴';
      }

      const commit = d.commit || {};
      const commitMsg = commit.message ? esc(commit.message) : '<span class="commit-message empty">No commit message provided</span>';
      const commitHash = commit.hash ? \`#\${esc(commit.hash)}\` : '';
      const branchName = commit.branch ? esc(commit.branch) : 'main';
      const updatedTime = timeAgo(d.finishedAt || d.createdAt || d.updatedAt);

      const liveUrl = d.liveUrl ? esc(d.liveUrl) : '';
      const dashboardUrl = d.dashboardUrl ? esc(d.dashboardUrl) : '';

      return \`
        <div class="deployment-card \${isBuilding ? 'is-building' : ''}">
          <div class="card-header">
            <div class="project-info">
              <div class="project-name-wrap">
                <span class="platform-pill \${isRender ? 'render' : 'cloudflare'}">
                  \${isRender ? '🟠 Render' : '🔵 Cloudflare'}
                </span>
                <span class="project-title">\${esc(d.projectName)}</span>
              </div>
              <span class="account-badge">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                \${esc(d.accountName)}
              </span>
            </div>

            <div class="status-badge \${badgeClass}">
              <span>\${badgeIcon}</span>
              \${esc(d.statusLabel || (isBuilding ? 'Building' : 'Live'))}
            </div>
          </div>

          <div class="commit-box">
            <div class="commit-meta">
              <span class="branch-pill">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>
                \${branchName}
              </span>
              <span class="commit-hash">\${commitHash}</span>
            </div>
            <div class="commit-message">\${commitMsg}</div>
          </div>

          \${liveUrl ? \`
            <div class="site-url-bar">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
              <a href="\${liveUrl}" target="_blank" rel="noopener" class="site-url-link">\${liveUrl}</a>
            </div>
          \` : ''}

          <div class="card-footer-actions">
            <div class="time-stamp">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
              \${isBuilding ? 'Build started ' + updatedTime : 'Deployed ' + updatedTime}
            </div>

            <div class="action-links">
              \${liveUrl ? \`<a href="\${liveUrl}" target="_blank" rel="noopener" class="action-link">Open Site ↗</a>\` : ''}
              \${dashboardUrl ? \`<a href="\${dashboardUrl}" target="_blank" rel="noopener" class="action-link" style="color:var(--text3);">Console ↗</a>\` : ''}
            </div>
          </div>
        </div>\`;
    }).join('');

    $('deploymentsContainer').innerHTML = '<div class="deployments-grid">' + html + '</div>';
  }

  // Filter and Search Event Listeners for Deployments
  document.querySelectorAll('#depFilterGroup .filter-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#depFilterGroup .filter-chip').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      depFilter = btn.dataset.depFilter;
      renderDeploymentsList();
    });
  });
  $('depSearchInput').addEventListener('input', renderDeploymentsList);

  // =========================================================================
  // UPTIME MONITORS ENGINE
  // =========================================================================
  function getStatusInfo(status) {
    switch (status) {
      case 2: return { key: 'up', label: 'Operational (UP)', badgeClass: 'badge-live' };
      case 9: return { key: 'down', label: 'Down (Outage)', badgeClass: 'badge-failed' };
      case 8: return { key: 'down', label: 'Seems Down', badgeClass: 'badge-building' };
      case 0: return { key: 'paused', label: 'Paused', badgeClass: 'badge-canceled' };
      default: return { key: 'paused', label: 'Not Checked', badgeClass: 'badge-canceled' };
    }
  }

  function parseRatios(str) {
    if (!str) return { d1: '100', d7: '100', d30: '100' };
    const parts = str.split('-');
    return {
      d1: parseFloat(parts[0] || '100').toFixed(1),
      d7: parseFloat(parts[1] || '100').toFixed(1),
      d30: parseFloat(parts[2] || '100').toFixed(1)
    };
  }

  async function loadMonitors(isSilent = false) {
    try {
      const res = await fetch('/api/monitors');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to fetch monitors');

      allMonitors = data.monitors || [];
      $('monitorTabBadge').textContent = allMonitors.length;

      updateMonitorMetrics();
      renderMonitorList();
    } catch (err) {
      $('monitorsContainer').innerHTML = \`<div class="empty-state"><h3>Unable to load monitors</h3><p>\${esc(err.message)}</p></div>\`;
    }
  }

  function updateMonitorMetrics() {
    const total = allMonitors.length;
    const up = allMonitors.filter(m => m.status === 2).length;
    const down = allMonitors.filter(m => m.status === 8 || m.status === 9).length;
    const paused = allMonitors.filter(m => m.status === 0 || m.status === 1).length;

    $('statTotal').textContent = total;
    $('statUp').textContent = up;
    $('statDown').textContent = down;

    $('countAll').textContent = total;
    $('countUp').textContent = up;
    $('countDown').textContent = down;
    $('countPaused').textContent = paused;

    const latencies = allMonitors.map(m => parseFloat(m.average_response_time) || 0).filter(v => v > 0);
    const avgLatency = latencies.length ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) : 0;
    $('statLatency').textContent = avgLatency ? avgLatency + ' ms' : '—';

    const ratios = allMonitors.map(m => parseFloat(parseRatios(m.custom_uptime_ratio).d30)).filter(v => !isNaN(v));
    const avgRatio = ratios.length ? (ratios.reduce((a, b) => a + b, 0) / ratios.length).toFixed(2) : '100.00';
    $('statRatio').textContent = avgRatio + '%';

    if (down > 0) {
      $('healthBanner').className = 'system-health-banner degraded';
      $('healthTitle').textContent = down + ' Service' + (down > 1 ? 's' : '') + ' Experiencing Outage';
      $('healthSub').textContent = 'One or more of your monitored applications are currently unreachable.';
      $('healthIcon').innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    } else {
      $('healthBanner').className = 'system-health-banner';
      $('healthTitle').textContent = 'All Systems Operational';
      $('healthSub').textContent = 'All ' + up + ' monitored services are healthy and responding.';
      $('healthIcon').innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>';
    }
  }

  function getFilteredMonitors() {
    const q = ($('searchInput').value || '').trim().toLowerCase();
    return allMonitors.filter(m => {
      const st = getStatusInfo(m.status);
      const matchFilter =
        monitorFilter === 'all'    ? true :
        monitorFilter === 'up'     ? st.key === 'up' :
        monitorFilter === 'down'   ? st.key === 'down' :
        monitorFilter === 'paused' ? st.key === 'paused' : true;

      const matchSearch = !q ||
        (m.friendly_name || '').toLowerCase().includes(q) ||
        (m.url || '').toLowerCase().includes(q) ||
        String(m.id).includes(q);

      return matchFilter && matchSearch;
    });
  }

  function renderMonitorList() {
    const monitors = getFilteredMonitors();
    if (!monitors.length) {
      $('monitorsContainer').innerHTML = '<div class="empty-state"><h3>No matching monitors</h3><p>Adjust your search query or filter tab.</p></div>';
      return;
    }

    const html = monitors.map(m => {
      const st = getStatusInfo(m.status);
      const ratios = parseRatios(m.custom_uptime_ratio);
      const avgLatency = m.average_response_time ? Math.round(m.average_response_time) + ' ms' : '—';
      const intervalText = m.interval ? ('Every ' + Math.round(m.interval / 60) + ' min') : '5 min';

      const rts = m.response_times || [];
      const sparkBars = rts.slice(-12).map(r => {
        const h = Math.min(Math.max((r.value / 600) * 100, 15), 100);
        return '<div class="spark-bar" style="height:' + h + '%;" title="' + r.value + ' ms"></div>';
      }).join('');

      return \`
        <div class="monitor-card" data-id="\${m.id}">
          <div class="card-header">
            <div>
              <div class="project-title">\${esc(m.friendly_name || '(Unnamed Monitor)')}</div>
              <div style="font-family:'JetBrains Mono',monospace;font-size:0.7rem;color:var(--text3);">ID: \${m.id}</div>
            </div>
            <div class="status-badge \${st.badgeClass}">
              <span class="dot \${st.key === 'up' ? 'dot-green' : 'dot-red'}"></span>
              \${st.label}
            </div>
          </div>

          <div class="site-url-bar">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
            <a href="\${esc(m.url)}" target="_blank" rel="noopener" class="site-url-link" onclick="event.stopPropagation()">\${esc(m.url)}</a>
          </div>

          <div class="meta-metrics">
            <div class="metric-item">
              <span class="metric-title">Check Interval</span>
              <span class="metric-value">\${intervalText}</span>
            </div>
            <div class="metric-item">
              <span class="metric-title">Avg Latency (24h)</span>
              <span class="metric-value mono">\${avgLatency}</span>
            </div>
            <div class="metric-item">
              <span class="metric-title">24h Uptime</span>
              <span class="metric-value" style="color:#34d399;">\${ratios.d1}%</span>
            </div>
            <div class="metric-item">
              <span class="metric-title">30d Uptime</span>
              <span class="metric-value" style="color:#a855f7;">\${ratios.d30}%</span>
            </div>
          </div>

          \${rts.length ? '<div style="display:flex;flex-direction:column;gap:3px;"><span style="font-size:0.65rem;color:var(--text3);text-transform:uppercase;font-weight:600;">Response Time Trend</span><div class="sparkline-wrap">' + sparkBars + '</div></div>' : ''}

          <div class="card-footer-actions">
            <span>7-Day: <strong class="uptime-pill">\${ratios.d7}%</strong></span>
            <span style="color:var(--accent-light);font-weight:600;">View History ↗</span>
          </div>
        </div>\`;
    }).join('');

    $('monitorsContainer').innerHTML = '<div class="monitors-grid">' + html + '</div>';
    document.querySelectorAll('.monitor-card').forEach(c => c.addEventListener('click', () => openModal(c.dataset.id)));
  }

  function openModal(monitorId) {
    const m = allMonitors.find(item => String(item.id) === String(monitorId));
    if (!m) return;

    const modal = $('monitorModal');
    modal.classList.add('open');
    $('modalTitle').textContent = m.friendly_name || '(Unnamed Monitor)';
    $('modalSubtitle').textContent = 'Monitor ID: ' + m.id;

    const st = getStatusInfo(m.status);
    const ratios = parseRatios(m.custom_uptime_ratio);
    const intervalText = m.interval ? (Math.round(m.interval / 60) + ' minutes') : '5 minutes';
    const logs = m.logs || [];

    const logRows = !logs.length
      ? '<tr><td colspan="4" style="text-align:center;color:var(--text3);padding:2rem;">No recent incident or downtime logs recorded. All checks passed!</td></tr>'
      : logs.slice(0, 15).map(l => {
          const isUp = l.type === 2;
          const dotClass = isUp ? 'dot-green' : 'dot-red';
          const reasonText = l.reason ? (l.reason.detail || l.reason.code || 'Normal') : (isUp ? 'Operational' : 'Down');
          return \`
            <tr>
              <td>\${formatTime(l.datetime)}</td>
              <td><span class="dot \${dotClass}"></span><strong>\${isUp ? 'Online (UP)' : 'Downtime (DOWN)'}</strong></td>
              <td>\${formatDuration(l.duration)}</td>
              <td><span style="font-family:'JetBrains Mono',monospace;color:\${isUp ? '#34d399' : 'var(--danger)'};">\${esc(reasonText)}</span></td>
            </tr>\`;
        }).join('');

    $('modalContent').innerHTML = \`
      <div>
        <div class="section-header">Status & Uptime Overview</div>
        <div class="specs-grid">
          <div class="spec-item"><div class="spec-name">Current Status</div><div class="spec-val"><span class="status-badge \${st.badgeClass}">\${st.label}</span></div></div>
          <div class="spec-item"><div class="spec-name">Check Frequency</div><div class="spec-val">\${intervalText}</div></div>
          <div class="spec-item"><div class="spec-name">24h Uptime</div><div class="spec-val" style="color:#34d399;">\${ratios.d1}%</div></div>
          <div class="spec-item"><div class="spec-name">7-Day Uptime</div><div class="spec-val" style="color:#34d399;">\${ratios.d7}%</div></div>
          <div class="spec-item"><div class="spec-name">30-Day Uptime</div><div class="spec-val" style="color:#a855f7;">\${ratios.d30}%</div></div>
          <div class="spec-item"><div class="spec-name">Avg Latency</div><div class="spec-val mono">\${m.average_response_time ? Math.round(m.average_response_time) + ' ms' : '—'}</div></div>
        </div>
      </div>

      <div>
        <div class="section-header">Target URL</div>
        <div class="spec-item"><a href="\${esc(m.url)}" target="_blank" rel="noopener" class="spec-val mono" style="color:var(--accent-light);text-decoration:none;">\${esc(m.url)}</a></div>
      </div>

      <div>
        <div class="section-header">Recent Incidents & Event History</div>
        <div class="history-card">
          <table>
            <thead><tr><th>Timestamp</th><th>Event</th><th>Duration</th><th>Details / Reason</th></tr></thead>
            <tbody>\${logRows}</tbody>
          </table>
        </div>
      </div>\`;
  }

  // Filter and Search Event Listeners for Monitors
  document.querySelectorAll('#monitorFilterGroup .filter-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#monitorFilterGroup .filter-chip').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      monitorFilter = btn.dataset.filter;
      renderMonitorList();
    });
  });
  $('searchInput').addEventListener('input', renderMonitorList);
  $('modalCloseBtn').addEventListener('click', () => $('monitorModal').classList.remove('open'));
  $('monitorModal').addEventListener('click', e => { if (e.target === $('monitorModal')) $('monitorModal').classList.remove('open'); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('monitorModal').classList.remove('open'); });

  // =========================================================================
  // MASTER REFRESH CONTROLLER
  // =========================================================================
  async function refreshAll(isSilent = false) {
    if (!isSilent) {
      $('refreshBtn').firstElementChild?.classList.add('spin');
    }

    await Promise.allSettled([
      loadDeployments(isSilent),
      loadMonitors(isSilent)
    ]);

    if (!isSilent) {
      $('refreshBtn').firstElementChild?.classList.remove('spin');
      toast('Refreshed live status', 'success');
    }

    // Schedule next auto-refresh (10s if any build is in progress, otherwise 30s)
    if (refreshTimer) clearTimeout(refreshTimer);
    const interval = isBuildingActive ? 10000 : 30000;
    refreshTimer = setTimeout(() => refreshAll(true), interval);
  }

  $('refreshBtn').addEventListener('click', () => refreshAll(false));

  // Initial Boot
  refreshAll(false);
</script>
</body>
</html>
`;
