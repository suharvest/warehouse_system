"""Provider 返回体的字段契约：库位 vs 规格不能对调。

回归一个现场事故：某备品系统的自写 Provider 把「型号」填进 product.location、
把物理库位填进 product.spec，于是手表一直播「位于 LH-815」（型号），真实库位
则彻底消失 —— 全程不报错、日志里也看不出来。

契约由播报层 ``_wrap_response`` 单方面决定：
  product.location → 「位于 XXX」，物理库位
  product.variant  → 「名称（XXX）」，规格/型号
  product.spec     → 不存在，写了会被静默丢弃
"""

import importlib
import os
import sys
import textwrap

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(REPO_ROOT, 'mcp')
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)


def _wrap():
    return importlib.import_module("warehouse_mcp")._wrap_response


class TestSayUsesLocationAndVariant:
    """播报层怎么用这两个字段 —— 自写 Provider 的唯一事实来源。"""

    PRODUCT = {
        "name": "撬具", "sku": "SP-0001",
        "current_stock": 120, "unit": "件",
    }

    def _say(self, **extra):
        p = dict(self.PRODUCT)
        p.update(extra)
        return _wrap()("query_stock", {"success": True, "product": p})["say"]

    def test_location_is_spoken_as_physical_location(self):
        assert "位于A101" in self._say(location="A101")

    def test_variant_is_spoken_as_spec_in_parentheses(self):
        assert self._say(variant="LH-815").startswith("撬具（LH-815）")

    def test_spec_field_is_ignored(self):
        """写 spec 不会报错，只会被丢掉 —— 这正是事故难查的原因。"""
        say = self._say(spec="A101")
        assert "A101" not in say

    def test_swapped_mapping_reproduces_the_incident(self):
        """把型号填进 location：手表会把型号播成库位，真实库位消失。"""
        say = self._say(location="LH-815", spec="A101")
        assert "位于LH-815" in say
        assert "A101" not in say


class TestValidatorFlagsSpecField:
    """接入阶段就要拦下来，别等到现场。"""

    def _validate(self, tmp_path, body):
        from providers.validator import validate_provider_file
        f = tmp_path / "p.py"
        f.write_text(textwrap.dedent(body), encoding="utf-8")
        return validate_provider_file(str(f))

    SKELETON = '''
        from providers.base import BaseProvider

        class P(BaseProvider):
            PROVIDER_NAME = "p"

            def resolve_name(self, text, entity_type="all"): return {}
            def query_stock(self, product_name, show_batches=False):
                return {"success": True, "product": %s}
            def stock_in(self, *a, **k): return {}
            def stock_out(self, *a, **k): return {}
            def search(self, *a, **k): return {}
            def get_today_statistics(self): return {}
        '''

    def test_spec_key_produces_warning_but_stays_valid(self, tmp_path):
        r = self._validate(tmp_path, self.SKELETON % '{"spec": "A101", "location": "LH-815"}')
        assert r["valid"] is True, "字段用错不该阻断上传"
        assert len(r["warnings"]) == 1
        assert "variant" in r["warnings"][0]
        assert "location" in r["warnings"][0]

    def test_matching_config_with_spec_key_is_not_flagged(self, tmp_path):
        """描述**对方系统**字段名的配置也含 spec 键，但完全合法，不能误报。"""
        body = self.SKELETON % '{"location": "A101", "variant": "LH-815"}'
        body = body.replace(
            'PROVIDER_NAME = "p"',
            'PROVIDER_NAME = "p"\n    fields = {"code": "code", "spec": "spec"}\n'
            '    weights = {"spec": 0.6}',
        )
        r = self._validate(tmp_path, body)
        assert r["warnings"] == []

    def test_correct_mapping_produces_no_warning(self, tmp_path):
        r = self._validate(tmp_path, self.SKELETON % '{"location": "A101", "variant": "LH-815"}')
        assert r["valid"] is True
        assert r["warnings"] == []
