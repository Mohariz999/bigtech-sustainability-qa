"""Versioned deterministic grading. No model calls or runtime prompt changes.

v1 is preserved for historical comparability. v2 uses numeric boundaries and
explicit unit conversion/tolerances. Neither version proves semantic correctness.
"""
import math
import re
from decimal import Decimal

from grounding import NUMBER, UNIT, decimal_numbers
from fact_quality import normalize_unit

VERSION = 'deterministic-v2'
DEFAULT_ATOL = 0.0
DEFAULT_RTOL = 0.0


def norm(text):
    return text.lower().replace(',', '')


def legacy_correct(question, result):
    if question['category'].startswith('not_found'):
        return result['status'] == 'not_found'
    if result['status'] != 'answered':
        return False
    if 'must_contain' in question:
        return all(norm(x) in norm(result['answer']) for x in question['must_contain'])
    return any(norm(x) in norm(result['answer']) for x in question['must_contain_any'])


def rejected(text, start, end):
    before, after = text[max(0, start-40):start], text[end:end+35]
    return bool(re.search(r'\b(?:not|rather than|instead of|incorrect|wrong)\s*$', before, re.I)
                or re.match(r'\s*(?:is|was)\s+(?:wrong|incorrect|false)\b', after, re.I))


def numeric_match(text, expected, unit=None, *, atol=DEFAULT_ATOL, rtol=DEFAULT_RTOL):
    if not all(math.isfinite(x) and x >= 0 for x in (atol, rtol)):
        raise ValueError('tolerances must be finite and nonnegative')
    expected = float(expected)
    if not math.isfinite(expected):
        raise ValueError('expected number must be finite')
    target = normalize_unit(expected, unit) if unit else None
    for match, value in decimal_numbers(text):
        if rejected(text, match.start(), match.end()):
            continue
        actual, wanted = float(value), expected
        if unit:
            found = re.match(r'\s*(' + UNIT + r')(?!\w)', text[match.end():])
            if not found:
                continue  # unit-qualified expectations require explicit units
            normalized = normalize_unit(actual, found.group(1))
            if target['dimension'] == 'unknown' or normalized['dimension'] != target['dimension']:
                continue
            actual, wanted = normalized['value'], target['value']
            # atol is expressed in the expected unit, then converted to canonical units.
            scale = normalize_unit(1, unit)['value']
            tolerance = max(atol * scale, rtol * abs(wanted))
        else:
            tolerance = max(atol, rtol * abs(wanted))
        if actual is not None and abs(actual - wanted) <= tolerance:
            return True
    return False


def token_match(text, expected):
    parsed = re.fullmatch(r'\s*(' + NUMBER + r')\s*(' + UNIT + r')?\s*', expected)
    if parsed:
        return numeric_match(text, Decimal(parsed.group(1).replace(',', '')), parsed.group(2))
    # Mixed phrases not covered by explicit units retain boundary-safe text matching.
    pattern = r'(?<!\w)' + re.escape(norm(expected)) + r'(?!\w)'
    return any(not rejected(norm(text), m.start(), m.end()) for m in re.finditer(pattern, norm(text)))


def grade(question, result, version=VERSION):
    if version == 'legacy-v1':
        correct = legacy_correct(question, result)
        return {'version': version, 'correct': correct, 'reasons': [] if correct else ['legacy criteria failed']}
    if version != VERSION:
        raise ValueError('unsupported grading version')
    if question['category'].startswith('not_found'):
        correct = result['status'] == 'not_found'
        return {'version': version, 'correct': correct, 'reasons': [] if correct else ['expected abstention']}
    if result['status'] != 'answered':
        return {'version': version, 'correct': False, 'reasons': ['expected answered status']}
    text, reasons = result['answer'], []
    if 'must_contain' in question:
        reasons += [f'missing asserted value/phrase: {x}' for x in question['must_contain'] if not token_match(text, x)]
    elif not any(token_match(text, x) for x in question.get('must_contain_any', [])):
        reasons.append('no accepted asserted value/phrase')
    # Optional additional criteria, never inferred or silently added to old gold data.
    for check in question.get('numeric_checks', []):
        if not numeric_match(text, check['value'], check.get('unit'),
                             atol=check.get('atol', DEFAULT_ATOL), rtol=check.get('rtol', DEFAULT_RTOL)):
            reasons.append(f"numeric criterion failed: {check}")
    return {'version': version, 'correct': not reasons, 'reasons': reasons,
            'tolerances': {'default_atol': DEFAULT_ATOL, 'default_rtol': DEFAULT_RTOL},
            'semantic_correctness': 'not_verified'}
