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

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    getDeploymentsConfig,
    fetchRenderAccountDeployments,
    fetchCloudflareAccountDeployments,
    fetchAllDeployments
  };
}
