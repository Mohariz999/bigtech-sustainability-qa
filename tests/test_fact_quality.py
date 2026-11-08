"""Offline fact quality, migration and publication regressions; no model or downloads."""
import csv
import json
import sqlite3
from pathlib import Path

import pytest

from extract import FACTS_SCHEMA, ensure_empty_facts, publish_facts
from fact_quality import (FIELDS, VERSION, analyze, compatible, facts, fingerprint,
                          import_csv, migrate, normalize_unit, period_info, quality_for)
from test_agent import make_agent
from test_answer import FakeClient


def fact(**changes):
    return dict(company='Apple', doc_id='apple_2026', page=10,
                metric='datacenter_electricity', value=2.5, unit='billion kWh',
                period='2025', basis='operational',
                quote='In 2025 we used 2.5 billion kWh around the world.') | changes


@pytest.fixture
def db():
    connection = sqlite3.connect(':memory:')
    connection.executescript('CREATE TABLE documents (doc_id TEXT PRIMARY KEY, company TEXT, '
                             'report_year INTEGER, data_year TEXT, n_pages INTEGER);' + FACTS_SCHEMA)
    connection.execute("INSERT INTO documents VALUES ('apple_2026','Apple',2026,'2025',100)")
    connection.commit()
    yield connection
    connection.close()


def insert(db, row=None):
    row = row or fact()
    db.execute('INSERT INTO facts (' + ','.join(FIELDS) + ') VALUES (' + ','.join('?' for _ in FIELDS) + ')',
               [row[k] for k in FIELDS])
    return row


@pytest.mark.parametrize('unit,value,expected,target', [
    ('kWh', 2500, 2.5, 'MWh'), ('MWh', 2.5, 2.5, 'MWh'),
    ('GWh', 2.5, 2500, 'MWh'), ('TWh', 2.5, 2500000, 'MWh'),
    ('million kWh', 2.5, 2500, 'MWh'), ('M kWh', 2.5, 2500, 'MWh'),
    ('billion kWh', 2.5, 2500000, 'MWh'), ('GW', 2.5, 2500, 'MW'),
    ('MW', 2.5, 2.5, 'MW'), ('m³', 2.5, 2.5, 'm3'),
    ('million cubic meters', 2.5, 2500000, 'm3'), ('ML', 2.5, 2500, 'm3'),
    ('billion liters', 2.5, 2500000, 'm3'), ('%', 78, 78, '%'),
    ('ratio', 1.09, 1.09, 'ratio'), ('tCO2e', 80.85, 80.85, 'tCO2e'),
    ('MMT CO₂e', 80.85, 80850000, 'tCO2e'),
])
def test_explicit_unit_conversions(unit, value, expected, target):
    normalized = normalize_unit(value, unit)
    assert normalized['value'] == pytest.approx(expected)
    assert normalized['unit'] == target


@pytest.mark.parametrize('unit', ['mtCO2e', 'MT CO2e', 'MMT', 'Mmt', 'gallons',
                                 'million gallons', 'Mgal', 'unknown', 'mw'])
def test_ambiguous_units_are_not_guessed(unit):
    assert normalize_unit(1, unit) == dict(dimension='unknown', value=None, unit=None)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_non_finite_values_not_normalized(value):
    assert normalize_unit(value, 'MWh')['value'] is None


@pytest.mark.parametrize('text,kind,year', [('2025', 'calendar', 2025), ('FY25', 'fiscal', 2025),
    ('FY 2025', 'fiscal', 2025), ('Fiscal year 2024', 'fiscal', 2024),
    ('2010-2025', 'range', None), ('Cumulative since FY2020 to FY2025', 'range', None),
    ('January 2025', 'unknown', None), ('FY251', 'unknown', None), ('n/a', 'unknown', None)])
def test_period_types_not_collapsed(text, kind, year):
    assert period_info(text) == dict(kind=kind, year=year)


@pytest.mark.parametrize('location,scope', [('Reno, Nevada','site'), ('Denmark','region'), ('China','region')])
def test_explicit_apple_location_scope(location, scope):
    m = analyze(fact(quote=f'{location}: Grid mix versus Apple-sourced renewable energy Electricity use in 2024: 454 million kWh'))
    assert (m['scope'], m['location']) == (scope, location)
    assert 'quote_period_mismatch' in m['flags']


def test_unknown_scope_not_inferred_from_company_or_page():
    m = analyze(fact(quote='ELECTRICITY USE IN 2025 267M KWH', page=96))
    assert m['scope'] == 'unknown' and m['verification'] == 'unreviewed'
    assert 'aggregation_scope_unverified' in m['flags']


def test_number_only_quote_and_unit_mismatch_flagged():
    assert 'quote_lacks_row_context' in analyze(fact(quote='47.40 2.76'))['flags']
    assert 'quote_unit_mismatch' in analyze(fact(unit='TWh', quote='Data centers MWh 2.5'))['flags']
    assert 'metric_unit_dimension_mismatch' in analyze(fact(unit='%'))['flags']


def test_replenishment_percent_and_volume_are_incompatible():
    left = fact(metric='water_replenished', unit='%', value=78)
    right = fact(metric='water_replenished', unit='million cubic meters', value=7.7)
    assert 'incompatible or unresolved units' in compatible(left, right)


def test_comparisons_preserve_basis_scope_and_period_type():
    a = fact()
    b = fact(unit='MWh', value=2500000, quote='In 2025 we used 2500000 MWh around the world.')
    assert compatible(a, b) == []
    assert 'different or unspecified accounting basis' in compatible(a, b | {'basis':'market-based'})
    assert 'different or unspecified accounting basis' in compatible(a, b | {'basis':''})
    assert 'incompatible or unresolved period types' in compatible(a, b | {'period':'FY2025'})
    assert 'incompatible or unverified aggregation scope' in compatible(a, b | {'quote':'2.5'})


def test_migration_additive_idempotent_and_lineage(db):
    insert(db)
    before = facts(db)
    migrate(db)
    first = db.execute('SELECT * FROM fact_quality').fetchall()
    migrate(db)
    assert db.execute('SELECT * FROM fact_quality').fetchall() == first
    assert facts(db) == before
    assert db.execute('PRAGMA user_version').fetchone()[0] == 0
    m = json.loads(first[0][3])
    assert m['document']['report_year'] == 2026 and m['period']['year'] == 2025
    assert m['verification'] == 'unreviewed' and first[0][1] == fingerprint(before[0])


def test_migration_does_not_commit_callers_transaction(db):
    insert(db)
    migrate(db)
    db.rollback()
    assert facts(db) == []
    assert not db.execute("SELECT name FROM sqlite_master WHERE name='fact_quality'").fetchall()


def test_newer_version_refused_and_preserved(db):
    insert(db)
    migrate(db)
    db.execute('UPDATE fact_quality SET version=?', (VERSION + 1,))
    with pytest.raises(ValueError, match='newer'):
        migrate(db)
    assert db.execute('SELECT version FROM fact_quality').fetchone()[0] == VERSION + 1


def test_raw_change_or_document_change_refreshes_derived_data(db):
    insert(db)
    migrate(db)
    old_hash = db.execute('SELECT raw_sha256 FROM fact_quality').fetchone()[0]
    db.execute("UPDATE facts SET unit='MWh'")
    db.execute('UPDATE documents SET report_year=2027')
    migrate(db)
    digest, metadata = db.execute('SELECT raw_sha256,metadata FROM fact_quality').fetchone()
    assert digest != old_hash and json.loads(metadata)['document']['report_year'] == 2027


def test_source_integrity_flags(db):
    assert 'source_company_mismatch' in quality_for(db, fact(company='Google'))['flags']
    assert 'source_page_out_of_range' in quality_for(db, fact(page=101))['flags']
    assert 'source_document_missing' in quality_for(db, fact(doc_id='missing'))['flags']


def snapshot(tmp_path, rows):
    path = tmp_path / 'facts.csv'
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_offline_import_preserves_raw_snapshot_and_refuses_overwrite(db, tmp_path):
    path = snapshot(tmp_path, [fact()])
    original = path.read_bytes()
    assert import_csv(db, path) == 1
    assert {k:facts(db)[0][k] for k in FIELDS} == fact()
    before = facts(db)
    with pytest.raises(ValueError, match='empty'):
        import_csv(db, path)
    assert facts(db) == before and path.read_bytes() == original


@pytest.mark.parametrize('change', [{'doc_id':'missing'}, {'page':0}, {'page':101},
                                   {'company':'Google'}, {'value':float('inf')}])
def test_invalid_import_rolls_back_all_rows(db, tmp_path, change):
    path = snapshot(tmp_path, [fact(), fact() | change])
    with pytest.raises(ValueError):
        import_csv(db, path)
    assert facts(db) == []


def test_duplicate_import_rolls_back(db, tmp_path):
    with pytest.raises(ValueError, match='duplicate'):
        import_csv(db, snapshot(tmp_path, [fact(), fact()]))
    assert facts(db) == []


def test_existing_facts_preflight_and_publication_refuse_replacement(db):
    insert(db)
    with pytest.raises(ValueError, match='preserved'):
        ensure_empty_facts(db)
    before = facts(db)
    with pytest.raises(ValueError, match='preserved'):
        publish_facts(db, [dict(doc_id='apple_2026',page=10,status='ok',kept=[fact()])], {('apple_2026',10)})
    assert facts(db) == before


@pytest.mark.parametrize('logs,expected', [
    ([], {('apple_2026',10)}),
    ([dict(doc_id='apple_2026',page=10,status='api error',kept=[])], {('apple_2026',10)}),
    ([dict(doc_id='apple_2026',page=10,status='ok',kept=[])], {('apple_2026',10),('apple_2026',11)}),
    ([dict(doc_id='apple_2026',page=10,status='ok',kept=[])]*2, {('apple_2026',10)}),
])
def test_failed_partial_or_duplicate_pages_never_publish(db, logs, expected):
    with pytest.raises(ValueError, match='Incomplete'):
        publish_facts(db, logs, expected)
    assert facts(db) == []


def test_successful_publication_and_source_error_rollback(db):
    log = dict(doc_id='apple_2026', page=10, status='ok', kept=[fact(),fact(page=11)])
    with pytest.raises(ValueError, match='source'):
        publish_facts(db, [log], {('apple_2026',10)})
    assert facts(db) == []
    log['kept'] = [fact()]
    publish_facts(db, [log], {('apple_2026',10)})
    assert len(facts(db)) == db.execute('SELECT COUNT(*) FROM fact_quality').fetchone()[0] == 1


def test_agent_legacy_db_qualifies_unknowns_and_keeps_conflicting_editions():
    agent = make_agent(FakeClient())
    agent.db.execute("INSERT INTO facts (company,doc_id,page,metric,value,unit,period,basis,quote) "
                     "VALUES ('Google','google_env_2025',107,'datacenter_electricity',30825600,'MWh','2024','other basis','Data centers MWh 30825600')")
    sources = {}
    text = agent._query_facts('Google','datacenter_electricity','2024',sources)
    assert '30,637,100' in text and '30,825,600' in text
    assert 'scope=unknown' in text and 'basis=other basis' in text and 'unreviewed' in text
    assert not agent.db.execute("SELECT 1 FROM sqlite_master WHERE name='fact_quality'").fetchone()


def test_agent_excludes_location_rows_before_limit():
    agent = make_agent(FakeClient())
    site = fact(doc_id='apple_epr_2025', page=101, value=454, unit='million kWh', period='2024',
                quote='Reno, Nevada: Grid mix versus Apple-sourced renewable energy Electricity use in 2024: 454 million kWh')
    for _ in range(45):
        insert(agent.db, site)
    insert(agent.db, fact(doc_id='apple_epr_2025',period='2024'))
    sources = {}
    text = agent._query_facts('Apple','datacenter_electricity','2024',sources)
    assert '454' not in text and len(sources) == 1
    assert 'normalized=2.5e+06 MWh' in text


def test_saved_fact_corpus_unchanged_and_known_site_cases_detected():
    path = Path(__file__).resolve().parents[1] / 'eval/results/facts.csv'
    with path.open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row['value'] = float(row['value'])
    assert len(rows) == 680
    known = [r for r in rows if r['company']=='Apple' and r['doc_id']=='apple_epr_2025'
             and r['metric']=='datacenter_electricity' and r['page']=='101']
    assert len(known) == 3
    assert {analyze(r)['scope'] for r in known} == {'site','region'}


def test_migration_failure_rolls_back_metadata_and_preserves_raw(db, monkeypatch):
    import fact_quality
    insert(db)
    before = facts(db)
    monkeypatch.setattr(fact_quality, 'quality_for', lambda *_: (_ for _ in ()).throw(ValueError('injected')))
    with pytest.raises(ValueError, match='injected'):
        migrate(db)
    assert facts(db) == before
    assert not db.execute("SELECT name FROM sqlite_master WHERE name='fact_quality'").fetchone()


def test_import_empty_or_wrong_header_is_rejected(db, tmp_path):
    with pytest.raises(ValueError, match='empty snapshot'):
        import_csv(db, snapshot(tmp_path, []))
    path = tmp_path / 'wrong.csv'
    path.write_text('value\n1\n')
    with pytest.raises(ValueError, match='columns'):
        import_csv(db, path)
    assert facts(db) == []


def test_cli_report_read_only_and_missing_db_not_created(db, tmp_path, monkeypatch, capsys):
    import fact_quality
    path = tmp_path / 'corpus.sqlite'
    db.commit()
    with sqlite3.connect(path) as target:
        db.backup(target)
    before = path.read_bytes()
    monkeypatch.setattr('sys.argv', ['fact_quality.py', str(path)])
    fact_quality.main()
    assert json.loads(capsys.readouterr().out) == []
    assert path.read_bytes() == before
    missing = tmp_path / 'missing.sqlite'
    monkeypatch.setattr('sys.argv', ['fact_quality.py', str(missing)])
    with pytest.raises(sqlite3.OperationalError):
        fact_quality.main()
    assert not missing.exists()


def test_extract_preflight_refuses_existing_data_before_client(db, tmp_path, monkeypatch):
    import extract
    path = tmp_path / 'corpus.sqlite'
    insert(db)
    db.commit()
    with sqlite3.connect(path) as target:
        db.backup(target)
    before = path.read_bytes()
    monkeypatch.setattr(extract, 'DB_PATH', path)
    monkeypatch.setattr(extract, 'candidate_pages', lambda _: [])
    monkeypatch.setattr(extract, 'Extractor', lambda: pytest.fail('must not construct a model client'))
    monkeypatch.setattr('sys.argv', ['extract.py'])
    with pytest.raises(ValueError, match='preserved'):
        extract.main()
    assert path.read_bytes() == before


@pytest.mark.parametrize('limited,failed', [(True,False), (False,True), (False,False)])
def test_extract_main_preserves_historical_artifacts(db, tmp_path, monkeypatch, limited, failed):
    import extract
    path = tmp_path / 'corpus.sqlite'
    db.commit()
    with sqlite3.connect(path) as target:
        db.backup(target)
    historical = [tmp_path/'logs/extraction.jsonl', tmp_path/'eval/results/facts.csv']
    for file in historical:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('historical bytes\n')
    page = dict(doc_id='apple_2026', page=10, text='text')
    class FakeExtractor:
        def extract_page(self, _):
            return dict(doc_id='apple_2026', page=10, status='failed' if failed else 'ok',
                        kept=[] if failed else [fact()], rejected=[], calls=[], cost_usd=0)
    monkeypatch.setattr(extract, 'DB_PATH', path)
    monkeypatch.setattr(extract, 'ROOT', tmp_path)
    monkeypatch.setattr(extract, 'candidate_pages', lambda _: [page])
    monkeypatch.setattr(extract, 'Extractor', FakeExtractor)
    monkeypatch.setattr('sys.argv', ['extract.py'] + (['--limit','1'] if limited else []))
    if failed:
        with pytest.raises(ValueError, match='Incomplete'):
            extract.main()
    else:
        extract.main()
    assert all(file.read_text() == 'historical bytes\n' for file in historical)
    assert len(list((tmp_path/'logs/extraction-runs').glob('*/facts.csv'))) == 1
    with sqlite3.connect(path) as result:
        assert len(facts(result)) == (0 if limited or failed else 1)


def test_agent_annual_lookup_excludes_cumulative_periods_but_any_retains_them():
    agent = make_agent(FakeClient())
    insert(agent.db, fact(company='Google',doc_id='google_env_2026',period='Cumulative since 2020 to 2025'))
    yearly = agent._query_facts('Google','datacenter_electricity','2025',{})
    assert 'Cumulative' not in yearly
    assert 'Cumulative' in agent._query_facts('Google','datacenter_electricity','any',{})


def test_comparison_refuses_explicit_quote_mismatch():
    left = fact()
    right = fact(unit='TWh', quote='In 2024 we used 2.5 MWh around the world.')
    assert 'unresolved quote attribution mismatch' in compatible(left, right)
