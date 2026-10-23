#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡片渲染与餐段判定的离线断言。不联网，跑法：

    python3 tests/test_render.py

守的是四件事：
  1. 卡片上的数字必须来自 JSON —— 价格漏了就报，不许静默出 ¥0.00；
  2. 设计令牌与兜底不能回退：品牌色写死、图挂了有 M 徽章、长名截断；
  3. 至少两套方案、单卡 ≤432px（并排比得动，窄屏能折）；
  4. 餐段判断别把「早餐菜单配晚餐」这个坑放回来。
"""

import datetime as dt
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import mcd_icons as I                      # noqa: E402
import render_card as R                    # noqa: E402
from mcd_planner import reservation_for_meal  # noqa: E402

NOW = dt.datetime(2026, 10, 23, 8, 58)


def _plan(price, kcal, protein, sodium, items, ppy=0.6, per=None):
    return {
        "items": items, "total": price, "total_original": price,
        "structure_saving": 0.0, "protein_per_yuan": ppy, "score": 0.6,
        "route": "per_person", "notes": ["人手一份 × 3"],
        "nutrition": {"kcal": kcal, "protein": protein, "sodium": sodium,
                      "matched_by": "aggregate"},
        "priced": {"ok": True, "price": price, "product_price": price},
        "final_price": price, "coupon_saving": 0.0, "per_person": per,
    }


REPORT = {
    "ok": True,
    "store": {"code": "1950739", "name": "容东店", "orderType": 1},
    "constraints": "钠≤1500mg · 预算¥60 · 3 人",
    "criteria": {"people": 3, "budget": 60, "sodium_max": 1500, "objective": "value"},
    "menu_note": "当前不在晚餐时段，按 10-23 17:30 的菜单规划",
    "coupons": [],
    "plans": [
        _plan(44.7, 1287, 81, 3051, [
            {"code": "9900013304", "name": "人气经典随心配", "price": 14.9,
             "quantity": 3, "is_combo": True,
             "image": "https://menu-img.mcd.cn/x.png",
             "nutrition": {"kcal": 429, "protein": 27, "sodium": 1017,
                           "matched_by": "combo_decomposed"},
             "parts": [{"name": "双层吉士汉堡", "quantity": 1, "round": "蓝区",
                        "nutrition": {"kcal": 429, "protein": 27, "sodium": 1017}},
                       {"name": "可乐麦炫酷", "quantity": 1, "round": "粉区",
                        "nutrition": {"kcal": 165, "protein": 0, "sodium": 40}}]},
        ], ppy=0.65, per=14.9),
        _plan(59.7, 1566, 87, 3159, [
            {"code": "9900013304", "name": "人气经典随心配", "price": 14.9,
             "quantity": 3, "is_combo": True,
             "parts": [{"name": "双层吉士汉堡", "quantity": 1, "round": "蓝区",
                        "nutrition": {"kcal": 429, "protein": 27, "sodium": 1017}}]},
            {"code": "5213", "name": "圆筒冰淇淋", "price": 5.0, "quantity": 3},
        ], ppy=0.55, per=19.9),
    ],
}


class TestReservationForMeal(unittest.TestCase):
    """菜单按时段切：不在该餐段就必须补 reservationDate。"""

    def test_dinner_at_morning_needs_reservation(self):
        r, note = reservation_for_meal("晚餐", NOW)
        self.assertEqual(r, "2026-10-23 17:30")
        self.assertIn("不在晚餐时段", note)

    def test_breakfast_at_morning_is_live_menu(self):
        r, note = reservation_for_meal("早餐", NOW)
        self.assertIsNone(r)
        self.assertIn("实时菜单", note)

    def test_lunch_before_window(self):
        r, _ = reservation_for_meal("午餐", dt.datetime(2026, 10, 23, 9, 0))
        self.assertEqual(r, "2026-10-23 11:00")

    def test_late_night_window_wraps(self):
        r, _ = reservation_for_meal("夜宵", dt.datetime(2026, 10, 23, 23, 30))
        self.assertIsNone(r)                     # 23:30 就在夜宵时段

    def test_unknown_meal_is_noop(self):
        self.assertEqual(reservation_for_meal("", NOW), (None, ""))


class TestCard(unittest.TestCase):

    def setUp(self):
        self.ctx = {"people": 3, "budget": 60, "sodium_max": 1500,
                    "objective": "value", "meal": "晚餐", "kcal_max": 700}
        self.html = R.render(REPORT, dict(self.ctx))

    def test_price_comes_through(self):
        """价格必须是真数，不能是 ¥0.00 —— 这是 --brief 顺序错过的坑。"""
        self.assertIn("¥44.70", self.html)
        self.assertIn("¥59.70", self.html)
        self.assertNotIn("¥0.00", self.html)

    def test_combo_parts_are_spelled_out(self):
        """搭配清晰：套餐要摊开成子项，不能只给一个套餐名。"""
        self.assertIn("双层吉士汉堡", self.html)
        self.assertIn("可乐麦炫酷", self.html)

    def test_combo_and_pick_mix_are_flattened(self):
        """套餐/随心配不占整行，拆成单品逐行展示。

        菜单上的「人气经典随心配」既没告诉人吃什么，也看不出每样多少卡。
        拆平后卡片上不该再出现这个 SKU 名，每一行都是一个单品。
        """
        self.assertNotIn("人气经典随心配", self.html)
        # 两层套餐（plan0 一份）、B 卡（plan1 的套餐 + 圆筒）= 4 行单品
        self.assertEqual(self.html.count('border-radius:12px;padding:7px 10px'), 4)
        for nm in ("双层吉士汉堡", "可乐麦炫酷", "圆筒冰淇淋"):
            self.assertIn(f'title="随心配-{nm}"' if nm != "圆筒冰淇淋" else f'title="{nm}"',
                          self.html)

    def test_combo_item_carries_source_prefix(self):
        """拆平后要留来源：「随心配-可乐麦炫酷」，不然看不出这三件是拿随心配点的。"""
        self.assertIn("随心配-</span>", self.html)          # 前缀是独立小字，不跟菜名同号
        self.assertIn('title="随心配-可乐麦炫酷"', self.html)
        self.assertEqual(R.combo_tag("人气经典随心配"), "随心配")
        self.assertEqual(R.combo_tag("龙焰美味四件套"), "四件套")
        self.assertEqual(R.combo_tag("巨无霸套餐"), "巨无霸")

    def test_single_item_has_no_prefix(self):
        """单点不加前缀 —— 圆筒冰淇淋是单点的，标成随心配就是假的。"""
        self.assertNotIn("随心配-圆筒冰淇淋", self.html)
        self.assertIn('title="圆筒冰淇淋"', self.html)

    def test_prefix_survives_for_every_combo_unit(self):
        """套餐拆出来的每一件都要带前缀，漏一件就等于这件不知从哪来。"""
        p = {"items": [{"name": "人气经典随心配", "quantity": 1, "parts": [
            {"name": "中可乐", "quantity": 1}, {"name": "双层吉士汉堡", "quantity": 1}]}]}
        self.assertEqual({u["tag"] for u in R.units(p)}, {"随心配"})
        p["items"].append({"name": "圆筒冰淇淋", "quantity": 1})
        self.assertEqual([u["tag"] for u in R.units(p) if u["name"] == "圆筒冰淇淋"], [""])

    def test_flattened_units_are_ordered_like_a_meal(self):
        """拆平后要重排：主食 → 配品 → 饮品 → 甜品，别让圆筒排汉堡前面。"""
        p = {"items": [
            {"name": "圆筒冰淇淋", "quantity": 1, "nutrition": {}},
            {"name": "中可乐", "quantity": 1, "nutrition": {}},
            {"name": "中薯条", "quantity": 1, "nutrition": {}},
            {"name": "双层吉士汉堡", "quantity": 1, "nutrition": {}},
        ]}
        self.assertEqual([u["name"] for u in R.units(p)],
                         ["双层吉士汉堡", "中薯条", "中可乐", "圆筒冰淇淋"])

    def test_flattened_units_merge_same_item(self):
        """同一单品出现在两个套餐里要合并份数，不能上下重复两行。"""
        p = {"items": [
            {"name": "A 套餐", "quantity": 1, "parts": [
                {"name": "中可乐", "quantity": 1},
                {"name": "双层吉士汉堡", "quantity": 1}]},
            {"name": "B 套餐", "quantity": 1, "parts": [
                {"name": "中可乐", "quantity": 1}]},
        ]}
        units = R.units(p)
        self.assertEqual([u["quantity"] for u in units if u["name"] == "中可乐"], [2])
        self.assertEqual(len(units), 2)

    def test_each_unit_carries_its_own_kcal(self):
        """每行 kcal 必须是这一行单品的，不是套餐合计。

        没有单品营养就不显示 —— 拿合计摊到每行上等于编数。
        """
        self.assertIn("429 kcal", self.html)
        self.assertIn("165 kcal", self.html)
        p = {"items": [{"name": "A 套餐", "quantity": 1, "parts": [
            {"name": "双层吉士汉堡", "quantity": 1, "nutrition": None}]}]}
        self.assertNotIn("kcal", R.unit_row(R.units(p)[0]))

    def test_no_banned_words(self):
        hits = [w for w in R.BANNED if w in self.html]
        self.assertEqual(hits, [], f"禁用词命中：{hits}")

    def test_design_tokens_are_hardcoded(self):
        """品牌令牌写死在代码里，不跟随宿主主题。"""
        for token in (R.RED, R.GOLD, R.CREAM, R.INK, R.SUB):
            self.assertIn(token, self.html)
        # 省钱绿标只在真有优惠时出现（见 test_discount_shows_green_tag...）
        self.assertEqual(R.GOLD, "#FFBC0D")
        self.assertEqual(R.INK, "#27251F")
        self.assertEqual(R.GREEN, "#1B873F")
        # 不许引用宿主主题变量，否则深色主题下整卡不可读
        self.assertNotIn("var(--color", self.html)

    def test_rounding_system(self):
        """外层 18px 大圆角、内层 12px 模块卡，别写回小圆角。"""
        self.assertEqual(R.R_OUT, "18px")
        self.assertEqual(R.R_IN, "12px")
        self.assertIn("border-radius:18px", self.html)
        self.assertIn("border-radius:12px", self.html)

    def test_at_least_two_cards_side_by_side(self):
        """至少两套，同排两列：只给一套等于没得选。"""
        self.assertIn("方案 A", self.html)
        self.assertIn("方案 B", self.html)
        self.assertIn("max-width:432px", self.html)
        self.assertIn("flex-wrap:wrap", self.html)

    def test_long_name_is_clipped_with_title(self):
        """长菜名不许撑破行：单行截断 + title 浮窗。"""
        self.assertIn("text-overflow:ellipsis", self.html)
        self.assertIn("white-space:nowrap", self.html)
        self.assertIn('title="双层吉士汉堡"', self.html)

    def test_icons_are_inline_svg_never_remote_images(self):
        """餐品图必须是内联 SVG。

        菜单接口的 image 是 427×320 的促销横幅（带「随心配1+1」角标文案），
        缩到 34px 只剩色块；外链还可能在宿主容器里被拦。
        """
        self.assertNotIn("<img", self.html)
        self.assertNotIn("menu-img.mcd.cn", self.html)
        self.assertEqual(self.html.count("viewBox=\"0 0 32 32\""), 4)  # 每行一个餐品图标

    def test_every_item_row_has_an_icon(self):
        """每个单品行都得有一个 34px 图标底板，缺图不能留空。"""
        html = R.render(json.loads(json.dumps(REPORT)), dict(self.ctx))
        self.assertEqual(html.count("flex:0 0 34px"), 4)

    def test_unknown_dish_gets_arches_not_generic_burger(self):
        """认不出的菜上金拱门，不硬套一个汉堡糊弄。"""
        badge = I.badge("神秘限定款", 38)
        self.assertIn("#DA291C", badge)            # 红底
        self.assertIn(I.ICONS["logo"], badge)      # 真金拱门路径
        self.assertEqual(I.pick("神秘限定款"), "logo")

    def test_reasons_are_numeric(self):
        self.assertIn("排这一位，因为", self.html)
        self.assertIn("比方案 A 贵 15.00 元", self.html)
        self.assertIn("算账", self.html)
        self.assertIn("营养", self.html)

    def test_price_title_is_brand_voice_not_plan_letter(self):
        """L1 是「主品名」+ 口感/营养基调，不是「方案A」当标题。"""
        self.assertIn("「双吉」", self.html)
        self.assertIn("套</div>", self.html)          # 名字以「套」收尾，不是编号

    def test_menu_note_is_shown(self):
        """按几点钟的菜单算的，用户有权知道。"""
        self.assertIn("17:30 的菜单", self.html)

    def test_discount_shows_green_tag_and_strikethrough(self):
        rep = json.loads(json.dumps(REPORT))
        rep["plans"][0]["total_original"] = 60.0
        html = R.render(rep, dict(self.ctx))
        self.assertIn("立省", html)
        self.assertIn("text-decoration:line-through", html)
        self.assertIn(R.GREEN, html)

    def test_over_budget_is_flagged(self):
        rep = json.loads(json.dumps(REPORT))
        rep["plans"][0]["over_budget"] = True
        rep["plans"][0]["over_by"] = 5.5
        html = R.render(rep, dict(self.ctx))
        self.assertIn("超预算 ¥5.50", html)

    def test_delivery_extra_fees_are_named(self):
        """外送要交代运费/打包，别让「实付」口径含糊。"""
        ctx = dict(self.ctx, freight=6.0, packing=1.1)
        html = R.render(REPORT, ctx)
        self.assertIn("运费 ¥6.00", html)
        self.assertIn("打包 ¥1.10", html)

    def test_people_count_is_real(self):
        """卡片上的人数必须是真人数，之前写死过 3 人。"""
        ctx = dict(self.ctx, people=2)
        html = R.render(REPORT, ctx)
        self.assertIn("2 人", R.page(html))
        self.assertNotIn("3 人</span>", html)

    def test_no_emoji(self):
        for ch in ("🍔", "🔥", "✨", "😋"):
            self.assertNotIn(ch, self.html)

    def test_fragment_and_page(self):
        page = R.page(self.html)
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertIn("PingFang SC", page)

    def test_missing_nutrition_says_unknown(self):
        rep = json.loads(json.dumps(REPORT))
        rep["plans"][0]["nutrition"] = {}
        html = R.render(rep, dict(self.ctx))
        self.assertIn("未收录", html)

    def test_no_quoted_user_speech(self):
        """卡片不代述用户原话：--prefer 可能是脚本默认值或测试参数。"""
        rep = json.loads(json.dumps(REPORT))
        ctx = dict(self.ctx)
        ctx["prefer"] = "想吃辣"
        html = R.render(rep, ctx)
        self.assertNotIn("你说", html)
        self.assertNotIn("想吃辣", html)
        self.assertIn("辣味", html)

    def test_widget_mode_no_shadow_and_single_quotes(self):
        """widget 片段：无投影、无双引号（否则塞进工具参数要逐字转义）。"""
        rep = json.loads(json.dumps(REPORT))
        frag = R._widget_safe(R.render(rep, dict(self.ctx), widget=True))
        self.assertNotIn("box-shadow:0 2px", frag)
        self.assertEqual(frag.count('"'), 0)
        self.assertIn("clip:rect(0 0 0 0)", frag)


class TestSplitCard(unittest.TestCase):
    """差异化配餐的卡片：一张卡上装着三份不同的东西，必须看得出哪份是谁的。"""

    @staticmethod
    def _report():
        def item(code, name, price, qty, kcal, for_):
            return {"code": code, "name": name, "price": price, "quantity": qty,
                    "nutrition": {"kcal": kcal, "protein": 20, "sodium": 500,
                                  "matched_by": "exact"}, "for_": for_}

        items = [
            {"code": "L1", "name": "麦香鸡", "price": 19.5, "quantity": 1,
             "nutrition": {"kcal": 369, "protein": 21, "sodium": 700, "matched_by": "exact"}},
            {"code": "L2", "name": "纯悦", "price": 5.0, "quantity": 1,
             "nutrition": {"kcal": 0, "protein": 0, "sodium": 0, "matched_by": "exact"}},
            {"code": "N1", "name": "双层深海鳕鱼堡", "price": 24.0, "quantity": 2,
             "nutrition": {"kcal": 485, "protein": 28, "sodium": 917, "matched_by": "exact"}},
            {"code": "N2", "name": "纯牛奶(盒装)", "price": 11.0, "quantity": 2,
             "nutrition": {"kcal": 129, "protein": 7, "sodium": 73, "matched_by": "exact"}},
            {"code": "S1", "name": "薯条", "price": 14.0, "quantity": 1,
             "nutrition": {"kcal": 289, "protein": 4, "sodium": 165,
                           "matched_by": "spec_default"}},
        ]
        return {
            "ok": True, "store": {"code": "1", "name": "容东店"},
            "constraints": "≤700 kcal · 3 人", "coupons": [],
            "criteria": {"people": 3, "budget": 130, "objective": "value"},
            "plans": [{
                "items": items, "total": 107.0, "total_original": 107.0,
                "structure_saving": 0.0, "protein_per_yuan": 0.83, "score": 0.9,
                "route": "split", "notes": ["1 人轻食 · 2 人照常", "共享 薯条 ×1"],
                "nutrition": {"kcal": 1886, "protein": 89, "sodium": 2876,
                              "matched_by": "aggregate"},
                "final_price": 107.0, "coupon_saving": 0.0, "per_person": 35.67,
                "assign": {"L1": "light", "L2": "light", "N1": "normal",
                           "N2": "normal", "S1": "share"},
            }],
        }

    def setUp(self):
        self.html = R.render(self._report(),
                             {"people": 3, "budget": 130, "meal": "午餐", "kcal_max": 700})

    def test_three_groups_are_labelled(self):
        for label in ("轻食", "照常", "共享"):
            self.assertIn(f">{label}</span>", self.html)
        self.assertIn("大家分", self.html)

    def test_group_head_shows_per_person_kcal(self):
        # 「人均 629」在这个场景里误导：控卡那位看到会以为自己也吃了 629
        self.assertIn("每人 369 kcal", self.html)     # 轻食那份
        self.assertIn("每人 614 kcal", self.html)     # 照常那份 485+129
        self.assertIn("轻食那位 369 kcal", self.html)
        self.assertNotIn("人均 629 kcal", self.html)

    def test_zero_kcal_is_not_missing(self):
        # 纯悦是真 0 卡。0 是 falsy，顺手写成 `if kcal` 就会打成「未收录」
        self.assertIn("0 kcal", self.html)
        self.assertEqual(self.html.count("未收录"), 0)

    def test_spec_default_is_marked_approximate(self):
        # 菜单没写规格、按中份推定的薯条，热量不能装成精确值
        self.assertIn("≈289 kcal", self.html)

    def test_title_uses_the_majority_main(self):
        # 桌上两位吃鳕鱼堡，一位吃麦香鸡 —— 卡面主体是鳕鱼堡
        self.assertIn("「深海鳕鱼」", self.html)
        self.assertIn("分餐套", self.html)


class TestCache(unittest.TestCase):

    def test_ttl_table_only_read_only(self):
        import mcd_mcp
        for tool in mcd_mcp.CACHE_TTL:
            self.assertNotIn(tool, mcd_mcp._NO_CACHE, f"{tool} 是写操作，不许进 TTL 表")

    def test_write_tools_never_cached(self):
        import mcd_mcp
        for tool in ("create-order", "cancel-order", "delivery-create-address"):
            self.assertIn(tool, mcd_mcp._NO_CACHE)

    def test_now_time_never_cached(self):
        import mcd_mcp
        self.assertEqual(mcd_mcp.CACHE_TTL.get("now-time-info"), 0)


class TestIconPick(unittest.TestCase):
    """餐品图标归类：画错比画不出更糟。"""

    def test_carton_milk_not_drawn_as_tray(self):
        """「纯牛奶(盒装)」要画盒装奶，不能被纸袋规则里的「盒」抢走。"""
        self.assertEqual(I.pick("纯牛奶(盒装)"), "carton")
        self.assertEqual(I.pick("100% 苹果汁(盒装)"), "carton")

    def test_combo_names_still_get_tray(self):
        """去掉「盒」这个宽词后，套餐类还得认出纸袋。"""
        self.assertEqual(I.pick("人气经典随心配"), "tray")
        self.assertEqual(I.pick("全明星双人分享餐八件套"), "tray")


if __name__ == "__main__":
    unittest.main(verbosity=2)
