// =============================================================================
// TinyURL Provider
// Auto-fetches shortened URLs, aliases, and titles from TinyURL API v2
// =============================================================================

function findTinyUrlEnv(env, ...keys) {
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

async function fetchTinyUrls(env) {
  const token = findTinyUrlEnv(env, 'TINYURL_API_TOKEN', 'TINYURL_API_KEY', 'TINYURL_TOKEN', 'TINYURL_KEY');

  if (!token) {
    return {
      configured: false,
      links: [],
      error: 'TINYURL_API_TOKEN is not set in environment variables.',
      message: 'Add TINYURL_API_TOKEN in Cloudflare Pages / .env to auto-fetch your project links.'
    };
  }

  const endpoints = [
    'https://api.tinyurl.com/urls/active',
    'https://api.tinyurl.com/urls/all',
    'https://api.tinyurl.com/urls'
  ];

  let lastError = null;

  for (const endpoint of endpoints) {
    try {
      const res = await fetch(endpoint, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Accept': 'application/json'
        }
      });

      if (!res.ok) {
        const errText = await res.text();
        let hint = '';
        if (res.status === 401) {
          hint = 'Invalid TinyURL API Token. Please verify your token.';
        } else if (res.status === 403) {
          hint = 'Permission denied. Ensure your TinyURL account plan supports API URL listing.';
        } else if (res.status === 404) {
          // Try next endpoint
          lastError = `HTTP ${res.status}: ${errText.slice(0, 100)}`;
          continue;
        }

        return {
          configured: true,
          links: [],
          error: hint || `TinyURL API error (HTTP ${res.status}): ${errText.slice(0, 140)}`
        };
      }

      const json = await res.json();
      let rawList = [];

      if (Array.isArray(json.data)) {
        rawList = json.data;
      } else if (json.data && Array.isArray(json.data.urls)) {
        rawList = json.data.urls;
      } else if (Array.isArray(json.urls)) {
        rawList = json.urls;
      } else if (json.data && typeof json.data === 'object' && json.data.tiny_url) {
        rawList = [json.data];
      }

      const links = rawList.map((item, idx) => {
        const domain = item.domain || 'tinyurl.com';
        const alias = item.alias || '';
        const shortUrl = item.tiny_url || (alias ? `https://${domain}/${alias}` : '');
        const targetUrl = item.url || '';
        
        // Derive clean title from description, alias, or target hostname
        let title = item.description || item.title || '';
        if (!title && alias) {
          // Convert 'post-pulse' or 'aniruddh_aroma' into clean title
          title = alias
            .replace(/[-_]+/g, ' ')
            .replace(/\b\w/g, c => c.toUpperCase());
        }
        if (!title && targetUrl) {
          try {
            const parsed = new URL(targetUrl);
            title = parsed.hostname.replace('.onrender.com', '').replace('.pages.dev', '');
          } catch {
            title = `Link ${idx + 1}`;
          }
        }

        return {
          id: item.id || `tiny-${idx}`,
          title: title || 'Project Link',
          alias: alias,
          tinyUrl: shortUrl,
          displayUrl: shortUrl.replace(/^https?:\/\//, ''),
          targetUrl: targetUrl,
          tags: item.tags || [],
          createdAt: item.created_at || null
        };
      });

      return {
        configured: true,
        links: links,
        count: links.length
      };
    } catch (err) {
      lastError = err.message;
    }
  }

  return {
    configured: true,
    links: [],
    error: `Failed to contact TinyURL API: ${lastError || 'Network Error'}`
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    fetchTinyUrls
  };
}
