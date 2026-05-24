// American Key Supply ("POP A LOCK") API client.
// Docs: https://docs.americankeysupply.com/
//
// The public docs we could find don't spell out auth — error codes hint that
// it's customer-credentialed (error 1000 = invalid email/password, error 5 =
// auth failed). So this client supports THREE auth modes; pick whichever
// matches what AKS gave you, by setting env vars accordingly:
//
//   Mode A — Bearer token (most APIs):
//     AKS_AUTH_MODE=bearer
//     AKS_API_TOKEN=<long token string>
//
//   Mode B — API key in a header:
//     AKS_AUTH_MODE=header
//     AKS_API_KEY_HEADER=X-API-Key      (or whatever header name they gave you)
//     AKS_API_KEY=<your key>
//
//   Mode C — Customer login (email+password → session token):
//     AKS_AUTH_MODE=login
//     AKS_EMAIL=...
//     AKS_PASSWORD=...
//     AKS_LOGIN_PATH=/V1/customers/login    (default; adjust if AKS uses a different path)
//
// If you don't know which, start with Mode C — it matches the error-code
// hints in the public docs. If that returns 401/403, switch to A or B.
const BASE = process.env.AKS_API_BASE_URL || 'https://pop-a-lock-api.americankeysupply.com';
const MODE = (process.env.AKS_AUTH_MODE || 'login').toLowerCase();

let cachedSessionToken = null;
let cachedTokenExpiresAt = 0;

async function getAuthHeaders() {
  if (MODE === 'bearer') {
    return { Authorization: `Bearer ${process.env.AKS_API_TOKEN}` };
  }
  if (MODE === 'header') {
    return { [process.env.AKS_API_KEY_HEADER || 'X-API-Key']: process.env.AKS_API_KEY };
  }
  if (MODE === 'login') {
    const token = await ensureLoginToken();
    return { Authorization: `Bearer ${token}` };
  }
  throw new Error(`Unknown AKS_AUTH_MODE: ${MODE}`);
}

async function ensureLoginToken() {
  // Re-use a cached token until ~50 minutes have passed (most session tokens live ~1h).
  const now = Date.now();
  if (cachedSessionToken && now < cachedTokenExpiresAt) return cachedSessionToken;

  const path = process.env.AKS_LOGIN_PATH || '/V1/customers/login';
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      email: process.env.AKS_EMAIL,
      password: process.env.AKS_PASSWORD,
    }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`AKS login failed ${res.status}: ${body}`);
  }
  const data = await res.json();
  // Try a few common field names — adjust here if AKS uses a different one.
  const token = data.token || data.access_token || data.sessionToken || data.session?.token;
  if (!token) {
    throw new Error(`AKS login: no token in response. Body keys: ${Object.keys(data).join(', ')}`);
  }
  cachedSessionToken = token;
  cachedTokenExpiresAt = now + 50 * 60 * 1000;
  return token;
}

// Fetch a single page of /V1/products.
export async function fetchProductsPage({ page = 1, updatedOn = null } = {}) {
  const url = new URL(`${BASE}/V1/products`);
  url.searchParams.set('page', String(page));
  if (updatedOn) url.searchParams.set('updatedOn', String(updatedOn));

  const res = await fetch(url, {
    method: 'GET',
    headers: { accept: 'application/json', ...(await getAuthHeaders()) },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`AKS products ${res.status}: ${body}`);
  }
  return res.json();
}

// Fetch every page, returning the full product list. Includes a tiny delay
// between pages so we don't hammer the API.
export async function fetchAllProducts({ updatedOn = null } = {}) {
  const all = [];
  let page = 1;
  while (true) {
    const data = await fetchProductsPage({ page, updatedOn });
    // The example response uses `Products` (capital P) but the docs use
    // `products` (lowercase). Accept either.
    const list = data.Products || data.products || [];
    all.push(...list);

    const totalResults = data.totalResults || 0;
    const displayResult = data.displayResult || list.length || 1;
    const totalPages = Math.ceil(totalResults / Math.max(displayResult, 1));
    if (page >= totalPages || list.length === 0) break;
    page += 1;
    await new Promise((r) => setTimeout(r, 250));
  }
  return all;
}

// Flatten the API response into one row per (product × car-it-fits) tuple,
// expanding "2005-2012" into individual years. This is the shape we want in
// the Google Sheet and in the lookup table.
export function flattenProducts(products) {
  const rows = [];
  for (const p of products) {
    const itemID = p.itemID;
    const name = p.name;
    const keyType = (p.Type || p.type || []).join(' / ');
    const manufacturerName = p.ManufacturerName || p.manufacturerName || '';
    const images = p.image || p.images || [];
    const mainImage = images[0] || '';
    const aksPrice = Number(p.price) || 0;
    const cars = p.Cars || p.cars || [];
    for (const car of cars) {
      const { make = '', model = '', years = '' } = car;
      for (const year of expandYearRange(years)) {
        rows.push({
          itemID,
          manufacturer: make,             // car maker (Tesla, Ford, ...)
          model,                          // car model (Model 3, F-150, ...)
          year,                           // single year (2024)
          keyType,                        // "Smart Key / Transponder"
          keyImageUrl: mainImage,
          aksPrice,                       // wholesale price from AKS
          keyName: name,                  // descriptive name of the key SKU
          keyManufacturer: manufacturerName, // who makes the key (BlueRocket, OEM, ...)
        });
      }
    }
  }
  return rows;
}

function expandYearRange(range) {
  if (!range) return [];
  // "2005-2012", "2005 - 2012", "2024", "2010-Present"
  const m = String(range).match(/(\d{4})\s*[-–]\s*(\d{4}|present|current|now)?/i);
  if (!m) {
    const single = String(range).match(/(\d{4})/);
    return single ? [Number(single[1])] : [];
  }
  const start = Number(m[1]);
  let end;
  if (!m[2] || /present|current|now/i.test(m[2])) {
    end = new Date().getFullYear();
  } else {
    end = Number(m[2]);
  }
  const out = [];
  for (let y = start; y <= end; y++) out.push(y);
  return out;
}
