"""Deterministic evidence diagnostics, not a semantic entailment verifier.

Checks identify explicit contradictions where context is available. A clean result
means no supported deterministic check failed, not that every claim is true.
"""
import re
from decimal import Decimal, InvalidOperation

from fact_quality import normalize_unit

COMPANIES = ('Google', 'Microsoft', 'Meta', 'Amazon', 'Apple')
NUMBER = r'(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\w|\.\d)'
UNIT = r'(?:million cubic met(?:er|re)s|million metric tons CO2e|million tCO2e|billion kWh|million kWh|MMT\s?CO[₂2]e|[kMGT]Wh|[MG]W|tCO2e|m[³3]|%|ratio)'


def normalized_text(text):
    return re.sub(r'\s+', ' ', text.replace('’', "'").replace('‘', "'")).strip()


def decimal_numbers(text):
    out = []
    for match in re.finditer(NUMBER, text):
        try:
            value = Decimal(match.group().replace(',', ''))
        except InvalidOperation:
            continue
        if value.is_finite():
            out.append((match, value))
    return out


def numeric_support(answer, evidence):
    """Signed numeric overlap with rounding, with explicit exponent support.

    This checks occurrence, not association to a metric/company/year. Unit-qualified
    claims are additionally compared as quantities when the source has that dimension.
    """
    source_values = [v for _, v in decimal_numbers(evidence)]
    missing = []
    for match, value in decimal_numbers(answer):
        # Decimal exponent gives the precision actually written, including scientific notation.
        quantum = Decimal(1).scaleb(value.as_tuple().exponent)
        def rounds_to(source):
            try:
                return source == value or source.quantize(quantum) == value
            except InvalidOperation:
                return False
        if not any(rounds_to(source) for source in source_values):
            missing.append(match.group())
    for match, value, unit, normalized in quantities(answer):
        if match.group() in missing and quantity_supported(value, normalized, quantities(evidence)):
            missing.remove(match.group())
    return missing


def quantity_supported(value, normalized, source_quantities):
    peers = [q[3] for q in source_quantities if q[3]['dimension'] == normalized['dimension']]
    tolerance = max(1e-9, abs(normalized['value']) * 1e-6,
                    normalized.get('scale', 1) * (10.0 ** max(-308, min(308, value.as_tuple().exponent))) / 2)
    return any(abs(p['value'] - normalized['value']) <= tolerance for p in peers)


def quantities(text):
    out = []
    for match, value in decimal_numbers(text):
        unit = re.match(r'\s*(' + UNIT + r')(?!\w)', text[match.end():])
        if unit:
            normalized = normalize_unit(float(value), unit.group(1))
            if normalized['value'] is not None:
                normalized['scale'] = normalize_unit(1, unit.group(1))['value']
                out.append((match, value, unit.group(1), normalized))
    return out


def evidence_diagnostics(answer, cited_sources, question=''):
    """Return structured partial checks using source metadata, never gold answers."""
    checks = []
    text = ' '.join(s.get('text', '') for s in cited_sources)
    supported_companies = set()
    for source in cited_sources:
        if source.get('company'):
            supported_companies.add(source['company'])
        else:
            supported_companies.update(c for c in COMPANIES if re.search(rf'\b{c}\b', source.get('text', ''), re.I)
                                       or (source.get('doc_id') or '').lower().startswith(c.lower() + '_'))
    named = {c for c in COMPANIES if re.search(rf'\b{c}\b', answer, re.I)}
    if supported_companies and named - supported_companies:
        checks.append({'code': 'company_not_supported', 'detail': ', '.join(sorted(named - supported_companies))})
    # Only flag a period contradiction for an explicit single-year question and
    # structured fact sources; publication years never count as reporting periods.
    asked_years = set(re.findall(r'\b20\d{2}\b', question))
    asked_years.update('20' + y for y in re.findall(r'\bFY(\d{2})\b', question, re.I))
    fact_periods = [s['quality']['period'] for s in cited_sources if s.get('quality')]
    document_sources = [s for s in cited_sources if s.get('kind') != 'calculation']
    if len(asked_years) == 1 and fact_periods and len(fact_periods) == len(document_sources):
        if all(str(p['year']) not in asked_years for p in fact_periods):
            checks.append({'code': 'reporting_period_not_supported', 'detail': next(iter(asked_years))})
    for source in cited_sources:
        for flag in source.get('quality', {}).get('flags', []):
            if flag in ('source_company_mismatch', 'source_page_out_of_range', 'source_document_missing',
                        'quote_unit_mismatch', 'quote_period_mismatch', 'metric_unit_dimension_mismatch'):
                checks.append({'code': 'source_attribution_flag', 'detail': flag})
    missing_signed = [n for n in numeric_support(answer, text) if n.startswith('-')]
    if missing_signed:
        checks.append({'code': 'signed_number_not_supported', 'detail': ', '.join(missing_signed)})
    source_quantities = quantities(text)
    dimensions = {q[3]['dimension'] for q in source_quantities}
    dimensions.update(s['quality']['normalized']['dimension'] for s in cited_sources
                      if s.get('quality', {}).get('normalized'))
    has_calculation = any(s.get('kind') == 'calculation' for s in cited_sources)
    for _, value, unit, normalized in quantities(answer):
        peers = [q[3] for q in source_quantities if q[3]['dimension'] == normalized['dimension']]
        if dimensions and normalized['dimension'] not in dimensions and not has_calculation:
            checks.append({'code': 'quantity_dimension_not_supported', 'detail': f'{value} {unit}'})
        if peers and not quantity_supported(value, normalized, source_quantities):
            checks.append({'code': 'quantity_not_supported', 'detail': f'{value} {unit}'})
    # A claim-metric check is deliberately narrow. It does not infer arbitrary prose.
    markers = {'pue': r'\bPUE\b|power usage effectiveness',
               'water_consumption': r'water consumption',
               'datacenter_electricity': r'data cent(?:er|re).*electricity'}
    structured_metrics = {s['metric'] for s in cited_sources if s.get('metric')}
    for metric, pattern in markers.items():
        if structured_metrics and re.search(pattern, answer, re.I) and metric not in structured_metrics:
            checks.append({'code': 'metric_not_supported', 'detail': metric})
    return {'structural': 'checked_separately', 'semantic_support': 'not_verified',
            'method': 'deterministic_partial_v1', 'checks': checks}
