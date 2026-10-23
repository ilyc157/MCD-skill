#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳中国 MCP 低层客户端。

只负责三件事：拿 token、发 tools/call、把返回的混合文本切成可用的 JSON。
业务语义（怎么配餐、怎么算价）不在这里，见 nutrition_solver.py / mcd_planner.py。

设计依据全部来自 2026-10-22 实调，踩过的坑写在 references/mcp-contract.md。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = "https://mcp.mcd.cn"  # 必须是根路径，/mcp 后缀返回 404（已实测）
MCP_CONFIG = os.path.expanduser("~/.workbuddy/mcp.json")

# 会把用户资产写进去、有真实副作用的工具，调用前必须过确认门
DESTRUCTIVE_TOOLS = {
    "create-order": "真实下单、真实扣款，且每日额度有限，取消不退额度",
    "cancel-order": "取消订单，但不退还当日 create-order 额度",
    "delivery-create-address": "在本人麦当劳账号地址簿写入新地址",
    "mall-create-order": "积分兑换下单",
    "party-order-create": "派对活动下单",
}


class McdError(RuntimeError):
    def __init__(self, message, code=None, payload=None):
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


# --------------------------------------------------------------------------- token

def load_token(explicit: str | None = None) -> str:
    """按 显式传入 > 环境变量 > mcp.json 的顺序找 token。"""
    if explicit:
        return explicit.strip()
    if os.environ.get("MCD_TOKEN"):
        return os.environ["MCD_TOKEN"].strip()

    if not os.path.exists(MCP_CONFIG):
        raise McdError(
            f"找不到 {MCP_CONFIG}，也没有 MCD_TOKEN 环境变量。\n"
            "  获取 token：https://open.mcd.cn/mcp → 手机号登录 → 控制台 → 激活"
        )
    with open(MCP_CONFIG, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    for name, server in (cfg.get("mcpServers") or {}).items():
        if "mcd" not in name.lower():
            continue
        auth = (server.get("headers") or {}).get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        env_auth = (server.get("env") or {}).get("MCD_TOKEN", "")
        if env_auth:
            return env_auth.strip()

    raise McdError(
        "mcp.json 里没有名字含 mcd 的 server，或该 server 没配 Authorization。\n"
        '  期望结构：mcpServers.mcdonald.headers.Authorization = "Bearer <token>"'
    )


# --------------------------------------------------------------------------- 传输

def _post(payload: dict, token: str, timeout: int = 40) -> str:
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        if e.code in (401, 403):
            raise McdError(
                f"鉴权失败 HTTP {e.code}。token 可能过期：去 https://open.mcd.cn/mcp 控制台重新激活后更新 mcp.json。",
                code=e.code,
            ) from e
        raise McdError(f"HTTP {e.code}: {body}", code=e.code) from e
    except urllib.error.URLError as e:
        raise McdError(f"网络不可达：{e.reason}") from e

    # 服务端可能走 SSE：把 data: 行拼起来
    if raw.lstrip().startswith("event:") or "\ndata:" in raw:
        raw = "".join(
            line[5:].strip() for line in raw.splitlines() if line.startswith("data:")
        )
    return raw


def _extract_json(blob: str) -> dict:
    """从 MCP 的混合文本里抠出第一个完整 JSON 对象。

    实测返回长这样（同一个 content[].text 里）：
        # API Response Information
        ## Response Structure
        - **code**: 错误码 ...
        ## Original Response
        {"success":true,...,"data":...}## 数据传参规则
                                          ^ 注意：JSON 后面直接跟 markdown，没有换行分隔

    所以 json.loads(index_start) 会报 Extra data，必须用 raw_decode 精确切片。
    """
    start = blob.find('{"success"')
    if start < 0:
        start = blob.find("{")
        if start < 0:
            raise McdError(f"响应里没有 JSON：{blob[:200]}")
    try:
        obj, _ = json.JSONDecoder().raw_decode(blob[start:])
        return obj
    except json.JSONDecodeError as e:
        raise McdError(f"JSON 解析失败（{e}）：{blob[start:start + 200]}") from e


# --------------------------------------------------------------------------- 调用

def call_tool(tool: str, arguments: dict | None = None, *, token: str | None = None,
              timeout: int = 40, raw_text: bool = False):
    """调用一个 MCP 工具。

    返回解析后的业务 dict（含 success / code / message / data）。
    raw_text=True 时返回 (dict, 原始文本)，用于需要 markdown 说明的场景（如券列表）。
    业务失败（code != 200）也会返回 dict，不抛异常 —— 让人自己决定怎么降级。
    """
    tk = load_token(token)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    }
    raw = _post(payload, tk, timeout=timeout)

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        raise McdError(f"外层 JSON-RPC 解析失败：{raw[:200]}")

    if "error" in envelope:
        err = envelope["error"]
        raise McdError(f"MCP 协议错误：{err}", payload=err)

    texts = [
        c.get("text", "")
        for c in (envelope.get("result") or {}).get("content", [])
        if c.get("type") == "text"
    ]
    blob = "\n".join(texts)
    if not blob.strip():
        raise McdError(f"{tool} 返回空 content。原始响应：{raw[:200]}")

    body = _extract_json(blob)
    return (body, blob) if raw_text else body


def require_ok(result: dict, tool: str) -> dict:
    """确认业务成功并返回 data；失败则抛带归因的异常。"""
    if result.get("code") == 200 and result.get("success", True):
        return result.get("data")
    raise McdError(
        f"{tool} 失败 code={result.get('code')} message={result.get('message')}",
        code=result.get("code"),
        payload=result,
    )


# --------------------------------------------------------------------------- 缓存
#
# 为什么要缓存：一次「3 人 60 元」的规划要 1 次菜单 + 30 次套餐详情 + 30 次核价，
# 共 60 来个往返。用户说「换一批」「预算改 40」时这些数据一条都没变，
# 重拉一遍纯属让用户干等。缓存让第二次出方案从 7s 掉到 0.2s。
#
# 只缓存只读工具，且只缓存成功的响应。时效按数据易变程度给：
# 菜单/核价 5 分钟（够一轮对话反复调），门店/营养表 1 天。

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", ".cache", "mcp")

CACHE_TTL = {
    "query-meals": 300,
    "query-meal-detail": 300,
    "calculate-price": 300,
    "query-store-coupons": 300,
    "now-time-info": 0,            # 时间不能缓存，缓存了餐段判断就错
    "list-nutrition-foods": 86400,
    "query-nearby-stores": 86400,
    "delivery-query-stores": 86400,
}

_NO_CACHE = {"create-order", "cancel-order", "delivery-create-address",
             "mall-create-order", "party-order-create"}


def _cache_file(tool: str, args: dict) -> str:
    key = json.dumps([tool, args], sort_keys=True, ensure_ascii=False)
    name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{tool}-{name}.json")


def call_cached(tool: str, arguments: dict | None = None, ttl: int | None = None):
    """带缓存的只读调用。返回 (body, hit)。写操作永不缓存。"""
    arguments = arguments or {}
    ttl = CACHE_TTL.get(tool, 0) if ttl is None else ttl
    if ttl <= 0 or tool in _NO_CACHE or os.environ.get("MCD_NO_CACHE"):
        return call_tool(tool, arguments), False

    path = _cache_file(tool, arguments)
    try:
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        if time.time() - blob.get("ts", 0) < ttl and isinstance(blob.get("body"), dict):
            return blob["body"], True
    except (OSError, ValueError):
        pass

    body = call_tool(tool, arguments)
    if body.get("code") == 200:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "body": body}, f, ensure_ascii=False)
        except OSError:
            pass
    return body, False


def clear_cache() -> int:
    n = 0
    try:
        for name in os.listdir(CACHE_DIR):
            if name.endswith(".json"):
                os.remove(os.path.join(CACHE_DIR, name))
                n += 1
    except OSError:
        pass
    return n


# --------------------------------------------------------------------------- 单位

def fen_to_yuan(fen) -> float:
    """calculate-price 系列字段的单位是分，除以 100 得到元。"""
    if fen in (None, ""):
        return 0.0
    return round(int(fen) / 100, 2)


def yuan_str(fen) -> str:
    """把分格式化成 '¥12.50'。"""
    return f"¥{fen_to_yuan(fen):.2f}"


def parse_menu_price(value) -> float:
    """query-meals 里的 currentPrice / originalPrice 是字符串元（'33.5'），直接用。

    注意：菜单是元、算价是分，两套单位混用时必错 100 倍。
    """
    if value in (None, ""):
        return 0.0
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    # 冒烟自检：连通性 + token 有效性
    try:
        d = require_ok(call_tool("now-time-info"), "now-time-info")
        print(f"✓ 连通正常  {d.get('formatted')}  {d.get('dayOfWeek')}")
    except McdError as e:
        print(f"✗ {e}")
        sys.exit(1)
