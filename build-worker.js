const fs = require('fs');

const htmlContent = fs.readFileSync('public/index.html', 'utf8');

// Escape backticks and dollar signs for safe template literal sitting
const escapedHtml = htmlContent
  .replace(/\\/g, '\\\\')
  .replace(/`/g, '\\`')
  .replace(/\$\{/g, '\\${');

const workerCode = `// =============================================================================
// Cloudflare Worker - UptimeRobot Monitor Dashboard
// =============================================================================
// Variable name in Cloudflare: UPTIMEROBOT_API_KEY
// Go to: Worker -> Settings -> Variables and Secrets -> Add "UPTIMEROBOT_API_KEY"
// =============================================================================

const UPTIMEROBOT_API_BASE = 'https://api.uptimerobot.com/v2';

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const pathname = url.pathname;
    // Support UPTIMEROBOT_API_KEY or fallback to CRONJOB_API_KEY
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

    // 1. API: Check if API key variable is configured
    if (pathname === '/api/config') {
      return jsonResponse({
        configured: Boolean(apiKey && apiKey.length > 0)
      });
    }

    // 2. API: Fetch all monitors from UptimeRobot
    if (pathname === '/api/monitors') {
      if (!apiKey) {
        return jsonResponse({
          error: 'UPTIMEROBOT_API_KEY is not set in Cloudflare Variables.',
          detail: 'Please go to Settings -> Variables and Secrets -> Add UPTIMEROBOT_API_KEY.'
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

    // 3. Default: Serve the single-page dashboard HTML
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
console.log('Successfully generated UptimeRobot worker.js and _worker.js');
