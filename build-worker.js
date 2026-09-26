const fs = require('fs');

const htmlContent = fs.readFileSync('public/index.html', 'utf8');
const deploymentsJs = fs.readFileSync('lib/deployments.js', 'utf8')
  .replace(/if \(typeof module !== 'undefined'[\s\S]*$/, ''); // strip CommonJS exports

// Escape backticks and dollar signs for safe template literal sitting
const escapedHtml = htmlContent
  .replace(/\\/g, '\\\\')
  .replace(/`/g, '\\`')
  .replace(/\$\{/g, '\\${');

const workerCode = `// =============================================================================
// Cloudflare Worker / Pages - Ops & Uptime Dashboard
// Real-time Git Deployments (Render & Cloudflare) + UptimeRobot Monitors
// =============================================================================

const UPTIMEROBOT_API_BASE = 'https://api.uptimerobot.com/v2';

${deploymentsJs}

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

const DASHBOARD_HTML = \`${escapedHtml}\`;
`;

fs.writeFileSync('worker.js', workerCode, 'utf8');
fs.writeFileSync('_worker.js', workerCode, 'utf8');
console.log('Successfully generated worker.js and _worker.js');
