"""Conservative, offline fact metadata. Raw facts are never rewritten.

Normalization is a representation change, not verification of a fact's meaning.
Unknown scope, ambiguous units and missing accounting basis remain explicit.
"""
import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

VERSION = 1
FIELDS = ('company', 'doc_id', 'page', 'metric', 'value', 'unit', 'period', 'basis', 'quote')
QUALITY_DDL = """CREATE TABLE IF NOT EXISTS fact_quality (
    fact_id INTEGER PRIMARY KEY REFERENCES facts(fact_id),
    raw_sha256 TEXT NOT NULL,
    version INTEGER NOT NULL,
    metadata TEXT NOT NULL
)"""


def fingerprint(fact):
    return hashlib.sha256(json.dumps({k: fact[k] for k in FIELDS}, sort_keys=True,
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def period_info(period):
    text = period.strip()
    match = re.fullmatch(r'(?:FY\s*(\d{2}|20\d{2})|Fiscal year\s+(20\d{2}))', text, re.I)
    if match:
        year = next(x for x in match.groups() if x)
        return {'kind': 'fiscal', 'year': int(year) + (2000 if len(year) == 2 else 0)}
    if re.fullmatch(r'20\d{2}', text):
        return {'kind': 'calendar', 'year': int(text)}
    # A range/cumulative period is not a single-year measurement.
    if re.search(r'cumulative|since|\bto\b|\d\s*[-–/]\s*\d', text, re.I):
        return {'kind': 'range', 'year': None}
    return {'kind': 'unknown', 'year': None}


def normalize_unit(value, unit):
    """Only explicit aliases. Gallons and mt/MT emissions are deliberately ambiguous."""
    key = re.sub(r'\s+', ' ', unit.strip()).replace('₂', '2').replace('³', '3')
    units = {
        'kWh': ('energy', 'MWh', .001), 'MWh': ('energy', 'MWh', 1),
        'GWh': ('energy', 'MWh', 1000), 'TWh': ('energy', 'MWh', 1e6),
        'million kWh': ('energy', 'MWh', 1000), 'M kWh': ('energy', 'MWh', 1000),
        'billion kWh': ('energy', 'MWh', 1e6),
        'Million kilowatt hours (mkWh)': ('energy', 'MWh', 1000),
        'MW': ('power', 'MW', 1), 'GW': ('power', 'MW', 1000),
        'm3': ('volume', 'm3', 1), 'm cubed': ('volume', 'm3', 1),
        'million cubic meters': ('volume', 'm3', 1e6), 'ML': ('volume', 'm3', 1000),
        'billion liters': ('volume', 'm3', 1e6), 'B Liters': ('volume', 'm3', 1e6),
        '%': ('percentage', '%', 1), '% of freshwater consumption': ('percentage', '%', 1),
        'ratio': ('ratio', 'ratio', 1),
        'tCO2e': ('emissions', 'tCO2e', 1), 't CO2e': ('emissions', 'tCO2e', 1),
        'metric tons CO2e': ('emissions', 'tCO2e', 1),
        'Metric tons CO2e': ('emissions', 'tCO2e', 1),
        'million metric tons CO2e': ('emissions', 'tCO2e', 1e6),
        'million tCO2e': ('emissions', 'tCO2e', 1e6),
        'MMTCO2e': ('emissions', 'tCO2e', 1e6), 'MMT CO2e': ('emissions', 'tCO2e', 1e6),
    }
    if not math.isfinite(value) or key not in units:
        return {'dimension': 'unknown', 'value': None, 'unit': None}
    dimension, target, factor = units[key]
    converted = value * factor
    if not math.isfinite(converted):
        return {'dimension': 'unknown', 'value': None, 'unit': None}
    return {'dimension': dimension, 'value': converted, 'unit': target}


def analyze(fact):
    quote = fact['quote']
    scope, location = 'unknown', None
    # Explicit labels only, rather than guesses based on document/page numbers.
    if match := re.search(r'^([^:]{1,80}): Grid mix versus Apple-sourced renewable energy', quote):
        location = match.group(1).strip()
        scope = 'site' if ',' in location else 'region'
    elif re.search(r'\b(fleet-wide|global fleet|around the world|company-wide)\b', quote, re.I):
        scope = 'company'
    normalized = normalize_unit(fact['value'], fact['unit'])
    period = period_info(fact['period'])
    flags = []
    if scope == 'unknown':
        flags.append('aggregation_scope_unverified')
    if normalized['dimension'] == 'unknown':
        flags.append('unit_unresolved')
    if period['kind'] in ('unknown', 'range'):
        flags.append('not_a_verified_single_year')
    if not fact['basis'].strip():
        flags.append('accounting_basis_unspecified')
    if not re.search(r'[A-Za-z]', quote):
        flags.append('quote_lacks_row_context')
    quote_years = set(re.findall(r'\b20\d{2}\b', quote))
    if period['year'] and len(quote_years) == 1 and str(period['year']) not in quote_years:
        flags.append('quote_period_mismatch')
    quote_units = set(re.findall(r'\b(?:kWh|MWh|GWh|TWh|MW|GW)\b', quote))
    if len(quote_units) == 1 and fact['unit'] in ('kWh', 'MWh', 'GWh', 'TWh', 'MW', 'GW'):
        if fact['unit'] not in quote_units:
            flags.append('quote_unit_mismatch')
    expected = ('energy' if fact['metric'] in ('datacenter_electricity', 'total_electricity') else
                'emissions' if 'emissions' in fact['metric'] else
                'percentage' if fact['metric'].endswith('_pct') else
                'ratio' if fact['metric'] == 'pue' else
                'power' if fact['metric'] == 'clean_energy_contracted' else None)
    if expected and normalized['dimension'] not in (expected, 'unknown'):
        flags.append('metric_unit_dimension_mismatch')
    return {'version': VERSION, 'verification': 'unreviewed', 'scope': scope, 'location': location,
            'period': period, 'basis': fact['basis'].strip() or None,
            'normalized': normalized, 'flags': flags,
            'attribution': 'quote/value presence does not verify company, period, unit or metric'}


def quality_for(db, fact):
    """Attach available document lineage without inventing missing legacy columns."""
    result = analyze(fact)
    columns = {r[1] for r in db.execute('PRAGMA table_info(documents)')}
    names = [name for name in ('company', 'report_year', 'data_year', 'n_pages') if name in columns]
    row = db.execute('SELECT doc_id' + (',' + ','.join(names) if names else '') +
                     ' FROM documents WHERE doc_id=?', (fact['doc_id'],)).fetchone()
    result['document'] = dict(zip(('doc_id', *names), row)) if row else None
    if not row:
        result['flags'].append('source_document_missing')
    else:
        if 'company' in names and result['document']['company'] != fact['company']:
            result['flags'].append('source_company_mismatch')
        if fact['page'] < 1 or ('n_pages' in names and fact['page'] > result['document']['n_pages']):
            result['flags'].append('source_page_out_of_range')
    return result


def compatible(left, right):
    """Conservative comparison eligibility; never authorizes arbitrary model arithmetic."""
    a, b = analyze(left), analyze(right)
    reasons = []
    if any(flag in m['flags'] for m in (a, b) for flag in ('quote_period_mismatch', 'quote_unit_mismatch')):
        reasons.append('unresolved quote attribution mismatch')
    if left['metric'] != right['metric']:
        reasons.append('different metrics')
    if (a['normalized']['dimension'] == 'unknown' or
            a['normalized']['dimension'] != b['normalized']['dimension']):
        reasons.append('incompatible or unresolved units')
    if any('metric_unit_dimension_mismatch' in m['flags'] for m in (a, b)):
        reasons.append('metric/unit mismatch')
    if a['scope'] == 'unknown' or b['scope'] == 'unknown' or (a['scope'], a['location']) != (b['scope'], b['location']):
        reasons.append('incompatible or unverified aggregation scope')
    if not a['basis'] or not b['basis'] or a['basis'].casefold() != b['basis'].casefold():
        reasons.append('different or unspecified accounting basis')
    if a['period']['kind'] not in ('calendar', 'fiscal') or a['period']['kind'] != b['period']['kind']:
        reasons.append('incompatible or unresolved period types')
    return reasons


def facts(db):
    columns = ('fact_id', *FIELDS)
    return [dict(zip(columns, row)) for row in db.execute('SELECT ' + ','.join(columns) + ' FROM facts ORDER BY fact_id')]


@contextmanager
def atomic(db):
    # Works inside an existing caller transaction without committing it.
    db.execute('SAVEPOINT fact_quality_write')
    try:
        yield
        db.execute('RELEASE fact_quality_write')
    except Exception:
        db.execute('ROLLBACK TO fact_quality_write')
        db.execute('RELEASE fact_quality_write')
        raise


def migrate(db):
    """Add/rebuild derived metadata atomically; raw facts and IDs never change.

    Version/fingerprint changes invalidate only derived metadata. Fail on unknown newer
    versions rather than silently downgrading a database. No PRAGMA user_version use.
    """
    with atomic(db):
        db.execute(QUALITY_DDL)
        if db.execute('SELECT 1 FROM fact_quality WHERE version > ? LIMIT 1', (VERSION,)).fetchone():
            raise ValueError('newer fact quality version; upgrade this code')
        db.execute('DELETE FROM fact_quality WHERE fact_id NOT IN (SELECT fact_id FROM facts)')
        for f in facts(db):
            digest = fingerprint(f)
            db.execute('INSERT OR REPLACE INTO fact_quality VALUES (?,?,?,?)',
                       (f['fact_id'], digest, VERSION, json.dumps(quality_for(db, f), sort_keys=True)))


def qualification(fact, metadata=None):
    """Derived on read too: legacy DBs work without writes or a mandatory migration."""
    m = metadata if metadata is not None else analyze(fact)
    n = m['normalized']
    norm = f"; normalized={n['value']:g} {n['unit']}" if n['value'] is not None else '; unit unresolved'
    document = m.get('document') or {}
    publication = f"; publication_year={document['report_year']}" if 'report_year' in document else ''
    return (f"scope={m['scope']}" + (f" ({m['location']})" if m['location'] else '') +
            f"; period={m['period']['kind']}; basis={m['basis'] or 'unspecified'}" + publication + norm +
            '; flags=' + ','.join(m['flags']) +
            '; unreviewed attribution; compare only with compatible units, scope, period type and basis')


def import_csv(db, path):
    """Import a saved raw snapshot into an EMPTY facts table; never replace history.

    Caller creates the legacy facts schema. Documents must already exist. CSV text is
    preserved; numbers/page are parsed to SQLite's existing types. All-or-nothing.
    """
    with open(path, newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(FIELDS):
            raise ValueError('unexpected facts CSV columns')
        rows = list(reader)
    if not rows:
        raise ValueError('empty snapshot')
    with atomic(db):
        if db.execute('SELECT COUNT(*) FROM facts').fetchone()[0]:
            raise ValueError('facts table must be empty; existing data is never replaced')
        docs = {r[0] for r in db.execute('SELECT doc_id FROM documents')}
        seen = set()
        for row in rows:
            row['page'], row['value'] = int(row['page']), float(row['value'])
            if row['doc_id'] not in docs or row['page'] < 1 or not math.isfinite(row['value']):
                raise ValueError('invalid source, page or value')
            quality = quality_for(db, row)
            if any(flag.startswith('source_') for flag in quality['flags']):
                raise ValueError('invalid source attribution')
            key = tuple(row[k] for k in FIELDS)
            if key in seen:
                raise ValueError('duplicate fact in snapshot')
            seen.add(key)
            db.execute('INSERT INTO facts (' + ','.join(FIELDS) + ') VALUES (' + ','.join('?' for _ in FIELDS) + ')', key)
        migrate(db)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--migrate', action='store_true', help='add derived metadata; does not alter facts')
    parser.add_argument('--import-csv', type=Path, help='import into existing empty facts table')
    args = parser.parse_args()
    # Default is strictly read-only; never silently create a missing database.
    mode = 'rw' if args.migrate or args.import_csv else 'ro'
    with sqlite3.connect(args.database.resolve().as_uri() + '?mode=' + mode, uri=True) as db:
        if args.import_csv:
            import_csv(db, args.import_csv)
        if args.migrate:
            migrate(db)
        print(json.dumps([{'fact_id': f['fact_id'], 'raw_sha256': fingerprint(f), **quality_for(db, f)} for f in facts(db)], indent=2))


if __name__ == '__main__':
    main()
