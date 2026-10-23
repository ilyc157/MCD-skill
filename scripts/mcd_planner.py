#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳配餐编排器：查店 → 菜单 → 套餐拆解 → 营养 → 求解 → 真实核价。

它是只读的。除了 calculate-price（免费、不影响账户额度）以外不写任何东西，
create-order 永远留给主流程在用户明确选择之后单独调用。

输出一份 JSON，直接喂给可视化卡片。

    python3 mcd_planner.py --city 雄安 --keyword 容东 --budget 30 --kcal-max 700
    python3 mcd_planner.py --store 1950739 --objective value --top-k 2 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import datetime as dt
import time
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

from mcd_mcp import McdError, call_cached, call_tool, fen_to_yuan, require_ok
from nutrition_solver import (
    SPLIT_NORMAL_RATIO,
    Constraints,
    MenuItem,
    NutritionTable,
    attach_nutrition,
    parse_combo_detail,
    parse_menu,
    solve,
    solve_group,
)


# --------------------------------------------------------------------------- 餐段
#
# 菜单是**按时段切**的。实测同一家店：早上 08:58 拿到 72 个 SKU，全是麦满分/油条/粥；
# 传 reservationDate=当天 18:00 后拿到 106 个 SKU，全是汉堡/套餐。
# 所以用户说「今晚吃」而现在还是早上，不传 reservationDate 就会拿早餐菜单配晚餐，
# 结果给出「油条×3」当晚饭 —— 价格算得没错，但没人这么吃。

MEAL_WINDOWS = {           # (起始时分, 结束时分)，跨夜的用 >24 表示
    "早餐": (6, 0, 10, 30),
    "午餐": (10, 30, 14, 0),
    "下午茶": (14, 0, 17, 0),
    "晚餐": (17, 0, 21, 0),
    "夜宵": (21, 0, 26, 0),
}


def reservation_for_meal(meal: str, now=None):
    """餐段 → (reservationDate, 说明)。

    当前就在该餐段 → 返回 None（实时菜单就是对的，别多此一举）；
    不在该餐段 → 取当天或次日该餐段的代表时间，透传给四个接口。
    """
    w = MEAL_WINDOWS.get(meal or "")
    if not w:
        return None, ""
    now = now or dt.datetime.now()
    start, end = w[0] * 60 + w[1], w[2] * 60 + w[3]
    cur = now.hour * 60 + now.minute
    if start <= cur < end or (end > 24 * 60 and cur < end - 24 * 60):
        return None, f"当前就是{meal}时段，用的是实时菜单"
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    target = base + dt.timedelta(minutes=start + 30)
    if target <= now:
        target += dt.timedelta(days=1)
    return target.strftime("%Y-%m-%d %H:%M"), (
        f"当前不在{meal}时段，按 {target:%-m-%-d %H:%M} 的菜单规划")


class _Clock:
    """分步计时。出方案慢在哪，靠这个说话，别靠感觉。"""

    def __init__(self):
        self.t0 = time.time()
        self.last = self.t0
        self.spans: dict[str, float] = {}

    def mark(self, name: str) -> None:
        now = time.time()
        self.spans[name] = round(now - self.last, 2)
        self.last = now

    @property
    def total(self) -> float:
        return round(time.time() - self.t0, 2)



# --------------------------------------------------------------------------- 门店

def find_stores(city: str, keyword: str, be_type: int = 1) -> list[dict]:
    """city 与 keyword 必须同时非空，否则 600058。规则见 references/store-lookup.md。"""
    r, _ = call_cached("query-nearby-stores", {
        "beType": be_type, "searchType": 2, "city": city, "keyword": keyword,
    })
    return r.get("data") or []


def pick_open_store(stores: list[dict], prefer: str = "") -> dict | None:
    """优先选 businessStatus 为真的门店，避开 600057（门店休息）。

    keyword 只做粗筛（实测传「奥威」和「容东」返回同样 2 家），所以这里再按
    门店名/地址做一次文字比对，避免用户说「容东」却推荐了隔壁片区的店。
    """
    open_stores = [s for s in stores if s.get("businessStatus", True)] or stores
    if prefer:
        for s in open_stores:
            text = f"{s.get('storeName', '')}{s.get('address', '')}"
            if prefer in text:
                return s
    return open_stores[0] if open_stores else None


# 门店名缓存：接口没有「按 storeCode 查门店详情」的工具，只给 storeCode 时
# 名字只能从历史查询里捞。卡片必须显示门店名，所以落一份本地缓存。
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", ".cache", "stores.json")


def cache_load() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def cache_put(stores: list[dict]) -> None:
    if not stores:
        return
    data = cache_load()
    for s in stores:
        code = s.get("storeCode")
        if not code:
            continue
        data[str(code)] = {
            "storeCode": str(code),
            "storeName": s.get("storeName") or s.get("store_name") or "",
            "address": s.get("address", ""),
            "beCode": s.get("beCode") or s.get("be_code") or "",
        }
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass  # 缓存写不进去不影响主流程


def resolve_store_name(store_code: str) -> dict | None:
    """只给了 storeCode 时补门店名：先查缓存，再翻历史订单。"""
    hit = cache_load().get(str(store_code))
    if hit and hit.get("storeName"):
        return hit
    try:
        orders = require_ok(call_cached("order-list", {}, ttl=300)[0], "order-list")
    except McdError:
        return hit
    for o in (orders.get("list") or []):
        if str(o.get("storeCode")) == str(store_code) and o.get("storeName"):
            found = {"storeCode": str(store_code), "storeName": o["storeName"],
                     "address": "", "beCode": o.get("beCode") or ""}
            cache_put([found])
            return found
    return hit


# --------------------------------------------------------------------------- 菜单

def fetch_menu(store_code: str, be_type: int = 1, order_type: int = 1,
               be_code: str | None = None, reservation: str | None = None) -> dict:
    args = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
    if be_code:
        args["beCode"] = be_code
    if reservation:
        args["reservationDate"] = reservation
    return require_ok(call_cached("query-meals", args)[0], "query-meals")


def fetch_combo_details(items: list[MenuItem], store_code: str, be_type: int = 1,
                        order_type: int = 1, be_code: str | None = None,
                        limit: int = 30, workers: int = 8,
                        reservation: str | None = None) -> dict:
    """对套餐逐个拉详情，用于拆解子项。

    只读调用，不消耗下单额度；套餐一般 10-30 个，串行要 20s+，所以并发拉。
    详情接口的参数名是 code，不是 mealCode。

    reservation **必须透传**：预约时段（如次日早餐 / 今日午餐）的 SKU code 与当前
    时段不是同一套，不传就全查不到 —— 实测预约午餐 106 个 SKU，拆解数 0。
    """
    targets = [i for i in items if i.is_combo][:limit]

    def one(it: MenuItem):
        args = {"storeCode": store_code, "orderType": order_type,
                "beType": be_type, "code": it.code}
        if be_code:
            args["beCode"] = be_code
        if reservation:
            args["reservationDate"] = reservation
        try:
            r, _ = call_cached("query-meal-detail", args)
            if r.get("code") == 200 and r.get("data"):
                cd = parse_combo_detail(r["data"])
                if cd.rounds:
                    return cd
        except McdError:
            pass  # 单个套餐拉不到不影响整体
        return None

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for cd in ex.map(one, targets):
            if cd:
                out[cd.code] = cd
    return out


# --------------------------------------------------------------------------- 核价

def repair_unit_prices(items: list[MenuItem], details: dict, store_code: str,
                       be_type: int = 1, order_type: int = 1,
                       be_code: str | None = None, workers: int = 8,
                       reservation: str | None = None) -> list[tuple]:
    """用 calculate-price 校正组合/套餐的真实单价。

    **菜单标价对组合类不可靠。** 实测同店：
      大脆鸡扒麦满分组合  菜单 ¥8.1  → 单点实付 ¥13.50
      脆香油条组合        菜单 ¥6.6  → 单点实付 ¥11.00
      火腿扒麦满分组合    菜单 ¥7.8  → 单点实付 ¥13.00
    菜单价是「加价购起价」（得先买主品才享这个价），单点下单按原价。
    单点商品的菜单价与实付完全一致，所以只对拆出详情的组合校正。

    不校正的后果有两个，都致命：
      1. 预算判断用菜单价 → 通过 → 核价后超支，等于给出买不起的方案；
      2. 排序偏向「看起来便宜」的组合，把真正便宜的 ¥8 油条挤掉。
    """
    targets = [i for i in items if i.code in details and i.price > 0]
    if not targets:
        return []

    def one(it: MenuItem):
        cd = details[it.code]
        entry = {
            "productCode": it.code,
            "quantity": 1,
            "roundList": [
                {"round": r["round"],
                 "comboItemList": [{"code": c["code"], "quantity": c.get("quantity") or 1}
                                   for c in r["chosen"]]}
                for r in cd.rounds
            ],
        }
        args = {"storeCode": store_code, "orderType": order_type,
                "beType": be_type, "items": [entry]}
        if be_code:
            args["beCode"] = be_code
        if reservation:
            args["reservationDate"] = reservation
        try:
            r, _ = call_cached("calculate-price", args)
            if r.get("code") == 200 and r.get("data"):
                return it, fen_to_yuan(r["data"].get("price"))
        except McdError:
            pass
        return it, None

    fixed = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for it, real in ex.map(one, targets):
            if real is None or real <= 0:
                continue
            if abs(real - it.price) > 0.01:
                fixed.append((it.name, it.price, real))
                # 不动 original_price：它是真实划线价，改成菜单价会造出假「结构省」
                it.price = real
    return fixed


def price_plan(plan, store_code: str, be_type: int = 1, order_type: int = 1,
               be_code: str | None = None, details: dict | None = None,
               reservation: str | None = None) -> dict:
    """调 calculate-price 拿真实结算价。返回的金额是分，必须除以 100。"""
    details = details or {}
    payload_items = []
    for it in plan.items:
        # 多人方案的份数在 plan.quantities 里，硬编码 1 会让多人单核价腰斩
        entry = {"productCode": it.code, "quantity": plan.qty_of(it.code)}
        cd = details.get(it.code)
        if cd:
            entry["roundList"] = [
                {"round": r["round"],
                 "comboItemList": [{"code": c["code"], "quantity": c["quantity"]}
                                   for c in r["chosen"]]}
                for r in cd.rounds
            ]
        payload_items.append(entry)

    args = {"storeCode": store_code, "orderType": order_type,
            "beType": be_type, "items": payload_items}
    if be_code:
        args["beCode"] = be_code
    if reservation:
        # 预约/跨餐段的 SKU 属于那份菜单，不传 reservationDate 会报
        # 60006「商品已售罄或不可售」—— 看着像售罄，其实是查错了菜单
        args["reservationDate"] = reservation
    try:
        r, _ = call_cached("calculate-price", args)
    except McdError as e:
        return {"ok": False, "error": str(e)}

    if r.get("code") != 200:
        return {"ok": False, "error": f"code={r.get('code')} {r.get('message')}"}

    d = r.get("data") or {}
    return {
        "ok": True,
        "price": fen_to_yuan(d.get("price")),
        "product_price": fen_to_yuan(d.get("productPrice")),
        "product_original_price": fen_to_yuan(d.get("productOriginalPrice")),
        "discount": fen_to_yuan(d.get("discount")),
        "take_way": [t.get("title") for t in (d.get("takeWayList") or [])],
        "per_product": [
            {"code": p.get("productCode"), "name": p.get("productName"),
             "subtotal": fen_to_yuan(p.get("subtotal")),
             "original_subtotal": fen_to_yuan(p.get("originalSubtotal"))}
            for p in (d.get("productList") or [])
        ],
    }


def fetch_coupons(store_code: str, be_type: int = 1, order_type: int = 1,
                  be_code: str | None = None) -> list[dict]:
    """门店可用券。注意：无券时返回 data: [] 而不是报错，不要当成接口故障。"""
    args = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
    if be_code:
        args["beCode"] = be_code
    try:
        r, _ = call_cached("query-store-coupons", args)
    except McdError:
        return []
    return r.get("data") or []


# --------------------------------------------------------------------------- 主流程

def _sig(plan) -> tuple:
    """一套方案的指纹：商品 + 份数。用来判「这两套是不是同一份」。（份数在 Plan 上，不在 MenuItem）"""
    return tuple(sorted(((i.code, plan.qty_of(i.code)) for i in plan.items)))


def _ensure_two(plans, items, c, people, kcal_max, objective, top_k):
    """至少给两套。只给一套等于没得选，用户没法「比一比」。

    约束刚好只命中一条路径时（比如 3 人 ¥30、钠 ≤1500 全撞死），逐级放宽再补：
    先松营养上限，再松预算。人数和口味不动 —— 那是用户明确说的。
    返回 (plans, 说明)；说明会进 warnings，卡片上不铺开。
    """
    need = 2 - len(plans)
    if need <= 0:
        return plans, ""
    seen = {_sig(p) for p in plans}
    rungs = [
        ("营养项放宽后（热量/蛋白不设上下限、钠不设上限）",
         replace(c, kcal_min=None, protein_min=None, sodium_max=None)),
        ("预算再放宽后",
         replace(c, kcal_min=None, protein_min=None, sodium_max=None, budget_max=None)),
    ]
    note = ""
    for label, cc in rungs:
        try:
            if people and people > 1:
                got = solve_group(items, cc, people, objective=objective,
                                  kcal_target=kcal_max, top_k=4)
            else:
                got = solve(items, cc, objective=objective, top_k=4)
        except Exception:                      # 一种口径炸了不该拖垮已经拿到的方案
            got = []
        for g in got:
            sig = _sig(g)
            if sig in seen:
                continue
            seen.add(sig)
            plans.append(g)
            note = f"约束较紧，只配出一套；第 {len(plans)} 套是{label}的备选"
            need -= 1
            if need <= 0:
                return plans, note
    return plans, note


def _split_extra(kcal_max, people: int, light_for: int) -> float:
    """同桌只有部分人控卡时，全桌比「人均 × 人数」多出来的热量额度。

    「人均 700 × 3 人」读作「三个人都吃 700」，但一个人减脂时真实分布是
    550 + 950 + 950 —— 他少吃的那部分不该连带另外两位一起吃少。
    差额 = 照常吃的人数 × 控卡上限 × (常规倍数 - 1)。

    light_for 为 0（没声明有人控卡）或覆盖全员时返回 0，这时人均语义成立，
    多人和单人路径都按原样跑。
    """
    if not kcal_max or people <= 1 or not light_for:
        return 0.0
    normal = people - min(max(light_for, 1), people)
    if normal <= 0:
        return 0.0
    return round(normal * kcal_max * (SPLIT_NORMAL_RATIO - 1), 1)


def build(city: str = "", keyword: str = "", store_code: str = "",
          be_type: int = 1, order_type: int = 1, be_code: str | None = None,
          reservation: str | None = None,
          kcal_max=None, kcal_min=None, protein_min=None, sodium_max=1500.0,
          budget=None, exclude=None, include=None, structure=None,
          objective: str = "value", top_k: int = 2, people: int = 1,
          with_coupons: bool = True, meal: str = "", light_for: int = 0) -> dict:

    report: dict = {"ok": False, "warnings": [], "steps": [], "people": people}
    clk = _Clock()

    # 1. 门店
    if store_code:
        store = {"storeCode": store_code, "storeName": "", "beCode": be_code}
        # 指定了 storeCode 但还给了城市/地标时，顺手补上门店名（卡片要显示）
        if city and keyword:
            found = find_stores(city, keyword, be_type)
            cache_put(found)
            for s in found:
                if str(s.get("storeCode")) == str(store_code):
                    store = s
                    break
        if not store.get("storeName"):
            hit = resolve_store_name(store_code)
            if hit:
                store = {**store, **{k: v for k, v in hit.items() if v}}
    else:
        if not (city and keyword):
            return {**report, "error": "city 与 keyword 必须同时提供（缺一报 600058）"}
        stores = find_stores(city, keyword, be_type)
        if not stores:
            return {**report, "error": f"city={city} keyword={keyword} 查不到门店。"
                                       "先换 POI，再换业务城市（见 references/store-lookup.md）"}
        cache_put(stores)
        store = pick_open_store(stores, prefer=keyword) or {}
        report["stores_found"] = len(stores)
    store_code = str(store.get("storeCode") or "")
    be_code = be_code or (str(store["beCode"]) if store.get("beCode") else None)
    if not store.get("storeName"):
        report["warnings"].append(
            "只给了 storeCode，接口无「按 code 查门店」的工具，且缓存与历史订单都没命中；"
            "卡片上的门店名需要人工补，或改传 --city/--keyword。"
        )
    report["store"] = {"code": store_code, "name": store.get("storeName", ""),
                       "address": store.get("address", ""), "beCode": be_code,
                       "beType": be_type, "orderType": order_type}
    report["steps"].append("store")
    clk.mark("store")

    # 1.5 餐段：菜单按时段切，跨餐段必须走 reservationDate，否则拿早餐菜单配晚餐
    if meal and not reservation:
        reservation, note = reservation_for_meal(meal)
        if note:
            report["menu_note"] = note

    # 2. 营养表 + 菜单
    table = NutritionTable.from_toon(
        require_ok(call_cached("list-nutrition-foods")[0], "list-nutrition-foods")
    )
    menu_data = fetch_menu(store_code, be_type, order_type, be_code, reservation)
    items = parse_menu(menu_data)
    report["sku_total"] = len(items)
    report["nutrition_rows"] = len(table.by_name)
    if table.duplicates:
        report["warnings"].append(f"营养表存在重名条目（已取首条）：{table.duplicates}")
    report["steps"].append("menu")
    clk.mark("menu")

    # 3. 拆套餐
    details = fetch_combo_details(items, store_code, be_type, order_type, be_code,
                                  reservation=reservation)
    report["combos_decomposed"] = len(details)
    report["steps"].append("combo_detail")
    clk.mark("combo_detail")

    # 3.5 校正组合真实单价（菜单价是加价购起价，不能直接算预算）
    fixed = repair_unit_prices(items, details, store_code, be_type, order_type, be_code,
                               reservation=reservation)
    if fixed:
        report["price_repaired"] = [
            {"name": n, "menu": m, "actual": a} for n, m, a in fixed
        ]
        report["warnings"].append(
            "{} 个组合的菜单标价低于单点实付（加价购起价），已按算价校正：{}".format(
                len(fixed),
                "、".join("{} ¥{}→¥{}".format(n, m, a) for n, m, a in fixed[:3])
                + ("…" if len(fixed) > 3 else ""))
        )
    report["steps"].append("price_repair")
    clk.mark("price_repair")

    # 4. 挂营养
    attach_nutrition(items, table, details)
    known = [i for i in items if i.nutrition and i.nutrition.known]
    report["nutrition_coverage"] = f"{len(known)}/{len(items)}"
    unmatched = [i.name for i in items if not (i.nutrition and i.nutrition.known)]
    report["unmatched"] = unmatched[:20]
    report["steps"].append("nutrition")
    clk.mark("nutrition")

    # 5. 求解
    c = Constraints(
        kcal_max=kcal_max, kcal_min=kcal_min, protein_min=protein_min,
        sodium_max=sodium_max, budget_max=budget,
        exclude_keywords=exclude or [], include_keywords=include or [],
        structure=structure,
        kcal_extra=_split_extra(kcal_max, people, light_for),
    )
    if people and people > 1:
        # 多人：预算=总额，营养约束=人均；分享装与人手一份两条路径并跑
        plans = solve_group(items, c, people, objective=objective,
                            kcal_target=kcal_max, top_k=top_k)
    else:
        plans = solve(items, c, objective=objective, kcal_target=kcal_max, top_k=top_k)
    report["constraints"] = c.describe()
    if people and people > 1:
        report["constraints"] += f" · {people} 人（预算为总额，营养约束为人均）"
    if light_for and people > light_for:
        report["constraints"] += f" · {light_for} 人控卡、其余照常"
    report["steps"].append("solve")
    clk.mark("solve")

    report["criteria"] = {
        "people": people, "budget": budget, "kcal_max": kcal_max,
        "kcal_min": kcal_min, "protein_min": protein_min,
        "sodium_max": sodium_max, "objective": objective,
        "structure": structure, "reservation": reservation,
        "light_for": light_for,
    }

    if not plans:
        # 预算卡死时不能交白卷。3 人 30 元在晚餐菜单上确实配不出来，
        # 但用户要的是「到底最少多少钱」，不是一句"无解"。
        #
        # objective 两个都跑：budget 口径在多人路径上会挑出比 value 更贵的解
        # （实测 3 人晚餐：budget→¥81，value→¥76.2），所以取总价更低的那一组。
        if budget:
            c2 = replace(c, budget_max=None)
            cands = []
            for obj in ("budget", "value"):
                try:
                    if people and people > 1:
                        pl = solve_group(items, c2, people, objective=obj,
                                         kcal_target=kcal_max, top_k=top_k)
                    else:
                        pl = solve(items, c2, objective=obj, top_k=top_k)
                except Exception:                 # 一种口径炸了不能block另一种
                    pl = []
                if pl:
                    cands.append(pl)
            if cands:
                plans = min(cands, key=lambda pl: min(x.total for x in pl))
                report["budget_relaxed"] = True
                # 「最少要多少钱」不能拿营养残缺的方案回答：精选超值随心配缺主品营养，
                # 3 人餐只算出 351 kcal / 蛋白 0g，却在价格上更便宜，会误导用户。
                def _edible(x):
                    n = x.nutrition
                    if not n:
                        return False
                    per = (n.kcal or 0) / max(people, 1)
                    return per >= 150 and (n.protein or 0) > 0

                full = [x for x in plans if _edible(x)]
                if full:
                    plans = full

    # 5.5 至少两套：只给一套等于没得选。约束刚好只命中一条路径时逐级放宽再补，
    #     先松营养上限，再松预算；人数和口味不动（那是用户明确说的）。
    plans, extra_note = _ensure_two(plans, items, c, people, kcal_max, objective, top_k)
    if extra_note:
        report["warnings"].append(extra_note)

    # 5.6 数据缺口必须点名：更划算的套餐只因缺一项营养就被整条排除，
    #     用户看到的是「又贵又素」，却不知道是数据问题 —— 会以为是配餐逻辑差。
    if plans:
        per_cap = min(p.total for p in plans) / max(people or 1, 1)
        holes = [i for i in items
                 if i.price > 0 and i.price <= per_cap * 1.6
                 and i.nutrition and i.nutrition.known
                 and (i.nutrition.kcal or 0) >= 250   # 残缺到不像一顿饭的不值得提
                 and i.nutrition.matched_by == "combo_partial"]
        holes.sort(key=lambda i: i.price)
        if holes:
            hit = holes[0]
            cd = details.get(hit.code)
            miss: list[str] = []
            if cd:
                for r in cd.rounds:
                    for ch in r["chosen"]:
                        pn = table.lookup(ch["name"])
                        if not (pn and pn.known):
                            miss.append(ch["name"])
            report["warnings"].append(
                "「{}」¥{:.1f} 没进方案：{} 未收录营养，套餐只能算到部分子项的热量，"
                "宁可不算也不报假数".format(
                    hit.name, hit.price, "、".join(dict.fromkeys(miss)) or "部分子项"))

    if not plans:
        report["error"] = "无可行解：约束过严或营养数据不足"
        report["suggest"] = [
            "放宽热量/蛋白/预算",
            "把 sodium_max 设为 None 再试（默认 1500mg 会挡掉不少组合）",
            "换 objective=budget 先看最便宜的",
        ]
        report["timing"] = clk.spans
        report["elapsed"] = clk.total
        return report

    # 6. 真实核价
    out_plans = []
    price_gap = 0
    # 套餐子项（汉堡/可乐）的图不在详情里，只能回菜单里按 code 找
    img_map = {i.code: i.image for i in items if getattr(i, "image", "")}
    for p in plans:
        priced = price_plan(p, store_code, be_type, order_type, be_code, details,
                            reservation=reservation)
        d = p.to_dict()
        # 套餐要摊开写：卡片上「搭配清晰」指的是看得见汉堡和可乐，不是只给一个套餐名
        for it in d.get("items") or []:
            cd = details.get(it["code"])
            if not it.get("image"):
                it["image"] = img_map.get(it.get("code"), "") or (cd.image if cd else "")
            if cd:
                # 子项也要带单品营养：卡片把套餐拆成单品逐行展示，
                # 每行右侧那颗 kcal 胶囊没有别的来源。查不到就留 None，
                # 让卡片不显示 —— 缺数不能补 0。
                parts = []
                for r in cd.rounds:
                    for c in r["chosen"]:
                        pn = table.lookup(c["name"])
                        parts.append({
                            "name": c["name"],
                            "quantity": c.get("quantity") or 1,
                            "round": r.get("name") or "",
                            "image": img_map.get(str(c.get("code")), ""),
                            "nutrition": (pn.to_dict()
                                          if (pn and pn.known) else None),
                        })
                it["parts"] = parts
        d["priced"] = priced
        if priced.get("ok"):
            # 以服务端算价为准；菜单价只作参考
            d["final_price"] = priced["price"]
            d["coupon_saving"] = round(
                max(0.0, priced.get("product_price", 0) - priced["price"]), 2
            )
            if d["total"] and d["final_price"] > d["total"] + 0.01:
                price_gap += 1
        else:
            d["final_price"] = d["total"]
            d["coupon_saving"] = 0.0
            d["price_failed"] = priced.get("error") or ""
            report["warnings"].append(f"核价失败（{priced.get('error')}），已回落到菜单价")
        if people and people > 1:
            d["per_person"] = round(d["final_price"] / people, 2)
        # 预算必须用核价后的实付判断：菜单标价可能是「加价购起价」，
        # 实测大脆鸡扒麦满分组合菜单 ¥8.1、单点实付 ¥13.50，差 5.4 元。
        # 用菜单价过预算会给出超支方案，这正是「总价有依据」不能出错的地方。
        if budget and d["final_price"] > budget:
            d["over_budget"] = True
            d["over_by"] = round(d["final_price"] - budget, 2)
        out_plans.append(d)
    if report.get("budget_relaxed"):
        low = min((d.get("final_price") or 0) for d in out_plans) if out_plans else 0
        report["warnings"].append(
            f"{budget:g} 元配不出 {people} 人的一餐，下面是实付最低的方案，"
            f"最少要 {low:.2f} 元（超 {low - budget:.2f}）")
    # 有的组合菜单上有、算价时却报 60006（售罄/不可售，如这家店 17:30 后的圆筒）。
    # 这种方案给了也下不了单，只要还有能核价的方案就别展示它。
    good = [d for d in out_plans if not d.get("price_failed")]
    if good and len(good) < len(out_plans):
        report["warnings"].append(
            f"{len(out_plans) - len(good)} 个方案核价报商品不可售，已剔除")
        out_plans = good
    if price_gap:
        report["warnings"].append(
            f"{price_gap} 个方案的菜单标价低于实付（组合类多为加价购起价），预算已按实付重新校验"
        )
    # 预算内优先；超支的排到最后但仍保留，让用户看得见差多少钱
    # 预算内优先；同档内按求解评分排（不能按价格排，便宜但营养残缺的方案会顶到最前）
    out_plans.sort(key=lambda x: (x.get("over_budget", False), -x.get("score", 0.0)))
    report["steps"].append("calculate_price")
    clk.mark("calculate_price")

    # 预算内的一律优先展示，超支的只保留 1 个作为「差多少钱」的参照，
    # 避免卡片上全是买不起的方案
    keep = [p for p in out_plans if not p.get("over_budget")]
    over = [p for p in out_plans if p.get("over_budget")]
    if keep:
        out_plans = keep[:top_k] + over[:1]
    else:
        out_plans = over[:top_k]

    # 7. 券
    if with_coupons:
        coupons = fetch_coupons(store_code, be_type, order_type, be_code)
        report["coupons"] = [
            {"title": c_.get("title"), "couponId": c_.get("couponId"),
             "couponCode": (c_.get("couponCode") or [None])[0]
                 if isinstance(c_.get("couponCode"), list) else c_.get("couponCode"),
             "tradeDateTime": c_.get("tradeDateTime"),
             "products": [p.get("productName") for p in (c_.get("products") or [])][:5]}
            for c_ in coupons
        ]
        report["steps"].append("coupons")
    clk.mark("coupons")

    report["plans"] = out_plans
    report["timing"] = clk.spans
    report["elapsed"] = clk.total
    report["ok"] = True
    return report


def _main():
    ap = argparse.ArgumentParser(description="麦当劳配餐编排（只读，不下单）")
    ap.add_argument("--city", default="")
    ap.add_argument("--keyword", default="")
    ap.add_argument("--store", default="", help="已知 storeCode 时可直接指定")
    ap.add_argument("--be-type", type=int, default=1, choices=[1, 2, 5, 6],
                    help="1=到店取 2=麦乐送 5=得来速 6=企业团餐")
    ap.add_argument("--order-type", type=int, default=1, choices=[1, 2])
    ap.add_argument("--be-code", default=None)
    ap.add_argument("--reservation", default=None, help="预约场景，格式 yyyy-MM-dd HH:mm")
    ap.add_argument("--kcal-max", type=float)
    ap.add_argument("--kcal-min", type=float)
    ap.add_argument("--protein-min", type=float)
    ap.add_argument("--sodium-max", type=float, default=1500)
    ap.add_argument("--budget", type=float)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--include", nargs="*", default=[])
    ap.add_argument("--structure", default=None, choices=["with_drink", "full"],
                    help="餐盘结构：with_drink=主食+饮品，full=主食+饮品或小食")
    ap.add_argument("--objective", default="value",
                    choices=["value", "budget", "kcal", "sodium"])
    ap.add_argument("--people", type=int, default=1,
                    help="用餐人数（赛题输入项）。≥2 时预算视为总额，营养约束按人均")
    ap.add_argument("--widget", default="",
                    help="输出卡片片段（无 HTML 骨架、无投影），直接喂可视化工具内嵌到对话")
    ap.add_argument("--top-k", type=int, default=2)
    ap.add_argument("--no-coupons", action="store_true")
    ap.add_argument("--no-cache", action="store_true",
                    help="绕开本地缓存，强制重拉（怀疑菜单变了再用）")
    # ↓ 出卡相关：给了 --html 就直接渲染，不用模型再写一遍
    ap.add_argument("--html", default="", help="同时输出品牌卡片 HTML 到这个路径")
    ap.add_argument("--meal", default="", help="餐段：早餐/午餐/下午茶/晚餐/夜宵")
    ap.add_argument("--prefer", default="", help="用户口味原话，写进卡片依据")
    ap.add_argument("--reason3", default="", help="覆盖卡片第三条依据")
    ap.add_argument("--freight", type=float, help="外送运费，元")
    ap.add_argument("--packing", type=float, help="打包费，元")
    ap.add_argument("--brief", action="store_true", help="只打印结论，不打印核价明细")
    ap.add_argument("--light-for", type=int, default=0,
                    help="同桌上几个人在控卡（需配 --kcal-max）。给了就按「N 人轻食、"
                         "其余照常、可加共享小食」出差异化方案；不给则人均口径全员适用")
    # ↓ 二次渲染：出了两个方案、用户挑了一个，不要重算，直接拿上次的存档出卡
    ap.add_argument("--from", dest="from_file", default="",
                    help="读取上次 --html 生成的 .json 存档，不重新求解")
    ap.add_argument("--pick", type=int, default=1,
                    help="配 --from 用：只渲染第 N 个方案（1 起）")
    a = ap.parse_args()

    if a.no_cache:
        os.environ["MCD_NO_CACHE"] = "1"

    t_render0 = time.time()
    if a.from_file:
        # 用户已经挑定了方案：读存档直接出卡。重跑一遍求解是纯浪费 ——
        # 菜单没变、约束没变，算出来的还是同一批解。
        with open(a.from_file, encoding="utf-8") as f:
            rep = json.load(f)
        old = rep.get("_all_plans") or rep.get("plans") or []
        idx = (a.pick or 1) - 1
        if not 0 <= idx < len(old):
            print(f"✗ 存档里只有 {len(old)} 个方案，没有第 {a.pick} 个", file=sys.stderr)
            sys.exit(1)
        rep["plans"] = [old[idx]]
        rep.pop("card", None)
        # 约束回填：二次渲染通常不再传 --budget/--kcal-max，卡片会丢预算口径
        c = rep.get("criteria") or {}
        for k in ("budget", "kcal_max", "kcal_min", "protein_min",
                  "sodium_max", "people", "light_for"):
            if getattr(a, k, None) in (None, 0, ""):
                setattr(a, k, c.get(k))
    else:
        try:
            rep = build(
                city=a.city, keyword=a.keyword, store_code=a.store,
                be_type=a.be_type, order_type=a.order_type, be_code=a.be_code,
                reservation=a.reservation,
                kcal_max=a.kcal_max, kcal_min=a.kcal_min, protein_min=a.protein_min,
                sodium_max=a.sodium_max, budget=a.budget,
                exclude=a.exclude, include=a.include, structure=a.structure,
                objective=a.objective, top_k=a.top_k, people=a.people,
                with_coupons=not a.no_coupons, meal=a.meal, light_for=a.light_for,
            )
        except McdError as e:
            print(f"✗ {e}", file=sys.stderr)
            if e.code:
                print(f"  code={e.code}", file=sys.stderr)
            sys.exit(1)

    # 出卡：一条命令连卡片一起给，省掉模型手写 HTML 的那几分钟。
    # 注意必须先出卡再 --brief —— 压缩会砍掉 priced/nutrition，卡片就只剩 ¥0.00。
    if (a.html or a.widget) and rep.get("ok"):
        try:
            import render_card
            ctx = {
                "people": a.people or rep.get("people") or 1,
                "budget": a.budget, "kcal_max": a.kcal_max,
                "sodium_max": a.sodium_max, "meal": a.meal,
                "prefer": a.prefer, "reason3": a.reason3,
                "freight": a.freight, "packing": a.packing,
            }
            hits: list[str] = []
            if a.html:                              # 独立页面：预览面板 / 发给别人
                frag = render_card.render(rep, dict(ctx), widget=False)
                with open(a.html, "w", encoding="utf-8") as f:
                    f.write(render_card.page(frag))
                rep["card"] = a.html
                hits += [w for w in render_card.BANNED if w in frag]
            if a.widget:                            # 纯片段：直接喂 show_widget 内嵌进对话
                frag = render_card.render(rep, dict(ctx), widget=True)
                frag = render_card._widget_safe(frag)
                with open(a.widget, "w", encoding="utf-8") as f:
                    f.write(frag)
                rep["widget"] = a.widget
                hits += [w for w in render_card.BANNED if w in frag]
            # 存档：用户挑完方案，--from 这个文件 + --pick N 就能秒出卡，不必重算
            dump = (a.html or a.widget) + ".json"
            with open(dump, "w", encoding="utf-8") as f:
                json.dump({**rep, "_all_plans": rep.get("plans")},
                          f, ensure_ascii=False)
            rep["dump"] = dump
            if hits:
                rep.setdefault("warnings", []).append(f"卡片文案命中禁用词：{hits}")
        except Exception as e:                      # 出卡失败不能拖垮配餐
            rep.setdefault("warnings", []).append(f"卡片渲染失败：{e}")

    if a.brief and rep.get("ok"):
        rep = _brief(rep)

    if a.from_file:
        # 存档里的 elapsed 是上一次求解的历史值，别拿它冒充这次的速度
        rep["elapsed"] = round(time.time() - t_render0, 2)
        rep["reused"] = True

    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if not rep.get("ok"):
        sys.exit(2)


def _brief(rep: dict) -> dict:
    """压缩输出：模型只需要看结论，核价明细留给卡片。"""
    plans = []
    for p in rep.get("plans") or []:
        plans.append({
            "price": p.get("final_price"), "total": p.get("total"),
            "kcal": (p.get("nutrition") or {}).get("kcal"),
            "protein": (p.get("nutrition") or {}).get("protein"),
            "route": p.get("route"), "score": p.get("score"),
            "per_person": p.get("per_person"),
            "over_budget": p.get("over_budget"),
            "items": [{"code": i["code"], "name": i["name"],
                       "quantity": i.get("quantity") or 1,
                       # 差异化方案里「这份是谁的」不能丢：模型复述时若只看到
                       # 一张平铺清单，就会把「一个人轻食」讲成「三个人都吃这个」
                       **({"for": p.get("assign", {})[i["code"]]}
                          if p.get("assign", {}).get(i["code"]) else {}),
                       # 卡片把套餐拆成单品展示，模型复述时也得知道拆出了什么
                       **({"parts": [x.get("name") for x in i.get("parts") or []]}
                          if i.get("parts") else {})}
                      for i in p.get("items") or []],
            "notes": p.get("notes"),
        })
    return {
        "ok": rep.get("ok"), "store": rep.get("store"),
        "constraints": rep.get("constraints"), "criteria": rep.get("criteria"),
        "menu_note": rep.get("menu_note"),
        "nutrition_coverage": rep.get("nutrition_coverage"),
        "plans": plans,
        "coupons": [c.get("title") for c in rep.get("coupons") or []][:3],
        "warnings": rep.get("warnings"), "timing": rep.get("timing"),
        "elapsed": rep.get("elapsed"), "error": rep.get("error"),
    }


if __name__ == "__main__":
    _main()
