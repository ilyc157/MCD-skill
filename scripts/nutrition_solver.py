#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳配餐营养求解器（纯算法，不联网）。

输入：营养表 TOON、门店菜单、套餐详情（可选）
输出：满足约束的候选方案，每个方案带完整的营养构成与来源标记

三条设计原则：
1. 宁可标 unknown，也不把缺失当 0 —— 否则控卡方案会算出假热量。
2. 歧义不猜。`可乐` 同时是 中杯/大杯/小杯 的前缀，猜错就是 100 kcal 的误差。
3. 每个数字都要能追溯：matched_by 记录这条营养数据是精确命中的、规格折算的、还是拆套餐得来的。

用法见 tests/test_solver.py。
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from dataclasses import dataclass, field

# 规格/量词字符集：出现在名称尾部但不改变食物本体的部分
_SPEC_CHARS = set("0123456789abcdefghijklmnopqrstuvwxyz()（）gmlML克千卡块片张个份袋杯盒装粒只支瓶听包")
_SPEC_WORDS = ("大杯", "中杯", "小杯", "大", "中", "小", "迷你", "一袋", "盒装", "装", "杯", "块", "个", "份", "张")
_COUNT_RE = re.compile(r"(\d+)\s*(块|片|个|只|支|粒|张|份)")

# 套餐名启发式：只有这些词才值得花一次 query-meal-detail 去拆
# 「组合」是早餐套餐的命名（脆香油条组合、吉士蛋麦满分组合），漏掉它等于整个早餐时段无营养
_COMBO_HINT_RE = re.compile(r"套餐|组合|件套|随心选|随心配|分享餐|双人餐|单人餐|亲子餐|乐园餐|全明星")

# 分享装启发式：多人方案的核心单品。「开心乐园餐」是单人儿童餐，不算分享装
_SHARE_HINT_RE = re.compile(r"桶|分享|双人|四人|多人|全家|亲子")

def is_share_item(name: str) -> bool:
    """是否为多人分享装（桶/分享盒/双人餐等），多人求解路径 B 的核心。"""
    return bool(_SHARE_HINT_RE.search(name or ""))


def looks_like_combo(name: str, tags: list[str] | None = None) -> bool:
    """是否需要拉详情。宁可多判几个，也不要漏掉一个真套餐 —— 漏掉就等于没有热量。"""
    if _COMBO_HINT_RE.search(name or ""):
        return True
    return any(t in ("套餐", "超值") for t in (tags or []))


# 餐盘角色：用来判断一个组合"像不像一顿饭"。纯按蛋白效率排序会选出单只汉堡，
# 用户要的是「有主食有喝的」，所以结构完整性必须单独约束。
_ROLE_KEYWORDS = {
    "main": ("汉堡", "堡", "卷", "麦满分", "麦香鱼", "麦香鸡", "鸡腿", "鸡排", "脆汁鸡", "鸡球",
             "巨无霸", "双吉", "板烧", "安格斯", "不素之霸",
             "麦乐鸡", "鸡翅", "鸡块", "饭", "分享盒", "小食盘", "薯条",
             # 早餐主力：粥/油条/松饼/大饼在早餐就是主食，不吃它们就没得吃
             "粥", "油条", "松饼", "大饼", "卷饼", "营养卷",
             # 套餐/组合/乐园餐一律含主品，名字里不含「堡」也当主食
             # （实测漏判：麦香鸡 ¥16.5、人气经典随心配 ¥14.9 曾被判成无角色，导致正餐无解）
             "套餐", "组合", "随心配", "三件套", "四件套", "乐园餐", "全明星"),
    "drink": ("可乐", "雪碧", "咖啡", "红茶", "奶铁", "牛奶", "豆浆", "果汁", "美式",
              "拿铁", "卡布", "怡泉", "柠", "橙", "雪冰", "玛奇朵", "阿芙佳朵", "冰咖",
              "红茶", "纯悦"),
    "side": ("薯条", "玉米杯", "苹果片", "麦乐鸡", "鸡块", "派", "薯饼", "沙拉", "鸡翅",
             # 甜品按配品走：卡片上不写「单点」，圆筒/新地本来就是配一份
             "冰淇淋", "新地", "圆筒", "旋风", "甜筒", "麦炫酷"),
}


def item_roles(name: str) -> set[str]:
    return {role for role, kws in _ROLE_KEYWORDS.items() if any(k in name for k in kws)}


def norm_name(s: str) -> str:
    """NFKC + 去空白 + 小写。全半角括号、空格差异都在这一步抹平。

    实测：营养表有「纯牛奶（盒装）」、菜单有「纯牛奶(盒装)」，NFKC 后一致。
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _strip_dashes(s: str) -> str:
    """「麦辣鸡翅-2块」→「麦辣鸡翅2块」。营养表用连字符分隔份数，菜单不用。"""
    return re.sub(r"[-—－–]", "", s)


def _parse_serving(name: str) -> tuple[str, int | None, str | None]:
    """拆出 (本体, 份数, 单位)。

    「麦辣鸡翅4块」→ ("麦辣鸡翅", 4, "块")
    「中薯条」    → ("中薯条", None, None)
    """
    n = _strip_dashes(norm_name(name))
    m = _COUNT_RE.search(n)
    if m:
        return n[: m.start()] + n[m.end():], int(m.group(1)), m.group(2)
    return n, None, None


def _rest_is_spec(rest: str) -> bool:
    """剩余部分是否只是规格描述（不改变食物本体）。"""
    if not rest:
        return True
    for w in _SPEC_WORDS:
        rest = rest.replace(w, "")
    rest = rest.strip("()（）")
    return all(c in _SPEC_CHARS for c in rest)


# --------------------------------------------------------------------------- 营养

@dataclass
class Nutrition:
    """一份餐品的营养。数值单位：kcal / g / mg。"""

    name: str
    kcal: float | None = None
    protein: float | None = None
    fat: float | None = None
    carb: float | None = None
    sodium: float | None = None
    calcium: float | None = None
    kj: float | None = None
    # 来源追溯：exact / spec / scaled / combo_direct / combo_decomposed
    matched_by: str = "exact"
    matched_from: str = ""
    scale: float = 1.0
    note: str = ""

    @property
    def known(self) -> bool:
        return self.kcal is not None

    def scaled_copy(self, factor: float, note: str = "") -> "Nutrition":
        def m(v):
            return None if v is None else round(v * factor, 1)

        return Nutrition(
            name=self.name,
            kcal=m(self.kcal), protein=m(self.protein), fat=m(self.fat),
            carb=m(self.carb), sodium=m(self.sodium), calcium=m(self.calcium),
            kj=m(self.kj), matched_by="scaled", matched_from=self.matched_from,
            scale=factor, note=note or f"按 {factor:g} 倍折算",
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kcal": self.kcal, "protein": self.protein,
            "fat": self.fat, "carb": self.carb, "sodium": self.sodium,
            "calcium": self.calcium, "matched_by": self.matched_by,
            "matched_from": self.matched_from, "scale": self.scale, "note": self.note,
        }


class NutritionTable:
    """官方营养表。原始结构是 TOON 字符串，不是 JSON：

        [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
          猪柳麦满分,null,1288,308,16,16,24,781,213
    """

    def __init__(self):
        self.by_name: dict[str, dict] = {}
        self.all_names: list[str] = []
        self.duplicates: list[str] = []

    @classmethod
    def from_toon(cls, toon: str) -> "NutritionTable":
        t = cls()
        lines = [ln for ln in (toon or "").splitlines() if ln.strip()]
        if not lines:
            return t
        # 表头行以 [N]{...}: 开头，跳过
        body = lines[1:] if lines[0].lstrip().startswith("[") else lines
        for ln in body:
            parts = [p.strip() for p in ln.strip().split(",")]
            if len(parts) != 9:
                continue
            rec = {
                "productName": parts[0],
                "energyKj": _num(parts[2]),
                "energyKcal": _num(parts[3]),
                "protein": _num(parts[4]),
                "fat": _num(parts[5]),
                "carbohydrate": _num(parts[6]),
                "sodium": _num(parts[7]),
                "calcium": _num(parts[8]),
            }
            key = norm_name(parts[0])
            if key in t.by_name:
                t.duplicates.append(parts[0])
                continue
            t.by_name[key] = rec
            t.all_names.append(parts[0])
        return t

    # ---------------------------------------------------------------- 查表
    def lookup(self, raw_name: str) -> Nutrition | None:
        """三层对齐：精确 → 规格折算 → 份数折算。歧义一律返回 None。"""
        if not raw_name:
            return None
        n = norm_name(raw_name)

        # L0 精确
        if n in self.by_name:
            return self._make(n, "exact")

        # L0.5 只差连字符（麦辣鸡翅2块 ↔ 麦辣鸡翅-2块）
        nd = _strip_dashes(n)
        for key in self.by_name:
            if _strip_dashes(key) == nd:
                return self._make(key, "spec")

        # L1 规格补全：营养表名 = 菜单名 + 纯规格后缀（苹果片 ← 苹果片一袋(60g)）
        cands = [
            key for key in self.by_name
            if n.startswith(key) and _rest_is_spec(n[len(key):])
        ]
        if len(cands) == 1:
            return self._make(cands[0], "spec")
        if len(cands) > 1:
            return None  # 例如「可乐」→ 中杯/大杯/小杯，不猜

        # L1b 反向：菜单名 = 营养表名 + 纯规格后缀（纯牛奶 ← 纯牛奶(盒装)）
        cands = [
            key for key in self.by_name
            if key.startswith(n) and _rest_is_spec(key[len(n):])
        ]
        if len(cands) == 1:
            return self._make(cands[0], "spec")
        if len(cands) > 1:
            return None

        # L1c 规格前置：「薯条」← 「中薯条」。营养表把小份/中份/大份写在前缀，
        # 菜单上不带规格时前后缀都对不上，整类小食（薯条/薯饼/玉米杯）因此全被
        # 判成未收录 —— 三人聚餐连一份能摆中间分的都出不来。
        # 只认「中」这一档：麦当劳的默认规格就是中份（中薯条/中杯可乐），
        # 菜单上不带规格的那款就是它。仍然标 spec_default 而不是 spec：
        # 这是规格推定，卡片和复述都得能看出这一行不是精确命中。
        for spec in ("中份", "中杯", "中"):
            hit = f"{spec}{n}"
            if hit in self.by_name:
                return self._make(hit, "spec_default")

        # L2 份数折算：本体相同、份数不同（麦辣鸡翅4块 → 麦辣鸡翅-2块 ×2）
        base, count, unit = _parse_serving(raw_name)
        if count:
            for key in self.by_name:
                kbase, kcount, kunit = _parse_serving(key)
                if kbase == base and kcount and kunit == unit and kcount != count:
                    factor = count / kcount
                    out = self._make(key, "scaled")
                    if out:
                        return out.scaled_copy(
                            factor,
                            f"官方营养表只有「{key}」，本项按 {count}{unit} ÷ {kcount}{unit} = {factor:g} 倍折算",
                        )
        return None

    def ambiguous_options(self, raw_name: str) -> list[str]:
        """返回可能的候选名，用于告诉用户「请指定规格」。

        「薯条」本身不是任何营养条目的前缀，但它是「中薯条/大薯条/小薯条」的后缀，
        所以前后缀都要试，否则候选给空、AI 就只能瞎猜。
        """
        n = norm_name(raw_name)
        out = []
        for k in self.all_names:
            nk = norm_name(k)
            if nk.startswith(n) or n.startswith(nk) or nk.endswith(n):
                out.append(k)
        return out

    def _make(self, key: str, matched_by: str) -> Nutrition | None:
        rec = self.by_name.get(key)
        if not rec:
            return None
        kcal = rec["energyKcal"]
        note = ""
        if kcal is None and rec["energyKj"] is not None:
            # 实测存在 energyKcal 缺失但 energyKj 有值的情况
            kcal = round(rec["energyKj"] * 0.239, 1)
            note = "energyKcal 缺失，按 kJ×0.239 换算"
        return Nutrition(
            name=rec["productName"], kcal=kcal, protein=rec["protein"],
            fat=rec["fat"], carb=rec["carbohydrate"], sodium=rec["sodium"],
            calcium=rec["calcium"], kj=rec["energyKj"],
            matched_by=matched_by, matched_from=rec["productName"], note=note,
        )


def _num(s: str):
    if s in ("", "null", "NULL", "None", None):
        return None
    try:
        v = float(s)
        return int(v) if v == int(v) else v
    except ValueError:
        return None


# --------------------------------------------------------------------------- 菜单

@dataclass
class MenuItem:
    """一个可下单 SKU（单点或套餐）。价格单位：元。"""

    code: str
    name: str
    price: float
    original_price: float = 0.0
    tags: list[str] = field(default_factory=list)
    nutrition: Nutrition | None = None
    is_combo: bool = False
    children: list[str] = field(default_factory=list)
    image: str = ""          # 官方产品图（menu-img.mcd.cn），卡片用它做餐品微缩图

    @property
    def structure_saving(self) -> float:
        """结构省：划线价 - 现价。仅当 original > current 时有意义。"""
        return round(max(0.0, self.original_price - self.price), 2)

    @property
    def protein_per_yuan(self) -> float | None:
        if not self.nutrition or self.nutrition.protein is None or self.price <= 0:
            return None
        return round(self.nutrition.protein / self.price, 2)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "price": self.price,
            "original_price": self.original_price,
            "image": self.image,
            "structure_saving": self.structure_saving,
            "is_combo": self.is_combo, "children": self.children,
            "protein_per_yuan": self.protein_per_yuan,
            "nutrition": self.nutrition.to_dict() if self.nutrition else None,
        }


def parse_menu(menu_data: dict) -> list[MenuItem]:
    """把 query-meals 的 data 转成 MenuItem 列表。

    真实结构（实测）：
        data.categories[].meals[].code   ← 只有 code 引用 + tags
        data.meals = {code: {name, currentPrice, originalPrice, ...}}   ← dict，不是 list
    直接遍历 meals 会丢分类归属，所以两边都要读。
    """
    meals = menu_data.get("meals") or {}
    tags_by_code: dict[str, list[str]] = {}
    order: list[str] = []

    for cat in menu_data.get("categories") or []:
        cat_name = (cat.get("name") or "").strip()
        for m in cat.get("meals") or []:
            code = str(m.get("code") or "")
            if not code:
                continue
            tags = list(m.get("tags") or [])
            if cat_name:
                tags.append(cat_name)
            tags_by_code.setdefault(code, []).extend(tags)
            if code not in order:
                order.append(code)

    for code in meals:
        if code not in order:
            order.append(code)

    items = []
    for code in order:
        raw = meals.get(code)
        if not raw:
            continue
        name = (raw.get("name") or "").strip()
        if not name:
            continue
        price = parse_price(raw.get("currentPrice"))
        tags = list(dict.fromkeys(tags_by_code.get(code, [])))
        items.append(
            MenuItem(
                code=code, name=name, price=price,
                original_price=parse_price(raw.get("originalPrice")),
                tags=tags,
                # 必须在 parse_menu 阶段就判出来：拉套餐详情要靠它决定拆哪个
                is_combo=looks_like_combo(name, tags),
                image=(raw.get("image") or "").strip(),
            )
        )
    return items


def parse_price(v) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- 套餐拆解

@dataclass
class ComboDetail:
    code: str
    name: str
    rounds: list[dict]  # [{round, name, chosen: [{code, name, quantity}]}]

    def chosen_names(self) -> list[str]:
        out = []
        for r in self.rounds:
            for c in r["chosen"]:
                out.extend([c["name"]] * int(c.get("quantity") or 1))
        return out


def parse_combo_detail(detail_data: dict) -> ComboDetail:
    """从 query-meal-detail 的 data 提取已选子项。

    取 quantity > 0 的子项。实测 12 个套餐里 quantity>0 与 isDefault==1 完全一致，
    但语义上 quantity 才是「当前已选数量」，因此以它为准（见 references/mcp-contract.md）。
    """
    rounds = []
    for r in detail_data.get("rounds") or []:
        chosen = [
            {"code": str(c.get("code")), "name": (c.get("name") or "").strip(),
             "quantity": int(c.get("quantity") or 0)}
            for c in (r.get("choices") or [])
            if int(c.get("quantity") or 0) > 0
        ]
        if chosen:
            rounds.append({
                "round": str(r.get("id")),
                "name": (r.get("name") or "").strip(),
                "chosen": chosen,
            })
    return ComboDetail(
        code=str(detail_data.get("code") or ""),
        name=(detail_data.get("name") or "").strip(),
        rounds=rounds,
    )


def attach_nutrition(
    items: list[MenuItem],
    table: NutritionTable,
    details: dict[str, ComboDetail] | None = None,
    keep_unknown: bool = True,
) -> list[MenuItem]:
    """给每个 SKU 挂营养。套餐走三级策略。

    实测依据（单店 109 SKU / 12 套餐）：
      - 单点 SKU 名称精确命中营养表仅 16.5%，因为很多是「可乐」「薯条」这类通用名
      - 套餐拆解后子项命中率 87.2%
    → 套餐必须优先拆解，这是拿到可信热量的唯一可靠路径。
    """
    details = details or {}
    for it in items:
        # 详情存在 = 铁证是套餐；名称启发式可能误判，这里是最终裁决
        it.is_combo = it.is_combo or it.code in details

        if it.code in details:
            cd = details[it.code]
            agg = _aggregate(cd, table)
            if agg:
                it.nutrition = agg
                it.children = [c["name"] for r in cd.rounds for c in r["chosen"]]
                continue

        direct = table.lookup(it.name)
        if direct:
            direct.matched_by = "combo_direct" if it.is_combo else direct.matched_by
            it.nutrition = direct
            continue

        if it.is_combo and it.code not in details:
            # 有套餐但在营养表里查不到，且这次没拉详情 → 明确标未知，不猜
            it.nutrition = Nutrition(
                name=it.name, matched_by="unknown",
                note="套餐未拆解且营养表无同名条目，需 query-meal-detail 才能算热量",
            )
        elif not keep_unknown:
            pass

    return items


def _aggregate(cd: ComboDetail, table: NutritionTable) -> Nutrition | None:
    """把套餐子项营养累加。任一子项缺失 → 整体标不完整。"""
    total = Nutrition(name=cd.name, kcal=0.0, protein=0.0, fat=0.0, carb=0.0,
                      sodium=0.0, calcium=0.0, kj=0.0,
                      matched_by="combo_decomposed")
    missing: list[str] = []
    notes: list[str] = []

    for r in cd.rounds:
        for c in r["chosen"]:
            qty = int(c.get("quantity") or 1)
            n = table.lookup(c["name"])
            if not n or not n.known:
                # 注意：c["name"] * qty 会把名字重复 qty 遍（字符串乘法），不是列表
                missing.append(c["name"] if qty == 1 else f"{c['name']}×{qty}")
                continue
            if qty > 1:
                n = n.scaled_copy(qty, f"{c['name']} × {qty}")
            for f in ("kcal", "protein", "fat", "carb", "sodium", "calcium"):
                cur = getattr(total, f)
                add = getattr(n, f)
                if add is None:
                    continue
                setattr(total, f, round((cur or 0) + add, 1))
            if n.matched_by in ("scaled", "spec") and n.note:
                notes.append(n.note)

    if missing:
        total.matched_by = "combo_partial"
        total.note = "；".join(
            [f"缺营养数据：{'、'.join(dict.fromkeys(missing))}"] + list(dict.fromkeys(notes))
        )
    elif notes:
        total.note = "；".join(dict.fromkeys(notes))

    return total


# --------------------------------------------------------------------------- 约束

@dataclass
class Constraints:
    kcal_max: float | None = None
    kcal_min: float | None = None
    protein_min: float | None = None
    sodium_max: float | None = 1500.0
    budget_max: float | None = None
    budget_min: float | None = None
    exclude_keywords: list[str] = field(default_factory=list)   # 忌口：辣/牛/炸...
    include_keywords: list[str] = field(default_factory=list)   # 必须含：咖啡...
    max_items: int = 4
    min_items: int = 1
    require_full_nutrition: bool = True   # 缺营养数据的商品直接不参与求解
    structure: str | None = None          # None / main / with_drink / full
    # 同桌只有部分人控卡时，全桌热量上限 = 人均 × 人数 + 这个额度（kcal）。
    # 「人均 700 × 3 人」是「三个人都吃 700」，但一个人减脂时他少吃的那部分
    # 不该被浪费 —— 真实是 550 + 950 + 950。差额由调用方算好传进来，
    # 求解器不猜有几个人在控卡（那是调用方知道的事）。
    kcal_extra: float = 0.0

    @property
    def strict_nutrition(self) -> bool:
        """是否必须营养完整才允许进解。

        只在用户真的设了热量/蛋白目标时才严格 —— 缺数据的商品会让控卡结果失真。
        没设目标时（只想吃饱、控预算）不该因营养缺失被排除，如实标「未收录」即可。
        实测依据：早餐组合的配饮「鲜萃冰咖」不在营养表里，导致全部早餐组合被判
        combo_partial 整体排除，30 元 3 人的早餐只剩「脆香油条×3」可选。
        """
        if not self.require_full_nutrition:
            return False
        return (self.kcal_max is not None or self.kcal_min is not None
                or self.protein_min is not None)

    def describe(self) -> str:
        bits = []
        if self.kcal_max is not None:
            bits.append(f"≤{self.kcal_max:g} kcal")
        if self.kcal_min is not None:
            bits.append(f"≥{self.kcal_min:g} kcal")
        if self.protein_min is not None:
            bits.append(f"蛋白≥{self.protein_min:g}g")
        if self.sodium_max is not None:
            bits.append(f"钠≤{self.sodium_max:g}mg")
        if self.budget_max is not None:
            bits.append(f"预算¥{self.budget_max:g}")
        if self.exclude_keywords:
            bits.append("忌：" + "/".join(self.exclude_keywords))
        if self.structure == "with_drink":
            bits.append("需含主食+饮品")
        elif self.structure == "full":
            bits.append("需含主食+饮品/小食")
        return " · ".join(bits) or "无约束"


@dataclass
class Plan:
    items: list[MenuItem]
    nutrition: Nutrition
    total: float
    total_original: float
    objective: str
    score: float
    notes: list[str] = field(default_factory=list)
    # code → 份数，缺省 1。多人方案里同一件商品只出现一次，用 quantities 表达 ×N
    quantities: dict[str, int] = field(default_factory=dict)
    # 方案来源：single=单人组合 / per_person=人手一份×N / share=分享装+饮品
    #          mixed=两种主品各管一部分人 / split=差异化（一人轻食、其余照常、共享一份）
    route: str = "single"
    # code → 这件的归属：light（控卡那位）/ normal（其余人）/ share（大家分）。
    # 空 dict = 全员同款，卡片按平铺渲染。多人不同款时必须给，否则卡片上
    # 看不出「哪份是谁的」—— 一桌子菜摊平了列出来，等于让用户自己猜。
    assign: dict[str, str] = field(default_factory=dict)

    def qty_of(self, code: str) -> int:
        return self.quantities.get(code, 1)

    @property
    def structure_saving(self) -> float:
        return round(max(0.0, self.total_original - self.total), 2)

    @property
    def protein_per_yuan(self) -> float | None:
        p = self.nutrition.protein
        if p is None or self.total <= 0:
            return None
        return round(p / self.total, 2)

    @property
    def fingerprint(self) -> tuple:
        """营养指纹：指纹相同只保留最便宜的那个，防止多买 0 卡饮料凑数。

        必须带上「数据是否完整」这一维。combo_partial 的数值是缺子项后的下限，
        与同数值的完整单点撞车时按价格去重会误留组合 —— 实测「脆香油条组合」
        ¥6.6 与「脆香油条」¥8 都是 202kcal/蛋白 5g（组合里的鲜萃冰咖未收录），
        结果 ¥8 的单点被 ¥6.6 的组合顶掉，而组合实付反而是 ¥12.5。
        """
        n = self.nutrition
        incomplete = (n.matched_by == "combo_partial") or (not n.known)
        return (n.kcal, n.protein, n.fat, n.carb, incomplete)

    def to_dict(self) -> dict:
        return {
            "items": [{"code": i.code, "name": i.name, "price": i.price,
                       "original_price": i.original_price, "is_combo": i.is_combo,
                       "image": i.image,
                       # 卡片要按行标千卡/蛋白，这里必须给单品营养，不能只给合计
                       "nutrition": (i.nutrition.to_dict()
                                     if (i.nutrition and i.nutrition.known) else None),
                       "quantity": self.qty_of(i.code)}
                      for i in self.items],
            "total": self.total,
            "total_original": self.total_original,
            "structure_saving": self.structure_saving,
            "nutrition": self.nutrition.to_dict(),
            "protein_per_yuan": self.protein_per_yuan,
            "objective": self.objective,
            "score": round(self.score, 3),
            "route": self.route,
            "assign": dict(self.assign),
            "notes": self.notes,
        }


OBJECTIVES = ("value", "budget", "kcal", "sodium")


# pool 超过这个规模时按目标分截断，防 C(n,4) 组合爆炸
# （实测 109 SKU 全量枚举 9.3s；cap=48 时约 21 万组合、1s 内出解）
_POOL_CAP = 48

def _filter_pool(items: list[MenuItem], constraints: Constraints) -> list[MenuItem]:
    """求解池：价格有效、不触忌口、营养数据满足「约束落到哪个字段，哪个字段就不许缺」。

    字段级排除是铁律「不把 null 当 0」的执行：钠上限启用时缺钠数据的商品若进解，
    聚合钠是部分和，会假达标 —— 和把 null 填 0 是同一个错误。
    """
    pool = []
    for it in items:
        if it.price <= 0:
            continue
        if _violates(it, constraints):
            continue
        n = it.nutrition
        if constraints.strict_nutrition:
            # combo_partial 是「拆开了但缺几项」——热量必然偏低，把它当完整数据用
            # 会让控卡方案算出假数字，所以和 unknown 一样排除。
            if not n or not n.known or n.matched_by == "combo_partial":
                continue
        else:
            # 非严格模式（用户只想按预算吃饱）允许 combo_partial 进解，但完全没数据的
            # 不行：卡片上写着 0 kcal / 蛋白 0g 的方案就是骗人，宁可说「数据不足」不给方案。
            if not n or n.kcal is None or n.kcal <= 0:
                continue
        if n:
            if constraints.sodium_max is not None and n.sodium is None:
                continue
            if constraints.protein_min is not None and n.protein is None:
                continue
            if (constraints.kcal_max is not None or constraints.kcal_min is not None) \
                    and n.kcal is None:
                continue
        pool.append(it)
    return pool

def _item_score(it: MenuItem, objective: str, kcal_target: float | None) -> float:
    """单品分，仅用于 pool 截断排序，与方案打分无关。"""
    n = it.nutrition
    if objective == "budget":
        return -it.price
    if objective == "sodium":
        return -(n.sodium if n and n.sodium is not None else 99999)
    if objective == "kcal":
        tgt = kcal_target or 600
        return -abs((n.kcal if n and n.kcal is not None else tgt * 2) - tgt)
    if n and n.protein is not None and it.price > 0:  # value 默认：蛋白/元
        return n.protein / it.price
    return 0.0

def _cap_pool(pool: list[MenuItem], objective: str, kcal_target: float | None,
              cap: int = _POOL_CAP) -> list[MenuItem]:
    """pool 超限时按目标分截断。角色配额保底，免得截完全剩汉堡、没有喝的。"""
    if len(pool) <= cap:
        return pool
    scored = sorted(pool, key=lambda i: -_item_score(i, objective, kcal_target))
    # 价格保底：预算紧时最优解往往就是便宜货，纯按效率分排序会把它们整批截掉。
    # 实测 30 元 3 人（人均 10 元）因此无解 —— 便宜的主食全被高分高价的挤出去了。
    cheap = sorted(pool, key=lambda i: i.price)[: max(10, cap // 4)]
    picked = {id(i) for i in cheap}
    quota = {"main": 18, "drink": 14, "side": 10, "other": 6}
    buckets: dict[str, list[MenuItem]] = {k: [] for k in quota}
    for it in scored:
        if id(it) in picked:
            continue
        roles = item_roles(it.name)
        key = ("main" if "main" in roles else
               "drink" if "drink" in roles else
               "side" if "side" in roles else "other")
        buckets[key].append(it)
    out: list[MenuItem] = list(cheap)
    for k, q in quota.items():
        out.extend(buckets[k][:q])
    if len(out) < cap:  # 某角色不够数时，按分数补齐
        seen = {id(i) for i in out}
        out.extend(i for i in scored if id(i) not in seen)
    return out[:cap]

def _iter_combos(pool: list[MenuItem], n_range, budget_max: float | None):
    """组合枚举。有预算上限时按价格升序 + 前缀剪枝；无上限时暴力枚举。"""
    if budget_max is None:
        for n in n_range:
            yield from itertools.combinations(pool, n)
        return

    sp = sorted(pool, key=lambda i: i.price)
    L = len(sp)

    def rec(start: int, picked: list, cost: float, n: int):
        if len(picked) == n:
            yield tuple(picked)
            return
        need = n - len(picked)
        for i in range(start, L - need + 1):
            it = sp[i]
            # 价格升序，后面只会更贵 —— 超预算即可 break
            if cost + it.price > budget_max:
                break
            yield from rec(i + 1, picked + [it], cost + it.price, n)

    for n in n_range:
        yield from rec(0, [], 0.0, n)

def _dedup_rank(plans: list[Plan], top_k: int, prefer_route: str = "") -> list[Plan]:
    """指纹去重（营养相同只留最便宜）+ 按分数降序取 top_k。

    prefer_route：声明了「部分人控卡」时传 "split"。不同 route 回答的是不同的
    问题，不该硬放在一个标量上比 —— 用户问「那位怎么吃」，把「三个人一起吃
    减脂餐」排在前面不是分数低，是答非所问。所以这一类先占位，同 route 内部
    才按分数排。
    """
    best: dict[tuple, Plan] = {}
    for p in plans:
        cur = best.get(p.fingerprint)
        if cur is None or p.total < cur.total:
            best[p.fingerprint] = p
    ranked = sorted(best.values(),
                    key=lambda p: (p.route != prefer_route, -p.score))
    return ranked[:top_k]

def solve(
    items: list[MenuItem],
    constraints: Constraints,
    objective: str = "value",
    kcal_target: float | None = None,
    top_k: int = 3,
) -> list[Plan]:
    """枚举组合并打分。

    objective:
      value  —— 蛋白质每元最大化（自律党/控卡党的主指标）
      budget —— 总价最低（毛党）
      kcal   —— 热量贴近 target（增肌/控卡同时要求"吃到目标"）
      sodium —— 钠最低（高血压/清淡党）
    """
    pool = _filter_pool(items, constraints)
    if not pool:
        return []
    pool = _cap_pool(pool, objective, kcal_target)

    plans: list[Plan] = []
    hi = min(constraints.max_items, 4)
    n_range = range(max(1, constraints.min_items), hi + 1)
    for combo in _iter_combos(pool, n_range, constraints.budget_max):
        plan = _evaluate(combo, constraints, objective, kcal_target)
        if plan:
            plans.append(plan)

    return _dedup_rank(plans, top_k)


def _violates(it: MenuItem, c: Constraints) -> bool:
    """忌口检查：名字 + 标签 + **套餐子项**。

    只看套餐名会漏：分享装叫「全明星双人分享餐八件套」，里面装着麦辣鸡腿汉堡
    和麦辣鸡翅。不吃辣的人收到这一桌，等于忌口没生效。
    """
    text = it.name + "".join(it.tags) + "".join(it.children or [])
    for kw in c.exclude_keywords:
        if kw and kw in text:
            return True
    return False


def _evaluate(combo, c: Constraints, objective: str,
              kcal_target: float | None) -> Plan | None:
    total = round(sum(i.price for i in combo), 2)
    total_original = round(sum(i.original_price or i.price for i in combo), 2)

    agg = Nutrition(name="+".join(i.name for i in combo), kcal=0.0, protein=0.0,
                    fat=0.0, carb=0.0, sodium=0.0, calcium=0.0, kj=0.0,
                    matched_by="aggregate")
    notes: list[str] = []
    for i in combo:
        n = i.nutrition
        if not n or not n.known:
            return None
        for f in ("kcal", "protein", "fat", "carb", "sodium", "calcium"):
            v = getattr(n, f)
            if v is not None:
                setattr(agg, f, round((getattr(agg, f) or 0) + v, 1))
        if n.note and n.matched_by in ("scaled", "spec", "combo_partial"):
            notes.append(f"{i.name}：{n.note}")

    # 四重约束
    if c.kcal_max is not None and agg.kcal > c.kcal_max:
        return None
    if c.kcal_min is not None and agg.kcal < c.kcal_min:
        return None
    if c.protein_min is not None and (agg.protein or 0) < c.protein_min:
        return None
    if c.sodium_max is not None and (agg.sodium or 0) > c.sodium_max:
        return None
    if c.budget_max is not None and total > c.budget_max:
        return None
    if c.budget_min is not None and total < c.budget_min:
        return None
    if c.include_keywords:
        text = "".join(i.name for i in combo)
        if not any(k in text for k in c.include_keywords):
            return None

    if c.structure:
        roles: set[str] = set()
        for i in combo:
            roles |= item_roles(i.name)
            # 套餐是一个 SKU，但它的子项决定这顿有没有喝的。
            # 不把 children 算进来，「随心配（汉堡+可乐）」会被判成"没饮品"，
            # 逼着再加一份 ¥10.5 牛奶 —— 实测 3 人餐因此从 ¥44.70 抬到 ¥76.20。
            for ch in (i.children or []):
                roles |= item_roles(ch)
        # 任何结构档位都先保证有主食 —— 否则会给出「3 根油条」「3 个冰淇淋」这种不算饭的方案
        if "main" not in roles:
            return None
        if c.structure == "with_drink" and "drink" not in roles:
            return None
        if c.structure == "full" and not (roles & {"drink", "side"}):
            return None

    score = _score(agg, total, objective, kcal_target, c)

    # 含未收录子项的方案（combo_partial）营养是「已知部分」，热量和蛋白都偏低。
    # 不降权的话它会凭虚高的蛋白/元压过数据完整的方案 —— 实测「精选超值随心配」
    # 缺主品营养，只剩 351kcal/蛋白 0g，却排在营养完整的「人气经典随心配」前面。
    if any(i.nutrition and i.nutrition.matched_by == "combo_partial" for i in combo):
        score *= 0.85

    return Plan(items=list(combo), nutrition=agg, total=total,
                total_original=total_original, objective=objective,
                score=score, notes=list(dict.fromkeys(notes)))

def _score(agg: Nutrition, total: float, objective: str,
           kcal_target: float | None, c: Constraints) -> float:
    """方案打分。agg 为聚合营养（多人方案是总量），total 为总价。"""
    if objective == "value":
        ppy = (agg.protein or 0) / total if total else 0
        # 蛋白质效率为主，钠和脂肪做轻微惩罚
        penalty = 1.0 - min(0.25, (agg.sodium or 0) / 6000)
        return ppy * penalty
    if objective == "budget":
        return 1000.0 / total if total else 0
    if objective == "kcal":
        target = kcal_target if kcal_target is not None else (c.kcal_max or 600)
        return 1.0 / (1.0 + abs(agg.kcal - target) / max(target, 1))
    if objective == "sodium":
        return 1000.0 / max(agg.sodium or 1, 1)
    return 0.0


def _agg_members(members: list[tuple[MenuItem, int]]) -> Nutrition | None:
    """按 (商品, 份数) 聚合营养。任一商品营养未知 → 整体不可信，返回 None。"""
    agg = Nutrition(name="+".join(i.name for i, _ in members), kcal=0.0, protein=0.0,
                    fat=0.0, carb=0.0, sodium=0.0, calcium=0.0, kj=0.0,
                    matched_by="aggregate")
    for it, qty in members:
        n = it.nutrition
        if not n or not n.known:
            return None
        for f in ("kcal", "protein", "fat", "carb", "sodium", "calcium"):
            v = getattr(n, f)
            if v is not None:
                setattr(agg, f, round((getattr(agg, f) or 0) + v * qty, 1))
    return agg


# 「能摆在桌上分」的小食。圆筒冰淇淋也算 side，但三个人分一个圆筒不像话，
# 而它在控卡场景里还总是最便宜的那个，所以按名字认，不按角色认。
_SHAREABLE = ("薯条", "鸡块", "麦乐鸡", "鸡翅", "玉米杯", "苹果片", "沙拉", "派", "年糕")

# 差异化配餐里，「照常吃」那几位相对于控卡上限的倍数。
# 一个人减脂不该把另外两个人也按减脂喂：控卡 700 时，另外两位按 700×1.35 ≈ 950
# 的常规量算，多出来的这部分由 planner 折成 Constraints.kcal_extra 传进来。
SPLIT_NORMAL_RATIO = 1.35


def _is_shareable(name: str) -> bool:
    return any(k in (name or "") for k in _SHAREABLE)


def _feast_factor(members: list[tuple[MenuItem, int]], people: int) -> float:
    """多人聚餐「像不像一顿饭」的加成。

    蛋白/元 只看得见效率，看不见「桌上有几种主食、有没有一份能分的」——
    三个人各啃同款汉堡、各喝同款牛奶，蛋白/元 最高，但那不叫聚餐。

    这两个系数是**人为取舍，不是测量值**：多一种主食算 +8%，有一份能摆中间
    分的算 +20%。20% 这个量级来自实测 —— 一份中薯 ¥14 只带 4g 蛋白，摊薄
    蛋白/元 约 9%，加成本必须明显大于这个数它才进得来。金额化的意思是：
    「愿意为一份可分享的小食，让掉两成的蛋白性价比」。只作用于多人路径。
    """
    if people < 2:
        return 1.0
    mains = {i.name for i, _ in members if "main" in item_roles(i.name)}
    factor = 1.0
    if len(mains) >= 2:
        factor += 0.08      # 桌上有两种主食，不是三个人同一款
    if any(q == 1 and _is_shareable(i.name) for i, q in members):
        factor += 0.20      # 有一份能摆中间分的
    return factor


def _diversify(plans: list[Plan], top_k: int) -> list[Plan]:
    """挑出主品组合不同的方案。

    同一次求解里「你吃双吉、他们吃鳕鱼堡」和「你吃鳕鱼堡、他们吃双吉」是
    同一桌菜换个摆法，营养还不同，指纹去重拦不住。两套方案摆在一起看，
    用户要的是两个选择，不是一道数学题的两个排列。

    按主品集合的 Jaccard 相似度去重，超过 0.6 视为同款。
    """
    def mains(p: Plan) -> set[str]:
        got = {i.name for i in p.items if "main" in item_roles(i.name)}
        return got or {i.name for i in p.items}

    out: list[Plan] = []
    for p in plans:
        a = mains(p)
        dup = False
        for q in out:
            b = mains(q)
            inter = len(a & b)
            union = len(a | b) or 1
            if inter / union >= 0.6:
                dup = True
                break
        if not dup:
            out.append(p)
        if len(out) >= top_k:
            break
    return out


def solve_group(
    items: list[MenuItem],
    constraints: Constraints,
    people: int,
    objective: str = "value",
    kcal_target: float | None = None,
    top_k: int = 3,
) -> list[Plan]:
    """多人方案。赛题输入项「人数」在这里进入求解，不许只落在追问记录里。

    约束语义：预算是总额（用户说的就是「一共花多少」）；
    热量/蛋白/钠是人均值（控卡从来都是人均概念），聚合校验时 × people。

    三条路径并跑出解，交给排序分胜负：
      A 人手一份：人均预算下单人求解，方案 × people —— 人人有主食有喝的
      B 分享装：分享装核心 + 饮品×people（+ 可选小食）—— 桶类的结构省通常更大
      C 差异化：1 份轻食 + (people-1) 份照常 + 可选共享小食 —— 只在有人控卡时跑，
        因为 A/B 都是「全员同款 ×N」，会把一个人的减脂摊给整桌
    """
    if people < 2:
        return solve(items, constraints, objective, kcal_target, top_k)

    def total_ok(agg: Nutrition, total_price: float) -> bool:
        c = constraints
        if c.kcal_max is not None and (agg.kcal or 0) > c.kcal_max * people:
            return False
        if c.kcal_min is not None and (agg.kcal or 0) < c.kcal_min * people:
            return False
        if c.protein_min is not None and (agg.protein or 0) < c.protein_min * people:
            return False
        if c.sodium_max is not None and (agg.sodium or 0) > c.sodium_max * people:
            return False
        if c.budget_max is not None and total_price > c.budget_max:
            return False
        return True

    pool = _filter_pool(items, constraints)
    if not pool:
        return []
    pool = _cap_pool(pool, objective, kcal_target)

    plans: list[Plan] = []

    # ---- 路径 A：人手一份 ----
    per_c = Constraints(
        kcal_max=constraints.kcal_max, kcal_min=constraints.kcal_min,
        protein_min=constraints.protein_min, sodium_max=constraints.sodium_max,
        budget_max=(constraints.budget_max / people) if constraints.budget_max else None,
        exclude_keywords=constraints.exclude_keywords,
        include_keywords=constraints.include_keywords,
        structure=constraints.structure or "with_drink",  # 多人默认人人有喝的
    )
    single = solve(items, per_c, objective, kcal_target, top_k=max(12, top_k * 3))
    structure_note = ""
    if not single and per_c.structure:
        # 预算紧到配不起饮品时降级：**不能把结构约束整个丢掉**，否则会选出
        # 「3 根油条」「3 个冰淇淋」这种不算饭的方案。降到「人人有主食」即可。
        per_c.structure = "main"
        single = solve(items, per_c, objective, kcal_target, top_k=3)
        structure_note = "预算内配不齐饮品，先保人人有主食"
    for sp in single:
        total = round(sp.total * people, 2)
        if constraints.budget_max is not None and total > constraints.budget_max:
            continue
        agg = sp.nutrition.scaled_copy(people, f"单人方案 × {people}")
        agg.matched_by = "aggregate"
        notes = [f"人手一份 × {people}"]
        if structure_note:
            notes.append(structure_note)
        notes.extend(sp.notes)
        plans.append(Plan(
            items=sp.items, nutrition=agg, total=total,
            total_original=round(sp.total_original * people, 2),
            objective=objective, score=sp.score,  # 效率类指标与份数无关
            notes=notes,
            quantities={i.code: people for i in sp.items},
            route="per_person",
        ))

    # ---- 路径 B：分享装 ----
    cores = [i for i in pool if is_share_item(i.name)]
    if cores:
        drinks = [i for i in pool
                  if "drink" in item_roles(i.name) and not is_share_item(i.name)]
        sides = [i for i in pool
                 if "side" in item_roles(i.name) and not is_share_item(i.name)]
        # 饮品候选：最便宜 2 种 + 热量最低 1 种（控卡场景），去重
        drink_cands = sorted(drinks, key=lambda i: i.price)[:2]
        for d in sorted(drinks,
                        key=lambda i: (i.nutrition.kcal
                                       if i.nutrition and i.nutrition.kcal is not None
                                       else 9e9))[:1]:
            if all(d.code != x.code for x in drink_cands):
                drink_cands.append(d)
        side_cands = sorted(sides, key=lambda i: i.price)[:3]
        # 分享路径必带饮品×people：多人餐没有喝的不像一顿饭，
        # 与路径 A 的 with_drink 默认对齐。用户不渴可以在选卡环节换
        for core in cores:
            if not core.nutrition or not core.nutrition.known:
                continue  # 分享装多是套餐，没拆出营养就不能进解（铁律 4/5）
            for drink in drink_cands:
                base = [(core, 1), (drink, people)]
                for extra in [None] + side_cands:
                    members = base + ([(extra, 1)] if extra else [])
                    agg = _agg_members(members)
                    if agg is None:
                        continue
                    total = round(sum(i.price * q for i, q in members), 2)
                    if not total_ok(agg, total):
                        continue
                    plans.append(Plan(
                        items=[i for i, _ in members],
                        nutrition=agg,
                        total=total,
                        total_original=round(
                            sum((i.original_price or i.price) * q for i, q in members), 2),
                        objective=objective,
                        notes=[f"分享装 + 饮品×{people}"],
                        quantities={i.code: q for i, q in members},
                        route="share",
                        score=_score(agg, total, objective, kcal_target, constraints)
                              * _feast_factor(members, people),
                    ))

    # ---- 路径 C：差异化配餐（同桌有人控卡）----
    # A、B 都是「全员同款 ×N」，等于把一个人的减脂摊给整桌：三个人一起啃
    # 鳕鱼堡喝牛奶。真实场景是那位吃轻的、其余照常、桌上加一份能分的。
    # 触发条件是 kcal_extra > 0 —— 那表示调用方明确了「控卡的是部分人」，
    # 全桌总量因此比「人均 × 人数」高出一截。不声明时 A/B 的语义才是对的。
    kcal_max = constraints.kcal_max
    if kcal_max and constraints.kcal_extra > 0:
        def known(i: MenuItem) -> bool:
            return bool(i.nutrition and i.nutrition.known)

        mains = [i for i in pool
                 if "main" in item_roles(i.name) and not is_share_item(i.name) and known(i)]
        drinks = [i for i in pool
                  if "drink" in item_roles(i.name) and not is_share_item(i.name) and known(i)]
        # 共享小食只在「三个人分一袋苹果片」和「没有」之间选择时，
        # 蛋白/元 会选苹果片（0 蛋白、¥7、不摊薄）。但聚餐共享的默认是薯条 ——
        # 这是「桌上该有什么」的常识，不该交给热量性价比裁决。
        feast_sides = [i for i in pool if known(i) and any(
            k in i.name for k in ("薯条", "鸡块", "麦乐鸡", "鸡翅", "年糕", "派", "玉米杯"))]
        side_cands = (sorted(feast_sides, key=lambda i: i.price)[:2]
                      or sorted([i for i in pool if _is_shareable(i.name) and known(i)],
                                key=lambda i: i.price)[:1])

        # 轻食：热量先筛，再按效率排。上限留 15% 余量 —— 那个人还得喝一口东西，
        # 主品就把额度吃满的话（汉堡 680 / 上限 700）配出来的方案一看就知道没算饮品。
        # 轻食：热量先筛，再按「谁更轻」排。这里不能用蛋白/元 —— 那是效率指标，
        # 会把最划算的双吉排在板烧前面，而控卡的人要的是热量低，不是性价比高。
        light_mains = sorted([i for i in mains if (i.nutrition.kcal or 0) <= kcal_max * 0.85],
                             key=lambda i: (i.nutrition.kcal or 9e9,
                                            -(i.nutrition.protein or 0)))[:3]
        # 照常吃的那几位：不另设上限，只受全桌总量约束
        normal_mains = sorted(mains, key=lambda i: -_item_score(i, objective, kcal_target))[:4]
        # 轻食的饮品要真轻：无糖汽水/水/纯牛奶。没有就退一步拿全场最低卡的那一款。
        light_drinks = sorted([i for i in drinks if (i.nutrition.kcal or 0) <= 60],
                              key=lambda i: -_item_score(i, objective, kcal_target))[:2]
        if not light_drinks:
            light_drinks = sorted(drinks, key=lambda i: i.nutrition.kcal or 9e9)[:1]
        normal_drinks = sorted(drinks, key=lambda i: -_item_score(i, objective, kcal_target))[:2]

        ceiling = kcal_max * people + constraints.kcal_extra
        for lm in light_mains:
            for ld in light_drinks:
                own = _agg_members([(lm, 1), (ld, 1)])
                # 控卡那位自己就得达标。全桌总量达标、但那位一个人吃了 900 kcal
                # 的方案是自欺 —— 他要的就是「我这顿轻」。
                if own is None or (own.kcal or 0) > kcal_max:
                    continue
                for nm in normal_mains:
                    if nm.code == lm.code:
                        continue
                    for nd in normal_drinks:
                        if nd.code == ld.code:
                            continue
                        base = [(lm, 1), (ld, 1), (nm, people - 1), (nd, people - 1)]
                        for sh in [None] + side_cands:
                            members = base + ([(sh, 1)] if sh else [])
                            agg = _agg_members(members)
                            if agg is None:
                                continue
                            total = round(sum(i.price * q for i, q in members), 2)
                            if constraints.budget_max is not None \
                                    and total > constraints.budget_max:
                                continue
                            if (agg.kcal or 0) > ceiling:
                                continue
                            if constraints.sodium_max is not None \
                                    and (agg.sodium or 0) > constraints.sodium_max * people:
                                continue
                            if constraints.protein_min is not None \
                                    and (agg.protein or 0) < constraints.protein_min * people:
                                continue
                            assign = {lm.code: "light", ld.code: "light",
                                      nm.code: "normal", nd.code: "normal"}
                            notes = [f"1 人轻食 · {people - 1} 人照常"]
                            if sh is not None:
                                assign[sh.code] = "share"
                                notes.append(f"共享 {sh.name} ×1")
                            plans.append(Plan(
                                items=[i for i, _ in members],
                                nutrition=agg,
                                total=total,
                                total_original=round(
                                    sum((i.original_price or i.price) * q
                                        for i, q in members), 2),
                                objective=objective,
                                score=_score(agg, total, objective, kcal_target, constraints)
                                      * _feast_factor(members, people),
                                notes=notes,
                                quantities={i.code: q for i, q in members},
                                route="split",
                                assign=assign,
                            ))

    ranked = _dedup_rank(plans, max(top_k * 4, top_k + 4),
                         prefer_route="split" if constraints.kcal_extra > 0 else "")
    return _diversify(ranked, top_k)


# --------------------------------------------------------------------------- CLI

def _main():
    import argparse
    import json
    import os
    import sys

    ap = argparse.ArgumentParser(description="麦当劳配餐求解器（离线，吃 fixtures 或现场抓的 JSON）")
    ap.add_argument("--fixtures", default=os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures"))
    ap.add_argument("--kcal-max", type=float)
    ap.add_argument("--kcal-min", type=float)
    ap.add_argument("--protein-min", type=float)
    ap.add_argument("--sodium-max", type=float, default=1500)
    ap.add_argument("--budget", type=float)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--objective", default="value", choices=OBJECTIVES)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    table = NutritionTable.from_toon(
        open(os.path.join(a.fixtures, "nutrition.toon"), encoding="utf-8").read()
    )
    menu = json.load(open(os.path.join(a.fixtures, "menu_1950739_dinner.json"), encoding="utf-8"))
    details = {}
    for fn in os.listdir(a.fixtures):
        if fn.startswith("detail_") and fn.endswith(".json"):
            d = json.load(open(os.path.join(a.fixtures, fn), encoding="utf-8"))
            cd = parse_combo_detail(d)
            details[cd.code] = cd

    items = attach_nutrition(parse_menu(menu), table, details)
    c = Constraints(kcal_max=a.kcal_max, kcal_min=a.kcal_min, protein_min=a.protein_min,
                    sodium_max=a.sodium_max, budget_max=a.budget, exclude_keywords=a.exclude)
    plans = solve(items, c, objective=a.objective, top_k=a.top_k)

    if a.json:
        print(json.dumps([p.to_dict() for p in plans], ensure_ascii=False, indent=2))
        return

    known = sum(1 for i in items if i.nutrition and i.nutrition.known)
    print(f"营养覆盖：{known}/{len(items)} 个 SKU　约束：{c.describe()}　目标：{a.objective}\n")
    if not plans:
        print("无可行解。放宽约束或降低 require_full_nutrition。")
        sys.exit(2)
    for i, p in enumerate(plans, 1):
        names = " + ".join(x.name for x in p.items)
        print(f"[{i}] {names}")
        print(f"    ¥{p.total:.2f}（划线 ¥{p.total_original:.2f}，结构省 ¥{p.structure_saving:.2f}）"
              f"  {p.nutrition.kcal:g} kcal · 蛋白 {p.nutrition.protein:g}g · 钠 {p.nutrition.sodium:g}mg"
              f" · 蛋白/元 {p.protein_per_yuan}")
        for n in p.notes:
            print(f"    ⚠ {n}")
        print()


if __name__ == "__main__":
    _main()
