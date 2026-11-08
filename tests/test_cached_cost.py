"""Known standard Claude API rates; arithmetic only, no service requests."""
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaUsage, BetaCacheCreation

from answer import cache_usage, cost, cost_is_estimate


def usage(*, reads=0, writes=0, short=0, long=0, details=True, inp=0, out=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_read_input_tokens=reads, cache_creation_input_tokens=writes,
                           cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=short,
                                                          ephemeral_1h_input_tokens=long) if details else None)


@pytest.mark.parametrize('data,expected', [
    (usage(reads=1_000_000), .20),
    (usage(writes=1_000_000,short=1_000_000), 5),
    (usage(writes=1_000_000,long=1_000_000), 8),
    (usage(writes=2_000_000,short=1_000_000,long=1_000_000), 13),
    (usage(inp=1_000_000), 4),
    (usage(out=1_000_000), 20),
    (usage(inp=1000,out=2000,reads=3000,writes=12000,short=5000,long=7000), .1256),
    (usage(), 0),
])
def test_opus_55_verified_standard_prices(data, expected):
    assert cost('claude-opus-5-5',data)==pytest.approx(expected)
    assert not cost_is_estimate('claude-opus-5-5',data)


@pytest.mark.parametrize('model',['claude-opus-5','claude-opus-4-8'])
def test_fallback_models_keep_their_own_cache_read_rate(model):
    data=usage(reads=1_000_000,writes=2_000_000,short=1_000_000,long=1_000_000)
    assert cost(model,data)==pytest.approx(.5+6.25+10)
    assert not cost_is_estimate(model,data)


def test_sdk_115_usage_models_with_mixed_ttls():
    data=BetaUsage(input_tokens=1000,output_tokens=2000,cache_read_input_tokens=3000,
                   cache_creation_input_tokens=12000,
                   cache_creation=BetaCacheCreation(ephemeral_5m_input_tokens=5000,ephemeral_1h_input_tokens=7000))
    assert cost('claude-opus-5-5',data)==pytest.approx(.1256)
    assert cache_usage(data)=={'cache_write_5m_tokens':5000,'cache_write_1h_tokens':7000,'cache_ttl_estimated':False}


def test_dict_cache_breakdown_is_supported():
    data=usage(writes=1_000_000)
    data.cache_creation={'ephemeral_1h_input_tokens':1_000_000}
    assert cost('claude-opus-5-5',data)==8
    assert not cost_is_estimate('claude-opus-5-5',data)


def test_missing_ttl_uses_explicit_legacy_estimate_not_claimed_exact():
    data=usage(writes=1_000_000,details=False)
    assert cost('claude-opus-5-5',data)==5
    assert cost_is_estimate('claude-opus-5-5',data)
    assert cache_usage(data)['cache_ttl_estimated']


def test_partial_breakdown_does_not_double_count_aggregate():
    data=usage(writes=2_000_000,long=1_000_000)
    assert cost('claude-opus-5-5',data)==13
    assert cost_is_estimate('claude-opus-5-5',data)


def test_inconsistent_breakdown_is_flagged():
    data=usage(writes=1_000_000,short=1_000_000,long=1_000_000)
    assert cost('claude-opus-5-5',data)==13
    assert cost_is_estimate('claude-opus-5-5',data)


def test_legacy_usage_without_cache_fields_remains_compatible():
    data=SimpleNamespace(input_tokens=1000,output_tokens=100)
    assert cost('claude-opus-5-5',data)==pytest.approx(.006)
    assert not cost_is_estimate('claude-opus-5-5',data)


@pytest.mark.parametrize('field,value',[('speed','fast'),('inference_geo','us'),('service_tier','priority')])
def test_nonstandard_billing_modes_cannot_be_reported_as_exact(field,value):
    data=usage()
    setattr(data,field,value)
    assert cost_is_estimate('claude-opus-5-5',data)


def test_unknown_model_remains_an_explicit_estimate():
    assert cost_is_estimate('unknown-model',usage(inp=1000))


def test_per_call_logs_record_ttls_and_estimation_status():
    from test_answer import FakeClient, reply, response, run
    resp=response(reply())
    resp.usage=usage(writes=1_000_000,long=1_000_000)
    result=run(FakeClient(resp))
    call=result['calls'][0]
    assert call['cache_write_1h_tokens']==1_000_000 and call['cache_write_5m_tokens']==0
    assert call['cost_usd']==8 and not call['cost_is_estimate']
