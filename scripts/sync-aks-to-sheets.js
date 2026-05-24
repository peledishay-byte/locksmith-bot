// One-shot sync: pull the full key catalog from American Key Supply and
// (over)write it into the configured Airtable table.
//
// Run manually:    npm run sync-keys
// Run on schedule: Railway "Cron" plugin pointed at this script (e.g. daily 6am ET).
import 'dotenv/config';
import { fetchAllProducts, flattenProducts } from '../src/aks-client.js';
import { replaceAllRows } from '../src/airtable.js';

const start = Date.now();
console.log('[sync] pulling catalog from American Key Supply...');
const products = await fetchAllProducts();
console.log(`[sync] got ${products.length} products`);

const rows = flattenProducts(products);
console.log(`[sync] flattened to ${rows.length} (vehicle x key) rows`);

const written = await replaceAllRows(rows);
const elapsed = ((Date.now() - start) / 1000).toFixed(1);
console.log(`[sync] wrote ${written} rows to Airtable in ${elapsed}s`);
