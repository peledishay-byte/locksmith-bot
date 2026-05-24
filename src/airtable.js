// Airtable layer. We use Airtable's REST API directly (no SDK needed — keeps deps small).
//
// Env vars we expect:
//   AIRTABLE_TOKEN       Personal Access Token from https://airtable.com/create/tokens
//   AIRTABLE_BASE_ID     The "appXXXXXXXXXXXXXX" id from your base URL
//   AIRTABLE_TABLE       Table name (default "Keys")
//
// Sheet/table schema expected (set this up in Airtable UI — see README):
//   Item ID            (Number)
//   Manufacturer       (Single line text)        -- car maker
//   Model              (Single line text)
//   Year               (Number)
//   Key Type           (Single line text)
//   Key Name           (Single line text)
//   Key Image          (Attachment)              -- we send a URL; Airtable downloads it
//   AKS Price (USD)    (Currency)
//   Markup %           (Number, default 60)
//   Customer Price     (Formula: {AKS Price (USD)} * (1 + {Markup %}/100))
const TABLE = () => process.env.AIRTABLE_TABLE || 'Locksmith Keys Catalog';
const BASE  = () => process.env.AIRTABLE_BASE_ID;
const TOKEN = () => process.env.AIRTABLE_TOKEN;

const API = (path) =>
  `https://api.airtable.com/v0/${encodeURIComponent(BASE())}/${encodeURIComponent(TABLE())}${path}`;

function authHeaders() {
  return {
    Authorization: `Bearer ${TOKEN()}`,
    'content-type': 'application/json',
  };
}

// --- Write side (sync script) -----------------------------------------------
// Airtable's POST endpoint accepts up to 10 records per request.
export async function replaceAllRows(rows) {
  // 1. Wipe existing rows.
  await deleteAllRows();

  // 2. Insert in batches of 10.
  const records = rows.map((r) => {
    const customerPrice = +(r.aksPrice * (1 + (Number(process.env.DEFAULT_MARKUP_PERCENT) || 60) / 100)).toFixed(2);
    return {
      fields: {
        'Item ID': r.itemID,
        'Manufacturer': r.manufacturer,
        'Model': r.model,
        'Year': r.year,
        'Key Type': r.keyType,
        'Key Name': r.keyName,
        'Key Image': r.keyImageUrl ? [{ url: r.keyImageUrl }] : [],
        'AKS Price (USD)': r.aksPrice,
        'Markup %': Number(process.env.DEFAULT_MARKUP_PERCENT) || 60,
        // 'Customer Price' is a FORMULA column — Airtable computes it; don't write it.
      },
    };
  });

  let inserted = 0;
  for (let i = 0; i < records.length; i += 10) {
    const batch = records.slice(i, i + 10);
    const res = await fetch(API(''), {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ records: batch, typecast: true }),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Airtable insert ${res.status}: ${body}`);
    }
    const data = await res.json();
    inserted += data.records.length;
    // Be polite — Airtable allows 5 req/s. We're well under, but pause anyway.
    await new Promise((r) => setTimeout(r, 250));
  }
  return inserted;
}

async function deleteAllRows() {
  // Paginate, collect record IDs, delete in batches of 10 (Airtable limit).
  let offset = null;
  const ids = [];
  do {
    const url = new URL(API(''));
    url.searchParams.set('pageSize', '100');
    url.searchParams.set('fields[]', 'Item ID'); // minimal payload
    if (offset) url.searchParams.set('offset', offset);
    const res = await fetch(url, { headers: authHeaders() });
    if (!res.ok) throw new Error(`Airtable list ${res.status}: ${await res.text()}`);
    const data = await res.json();
    ids.push(...data.records.map((r) => r.id));
    offset = data.offset;
  } while (offset);

  for (let i = 0; i < ids.length; i += 10) {
    const url = new URL(API(''));
    for (const id of ids.slice(i, i + 10)) url.searchParams.append('records[]', id);
    const res = await fetch(url, { method: 'DELETE', headers: authHeaders() });
    if (!res.ok) throw new Error(`Airtable delete ${res.status}: ${await res.text()}`);
    await new Promise((r) => setTimeout(r, 250));
  }
}

// --- Read side (bot lookup) -------------------------------------------------
export async function findKeyForVehicle({ make, model, year }) {
  // Build an Airtable formula filter. LOWER + FIND lets us do contains-match.
  const escape = (s) => String(s).replace(/'/g, "\\'");
  const clauses = [];
  if (make)  clauses.push(`FIND(LOWER('${escape(make)}'), LOWER({Manufacturer})) > 0`);
  if (model) clauses.push(`FIND(LOWER('${escape(model)}'), LOWER({Model})) > 0`);
  if (year)  clauses.push(`{Year} = ${Number(year)}`);
  const formula = clauses.length ? `AND(${clauses.join(',')})` : '';

  const url = new URL(API(''));
  if (formula) url.searchParams.set('filterByFormula', formula);
  url.searchParams.set('pageSize', '10');
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Airtable read ${res.status}: ${await res.text()}`);
  const data = await res.json();

  return (data.records || []).map((r) => {
    const f = r.fields;
    const img = Array.isArray(f['Key Image']) && f['Key Image'][0] ? f['Key Image'][0].url : '';
    return {
      itemID: f['Item ID'],
      manufacturer: f['Manufacturer'] || '',
      model: f['Model'] || '',
      year: f['Year'] || null,
      keyType: f['Key Type'] || '',
      keyName: f['Key Name'] || '',
      keyImageUrl: img,
      aksPrice: Number(f['AKS Price (USD)']) || 0,
      markupPct: Number(f['Markup %']) || 0,
      customerPrice: Number(f['Customer Price']) ||  0,
    };
  });
}
