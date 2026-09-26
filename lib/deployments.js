// =============================================================================
// Deployments Provider Aggregator
// Fetches and unifies deployment status across multiple Render and Cloudflare accounts
// =============================================================================

// Helper: Case-insensitive and alias-aware environment variable finder
function findEnv(env, ...keys) {
  if (!env) return '';
  for (const k of keys) {
    if (env[k] !== undefined && env[k] !== null && String(env[k]).trim().length > 0) {
      return String(env[k]).trim();
    }
    const lower = k.toLowerCase();
    for (const envKey of Object.keys(env)) {
      if (envKey.toLowerCase() === lower && env[envKey] !== undefined && env[envKey] !== null) {
        const val = String(env[envKey]).trim();
        if (val.length > 0) return val;
      }
    }
  }
  return '';
}

function getDeploymentsConfig(env) {
  const config = {
    render: [],
    cloudflare: []
  };

  if (!env) return config;

  // 1. JSON Configuration: DEPLOYMENTS_CONFIG
  const rawJson = findEnv(env, 'DEPLOYMENTS_CONFIG', 'DEPLOYMENT_CONFIG');
  if (rawJson) {
    try {
      const parsed = typeof rawJson === 'string' ? JSON.parse(rawJson) : rawJson;
      if (Array.isArray(parsed.render)) config.render.push(...parsed.render);
      if (Array.isArray(parsed.cloudflare)) config.cloudflare.push(...parsed.cloudflare);
    } catch (e) {
      console.error('Error parsing DEPLOYMENTS_CONFIG JSON:', e.message);
    }
  }

  // 2. Render Single Account (All aliases)
  const singleRenderKey = findEnv(env,
    'RENDER_API_KEY', 'RENDER_API_TOKEN', 'RENDER_TOKEN', 'RENDER_KEY',
    'RENDER_API_KEY_1', 'RENDER_KEY_1'
  );
  if (singleRenderKey && !config.render.some(r => r.apiKey === singleRenderKey)) {
    const label = findEnv(env, 'RENDER_LABEL', 'RENDER_LABEL_1') || 'Render (Account 1)';
    config.render.push({
      name: label,
      apiKey: singleRenderKey
    });
  }

  // 3. Render Comma-separated: RENDER_API_KEYS="rnd_key1,rnd_key2"
  const renderKeysCsv = findEnv(env, 'RENDER_API_KEYS', 'RENDER_KEYS');
  if (renderKeysCsv) {
    const keys = renderKeysCsv.split(',').map(k => k.trim()).filter(Boolean);
    keys.forEach((k) => {
      if (!config.render.some(r => r.apiKey === k)) {
        config.render.push({
          name: `Render (Account ${config.render.length + 1})`,
          apiKey: k
        });
      }
    });
  }

  // 4. Render Numbered: RENDER_API_KEY_2, RENDER_API_KEY_3, etc.
  for (let i = 2; i <= 20; i++) {
    const key = findEnv(env,
      `RENDER_API_KEY_${i}`, `RENDER_API_TOKEN_${i}`, `RENDER_TOKEN_${i}`, `RENDER_KEY_${i}`
    );
    if (key && !config.render.some(r => r.apiKey === key)) {
      const name = findEnv(env, `RENDER_LABEL_${i}`) || `Render (Account ${i})`;
      config.render.push({ name, apiKey: key });
    }
  }

  // 5. Cloudflare Single Account (All aliases: CF_*, CLOUDFLARE_*, with or without _1)
  const singleCfAccId = findEnv(env,
    'CF_ACCOUNT_ID', 'CLOUDFLARE_ACCOUNT_ID', 'CF_ACCOUNT', 'CLOUDFLARE_ACCOUNT',
    'CF_ACCOUNT_ID_1', 'CLOUDFLARE_ACCOUNT_ID_1'
  );
  const singleCfToken = findEnv(env,
    'CF_API_TOKEN', 'CLOUDFLARE_API_TOKEN', 'CF_TOKEN', 'CLOUDFLARE_TOKEN',
    'CF_API_KEY', 'CLOUDFLARE_API_KEY', 'CF_KEY', 'CLOUDFLARE_KEY',
    'CF_API_TOKEN_1', 'CLOUDFLARE_API_TOKEN_1'
  );

  if (singleCfAccId && singleCfToken && !config.cloudflare.some(c => c.accountId === singleCfAccId)) {
    const label = findEnv(env, 'CF_LABEL', 'CLOUDFLARE_LABEL', 'CF_LABEL_1') || 'Cloudflare (Account 1)';
    config.cloudflare.push({
      name: label,
      accountId: singleCfAccId,
      apiToken: singleCfToken
    });
  }

  // 6. Cloudflare Comma-separated: CF_ACCOUNTS="accId1:token1:Label1,accId2:token2:Label2"
  const cfAccountsCsv = findEnv(env, 'CF_ACCOUNTS', 'CLOUDFLARE_ACCOUNTS');
  if (cfAccountsCsv) {
    const items = cfAccountsCsv.split(',').map(s => s.trim()).filter(Boolean);
    items.forEach((item) => {
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

  // 7. Cloudflare Numbered: CF_ACCOUNT_ID_2, CF_API_TOKEN_2, etc.
  for (let i = 2; i <= 20; i++) {
    const accId = findEnv(env,
      `CF_ACCOUNT_ID_${i}`, `CLOUDFLARE_ACCOUNT_ID_${i}`, `CF_ACCOUNT_${i}`, `CLOUDFLARE_ACCOUNT_${i}`
    );
    const token = findEnv(env,
      `CF_API_TOKEN_${i}`, `CLOUDFLARE_API_TOKEN_${i}`, `CF_TOKEN_${i}`, `CLOUDFLARE_TOKEN_${i}`,
      `CF_API_KEY_${i}`, `CLOUDFLARE_API_KEY_${i}`
    );
    if (accId && token && !config.cloudflare.some(c => c.accountId === accId)) {
      const name = findEnv(env, `CF_LABEL_${i}`, `CLOUDFLARE_LABEL_${i}`) || `Cloudflare (Account ${i})`;
      config.cloudflare.push({
        name,
        accountId: accId,
        apiToken: token
      });
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
        error: `HTTP ${srvRes.status}: ${errText.slice(0, 150)}`
      }];
    }

    const srvData = await srvRes.json();
    let rawList = Array.isArray(srvData) ? srvData : (srvData.services || []);
    const services = rawList.map(item => item.service || item);

    if (!services.length) {
      return [{
        id: `render-empty-${Date.now()}`,
        provider: 'render',
        accountName: name,
        projectName: 'No Services Found',
        serviceType: 'Render Account',
        status: 'unknown',
        statusLabel: '0 Services',
        error: 'Connected to Render, but no services were found in this account.'
      }];
    }

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

  const deployments = [];
  let pagesError = null;

  // 1. Fetch Pages Projects
  try {
    const res = await fetch(`https://api.cloudflare.com/client/v4/accounts/${accountId}/pages/projects`, {
      headers: {
        'Authorization': `Bearer ${apiToken}`,
        'Content-Type': 'application/json'
      }
    });

    if (!res.ok) {
      const errText = await res.text();
      let hint = '';
      if (res.status === 403) {
        hint = 'Token lacks permission. In Cloudflare API Tokens, please add: Account -> Cloudflare Pages -> Read.';
      } else if (res.status === 401) {
        hint = 'Invalid API Token. Please verify you copied the full token.';
      } else if (res.status === 404) {
        hint = `Account ID "${accountId}" not found. Verify your Account ID in Cloudflare sidebar.`;
      }

      pagesError = {
        status: res.status,
        message: hint || `HTTP ${res.status}: ${errText.slice(0, 150)}`
      };
    } else {
      const data = await res.json();
      if (!data.success) {
        const msg = (data.errors && data.errors[0] && data.errors[0].message) || 'Unknown error';
        pagesError = { status: 400, message: msg };
      } else {
        const projects = data.result || [];
        projects.forEach(proj => {
          const projName = proj.name || 'Unnamed Project';
          const subdomain = proj.subdomain || '';
          const defaultUrl = subdomain ? `https://${subdomain}` : '';
          const prodBranch = (proj.source && proj.source.config && proj.source.config.production_branch) || 'main';

          const dep = proj.latest_deployment || proj.canonical_deployment || null;

          if (!dep) {
            deployments.push({
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
            });
            return;
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

          deployments.push({
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
          });
        });
      }
    }
  } catch (err) {
    pagesError = { status: 500, message: err.message };
  }

  // 2. Also check Workers scripts (in case user has Worker services)
  try {
    const workerRes = await fetch(`https://api.cloudflare.com/client/v4/accounts/${accountId}/workers/scripts`, {
      headers: {
        'Authorization': `Bearer ${apiToken}`,
        'Content-Type': 'application/json'
      }
    });

    if (workerRes.ok) {
      const workerData = await workerRes.json();
      if (workerData.success && Array.isArray(workerData.result)) {
        workerData.result.forEach(w => {
          // If already added via Pages with same name, skip
          if (deployments.some(d => d.projectName.toLowerCase() === (w.id || '').toLowerCase())) return;

          deployments.push({
            id: `cf-worker-${w.id}`,
            provider: 'cloudflare',
            accountName: name,
            projectName: w.id || 'Worker Script',
            serviceType: 'Worker',
            liveUrl: '',
            dashboardUrl: `https://dash.cloudflare.com/${accountId}/workers/services/view/${w.id}`,
            status: 'live',
            statusLabel: 'Active Worker',
            rawStatus: 'active',
            commit: {
              message: `Worker Script (${w.usage_model || 'standard'})`,
              branch: 'main'
            },
            trigger: 'worker_upload',
            createdAt: w.created_on || null,
            finishedAt: w.modified_on || null
          });
        });
      }
    }
  } catch (e) {
    // Non-fatal if workers check fails
  }

  // If we collected any deployments (Pages or Workers), return them!
  if (deployments.length > 0) {
    return deployments;
  }

  // If nothing found and Pages had an error, return a clear actionable error card
  if (pagesError) {
    return [{
      id: `cf-err-${accountId}`,
      provider: 'cloudflare',
      accountName: name,
      projectName: 'Cloudflare Connection Error',
      serviceType: 'Pages API',
      status: 'failed',
      statusLabel: `Error (${pagesError.status})`,
      error: pagesError.message,
      commit: {
        message: pagesError.message,
        branch: 'API Check'
      }
    }];
  }

  // If no error, but 0 projects and 0 workers found in this account
  return [{
    id: `cf-empty-${accountId}`,
    provider: 'cloudflare',
    accountName: name,
    projectName: '0 Projects Found',
    serviceType: 'Cloudflare Account',
    status: 'unknown',
    statusLabel: 'No Projects',
    error: `Connected to Cloudflare Account (${accountId}), but no Pages projects or Workers were found. Verify this is the correct Account ID where your project is hosted.`,
    commit: {
      message: 'No Pages projects or Workers found under this Account ID.',
      branch: 'Check Account ID'
    }
  }];
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
      cloudflareCount: config.cloudflare.length,
      renderNames: config.render.map(r => r.name),
      cfNames: config.cloudflare.map(c => c.name)
    }
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    findEnv,
    getDeploymentsConfig,
    fetchRenderAccountDeployments,
    fetchCloudflareAccountDeployments,
    fetchAllDeployments
  };
}
