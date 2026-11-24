"""Week 0 PoC：用测试账号跑通一次 TT getOrderList API。

跑法：
    1. cp .env.example .env，填入 docx 里给的测试账号凭证
    2. pip install -r requirements.txt
    3. python scripts/poc_get_order_list.py

目标：
    - 验证签名算法与 TT 服务端一致
    - 验证 refresh_token → access_token 流程可行
    - 打印响应，确认账号/权限是否 OK

成功标准：收到 HTTP 200 且 body 里 `code == 0`。
如果收到 401/invalid_sign：检查 signer.py 的参数顺序和 body 拼接
如果收到其它业务错：说明签名对了，继续调后续流程
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 允许直接 python scripts/xxx.py 运行而不需要装包
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from app.auth.signer import attach_signature, current_timestamp  # noqa: E402


load_dotenv(ROOT / ".env")

def _required_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"❌ .env 里 {name} 没填。请打开 .env 填入测试账号凭证后重试。")
        print("   测试账号在 docs/虚拟类目商家 Onboarding Guide.docx 的 2.3 节。")
        sys.exit(3)
    return val


APP_KEY = _required_env("TTS_APP_KEY")
APP_SECRET = _required_env("TTS_APP_SECRET")
REFRESH_TOKEN = _required_env("TTS_REFRESH_TOKEN")
API_BASE = os.environ.get("TTS_API_BASE", "https://open-api.tiktokglobalshop.com")
AUTH_BASE = os.environ.get("TTS_AUTH_BASE", "https://auth.tiktok-shops.com")
HTTP_PROXY = os.environ.get("TTS_HTTP_PROXY", "").strip() or None


def _build_client() -> httpx.Client:
    """构造 httpx 客户端：显式走 .env 指定的代理（不依赖 shell 环境变量，避免串台）。"""
    if HTTP_PROXY:
        print(f"[client] using proxy: {HTTP_PROXY}")
        # trust_env=False 屏蔽 shell 里的 HTTPS_PROXY 等变量，只认我们给的 proxy
        return httpx.Client(proxy=HTTP_PROXY, trust_env=False, timeout=30)
    print("[client] no proxy (direct connection)")
    return httpx.Client(trust_env=False, timeout=30)


def _preflight() -> None:
    """启动前先验证代理和目标可达，拿到真证书不是 GFW 伪造的。"""
    host = "auth.tiktok-shops.com"
    try:
        with _build_client() as c:
            r = c.get(f"https://{host}/")
        print(f"[preflight] {host} reachable, status={r.status_code}")
    except httpx.ConnectError as e:
        msg = str(e)
        if "CERTIFICATE_VERIFY_FAILED" in msg:
            print(
                f"[preflight] ❌ 证书验证失败：可能是代理没开 / GFW 劫持。\n"
                f"           打开 Clash/Surge/V2rayN 等工具，把 HTTP 代理端口填到 .env 的 TTS_HTTP_PROXY"
            )
        elif "Connection refused" in msg or "Connect call failed" in msg:
            print(
                f"[preflight] ❌ 连不上代理：确认 .env 的 TTS_HTTP_PROXY 端口和你科学上网工具里的端口一致"
            )
        else:
            print(f"[preflight] ❌ 网络错误：{msg}")
        sys.exit(4)


def refresh_access_token() -> dict:
    """用 refresh_token 换 access_token。

    注意：授权服务在独立域名 auth.tiktok-shops.com，与业务 API 域名不同。
    授权相关接口不需要签名，只带 app_key/app_secret/refresh_token 即可。
    """
    url = f"{AUTH_BASE}/api/v2/token/refresh"
    params = {
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }
    print(f"[refresh] GET {url}")
    print(f"[refresh] params: app_key={APP_KEY}, app_secret=***, refresh_token=***, grant_type=refresh_token")
    with _build_client() as c:
        resp = c.get(url, params=params)
    print(f"[refresh] status: {resp.status_code}")
    print(f"[refresh] body: {resp.text}")
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"refresh token failed: {data}")
    return data["data"]


def get_authorized_shops(access_token: str) -> list[dict]:
    """拿到该 access_token 授权过的所有店铺列表，取其中一个 shop_cipher 用于后续调用。

    Ref: /authorization/202309/shops
    """
    path = "/authorization/202309/shops"
    params: dict[str, object] = {
        "app_key": APP_KEY,
        "timestamp": current_timestamp(),
        "version": "202309",
    }
    attach_signature(path, params, APP_SECRET)

    url = f"{API_BASE}{path}"
    print(f"\n[shops] GET {url}")
    print(f"[shops] params: {params}")

    headers = {"x-tts-access-token": access_token}
    with _build_client() as c:
        resp = c.get(url, params=params, headers=headers)
    print(f"[shops] status: {resp.status_code}")
    print(f"[shops] body: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"get shops failed: {data}")
    return data["data"].get("shops", [])


def call_get_order_list(access_token: str, shop_cipher: str | None = None) -> dict:
    """调 getOrderList v202309。

    Ref: https://partner.tiktokshop.com/docv2/page/get-order-list-202309

    最小 body：按创建时间倒序拉 10 条。
    """
    path = "/order/202309/orders/search"
    body_dict = {
        "order_status": "UNPAID",  # 拉任意状态看返回结构即可；测试账号里可能没订单
    }
    body = json.dumps(body_dict, separators=(",", ":"))

    # TT v2 API (202309+) access_token 放 header，不放 query；签名也不包含它
    params: dict[str, object] = {
        "app_key": APP_KEY,
        "timestamp": current_timestamp(),
        "version": "202309",
        "page_size": 10,
        "sort_field": "create_time",
        "sort_order": "DESC",
    }
    if shop_cipher:
        params["shop_cipher"] = shop_cipher

    attach_signature(path, params, APP_SECRET, body=body)

    url = f"{API_BASE}{path}"
    print(f"\n[order] POST {url}")
    print(f"[order] params: {params}")
    print(f"[order] body: {body}")

    headers = {
        "Content-Type": "application/json",
        "x-tts-access-token": access_token,
    }
    with _build_client() as c:
        resp = c.post(url, params=params, content=body, headers=headers)
    print(f"[order] status: {resp.status_code}")
    print(f"[order] body: {resp.text}")
    return resp.json()


def main() -> int:
    print("=" * 60)
    print("Step 0: preflight (代理 + 目标连通性)")
    print("=" * 60)
    _preflight()

    print()
    print("=" * 60)
    print("Step 1: refresh access_token")
    print("=" * 60)
    token_data = refresh_access_token()
    access_token = token_data["access_token"]
    print(f"[refresh] got access_token (len={len(access_token)})")
    print(f"[refresh] expires_in: {token_data.get('access_token_expire_in')}s")

    print()
    print("=" * 60)
    print("Step 2: get authorized shops (for shop_cipher)")
    print("=" * 60)
    shops = get_authorized_shops(access_token)
    if not shops:
        print("❌ 测试账号下没有授权店铺，无法继续")
        return 1
    shop = shops[0]
    shop_cipher = shop.get("cipher") or shop.get("shop_cipher")
    print(f"[shops] picked shop: id={shop.get('id')}, region={shop.get('region')}, "
          f"name={shop.get('name')}")
    print(f"[shops] shop_cipher={shop_cipher[:20]}... (len={len(shop_cipher)})")

    print()
    print("=" * 60)
    print("Step 3: call getOrderList with shop_cipher")
    print("=" * 60)
    result = call_get_order_list(access_token, shop_cipher=shop_cipher)

    print()
    print("=" * 60)
    print("Result")
    print("=" * 60)
    if result.get("code") == 0:
        print("✅ 签名算法与 TT 服务端一致，PoC 通过")
        return 0
    elif "sign" in result.get("message", "").lower():
        print("❌ 签名错误，检查 signer.py")
        return 2
    else:
        print(f"⚠️  签名对了但接口返回业务错误：{result.get('message')}")
        print("    （通常是 shop_cipher 缺失 / 权限未授 / 参数不合法）")
        return 1


if __name__ == "__main__":
    sys.exit(main())
