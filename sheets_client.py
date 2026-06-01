"""Google Sheets lookup for the locksmith key catalog.

Fetches the catalog by CSV export (gviz) once on demand and caches it in
memory for SHEETS_CACHE_TTL_SEC. No service account needed - the sheet
must be shared 'Anyone with the link - Viewer'.

The Google Sheet schema (columns A..I):
  A  Manufacturer
  B  Model
  C  Year                (range string like '2007-2017' or '2024')
  D  Key Type
  E  Key Name (SKU)
  F  Price (USD)         (price for a brand-new key replacement)
  G  Price Spare         (price for a spare/duplicate when the lead has one)
  H  (blank)
  I  Key Image URL       (usually an =IMAGE("...") formula in the cell)

Manufacturer/Model are often blank on continuation rows; we forward-fill
the most recent non-blank value as we parse rows top-down.
"""
import csv
import io
import logging
import re
import time

import httpx

import config

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 300
_cache = {"records": None, "fetched_at": 0.0}


def _csv_export_url() -> str:
    sid = config.SHEETS_SPREADSHEET_ID
    tab = config.SHEETS_TAB_NAME
    # gviz CSV export honours "Anyone with the link - Viewer" sharing.
    return f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={tab}"


def _parse_year(raw):
    """Return (low, high) ints, or (None, None) if unparseable.

    Examples:
      '2024'         -> (2024, 2024)
      '2007-2017'    -> (2007, 2017)
      '2007-Present' -> (2007, 2099)
      ''             -> (None, None)
    """
    if not raw:
        return (None, None)
    s = str(raw).strip().replace("–", "-").replace("—", "-")
    m = re.match(r"(\d{4})\s*[-/]\s*(\d{4})", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.match(r"(\d{4})\s*[-/]\s*(present|now|current|today|\+)", s, re.I)
    if m:
        return (int(m.group(1)), 2099)
    m = re.match(r"(\d{4})\+", s)
    if m:
        return (int(m.group(1)), 2099)
    m = re.match(r"(\d{4})", s)
    if m:
        return (int(m.group(1)), int(m.group(1)))
    return (None, None)


def _parse_price(raw):
    if raw is None:
        return None
    s = str(raw).replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


_IMAGE_RE = re.compile(r'IMAGE\s*\(\s*"([^"]+)"', re.I)


def _extract_image_url(raw):
    """Pull URL out of =IMAGE("...") or return raw if it already looks like a URL."""
    if not raw:
        return None
    s = str(raw).strip()
    m = _IMAGE_RE.search(s)
    if m:
        return m.group(1)
    if s.startswith("http"):
        return s
    return None


def _find_col(header, *names):
    """Return the index of the first matching column name, else None."""
    norm = [h.strip().lower() for h in header]
    for n in names:
        ln = n.strip().lower()
        if ln in norm:
            return norm.index(ln)
    return None


async def _fetch_catalog():
    url = _csv_export_url()
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(url)
    if r.status_code >= 400:
        log.error("Sheets fetch failed: %d %s", r.status_code, r.text[:200])
        return []
    reader = csv.reader(io.StringIO(r.text))
    rows = list(reader)
    if not rows:
        return []
    header = rows[0]
    i_mfg = _find_col(header, "Manufacturer")
    i_mod = _find_col(header, "Model")
    i_yr = _find_col(header, "Year")
    i_typ = _find_col(header, "Key Type")
    i_sku = _find_col(header, "Key Name (optional)", "Key Name", "SKU")
    i_pr = _find_col(header, "Price (USD)", "Price USD", "Price")
    i_sp = _find_col(header, "Price  Spare", "Price Spare", "Spare", "Spare Price")
    i_img = _find_col(header, "Key Image URL", "Image", "Image URL")

    records = []
    last_mfg = ""
    last_mod = ""
    for row in rows[1:]:
        def g(i):
            if i is None or i >= len(row):
                return ""
            return (row[i] or "").strip()
        mfg = g(i_mfg) or last_mfg
        mod = g(i_mod) or last_mod
        if g(i_mfg):
            last_mfg = mfg
            # Reset model when a new manufacturer starts and current row's model is blank
            # (avoids carrying e.g. "Enclave" into a new manufacturer block)
            if not g(i_mod):
                last_mod = ""
                mod = ""
        if g(i_mod):
            last_mod = mod
        sku = g(i_sku)
        if not (mfg and mod and sku):
            continue
        yr_low, yr_high = _parse_year(g(i_yr))
        records.append({
            "manufacturer": mfg,
            "model": mod,
            "year_raw": g(i_yr),
            "year_low": yr_low,
            "year_high": yr_high,
            "key_type": g(i_typ),
            "key_name": sku,
            "price_usd": _parse_price(g(i_pr)),
            "price_spare_usd": _parse_price(g(i_sp)),
            "key_image_url": _extract_image_url(g(i_img)),
        })
    log.info("Loaded %d records from Google Sheet catalog", len(records))
    return records


async def get_catalog():
    now = time.time()
    if _cache["records"] is None or (now - _cache["fetched_at"]) > _CACHE_TTL_SEC:
        try:
            _cache["records"] = await _fetch_catalog()
            _cache["fetched_at"] = now
        except Exception:
            log.exception("Sheet catalog refresh failed")
            if _cache["records"] is None:
                _cache["records"] = []
    return _cache["records"]


def _lower(s):
    return (s or "").strip().lower()


def price_range(price_usd, pct=None):
    """Return (low, high) tuple for a price +/- pct%. None if no price."""
    if price_usd is None:
        return None
    pct = pct if pct is not None else config.SHEETS_PRICE_RANGE_PCT
    low = max(1, round(price_usd * (1 - pct / 100)))
    high = round(price_usd * (1 + pct / 100))
    return (low, high)


async def lookup_car_key(manufacturer, model=None, year=None):
    """Find SKUs matching the given car. Returns list of plain dicts."""
    catalog = await get_catalog()
    tm = _lower(manufacturer)
    tmod = _lower(model) if model else None
    matches = []
    for r in catalog:
        rm = _lower(r["manufacturer"])
        rmod = _lower(r["model"])
        # Manufacturer must match (case-insensitive, allow substring either way).
        if tm and not (tm == rm or tm in rm or rm in tm):
            continue
        # Model: substring either way.
        if tmod and not (tmod == rmod or tmod in rmod or rmod in tmod):
            continue
        # Year: allow +/- 1 outside the printed range to be forgiving.
        if year is not None and r["year_low"] is not None and r["year_high"] is not None:
            if not (r["year_low"] - 1 <= year <= r["year_high"] + 1):
                continue
        matches.append(r)
    return matches
