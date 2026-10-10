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

  // Official TinyURL OpenAPI v2 endpoints: /urls/{type} where type is 'available' or 'archived'
  const endpoints = [
    'https://api.tinyurl.com/urls/available',
    'https://api.tinyurl.com/urls/archived'
  ];

  let collectedLinks = [];
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
          hint = 'Permission denied. Ensure your TinyURL account supports API URL listing.';
        } else if (res.status === 422 || res.status === 404) {
          lastError = `HTTP ${res.status}: ${errText.slice(0, 100)}`;
          continue;
        }

        // If this is the first endpoint and it fails with auth/permission error, return error
        if (!collectedLinks.length && (res.status === 401 || res.status === 403)) {
          return {
            configured: true,
            links: [],
            error: hint || `TinyURL API error (HTTP ${res.status}): ${errText.slice(0, 140)}`
          };
        }
        continue;
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

      rawList.forEach((item, idx) => {
        const domain = item.domain || 'tinyurl.com';
        const alias = item.alias || '';
        const shortUrl = item.tiny_url || (alias ? `https://${domain}/${alias}` : '');
        const targetUrl = item.url || '';

        // If duplicate shortUrl, don't re-add
        if (collectedLinks.some(l => l.tinyUrl === shortUrl)) return;

        // Derive clean title from description, alias, or target hostname
        let title = item.description || item.title || '';
        if (!title && alias) {
          title = alias
            .replace(/[-_]+/g, ' ')
            .replace(/\b\w/g, c => c.toUpperCase());
        }
        if (!title && targetUrl) {
          try {
            const parsed = new URL(targetUrl);
            title = parsed.hostname.replace('.onrender.com', '').replace('.pages.dev', '');
          } catch {
            title = `Link ${collectedLinks.length + 1}`;
          }
        }

        collectedLinks.push({
          id: item.id || `tiny-${alias || idx}`,
          title: title || 'Project Link',
          alias: alias,
          tinyUrl: shortUrl,
          displayUrl: shortUrl.replace(/^https?:\/\//, ''),
          targetUrl: targetUrl,
          tags: item.tags || [],
          createdAt: item.created_at || null
        });
      });

      // If 'available' succeeded, we have the active links!
      if (endpoint.includes('available')) {
        break;
      }
    } catch (err) {
      lastError = err.message;
    }
  }

  if (collectedLinks.length > 0) {
    return {
      configured: true,
      links: collectedLinks,
      count: collectedLinks.length
    };
  }

  // If 0 links found but no fatal auth error, it means the account has 0 available links
  if (!lastError || lastError.includes('404')) {
    return {
      configured: true,
      links: [],
      count: 0
    };
  }

  return {
    configured: true,
    links: [],
    error: `TinyURL API notice: ${lastError}`
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    fetchTinyUrls
  };
}
