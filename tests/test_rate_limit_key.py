"""业务接口限流按调用方身份计数，安全限速仍按 IP。

2026-09-05 harvest-pi 压测（evaluation/runs/2026-09-05-load/results.md
§库存更新 stock-out）：出库挂 ``@limiter.limit("60/minute")`` +
``key_func=get_remote_address``，来自单一来源 IP 的有效吞吐被钉死在约 1 req/s，
96%~97% 的请求返回 429。现场部署里多台手持终端／多个语音网关共用一个出口 IP
（NAT）是常态，按 IP 计数会让一个终端把同事全挡在外面。

反过来，注册 / 找回 / device_id 校验那几个 5/hour、10/hour 是**防枚举**：攻击者
没有合法身份，必须按 IP 计数，本次改动不能动它们。
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from slowapi.util import get_remote_address
from starlette.requests import Request

_IS_MYSQL = bool(os.environ.get('DATABASE_URL')) and not os.environ.get(
    'DATABASE_URL', ''
).startswith('sqlite')


def _fake_request(ip='10.0.0.7', headers=None, cookies=None):
    raw_headers = [(k.lower().encode(), v.encode())
                   for k, v in (headers or {}).items()]
    if cookies:
        raw_headers.append((
            b'cookie',
            '; '.join(f'{k}={v}' for k, v in cookies.items()).encode(),
        ))
    return Request({
        'type': 'http', 'method': 'POST', 'path': '/api/materials/stock-out',
        'headers': raw_headers, 'client': (ip, 12345), 'scheme': 'http',
        'query_string': b'', 'server': ('testserver', 80),
    })


class TestKeyFunction:
    def test_two_api_keys_on_one_ip_get_separate_buckets(self):
        from app import business_rate_limit_key
        a = _fake_request(headers={'X-API-Key': 'key-aaa'})
        b = _fake_request(headers={'X-API-Key': 'key-bbb'})
        assert business_rate_limit_key(a) != business_rate_limit_key(b)
        # 且都不是 IP 桶——否则同 IP 的两个用户还是会互相挤
        assert business_rate_limit_key(a) != 'ip:10.0.0.7'

    def test_two_sessions_on_one_ip_get_separate_buckets(self):
        from app import business_rate_limit_key
        a = _fake_request(cookies={'session_token': 'tok-aaa'})
        b = _fake_request(cookies={'session_token': 'tok-bbb'})
        assert business_rate_limit_key(a) != business_rate_limit_key(b)

    def test_same_credential_shares_a_bucket_across_ips(self):
        from app import business_rate_limit_key
        a = _fake_request(ip='10.0.0.7', headers={'X-API-Key': 'key-aaa'})
        b = _fake_request(ip='192.168.9.9', headers={'X-API-Key': 'key-aaa'})
        assert business_rate_limit_key(a) == business_rate_limit_key(b)

    def test_device_id_is_used_when_no_credential(self):
        from app import business_rate_limit_key
        a = _fake_request(headers={'X-Device-Id': 'dev-1'})
        b = _fake_request(headers={'X-Device-Id': 'dev-2'})
        assert business_rate_limit_key(a) != business_rate_limit_key(b)

    def test_falls_back_to_ip_without_any_identity(self):
        from app import business_rate_limit_key
        assert business_rate_limit_key(_fake_request()) == 'ip:10.0.0.7'
        assert business_rate_limit_key(_fake_request(ip='1.2.3.4')) == 'ip:1.2.3.4'

    def test_raw_credential_is_not_the_bucket_key(self):
        """凭据只以哈希形式进桶，避免明文 key 落进限流存储/日志。"""
        from app import business_rate_limit_key
        assert 'key-aaa' not in business_rate_limit_key(
            _fake_request(headers={'X-API-Key': 'key-aaa'}))


class TestLimitRegistry:
    """按 slowapi 的注册表核对每个限速端点用的是哪个 key_func。"""

    # 防枚举的安全限速：必须仍然按 IP 计数（app.py 1052/1107/1246 处的注释）。
    IP_KEYED = {
        'app.register_verify_device': '10 per 1 hour',
        'app.register_tenant': '5 per 1 hour',
        'app.reset_password': '5 per 1 hour',
        # 登录爆破同理按 IP；数据库整库导入是管理员的破坏性操作，也保持按 IP。
        'app.login': '20 per 1 minute',
        'app.import_database': '5 per 1 minute',
    }
    # 业务接口：按调用方身份计数。
    IDENTITY_KEYED = {'app.stock_out', 'app.preview_import_excel'}

    def _registry(self, app_instance):
        from app import limiter
        out = {}
        for name, limits in limiter._route_limits.items():
            for item in limits:
                out.setdefault(name, []).append(item)
        assert out, 'slowapi 没有注册任何 route limit，测试假设失效'
        return out

    def test_security_limits_stay_keyed_by_ip(self, app_instance):
        registry = self._registry(app_instance)
        for name, expected in self.IP_KEYED.items():
            assert name in registry, f'{name} 的限速消失了'
            for item in registry[name]:
                assert item.key_func is get_remote_address, (
                    f'{name} 的防枚举限速被改成了非 IP 计数：{item.key_func}')
                assert str(item.limit) == expected, (
                    f'{name} 的限速值变了：{item.limit} != {expected}')

    def test_business_limits_are_keyed_by_identity(self, app_instance):
        from app import business_rate_limit_key
        registry = self._registry(app_instance)
        for name in self.IDENTITY_KEYED:
            assert name in registry, f'{name} 没有注册限速'
            for item in registry[name]:
                assert item.key_func is business_rate_limit_key, (
                    f'{name} 仍在按 IP 计数：{item.key_func}')


@pytest.mark.skipif(_IS_MYSQL, reason="reload app + 独立限流实例，MySQL truncate 夹具会互相干扰")
def test_two_users_on_one_ip_count_independently(test_db, _admin_setup, monkeypatch):
    """端到端：同一来源 IP 的两个身份，各自独立计数。

    把业务阈值压到 3/minute 后重载 app（``_isolate_module_reloads`` 夹具负责把
    模块还原），A 打满 3 次拿到 429 时，B 的第一次请求必须仍然放行。
    """
    import importlib

    monkeypatch.setenv('BUSINESS_RATE_LIMIT', '3/minute')
    monkeypatch.setenv('DISABLE_RATE_LIMIT', '0')
    monkeypatch.setenv('DATABASE_PATH', test_db)

    import app as app_module
    importlib.reload(app_module)
    limited_app = app_module.app
    try:
        _assert_independent_buckets(limited_app, _admin_setup)
    finally:
        # importlib.reload 是就地改模块对象，conftest 的 _isolate_module_reloads
        # 还原不了模块里的常量。这里自己收尾：恢复环境变量后再 reload 一次，并把
        # limiter 关回去（app_instance 夹具的约定），避免污染后面的用例。
        monkeypatch.undo()
        importlib.reload(app_module)
        app_module.limiter.enabled = False


def _assert_independent_buckets(limited_app, _admin_setup):

    admin = TestClient(limited_app)
    login = admin.post('/api/auth/login', json={
        'username': _admin_setup['username'], 'password': _admin_setup['password']})
    assert login.status_code == 200, login.text

    def _key(role='operate'):
        r = admin.post('/api/api-keys',
                       json={'name': f'rl-{uuid.uuid4().hex[:6]}', 'role': role})
        assert r.status_code == 200, r.text
        return r.json()['key']

    key_a, key_b = _key(), _key()

    # A 连打 5 次不存在的物料出库：业务上会失败，但请求已经进到限流计数里。
    payload = {'product_name': f'nope-{uuid.uuid4().hex[:6]}', 'quantity': 1,
               'reason_category': 'sale'}
    client = TestClient(limited_app)
    a_codes = [client.post('/api/materials/stock-out', json=payload,
                           headers={'X-API-Key': key_a}).status_code
               for _ in range(5)]
    assert 429 in a_codes, f'限流没生效，A 的状态码={a_codes}'

    b_code = client.post('/api/materials/stock-out', json=payload,
                         headers={'X-API-Key': key_b}).status_code
    assert b_code != 429, (
        f'同一 IP 上 B 被 A 的配额挤掉了（A={a_codes}，B={b_code}）'
    )


def test_default_threshold_matches_load_test_profile():
    """默认阈值必须覆盖压测口径（~10 req/s 持续），且可被环境变量改。"""
    import app as app_module
    assert app_module.BUSINESS_RATE_LIMIT == os.environ.get(
        'BUSINESS_RATE_LIMIT', '600/minute')
    n, _, unit = app_module.BUSINESS_RATE_LIMIT.partition('/')
    if unit == 'minute':
        assert int(n) / 60 >= 10, '默认业务限速低于压测的 10 req/s 口径'
