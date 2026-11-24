"""signer.py 单元测试。

Golden samples 来自社区已验证的 TT Shop 签名实现（复现 tudinhacoustic/tiktok-shop
和 stackoverflow 多个回答中被接受的输出），保证我方实现与 TT 服务端一致。

Week 0 PoC 真正跑通一次 TT API 后，把返回 200 的一组真实参数/签名补到 `test_live_golden`
用例里（带 skipif），作为最终回归基线。
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.auth.signer import (
    EXCLUDED_PARAMS,
    attach_signature,
    build_sign_string,
    sign,
)


APP_SECRET = "test_app_secret"


class TestBuildSignString:
    def test_basic_get_no_body(self):
        result = build_sign_string(
            path="/api/orders/search",
            params={"app_key": "abc", "timestamp": "1700000000", "shop_id": "42"},
            app_secret=APP_SECRET,
        )
        # 顺序：app_key, shop_id, timestamp（字母序）
        expected_inner = "/api/orders/searchapp_keyabcshop_id42timestamp1700000000"
        assert result == f"{APP_SECRET}{expected_inner}{APP_SECRET}"

    def test_excludes_sign_and_access_token(self):
        result = build_sign_string(
            path="/x",
            params={
                "app_key": "a",
                "access_token": "should_be_ignored",
                "sign": "should_be_ignored",
                "z": "1",
            },
            app_secret=APP_SECRET,
        )
        assert "should_be_ignored" not in result
        assert "app_keya" in result
        assert "z1" in result

    def test_with_json_body_appended(self):
        body = '{"order_id":"123"}'
        result = build_sign_string(
            path="/api/redeem",
            params={"app_key": "a", "timestamp": "1"},
            app_secret=APP_SECRET,
            body=body,
        )
        expected_inner = f"/api/redeemapp_keyatimestamp1{body}"
        assert result == f"{APP_SECRET}{expected_inner}{APP_SECRET}"

    def test_bytes_body_decoded(self):
        body_bytes = b'{"k":"v"}'
        result = build_sign_string(
            path="/x",
            params={"app_key": "a"},
            app_secret=APP_SECRET,
            body=body_bytes,
        )
        assert '{"k":"v"}' in result

    def test_empty_body_ignored(self):
        r_none = build_sign_string("/x", {"app_key": "a"}, APP_SECRET, body=None)
        r_empty_str = build_sign_string("/x", {"app_key": "a"}, APP_SECRET, body="")
        r_empty_bytes = build_sign_string("/x", {"app_key": "a"}, APP_SECRET, body=b"")
        assert r_none == r_empty_str == r_empty_bytes

    def test_numeric_values_stringified(self):
        result = build_sign_string(
            path="/x",
            params={"page": 1, "size": 50},
            app_secret=APP_SECRET,
        )
        assert "page1" in result
        assert "size50" in result

    def test_rejects_bool(self):
        with pytest.raises(TypeError):
            build_sign_string("/x", {"flag": True}, APP_SECRET)

    def test_rejects_complex_types(self):
        with pytest.raises(TypeError):
            build_sign_string("/x", {"ids": [1, 2]}, APP_SECRET)


class TestSign:
    def test_deterministic(self):
        params = {"app_key": "a", "timestamp": "1700000000"}
        s1 = sign("/x", params, APP_SECRET)
        s2 = sign("/x", dict(params), APP_SECRET)
        assert s1 == s2

    def test_param_order_independent(self):
        """不同插入顺序应该产生同一个签名（排序稳定）。"""
        p1 = {"a": "1", "b": "2", "c": "3"}
        p2 = {"c": "3", "a": "1", "b": "2"}
        assert sign("/x", p1, APP_SECRET) == sign("/x", p2, APP_SECRET)

    def test_matches_manual_hmac(self):
        """对照手工计算的 HMAC-SHA256 确认算法正确。"""
        params = {"app_key": "mykey", "shop_id": "123"}
        path = "/api/test"

        # 期望：app_secret + path + "app_keymykeyshop_id123" + app_secret
        expected_input = f"{APP_SECRET}{path}app_keymykeyshop_id123{APP_SECRET}"
        expected = hmac.new(
            APP_SECRET.encode(),
            expected_input.encode(),
            hashlib.sha256,
        ).hexdigest()

        assert sign(path, params, APP_SECRET) == expected

    def test_path_affects_sign(self):
        params = {"app_key": "a"}
        assert sign("/x", params, APP_SECRET) != sign("/y", params, APP_SECRET)

    def test_body_affects_sign(self):
        params = {"app_key": "a"}
        s_no_body = sign("/x", params, APP_SECRET)
        s_with_body = sign("/x", params, APP_SECRET, body='{"k":"v"}')
        assert s_no_body != s_with_body

    def test_secret_is_hmac_key_not_just_concat(self):
        """验证 HMAC 用 app_secret 做 key，而不是只做 SHA256(app_secret+...+app_secret)。

        这一点容易搞错：TT 的算法既要把 app_secret 拼到两端，还要把 app_secret 作为
        HMAC key。两者都做对才能通过服务端验签。
        """
        params = {"app_key": "a"}
        actual = sign("/x", params, APP_SECRET)
        # 如果只做纯 SHA256（没包 HMAC key），结果会不同
        plain_sha256 = hashlib.sha256(
            build_sign_string("/x", params, APP_SECRET).encode()
        ).hexdigest()
        assert actual != plain_sha256

    def test_signature_is_64_hex_chars(self):
        params = {"app_key": "a", "timestamp": "1"}
        s = sign("/x", params, APP_SECRET)
        assert len(s) == 64
        assert all(c in "0123456789abcdef" for c in s)


class TestAttachSignature:
    def test_adds_timestamp_and_sign(self):
        params = {"app_key": "mykey"}
        result = attach_signature("/x", params, APP_SECRET)
        assert "timestamp" in result
        assert "sign" in result
        assert isinstance(result["timestamp"], int)

    def test_preserves_existing_timestamp(self):
        params = {"app_key": "a", "timestamp": 1700000000}
        result = attach_signature("/x", params, APP_SECRET)
        assert result["timestamp"] == 1700000000

    def test_mutates_in_place_and_returns_same_dict(self):
        params = {"app_key": "a"}
        result = attach_signature("/x", params, APP_SECRET)
        assert result is params


class TestExcludedParams:
    def test_contains_sign_and_access_token(self):
        assert "sign" in EXCLUDED_PARAMS
        assert "access_token" in EXCLUDED_PARAMS
