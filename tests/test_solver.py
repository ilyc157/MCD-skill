#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""求解器离线回归。全部断言基于 2026-10-22 对 mcp.mcd.cn 的实调快照。

    python3 tests/test_solver.py -v

不需要 token、不联网。如果这里挂了，说明脚本改坏了，不是服务端变了。
"""

import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
FIX = os.path.join(HERE, "fixtures")

from nutrition_solver import (  # noqa: E402
    Constraints,
    MenuItem,
    Nutrition,
    NutritionTable,
    _aggregate,
    attach_nutrition,
    is_share_item,
    item_roles,
    looks_like_combo,
    parse_combo_detail,
    parse_menu,
    solve,
    solve_group,
)

def _mk(code, name, price, kcal=300, protein=10, sodium=500, fat=10, carb=30):
    """合成一个营养齐全的菜单商品。"""
    return MenuItem(code=code, name=name, price=price,
                    nutrition=Nutrition(name=name, kcal=kcal, protein=protein,
                                        fat=fat, carb=carb, sodium=sodium))


def load_table():
    with open(os.path.join(FIX, "nutrition.toon"), encoding="utf-8") as f:
        return NutritionTable.from_toon(f.read())


def load_menu():
    with open(os.path.join(FIX, "menu_1950739_dinner.json"), encoding="utf-8") as f:
        return json.load(f)


def load_details():
    out = {}
    with open(os.path.join(FIX, "detail_9900005460.json"), encoding="utf-8") as f:
        cd = parse_combo_detail(json.load(f))
        out[cd.code] = cd
    return out


class TestNutritionTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = load_table()

    def test_parse_count_and_duplicates(self):
        self.assertEqual(len(self.t.by_name), 158)
        # 实测营养表有两条重名，必须记录而不是静默覆盖
        self.assertIn("小杯玉米杯", self.t.duplicates)
        self.assertIn("纯牛奶（盒装）", self.t.duplicates)

    def test_exact_match(self):
        n = self.t.lookup("麦香鱼")
        self.assertEqual(n.kcal, 325)
        self.assertEqual(n.protein, 16)
        self.assertEqual(n.sodium, 556)
        self.assertEqual(n.matched_by, "exact")

    def test_fullwidth_bracket_normalized(self):
        # 营养表「纯牛奶（盒装）」 vs 菜单「纯牛奶(盒装)」
        self.assertIsNotNone(self.t.lookup("纯牛奶（盒装）"))
        self.assertIsNotNone(self.t.lookup("纯牛奶(盒装)"))

    def test_spec_suffix_stripped(self):
        # 菜单「苹果片一袋(60g)」→ 营养表「苹果片」
        n = self.t.lookup("苹果片一袋(60g)")
        self.assertEqual(n.matched_from, "苹果片")
        self.assertEqual(n.matched_by, "spec")

    def test_dash_normalized(self):
        # 菜单「麦辣鸡翅2块」→ 营养表「麦辣鸡翅-2块」
        n = self.t.lookup("麦辣鸡翅2块")
        self.assertEqual(n.matched_from, "麦辣鸡翅-2块")
        self.assertEqual(n.kcal, 224)

    def test_serving_scaled_with_note(self):
        # 4 块没有官方数据，只能按 2 块折算，且必须留下说明
        n = self.t.lookup("麦辣鸡翅4块")
        self.assertEqual(n.kcal, 448.0)
        self.assertEqual(n.matched_by, "scaled")
        self.assertIn("2 倍折算", n.note)

    def test_ambiguous_generic_name_refuses(self):
        # 「可乐」同时是 中杯/大杯/小杯 的前缀 —— 猜错就是 100 kcal 的误差
        self.assertIsNone(self.t.lookup("可乐"))
        self.assertIsNone(self.t.lookup("雪碧"))

    def test_ambiguous_still_offers_candidates(self):
        # 拒答但必须给出候选，否则上层只能瞎猜
        opts = self.t.ambiguous_options("薯条")
        self.assertIn("中薯条", opts)
        self.assertIn("大薯条", opts)

    def test_combo_name_not_matched_to_main_item(self):
        # 「麦香鱼套餐」不能退化成「麦香鱼」——那会漏掉薯条和可乐
        self.assertIsNone(self.t.lookup("麦香鱼套餐"))

    def test_flavor_variant_not_guessed(self):
        # 椒盐风味 ≠ 原味，热量未必相同，不猜
        self.assertIsNone(self.t.lookup("那么大鸡排（椒盐风味）"))


class TestComboDecomposition(unittest.TestCase):
    def test_chosen_by_quantity(self):
        cd = load_details()["9900005460"]
        self.assertEqual(cd.chosen_names(), ["麦香鱼", "中薯条", "可乐中杯"])

    def test_aggregate_matches_sum(self):
        t, m, d = load_table(), load_menu(), load_details()
        items = attach_nutrition(parse_menu(m), t, d)
        combo = next(i for i in items if i.code == "9900005460")
        self.assertEqual(combo.nutrition.matched_by, "combo_decomposed")
        # 325 + 289 + 147
        self.assertEqual(combo.nutrition.kcal, 761)
        self.assertEqual(combo.nutrition.protein, 20)
        self.assertEqual(combo.nutrition.sodium, 721)

    def test_combo_without_detail_is_unknown_not_zero(self):
        t = load_table()
        items = attach_nutrition(parse_menu(load_menu()), t, details={})
        combo = next(i for i in items if i.code == "9900005460")
        self.assertFalse(combo.nutrition.known)
        self.assertEqual(combo.nutrition.kcal, None)


class TestMenuParsing(unittest.TestCase):
    def test_sku_count_and_fields(self):
        items = parse_menu(load_menu())
        self.assertEqual(len(items), 109)
        first = next(i for i in items if i.code == "9900005456")
        self.assertEqual(first.name, "麦辣鸡腿汉堡三件套")
        self.assertEqual(first.price, 33.5)
        self.assertEqual(first.original_price, 46.5)
        self.assertEqual(first.structure_saving, 13.0)

    def test_combo_detection(self):
        self.assertTrue(looks_like_combo("麦香鱼套餐"))
        self.assertTrue(looks_like_combo("巨无霸三件套"))
        self.assertTrue(looks_like_combo("人气超值四件套随心选"))
        self.assertFalse(looks_like_combo("麦香鱼"))
        self.assertFalse(looks_like_combo("中薯条"))

    def test_category_tags_carried(self):
        items = parse_menu(load_menu())
        combo = next(i for i in items if i.code == "9900005460")
        self.assertTrue(any("餐" in t for t in combo.tags))


class TestSolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t, m, d = load_table(), load_menu(), load_details()
        cls.items = attach_nutrition(parse_menu(m), t, d)

    def test_kcal_ceiling_respected(self):
        c = Constraints(kcal_max=500, sodium_max=2000)
        plans = solve(self.items, c, top_k=5)
        self.assertTrue(plans)
        for p in plans:
            self.assertLessEqual(p.nutrition.kcal, 500)

    def test_protein_floor_respected(self):
        c = Constraints(protein_min=30, sodium_max=2000)
        for p in solve(self.items, c, top_k=5):
            self.assertGreaterEqual(p.nutrition.protein, 30)

    def test_sodium_ceiling_respected(self):
        c = Constraints(sodium_max=600)
        for p in solve(self.items, c, top_k=5):
            self.assertLessEqual(p.nutrition.sodium, 600)

    def test_budget_respected(self):
        c = Constraints(budget_max=25, sodium_max=2000)
        for p in solve(self.items, c, top_k=5):
            self.assertLessEqual(p.total, 25)

    def test_exclude_keyword(self):
        c = Constraints(exclude_keywords=["辣"], sodium_max=2000)
        for p in solve(self.items, c, top_k=8):
            for i in p.items:
                self.assertNotIn("辣", i.name)

    def test_exclude_checks_combo_children(self):
        """忌口要看套餐子项，不能只看套餐名。

        「全明星双人分享餐八件套」名字里没有辣字，里面却装着麦辣鸡腿汉堡和
        麦辣鸡翅 —— 不吃辣的人收到这一桌，等于忌口没生效。
        """
        share = _mk("s1", "全明星双人分享餐八件套", 100, kcal=800, protein=30)
        share.is_combo = True
        share.children = ["麦辣鸡腿汉堡", "板烧鸡腿堡", "中薯条"]
        plain = _mk("s2", "板烧鸡腿堡", 25, kcal=390, protein=23)
        c = Constraints(exclude_keywords=["辣"], sodium_max=2000)
        plans = solve([share, plain], c, top_k=5)
        self.assertTrue(plans)
        for p in plans:
            self.assertNotIn("分享餐", "".join(i.name for i in p.items))

    def test_structure_requires_main_and_drink(self):
        c = Constraints(structure="with_drink", sodium_max=2000)
        plans = solve(self.items, c, top_k=5)
        self.assertTrue(plans)
        for p in plans:
            names = "".join(i.name for i in p.items)
            self.assertTrue(
                any(k in names for k in ("堡", "卷", "鸡", "麦满分")),
                f"缺主食：{names}",
            )

    def test_fingerprint_dedup(self):
        c = Constraints(sodium_max=5000)
        plans = solve(self.items, c, top_k=50)
        fps = [(p.nutrition.kcal, p.nutrition.protein) for p in plans]
        self.assertEqual(len(fps), len(set(fps)), "营养指纹重复的方案不应共存")

    def test_partial_nutrition_excluded(self):
        """缺子项营养的套餐不能进解 —— 否则热量必然偏低。"""
        c = Constraints(kcal_max=2000, sodium_max=5000)
        for p in solve(self.items, c, top_k=50):
            for i in p.items:
                if i.nutrition:
                    self.assertNotEqual(i.nutrition.matched_by, "combo_partial")

    def test_aggregate_marker_is_honest(self):
        """聚合方案不能谎称 exact。"""
        for p in solve(self.items, Constraints(sodium_max=5000), top_k=5):
            self.assertEqual(p.nutrition.matched_by, "aggregate")

    def test_infeasible_returns_empty(self):
        c = Constraints(kcal_max=50, protein_min=40)
        self.assertEqual(solve(self.items, c), [])


class TestComboPartialNote(unittest.TestCase):
    def test_missing_qty_note_no_string_repeat(self):
        # 回归：c["name"] * qty 是字符串乘法，会拼出「神秘食品神秘食品」
        t = load_table()
        detail = {"code": "T1", "name": "测试套餐", "rounds": [
            {"id": 1, "name": "主品", "choices": [
                {"code": "X1", "name": "不存在的神秘食品", "quantity": 2, "isDefault": 1},
                {"code": "1600", "name": "麦香鱼", "quantity": 1, "isDefault": 1}]}]}
        agg = _aggregate(parse_combo_detail(detail), t)
        self.assertEqual(agg.matched_by, "combo_partial")
        self.assertIn("不存在的神秘食品×2", agg.note)
        self.assertNotIn("神秘食品神秘食品", agg.note)

class TestFieldLevelNullGuard(unittest.TestCase):
    """铁律 5 的字段级执行：约束落到哪个字段，哪个字段就不许缺。"""

    def test_sodium_null_excluded_when_capped(self):
        bad = _mk("B1", "双层吉士汉堡", 23, kcal=429, protein=24, sodium=None)
        good = _mk("G1", "麦香鱼", 19.5, kcal=325, protein=16, sodium=556)
        plans = solve([bad, good], Constraints(sodium_max=1500), top_k=5)
        for p in plans:
            self.assertNotIn("B1", [i.code for i in p.items])

    def test_sodium_null_allowed_when_unconstrained(self):
        # 不设钠限时可以进解 —— 卡片上标「未收录」，不是替用户戒盐
        bad = _mk("B1", "双层吉士汉堡", 23, kcal=429, protein=24, sodium=None)
        plans = solve([bad], Constraints(sodium_max=None), top_k=5)
        self.assertTrue(plans)

    def test_protein_null_excluded_when_floored(self):
        bad = _mk("B1", "神秘汉堡", 20, kcal=400, protein=None, sodium=500)
        self.assertEqual(solve([bad], Constraints(protein_min=10), top_k=5), [])

class TestGroupSolve(unittest.TestCase):
    """赛题输入项「人数」必须体现在求解结果里。"""

    @staticmethod
    def _menu():
        return [
            _mk("M1", "麦香鱼", 19.5, 325, 16, 556),
            _mk("M2", "双层吉士汉堡", 23, 429, 24, 980),
            _mk("D1", "可乐中杯", 9, 147, 0, 0),
            _mk("D2", "纯牛奶(盒装)", 11, 169, 9, 240),
            _mk("S1", "苹果片", 6, 35, 0, 0),
        ]

    def test_people_must_appear_in_quantities(self):
        # 3 人 90 元：旧行为是给出一只汉堡（只够 1 人），现在必须人人有份
        plans = solve_group(self._menu(), Constraints(budget_max=90), 3,
                            objective="value", top_k=5)
        self.assertTrue(plans)
        for p in plans:
            self.assertLessEqual(p.total, 90)
            self.assertTrue(p.quantities, "多人方案必须带份数")
            if p.route == "per_person":
                self.assertTrue(all(q == 3 for q in p.quantities.values()))

    def test_group_total_price_is_real(self):
        for p in solve_group(self._menu(), Constraints(budget_max=90), 3, top_k=3):
            expect = round(sum(i.price * p.qty_of(i.code) for i in p.items), 2)
            self.assertEqual(p.total, expect)

    def test_share_route_with_drinks(self):
        menu = self._menu() + [_mk("SH1", "全明星双人分享餐八件套", 49.9, 1200, 45, 2000)]
        plans = solve_group(menu, Constraints(budget_max=100), 2, top_k=5)
        share = [p for p in plans if p.route == "share"]
        self.assertTrue(share, "有分享装且预算够时必须出分享路径方案")
        p = share[0]
        self.assertEqual(p.qty_of("SH1"), 1)
        drink_qty = sum(p.qty_of(i.code) for i in p.items
                        if "drink" in item_roles(i.name))
        self.assertEqual(drink_qty, 2, "分享路径默认人人有饮品")

    def test_share_without_nutrition_excluded(self):
        menu = self._menu() + [MenuItem(code="SH9", name="神秘全家桶", price=59.0)]
        for p in solve_group(menu, Constraints(budget_max=200), 4, top_k=5):
            self.assertNotIn("SH9", [i.code for i in p.items])

    def test_per_capita_nutrition_semantics(self):
        # kcal_max=500 是人均：3 人方案总量可以到 1500，预算仍是总额
        plans = solve_group(self._menu(), Constraints(kcal_max=500, budget_max=200),
                            3, top_k=5)
        self.assertTrue(plans)
        for p in plans:
            self.assertLessEqual(p.nutrition.kcal, 500 * 3)

    def test_plan_to_dict_carries_quantity(self):
        p = solve_group(self._menu(), Constraints(budget_max=90), 3, top_k=1)[0]
        d = p.to_dict()
        self.assertEqual(d["route"], "per_person")
        self.assertTrue(all(it["quantity"] == 3 for it in d["items"]))

    def test_is_share_item(self):
        self.assertTrue(is_share_item("全明星双人分享餐八件套"))
        self.assertTrue(is_share_item("开心亲子双人餐"))
        self.assertFalse(is_share_item("汉堡开心乐园餐"))  # 单人儿童餐
        self.assertFalse(is_share_item("麦香鱼"))


class TestSplitRoute(unittest.TestCase):
    """同桌有人控卡：一人轻食、其余照常、桌上加一份能分的。

    A/B 两条路径都是「全员同款 ×N」，会把一个人的减脂摊给整桌 ——
    实测 3 人午餐给出「三个鳕鱼堡 + 三盒牛奶」。
    """

    @staticmethod
    def _menu():
        return [
            _mk("M1", "板烧鸡腿堡", 23.5, 391, 23, 780),
            _mk("M2", "双层吉士汉堡", 23, 429, 24, 980),
            _mk("M3", "双层深海鳕鱼堡", 24, 485, 28, 917),
            _mk("D1", "纯悦", 5, 0, 0, 0),
            _mk("D2", "纯牛奶(盒装)", 11, 129, 7, 73),
            _mk("S1", "薯条", 14, 289, 4, 165),
        ]

    def _c(self, **kw):
        base = dict(kcal_max=700, sodium_max=1500, budget_max=130,
                    exclude_keywords=["辣"], kcal_extra=490)
        base.update(kw)
        return Constraints(**base)

    def test_split_only_when_declared(self):
        # 不声明「控卡的是部分人」时不出差异化方案，人均语义才是对的
        menu = self._menu()
        plain = solve_group(menu, self._c(kcal_extra=0.0), 3, top_k=5)
        self.assertTrue(plain)
        self.assertFalse([p for p in plain if p.route == "split"])

        declared = solve_group(menu, self._c(), 3, top_k=5)
        self.assertTrue([p for p in declared if p.route == "split"])

    def test_split_assigns_every_item(self):
        plans = [p for p in solve_group(self._menu(), self._c(), 3, top_k=5)
                 if p.route == "split"]
        self.assertTrue(plans)
        for p in plans:
            for i in p.items:
                self.assertIn(p.assign.get(i.code), ("light", "normal", "share"),
                              f"{i.name} 没有归属，卡片上就分不清是谁的")
            self.assertEqual(sum(1 for c in p.assign.values() if c == "light"), 2,
                             "轻食那位要有主品也有喝的")
            self.assertEqual(sum(1 for c in p.assign.values() if c == "normal"), 2,
                             "另外两位各一件")

    def test_split_light_person_under_cap(self):
        # 全桌总量达标、但控卡那位自己吃了 900 kcal 的方案是自欺
        for p in solve_group(self._menu(), self._c(), 3, top_k=5):
            if p.route != "split":
                continue
            own = sum(i.nutrition.kcal for i in p.items
                      if p.assign.get(i.code) == "light")
            self.assertLessEqual(own, 700)

    def test_split_has_two_distinct_mains(self):
        # 轻食和照常不能是同一款，否则「差异化」是空的
        for p in solve_group(self._menu(), self._c(), 3, top_k=5):
            if p.route != "split":
                continue
            light = {i.name for i in p.items if p.assign.get(i.code) == "light"
                     and "main" in item_roles(i.name)}
            normal = {i.name for i in p.items if p.assign.get(i.code) == "normal"
                      and "main" in item_roles(i.name)}
            self.assertTrue(light and normal)
            self.assertFalse(light & normal)

    def test_split_does_not_beat_itself_into_budget(self):
        # 预算压到只够两份时，宁可不出差异化，也不能超预算
        for p in solve_group(self._menu(), self._c(budget_max=45), 3, top_k=5):
            self.assertLessEqual(p.total, 45)

    def test_two_plans_are_not_the_same_meal(self):
        # 「你吃双吉、他们吃鳕鱼堡」和「你吃鳕鱼堡、他们吃双吉」是同一桌菜
        plans = solve_group(self._menu(), self._c(), 3, top_k=2)
        self.assertEqual(len(plans), 2)
        a = {i.name for i in plans[0].items}
        b = {i.name for i in plans[1].items}
        self.assertNotEqual(a, b, "两套方案不能是同一批商品的两种摆法")

class TestPoolCapAndPruning(unittest.TestCase):
    def test_huge_pool_capped_and_fast(self):
        menu = []
        for k in range(150):
            role = "汉堡" if k % 3 == 0 else "可乐" if k % 3 == 1 else "薯条"
            menu.append(_mk(f"K{k}", f"{role}{k}号", 8 + k % 30,
                            kcal=200 + k, protein=5 + k % 20, sodium=100 + k))
        t0 = time.time()
        plans = solve(menu, Constraints(sodium_max=5000), top_k=5)
        dt = time.time() - t0
        self.assertLess(dt, 3.0, f"pool=150 求解 {dt:.1f}s，截断没生效")
        self.assertTrue(plans)

    def test_pruning_matches_bruteforce(self):
        # 剪枝只是跳过超预算组合，不许漏解：与暴力枚举的可行解指纹集一致
        menu = [_mk(f"P{k}", f"汉堡{k}号", 5 + k * 0.7, 300 + k * 3, 8 + k % 15, 200)
                for k in range(20)]
        pruned = solve(menu, Constraints(budget_max=25, sodium_max=9000), top_k=10000)
        brute = [p for p in solve(menu, Constraints(sodium_max=9000), top_k=10000)
                 if p.total <= 25]
        self.assertEqual({p.fingerprint for p in pruned},
                         {p.fingerprint for p in brute})

if __name__ == "__main__":
    unittest.main(verbosity=2)
