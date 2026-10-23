#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门店定位与降级排查。

`mcd_planner.py` 已经内置查店，这个脚本是**查不到店时单独排障用的**：
自动补 searchType=2、翻译错误码、按备用地标依次重试、明确标注 distance 不可信。

    python3 locate_store.py --city 雄安 --keyword 容东
    python3 locate_store.py --city 保定 --keyword 容城 --fallback 雄安 容城 盛容荟
    python3 locate_store.py --city 北京市 --keyword 国贸 --be-type 5 --json
"""

from __future__ import annotations

import argparse
import json
import sys

from mcd_mcp import McdError, call_tool

ERROR_HINTS = {
    600014: "keyword 在该 city 围栏内解析不到地址。换成该城市内确定存在的 POI（商圈/小区/路名/地铁站）。",
    600047: "地址解析成功，但该点周边没有门店。**先换 city**（业务城市可能不等于行政区），再换 POI。",
    600050: "searchType 用了默认值 1（搜收藏餐厅）。本脚本已自动传 2，若仍报此错说明服务端变更。",
    600058: "city 与 keyword 必须同时非空。报错文案「城市名或者关键词不能为空」是误导——两个都要给。",
    600057: "门店已打烊。换一家 businessStatus 为 true 的门店，或改用预约。",
}


def query(city: str, keyword: str, be_type: int) -> dict:
    try:
        return call_tool("query-nearby-stores", {
            "beType": be_type, "searchType": 2, "city": city, "keyword": keyword,
        })
    except McdError as e:
        return {"code": -1, "message": str(e), "data": []}


def render(stores: list[dict], show_be: bool) -> str:
    out = ["| storeCode | 门店 | 地址 | 营业时间 | 预约 | distance* |",
           "|---|---|---|---|---|---|"]
    for s in stores:
        code = str(s.get("storeCode", ""))
        if show_be and s.get("beCode"):
            code += f"<br>beCode: {s['beCode']}"
        hours = f"{s.get('businessStartTime', '?')}-{s.get('businessEndTime', '?')}"
        if s.get("businessStatus") is False:
            hours += "（当前休息）"
        out.append(
            f"| `{code}` | {s.get('storeName', '')} | {s.get('address', '')} "
            f"| {hours} | {'是' if s.get('reservation') else '否'} | {s.get('distance', '-')} m |"
        )
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="麦当劳门店定位（含错误码归因与降级）")
    ap.add_argument("--city", required=True, help="业务城市名，如 雄安 / 北京市（不需要后缀）")
    ap.add_argument("--keyword", default="", help="地址 POI，如 容东 / 国贸（别用店名或泛词）")
    ap.add_argument("--be-type", type=int, default=1, choices=[1, 5],
                    help="1=到店自取（默认），5=得来速（独立门店库）")
    ap.add_argument("--fallback", nargs="*", default=[], help="主查询失败后依次尝试的备用地标")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    attempts = [a.keyword] + [k for k in a.fallback if k != a.keyword]
    log = []
    for kw in attempts:
        r = query(a.city, kw, a.be_type)
        stores = r.get("data") or []
        log.append({"keyword": kw, "code": r.get("code"),
                    "message": r.get("message"), "count": len(stores), "stores": stores})
        if r.get("code") == 200 and stores:
            if a.json:
                print(json.dumps({"hit": log[-1], "attempts": log},
                                 ensure_ascii=False, indent=2))
                return
            print(f"# city={a.city}  keyword={kw or '(空)'}  beType={a.be_type}\n")
            print(f"命中 {len(stores)} 家：\n")
            print(render(stores, show_be=(a.be_type == 5)))
            if len(stores) >= 5:
                print("\n> 返回 5 家 = 触达硬上限，不代表该区域只有 5 家。换 keyword 可继续逼近其他门店。")
            print("\n\\* distance 是 keyword 解析点到门店的直线距离，**不代表你离店多远**，不能用于排序。")
            return

    if a.json:
        print(json.dumps({"hit": None, "attempts": log}, ensure_ascii=False, indent=2))
        return

    print(f"# 未命中：city={a.city}  beType={a.be_type}\n")
    for e in log:
        print(f"- keyword=`{e['keyword'] or '(空)'}` → code={e['code']}  {e['message']}")

    last = log[-1]
    code = last["code"]
    print("\n## 诊断\n")
    if code in ERROR_HINTS:
        print(f"- {ERROR_HINTS[code]}")
    elif code not in (200, None):
        print(f"- 未收录的错误码 {code}，原始 message：{last['message']}")

    print("\n## 降级顺序\n")
    if code == 600047:
        print("1. **换 city**（业务城市，不是上级市；新区/开发区常与上级市不同）")
        print("2. 再换 POI")
    else:
        print("1. 换本城市内确定存在的 POI")
        print("2. **换 city**（业务城市）—— 关键词本身很明确时，大概率是 city 写错")
    print("3. 改 --be-type 5 试得来速（另一套门店库）")
    print("4. 走外送库 delivery-query-stores（第三套库，需 addressId）")
    print("5. 以上全空，才能说「无门店」，并说清是哪一套库")
    sys.exit(2)


if __name__ == "__main__":
    main()
