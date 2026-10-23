#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 mcd_planner 的输出渲染成麦当劳品牌卡片（HTML）。

**为什么必须用脚本出卡，而不是让模型写：**

卡片是几百行内联样式 + 每套方案独立的营养条、价格看板、三条依据。模型逐字生成
要几分钟，而且每次写法都不一样 —— 品牌一致性靠"自觉遵守文档"，实际做不到。
改成确定性渲染后：

  1. 出卡 < 20ms，与规划耗时彻底解耦；
  2. 设计令牌（色板/圆角/字号/禁用词）由代码强制，不靠模型记规则；
  3. 模板里的数字全部来自 planner 的 JSON，模型没有机会编。

版式（v1.2，按《品牌卡片设计规范》重做）：

  L1 品牌口吻主标题   红底：「主品名」+ 口感/营养基调 + 「套」
  L2 餐品清单         套餐/随心配拆成单品，一行一件：分类图标 + 来源前缀 + 截断菜名 + 份数 + 千卡胶囊
  L3 价格看板与理由   超大实付 + 立省绿标 + 三条推荐依据
  兜底                图标按 21 类自绘 SVG，认不出上红底金拱门；菜名 nowrap 截断；
                      营养缺项写「未收录」；单卡 ≤432px，宽屏两列并排、窄屏上下流式

用法：

    python3 mcd_planner.py ... --html /tmp/mcd.html      # 推荐：一条命令出方案+卡片
    python3 render_card.py --report /tmp/report.json --out /tmp/card.html
    python3 render_card.py --report - --fragment          # stdin 进，只吐卡片片段
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys

try:                                  # 允许脚本单独复制出去跑
    from nutrition_solver import item_roles
except ImportError:                   # pragma: no cover
    def item_roles(name: str) -> set[str]:
        return set()

try:
    import mcd_icons
except ImportError:                   # pragma: no cover
    mcd_icons = None

# --------------------------------------------------------------------------- 设计令牌
# 对外交付物：写死品牌色，不跟随 IDE / 系统深色主题（否则深色下整卡不可读）

RED = "#DA291C"        # 麦当劳红：标题底色、实付金额、超阈值
GOLD = "#FFBC0D"       # 金拱门黄：角标、按钮、强调
CREAM = "#FFF8E7"      # 暖奶油：餐品底衬
INK = "#27251F"        # 深炭黑：正文（奶油底上 WCAG AAA）
SUB = "#8C857B"        # 中灰：次级字、划线原价
GREEN = "#1B873F"      # 省钱绿标
LINE = "#EFE4CD"       # 卡片内分隔线
PAGE = "#F2E9D8"       # 页面底色（比奶油更深一档，让卡片浮起来）
WHITE = "#FFFFFF"

R_OUT = "18px"         # 外层容器圆角
R_IN = "12px"          # 内层模块圆角

# 出现任何一个就是文案事故（references/brand-visual.md 第四节）
BANNED = ("零负担", "无负担", "吃不胖", "健康之选", "营养均衡", "经典搭配",
          "不容错过", "家人们", "绝绝子", "YYDS", "超值优惠", "暖心推荐",
          "低卡轻食", "健康无负担")

LETTERS = "ABCDEFGH"


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def money(x) -> str:
    """价格两位小数、等宽对齐。允许 None。"""
    try:
        return f"¥{float(x):.2f}"
    except (TypeError, ValueError):
        return "¥—"


def num(x, digits: int = 0, suffix: str = "") -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{v:.{digits}f}{suffix}"


def _kcal(p):
    return (p.get("nutrition") or {}).get("kcal")


def _protein(p):
    return (p.get("nutrition") or {}).get("protein")


def _qty(p) -> int:
    return sum(i.get("quantity") or 1 for i in (p.get("items") or []))


OBJECTIVE_CN = {"value": "每元蛋白", "budget": "价格", "kcal": "热量", "sodium": "钠"}


# --------------------------------------------------------------------------- 方案命名

# 菜单上高频主品给个黑话简称：截断切不出「双吉」这种叫法
_NICK = {
    "双层吉士汉堡": "双吉", "吉士汉堡": "吉士", "巨无霸": "巨无霸",
    "板烧鸡腿堡": "板烧", "麦辣鸡腿堡": "麦辣", "麦香鸡": "麦香鸡",
    "双层深海鳕鱼堡": "深海鳕鱼", "麦香鱼": "麦香鱼", "安格斯": "安格斯",
}


def _nick(name: str) -> str:
    """主品简称：先查黑话表，查不到就去后缀取前 4 字。切不出好词时宁可用原名。"""
    s = name or ""
    for k, v in _NICK.items():
        if s.startswith(k):
            return v
    for suf in ("组合", "套餐", "随心配", "人气经典", "精选超值", "超值", "经典"):
        s = s.replace(suf, "")
    s = s.strip()
    if len(s) > 5 and s.startswith("双层"):
        s = s[2:]                      # 双层深海鳕鱼堡 → 深海鳕鱼堡
    return (s or "这一套")[:4]


def main_item(p) -> dict:
    """找主品：套餐要看子项，否则「人气经典随心配」会当主品名用。

    差异化配餐时取「照常那组」的主品 —— 那是桌上多数人的饭，也就是这张卡
    的主体。取轻食那份会把「麦香鸡」写进标题，而两个同事吃的是鳕鱼堡。
    """
    items = p.get("items") or []
    assign = p.get("assign") or {}
    if assign:
        normal = [i for i in items if assign.get(i.get("code")) == "normal"]
        if normal:
            items = normal
    mains = [i for i in items if "main" in item_roles(i.get("name", ""))]
    pick = mains[0] if mains else (items[0] if items else {})
    parts = pick.get("parts") or []
    if parts:
        burger = [x for x in parts if any(k in (x.get("name") or "")
                                          for k in ("汉堡", "堡", "卷", "麦满分", "派"))]
        return burger[0] if burger else parts[0]
    return pick


def _has_sweet(p) -> bool:
    txt = "".join(i.get("name", "") for i in p.get("items") or [])
    txt += "".join(x.get("name", "")
                   for i in p.get("items") or [] for x in (i.get("parts") or []))
    return any(k in txt for k in ("甜", "冰淇淋", "新地", "旋风", "圆筒", "派", "麦炫酷"))


TAG_TAIL = {
    "蛋白最高": "高蛋白暴击套",
    "最省": "精算管饱套",
    "热量最低": "轻装一套",
    "份量最足": "加量一套",
}

# 差异化配餐的尾词：一张卡上装着三份不同的东西，「管饱套」这种词只描述了其中两份
SPLIT_TAIL = "分餐套"


def plan_name(p, plans, idx) -> str:
    """L1 主标题：主品黑话 + 这套的性格。性格跟角标同源，避免「标签说蛋白最高、名字叫管饱」。"""
    nick = _nick(main_item(p).get("name", ""))
    if p.get("assign"):
        return f"「{nick}」{SPLIT_TAIL}"
    tail = TAG_TAIL.get(plan_tag(p, plans, idx)) or (
        "甜口收尾套" if _has_sweet(p) else "管饱一套")
    return f"「{nick}」{tail}"


# --------------------------------------------------------------------------- 方案标签

def plan_tag(p, plans, idx) -> str:
    """按"这套在哪一项上更强"给标签，不写虚词。并列就老实说并列。"""
    others = [q for j, q in enumerate(plans) if j != idx]
    if not others:
        return "首选"
    price = p.get("final_price") or 0
    if all(price < (q.get("final_price") or 0) - 0.005 for q in others):
        return "最省"
    k, ok = _kcal(p), [x for x in (_kcal(q) for q in others) if x]
    if k and ok and k < min(ok) - 1:
        return "热量最低"
    pr, opr = _protein(p), [x for x in (_protein(q) for q in others) if x]
    if pr and opr and pr > max(opr) + 0.5:
        return "蛋白最高"
    if all(_qty(p) > _qty(q) for q in others):
        return "份量最足"
    return "并列"


def why_line(p, plans, idx, objective="value") -> str:
    """一句话说清凭什么排这个位置，全部用可比数字。"""
    if idx == 0:
        bits = []
        others = [q for j, q in enumerate(plans) if j != idx]
        price = p.get("final_price") or 0
        if others and all(price < (q.get("final_price") or 0) - 0.005 for q in others):
            bits.append("实付最低")
        k = _kcal(p)
        if k and others and all(k < (_kcal(q) or 1e9) - 1 for q in others):
            bits.append("热量最低")
        pr = _protein(p)
        if pr and others and all(pr > (_protein(q) or 0) + 0.5 for q in others):
            bits.append("蛋白最高")
        ppy = p.get("protein_per_yuan")
        if not bits and ppy and others and all(
                ppy >= (q.get("protein_per_yuan") or 0) - 1e-9 for q in others):
            bits.append(f"每元换到 {ppy:.2f}g 蛋白，这套里最高")
        if not bits:
            return f"按{OBJECTIVE_CN.get(objective, '综合')}排序排在这一位"
        return "排这一位，因为" + "、".join(bits)

    prev = plans[idx - 1]
    letter = LETTERS[idx - 1] if idx - 1 < len(LETTERS) else str(idx)
    bits = []
    dp = (p.get("final_price") or 0) - (prev.get("final_price") or 0)
    if abs(dp) < 0.005:
        bits.append(f"和方案 {letter} 同价")
    elif dp > 0:
        bits.append(f"比方案 {letter} 贵 {dp:.2f} 元")
    else:
        bits.append(f"比方案 {letter} 便宜 {abs(dp):.2f} 元")
    if _kcal(p) and _kcal(prev):
        dk = _kcal(p) - _kcal(prev)
        if abs(dk) >= 1:
            bits.append(f"热量{'多' if dk > 0 else '少'} {abs(dk):.0f} kcal")
    if _protein(p) and _protein(prev):
        dpr = _protein(p) - _protein(prev)
        if abs(dpr) >= 0.5:
            bits.append(f"蛋白{'多' if dpr > 0 else '少'} {abs(dpr):.0f}g")
    return "换它的代价：" + "，".join(bits)


# --------------------------------------------------------------------------- 三条依据

SPICY_MARKS = ("辣", "椒", "龙焰", "香辣", "麻辣")
SWEET_MARKS = ("甜", "派", "冰淇淋", "新地", "旋风", "圆筒", "蛋糕", "枫糖")


def _taste_word(prefer: str) -> str:
    """把偏好参数翻成口味词。参数是「谁传的」不可追溯，所以只描述口味，不代述谁说的。"""
    if "辣" in prefer:
        return "辣味"
    if "甜" in prefer:
        return "甜口"
    if any(k in prefer for k in ("清淡", "不辣", "别辣")):
        return "清淡"
    return "这个口味"


def _prefer_matched(prefer: str, names: list[str]) -> bool | None:
    """口味偏好能不能在菜名上验证。返回 None = 判不了，别硬说对得上。"""
    if not prefer:
        return None
    text = "".join(names)
    if "辣" in prefer:
        return any(k in text for k in SPICY_MARKS)
    if "甜" in prefer:
        return any(k in text for k in SWEET_MARKS)
    if any(k in prefer for k in ("清淡", "不辣", "别辣")):
        return not any(k in text for k in SPICY_MARKS)
    return None


def _dish_parts(p) -> tuple[str, list[str]]:
    """把这一套摊成（主品名, [配品名…]）。套餐走子项，单点走自身。

    直接对 items 做角色判定会让「人气经典随心配」当上主品 —— 那是 SKU 名，
    用户看到的应该是「双层吉士汉堡」。
    """
    names: list[str] = []
    for it in p.get("items") or []:
        parts = it.get("parts") or []
        for x in (parts or [it]):
            nm = (x.get("name") or "").strip()
            if nm:
                names.append(nm)
        if not parts:                       # 单点：按份数不重复，名字出现一次就够
            continue
    seen: set[str] = set()
    uniq = [n for n in names if not (n in seen or seen.add(n))]
    if not uniq:
        return "主食", []
    mains = [n for n in uniq if "main" in item_roles(n)]
    main = mains[0] if mains else uniq[0]
    return main, [n for n in uniq if n != main]


def reasons(p, ctx) -> list[tuple[str, str]]:
    """算账 / 营养 / 口味。数字必须来自 planner，讲不出依据就如实说没有。"""
    out = []

    # 1. 算账
    final = p.get("final_price") or 0
    prod = (p.get("priced") or {}).get("product_price") or p.get("total") or 0
    orig = p.get("total_original") or 0
    save_struct = p.get("structure_saving") or 0
    save_coupon = p.get("coupon_saving") or 0
    if save_coupon > 0.005:
        out.append(("算账", f"实付 {money(final)}，券后比商品金额 {money(prod)} 省 "
                            f"{money(save_coupon)}"))
    elif save_struct > 0.005:
        out.append(("算账", f"实付 {money(final)}，菜单划线价 {money(orig)}，结构省 "
                            f"{money(save_struct)}（套餐自带折价，不是券）"))
    else:
        out.append(("算账", f"实付 {money(final)}，商品 {money(prod)}，这单没有额外优惠"))

    # 2. 营养
    n = p.get("nutrition") or {}
    k = n.get("kcal")
    people = ctx.get("people") or 1
    kcal_cap = ctx.get("kcal_max")
    if not k:
        out.append(("营养", "营养表没收录这套里的主品，热量和钠都给不出数"))
    else:
        bits = [f"{k:.0f} kcal"]
        if kcal_cap:
            cap = kcal_cap * people
            bits.append(f"占 {cap:.0f} 上限的 {k / cap * 100:.0f}%")
        if n.get("protein"):
            bits.append(f"蛋白 {n['protein']:.0f}g")
        if n.get("sodium"):
            bits.append(f"钠 {n['sodium']:.0f}mg")
        if p.get("assign"):
            # 差异化方案里「人均」是最没用的那个数：控卡那位看到 629 会以为
            # 自己也吃了 629，实际他 369。这里直接说他那份多少。
            lit = [i for i in p.get("items") or []
                   if p["assign"].get(i.get("code")) == "light"]
            lk = sum((i.get("nutrition") or {}).get("kcal") or 0 for i in lit)
            if lk:
                bits.append(f"轻食那位 {lk:.0f} kcal")
        elif people > 1:
            bits.append(f"人均 {k / people:.0f} kcal")
        if n.get("matched_by") in ("aggregate", "combo_partial"):
            bits.append("按子项累加")
        out.append(("营养", "，".join(bits)))

    # 3. 口味 / 场景
    main_name, sides = _dish_parts(p)
    prefer = ctx.get("prefer")
    all_names = [i.get("name", "") for i in p.get("items") or []]
    all_names += [x.get("name", "")
                  for i in p.get("items") or [] for x in (i.get("parts") or [])]
    all_names.append(main_name)
    if ctx.get("reason3"):
        out.append(("场景", ctx["reason3"]))
    elif prefer:
        # 只描述这套里有什么、对不对得上，不写「你说想吃 X」——
        # 参数可能来自脚本默认值或测试命令，写成第一人称代述就是替他发言
        hit = _prefer_matched(prefer, all_names)
        taste = _taste_word(prefer)
        tail = (f"，配{'、'.join(sides[:2])}" if sides else "")
        if hit:
            out.append(("口味", f"主品{main_name}，{taste}对得上{tail}"))
        elif hit is False:
            out.append(("口味", f"这套里没有{taste}，是照"
                                f"{OBJECTIVE_CN.get(ctx.get('objective'), '综合')}排的先后"))
        else:
            out.append(("口味", f"主品{main_name}{tail}"))
    else:
        side_txt = f"，配{'、'.join(sides[:2])}" if sides else ""
        who = f"{people} 人各一份" if people > 1 else "一人份"
        out.append(("搭配", f"{who}，主品{main_name}{side_txt}"))
    return out[:3]


# --------------------------------------------------------------------------- 组件

def thumb(name: str, size: int = 38) -> str:
    """餐品图标：按菜名归到 21 类自绘 SVG 里，认不出的用金拱门兜底。

    刻意不用菜单接口的 `image`：那是 427×320 的促销横幅，带「随心配1+1」
    这类角标文案，缩到 34px 只剩色块和字。图标见 scripts/mcd_icons.py。
    """
    return mcd_icons.badge(name, size)


def pill(text: str, muted: bool = False) -> str:
    color = SUB if muted else INK
    return (f'<span style="display:inline-block;white-space:nowrap;font-size:16px;'
            f'line-height:1.55;color:{color};font-variant-numeric:tabular-nums;'
            f'background:{CREAM};border:1px solid {LINE};border-radius:999px;'
            f'padding:0 7px;">{esc(text)}</span>')


# --------------------------------------------------------------------------- 餐品行

# 拆成单品后要重排：主食在前、配品居中、饮品垫后、甜品收尾。
# 求解器是按价格挑的，顺序随机 —— 「先圆筒再汉堡」读着就不像一顿饭。
_UNIT_ORDER = (
    ("汉堡", "堡", "卷", "麦满分", "巨无霸", "不素之霸", "鸡腿", "鸡排",
     "脆汁鸡", "鸡球", "粥", "油条", "松饼", "大饼",
     # 这几个名字里没有「堡」，漏了就会被当甜品垫到最后 ——
     # 实测「麦香鸡」排到了「纯悦」后面，一张卡上先喝水再吃堡
     "麦香鸡", "麦香鱼", "板烧", "安格斯", "双吉"),
    ("薯条", "玉米", "苹果", "沙拉", "薯饼", "派", "麦乐鸡", "鸡块", "鸡翅"),
    ("可乐", "雪碧", "咖啡", "红茶", "奶铁", "牛奶", "豆浆", "果汁", "美式",
     "拿铁", "卡布", "怡泉", "柠", "橙", "雪冰", "玛奇朵", "冰咖", "纯悦"),
)


def _unit_rank(name: str) -> int:
    n = name or ""
    for i, keys in enumerate(_UNIT_ORDER):
        if any(k in n for k in keys):
            return i
    return len(_UNIT_ORDER)      # 甜品、认不出的垫最后


_COMBO_TAG = ("随心配", "1+1", "四件套", "三件套", "两件套", "五件套",
              "六件套", "分享装", "双人餐", "三人餐")


def combo_tag(name: str) -> str:
    """套餐名 → 能挂在单品前面当归属标记的短词。

    「人气经典随心配」整串挂上去会吃掉半行菜名的宽度，取其中的品类词「随心配」，
    读起来就是「随心配-可乐麦炫酷」。没有已知品类词的就取套餐名前 3 个字。
    """
    n = (name or "").strip()
    for t in _COMBO_TAG:
        if t in n:
            return t
    n = re.sub(r"[（(].*?[)）]", "", n).replace("套餐", "").strip()
    return n[:3]


def units(p) -> list[dict]:
    """把一套方案摊成单品行。

    套餐和随心配不占一整行。菜单上的「人气经典随心配」既不告诉人吃什么，
    也没法逐项看热量 —— 摊开后每行是一个能单独点到的单品，各带自己的 kcal。
    同一单品在多个套餐里重复出现时合并份数，买什么一目了然。

    每行还留一个 tag：这单品是从哪个套餐里拆出来的（单点为 ""）。
    拆平之后套餐名消失了，不留标记就看不出「这三件是拿随心配点的」，
    折扣也对不上账。

    group 来自求解器的 assign：light / normal / share。差异化配餐（一人控卡）
    时它是必填的 —— 一桌子东西摊平了列出来，用户还得自己猜哪份是自己的。
    全员同款时为空，卡片按平铺渲染。
    """
    assign = p.get("assign") or {}
    raw: list[dict] = []
    for it in p.get("items") or []:
        parts = it.get("parts") or []
        times = it.get("quantity") or 1
        tag = combo_tag(it.get("name") or "") if parts else ""
        group = assign.get(it.get("code"), "")
        if not parts:                                  # 单点：自己就是一行
            raw.append({"name": (it.get("name") or "").strip(),
                        "quantity": times,
                        "nutrition": it.get("nutrition") or {},
                        "tag": "", "group": group})
            continue
        for x in parts:                                # 套餐：按子项摊开
            raw.append({"name": (x.get("name") or "").strip(),
                        "quantity": times * (x.get("quantity") or 1),
                        "nutrition": x.get("nutrition") or {},
                        "tag": tag, "group": group})

    agg: dict[str, dict] = {}
    for u in raw:
        if not u["name"]:
            continue
        if u["name"] in agg:
            agg[u["name"]]["quantity"] += u["quantity"]
            # 同一单品来自不同套餐时（套餐点一份、又单独加一个），保留先出现的来源，
            # 留空会比乱标更让人以为漏了
            agg[u["name"]]["tag"] = agg[u["name"]].get("tag") or u["tag"]
            agg[u["name"]]["group"] = agg[u["name"]].get("group") or u["group"]
        else:
            agg[u["name"]] = dict(u)
    out = sorted(agg.values(), key=lambda u: _unit_rank(u["name"]))
    # 有分组时按 light → normal → share 排，组内仍按主品/配品/饮品/甜品
    if any(u.get("group") for u in out):
        order = {"light": 0, "normal": 1, "share": 2}
        out.sort(key=lambda u: (order.get(u.get("group") or "", 3),
                                _unit_rank(u["name"])))
    return out


def unit_row(u, hide_qty: bool = False) -> str:
    """L2 一行 = 一个单品：图标 + 来源前缀 + 菜名 + 份数 + 千卡。

    没有副行。套餐拆平后一行的信息量只剩「这是什么、几份、多少卡」，
    再挂一行字只会把卡面拉长；kcal 是单份值（多份靠 ×N 表达，合计看底部）。

    tag 用 14px 灰字挂在菜名前面（「随心配-可乐麦炫酷」）：既交代了这件是从哪个
    套餐里拆的，又不跟 18px 的菜名抢视线。前缀和菜名放在同一个 nowrap 容器里，
    宽度不够时截断的是菜名尾部，前缀不许被切 —— 切掉前缀就白标了。

    hide_qty：全清单份数一致时（人手一份 ×3），标题已经交代过，行内再挂三个
    ×3 纯属重复 —— 那点宽度在 300px 窄卡上就是菜名能不能显示全的差别。
    """
    nm = u.get("name") or ""
    tag = u.get("tag") or ""
    qty = u.get("quantity") or 1
    n = u.get("nutrition") or {}
    if n.get("kcal") is not None:
        # 纯悦这类是真 0 卡，不能因为 0 是 falsy 就打成「未收录」。
        # spec_default 是菜单没写规格、按中份推定的（薯条 ← 中薯条），
        # 加个 ≈ 让人知道这个数不是精确命中。
        mark = "≈" if n.get("matched_by") == "spec_default" else ""
        kcal_html = pill(f"{mark}{n['kcal']:.0f} kcal")
    else:
        # 查不到就老实说查不到，不能让缺口看着像「这项没有热量」
        kcal_html = pill("未收录", muted=True)
    qty_html = (f'<span style="flex:0 0 auto;font-size:16px;color:{SUB};'
                f'font-variant-numeric:tabular-nums;">×{qty}</span>'
                ) if (qty > 1 and not hide_qty) else ""
    weight = "600" if _unit_rank(nm) == 0 else "400"
    tag_html = (f'<span style="flex:0 0 auto;font-size:14px;color:{SUB};'
                f'line-height:1.5;">{esc(tag)}-</span>') if tag else ""
    full = f"{tag}-{nm}" if tag else nm

    return (f'<div style="display:flex;align-items:center;gap:9px;background:{WHITE};'
            f'border:1px solid {LINE};border-radius:{R_IN};padding:7px 10px;margin-top:7px;">'
            f'{thumb(nm, 34)}'
            f'<span title="{esc(full)}" style="flex:1;min-width:0;display:flex;'
            f'align-items:baseline;white-space:nowrap;overflow:hidden;">'
            f'{tag_html}'
            f'<span style="flex:0 1 auto;min-width:0;font-size:18px;'
            f'font-weight:{weight};color:{INK};line-height:1.5;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis;">{esc(nm)}</span></span>'
            f'{qty_html}{kcal_html}</div>')


def price_board(p, ctx) -> str:
    """L3 价格看板：实付最大，立省用绿标，原价划线。"""
    final = p.get("final_price") or 0
    orig = p.get("total_original") or 0
    save = round(max(0.0, orig - final), 2)
    if save < 0.005:
        save = round(p.get("structure_saving") or 0, 2)

    save_html = ""
    if save > 0.005:
        save_html = (f'<span style="display:inline-block;background:{GREEN};color:#fff;'
                     f'font-size:16px;border-radius:8px;padding:1px 8px;">'
                     f'立省 {money(save)}</span>')
    elif p.get("coupon_saving"):
        save_html = ""
    orig_html = (f'<div style="font-size:16px;color:{SUB};text-decoration:line-through;">'
                 f'{money(orig)}</div>') if (orig and orig > final + 0.005) else ""

    over = p.get("over_budget")
    over_html = (f'<div style="margin-top:6px;font-size:16px;color:{RED};">'
                 f'超预算 {money(p.get("over_by"))}</div>') if over else ""

    per = p.get("per_person")
    people = ctx.get("people") or 1
    per_html = (f'<div style="font-size:16px;color:{SUB};margin-top:2px;">'
                f'人均 {money(per)}</div>') if per and people > 1 else ""

    extra = [x for x in (
        f"运费 {money(ctx['freight'])}" if ctx.get("freight") else "",
        f"打包 {money(ctx['packing'])}" if ctx.get("packing") else "") if x]
    extra_html = (f'<div style="margin-top:8px;font-size:16px;color:{SUB};">'
                  f'另含 {esc(" · ".join(extra))}</div>') if extra else ""

    return (f'<div style="display:flex;align-items:flex-end;gap:10px;margin-top:14px;'
            f'padding-top:12px;border-top:1px solid {LINE};">'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="font-size:16px;color:{SUB};">实付</div>'
            f'<div style="font-size:42px;font-weight:700;color:{RED};line-height:1.15;'
            f'letter-spacing:-1px;font-variant-numeric:tabular-nums;">{money(final)}</div>'
            f'</div>'
            f'<div style="text-align:right;">{save_html}{orig_html}{per_html}{over_html}</div>'
            f'</div>{extra_html}')


def nutrition_strip(p, ctx) -> str:
    """数据透明度：合计值摆在明面上，超了标红，不藏在理由里。"""
    n = p.get("nutrition") or {}
    if not n.get("kcal"):
        return (f'<div style="margin-top:10px;font-size:16px;color:{SUB};">'
                f'营养表未收录这套的主品，热量与钠给不出数，别按它控卡</div>')
    people = ctx.get("people") or 1
    cells = [(f"{n['kcal']:.0f}", "kcal", False)]
    if n.get("protein"):
        cells.append((f"{n['protein']:.0f}", "g 蛋白", False))
    if n.get("sodium"):
        hot = bool(ctx.get("sodium_max")) and n["sodium"] > ctx["sodium_max"] * people
        cells.append((f"{n['sodium']:.0f}", "mg 钠", hot))
    html_cells = "".join(
        (f'<div style="width:1px;background:{LINE};margin:2px 0;"></div>' if i else "")
        + f'<div style="flex:1 1 0;min-width:0;text-align:center;">'
        f'<div style="font-size:20px;font-weight:600;line-height:1.35;white-space:nowrap;'
        f'color:{RED if hot else INK};font-variant-numeric:tabular-nums;">{v}'
        f'<span style="font-size:15px;font-weight:400;color:{SUB};margin-left:2px;">{u}</span>'
        f'</div></div>'
        for i, (v, u, hot) in enumerate(cells))
    return (f'<div style="display:flex;gap:6px;margin-top:12px;background:#FFFDF7;'
            f'border:1px solid {LINE};border-radius:{R_IN};padding:8px 4px;">{html_cells}</div>')


def reason_list(p, ctx) -> str:
    rows = "".join(
        f'<div style="display:flex;gap:8px;font-size:17px;line-height:1.75;'
        f'margin-top:6px;">'
        f'<span style="flex:0 0 auto;color:{RED};font-weight:600;">{esc(k)}</span>'
        f'<span style="color:{INK};">{esc(v)}</span></div>'
        for k, v in reasons(p, ctx))
    return f'<div style="margin-top:12px;">{rows}</div>'


# --------------------------------------------------------------------------- 单套方案

def group_head(key: str, members: list, people: int) -> str:
    """L2 的分组小标题。差异化配餐时一张卡上装着三份不同的东西。

    标题右侧挂这一组每人多少卡，因为底部的「人均 649 kcal」在这个场景里
    反而误导：控卡那位看到 649 会以为自己也吃了 649，实际他 429、另外两位 614。
    差异化的全部意义就在这两个数字上，藏在合计里等于没做。
    """
    qty = max((u.get("quantity") or 1) for u in members) if members else 1
    if key == "share":
        # 共享那份不是「谁的」：qty 是 1，写成「1 人」会被读成只有一个人吃
        label, note = "共享", "大家分"
    else:
        # split 路径保证每人一份，所以份数就是人数
        label, note = ("轻食" if key == "light" else "照常"), f"{qty} 人"

    kcal_txt = ""
    if key != "share":
        total, hole = 0.0, False
        for u in members:
            k = (u.get("nutrition") or {}).get("kcal")
            if k is None:
                hole = True
            else:
                total += k * (u.get("quantity") or 1)
        if total:
            per = round(total / max(qty, 1))
            kcal_txt = f"{'≈' if hole else ''}每人 {per:g} kcal"

    return (f'<div style="display:flex;align-items:center;gap:8px;margin-top:14px;">'
            f'<span style="flex:0 0 auto;width:3px;height:15px;background:{GOLD};'
            f'border-radius:2px;"></span>'
            f'<span style="font-size:18px;font-weight:600;color:{INK};'
            f'line-height:1.5;">{esc(label)}</span>'
            f'<span style="font-size:16px;color:{SUB};line-height:1.5;">{esc(note)}</span>'
            f'<span style="margin-left:auto;flex:0 0 auto;font-size:15px;color:{SUB};'
            f'line-height:1.5;font-variant-numeric:tabular-nums;">{esc(kcal_txt)}</span>'
            f'</div>')


def plan_card(p, plans, idx, ctx) -> str:
    letter = LETTERS[idx] if idx < len(LETTERS) else str(idx + 1)
    tag = plan_tag(p, plans, idx)
    over = p.get("over_budget")

    head_chip = (f'<span style="background:{GOLD};color:{INK};font-size:16px;'
                 f'border-radius:999px;padding:2px 10px;font-weight:600;">{esc(tag)}</span>')
    over_chip = (f'<span style="border:1px solid {GOLD};color:{GOLD};font-size:15px;'
                 f'border-radius:999px;padding:1px 8px;">超预算</span>') if over else ""

    us = units(p)
    people = int(ctx.get("people") or 1)
    if any(u.get("group") for u in us):
        # 差异化配餐：按「谁的」分组。每组内部份数一致就把 ×N 收掉 ——
        # 组标题已经写了「照常 · 2 人」，行里再挂两个 ×2 是同一件事说三遍。
        chunks: list[str] = []
        cur = None
        bucket: list[dict] = []
        for u in list(us) + [{"group": "__end__"}]:
            g = u.get("group") or ""
            if g != cur:
                if bucket and cur is not None:
                    same = len({x["quantity"] for x in bucket}) == 1
                    if cur:                       # 有归属才配标题，无归属直接出行
                        chunks.append(group_head(cur, bucket, people))
                    chunks.extend(unit_row(x, hide_qty=same) for x in bucket)
                bucket = []
                cur = g
            bucket.append(u)
        rows = "".join(chunks)
    else:
        # 全清单份数一致 → 标题已说「人手一份 ×3」，行内不必每行重复
        hide_qty = bool(us) and len({u["quantity"] for u in us}) == 1
        rows = "".join(unit_row(u, hide_qty=hide_qty) for u in us)
    # 行数不同的两卡并排时，价格看板会错位。按最多的那套占位，实付才对得齐。
    want = ctx.get("_max_units") or len(us)
    rows_min = f"min-height:{want * 57}px;" if want > len(us) else ""

    keep_note = [x for x in (p.get("notes") or [])
                 if "人手一份" in x or "分享装" in x]
    hint = f' · {esc(keep_note[0])}' if keep_note else ""

    # 内嵌到对话里（widget）时：不要投影，基宽收到 300 ——
    # 宿主容器约 680px，392 的两卡会被折成一列，用户就没法并排比
    widget = bool(ctx.get("widget"))
    shell = ("box-shadow:none;" if widget else
             "box-shadow:0 2px 10px rgba(39,37,31,0.07);")
    basis = "300px" if widget else "392px"

    return f'''
<div style="flex:1 1 {basis};max-width:432px;min-width:0;box-sizing:border-box;background:{WHITE};
            border-radius:{R_OUT};overflow:hidden;border:1px solid {LINE};
            {shell}
            display:flex;flex-direction:column;">

  <div style="background:{RED};padding:16px 18px 14px;position:relative;
              overflow:hidden;">
    <div style="display:flex;align-items:center;gap:8px;">
      <span style="color:{GOLD};font-size:16px;font-weight:600;
                   letter-spacing:1px;">方案 {letter}</span>{head_chip}{over_chip}
      <span style="margin-left:auto;color:{CREAM};opacity:.75;font-size:16px;">
        {(str(ctx.get('people') or 1) + ' 人') if (ctx.get('people') or 1) > 1 else '1 人'}</span>
    </div>
    <svg width="96" height="72" viewBox="0 0 48 36" aria-hidden="true"
         style="position:absolute;right:-10px;bottom:-8px;opacity:.10;">
      <path fill="{GOLD}" d="M2 36V16C2 7 8 2 14 2c4.6 0 8 2.6 10 7 2-4.4 5.4-7 10-7 6 0 12 5 12 14v20H37V16c0-4-1.6-6-4-6s-4 2-4 6v20H26V16c0-4-1.6-6-4-6s-4 2-4 6v20H2z"/>
    </svg>
    <div style="color:{CREAM};font-size:26px;font-weight:700;line-height:1.35;
                margin-top:6px;position:relative;">{esc(plan_name(p, plans, idx))}</div>
    <div style="color:#FFE8AE;font-size:16px;line-height:1.6;margin-top:4px;
                min-height:52px;position:relative;">
      {esc(why_line(p, plans, idx, ctx.get('objective', 'value')))}</div>
  </div>

  <div style="background:{CREAM};padding:12px 16px 16px;flex:1;
              display:flex;flex-direction:column;">
    <div style="display:flex;align-items:baseline;gap:6px;">
      <span style="font-size:16px;color:{SUB};letter-spacing:1px;">这一份里有什么</span>
      <span style="font-size:16px;color:{SUB};">{hint}</span>
    </div>
    <div style="{rows_min}">{rows}</div>
    {price_board(p, ctx)}
    {nutrition_strip(p, ctx)}
    {reason_list(p, ctx)}
  </div>
</div>'''


# --------------------------------------------------------------------------- 整卡

def render(report: dict, ctx: dict, widget: bool = False) -> str:
    ctx["widget"] = bool(widget or ctx.get("widget"))
    store = report.get("store") or {}
    plans = report.get("plans") or []
    people = ctx.get("people") or report.get("people") or 1
    ctx["people"] = people
    ctx["objective"] = (report.get("criteria") or {}).get("objective") or "value"

    take = "外送" if int(store.get("orderType") or 1) == 2 else "到店自取"
    scen = " · ".join(x for x in [
        store.get("name") or "门店待确认",
        take,
        ctx.get("meal") or "",
        f"{people} 人",
        f"预算 {money(ctx['budget'])}" if ctx.get("budget") else "",
    ] if x)

    crit = report.get("constraints") or ""
    menu_note_html = (f'<div style="margin-top:3px;font-size:16px;color:{SUB};">'
                      f'{esc(report["menu_note"])}</div>') if report.get("menu_note") else ""

    # 拆平后各套行数不等，占位按最多的那套算，并排时实付才对得齐
    ctx["_max_units"] = max([len(units(p)) for p in plans] or [0])
    cards = "".join(plan_card(p, plans, i, ctx) for i, p in enumerate(plans))
    coupon_html = ""
    coupon_list = report.get("coupons") or []
    if coupon_list:
        items = "".join(
            f'<div style="font-size:17px;line-height:1.8;">'
            f'<span style="color:{RED};font-weight:600;">券</span>　{esc(c.get("title"))}'
            + (f'　<span style="color:{SUB};font-size:16px;">限 '
               f'{esc("、".join((c.get("products") or [])[:2]))}</span>'
               if c.get("products") else "")
            + '</div>'
            for c in coupon_list[:3])
        coupon_html = (f'<div style="background:{WHITE};border:1px solid {LINE};'
                       f'border-radius:{R_OUT};padding:14px 18px;margin-top:18px;'
                       f'max-width:920px;">'
                       f'<div style="font-size:18px;font-weight:600;margin-bottom:4px;">'
                       f'这家店有的券</div>{items}</div>')

    stamp = ctx.get("stamp") or dt.datetime.now().strftime("%m-%d %H:%M")
    foot = (f'<div style="margin-top:18px;font-size:16px;color:{SUB};line-height:1.8;'
            f'max-width:920px;">价格与热量来自麦当劳中国官方接口，{esc(stamp)} 取的数；'
            f'实付以提交订单时的结算页为准。</div>')

    # 数据缺口要说：更便宜的套餐只因缺一项营养没进方案，卡片上却看不出原因，
    # 用户会以为是配餐配得差。一行小字放在最底部，不挡主信息。
    gap = next((w for w in (report.get("warnings") or []) if "没进方案" in w), "")
    gap_html = (f'<div style="margin-top:14px;font-size:16px;color:{SUB};'
                f'line-height:1.8;max-width:920px;">{esc(gap)}</div>') if gap else ""

    sr = ('<div style="position:absolute;width:1px;height:1px;overflow:hidden;'
          'clip:rect(0 0 0 0);white-space:nowrap;">麦当劳配餐方案：'
          f'{esc(scen)}，{len(plans)} 套可选</div>') if ctx["widget"] else ""
    outer_max = "100%" if ctx["widget"] else "920px"
    return f'''{sr}<div style="width:100%;max-width:{outer_max};margin:0 auto;box-sizing:border-box;
     color:{INK};line-height:1.8;">

  <div style="display:flex;align-items:baseline;flex-wrap:wrap;gap:10px;">
    <span style="background:{RED};color:{CREAM};font-size:19px;font-weight:700;
                 border-radius:999px;padding:3px 14px;">麦当劳</span>
    <span style="font-size:24px;font-weight:700;">这顿怎么点</span>
    <span style="margin-left:auto;font-size:17px;color:{SUB};">{esc(scen)}</span>
  </div>
  <div style="margin-top:6px;font-size:16px;color:{SUB};">口径：{esc(crit)}</div>
  {menu_note_html}

  <div style="display:flex;flex-wrap:wrap;gap:18px;align-items:stretch;
              margin-top:16px;">{cards}</div>
  {coupon_html}
  {gap_html}
  {foot}
</div>'''


def _widget_safe(html_text: str) -> str:
    """把属性引号统一成单引号。

    卡片片段要作为字符串塞进可视化工具的 widget_code，每个双引号都得转义一次，
    几百处手写必错。改成单引号后片段可直接粘贴，JSON 里不再需要 `\"`。
    """
    out = html_text.replace("'none'", "&#39;none&#39;") \
                   .replace("'flex'", "&#39;flex&#39;")
    return out.replace('"', "'")


def page(fragment: str) -> str:
    """包成独立页面：预览面板直接开，不依赖任何主题变量。"""
    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>麦当劳配餐方案</title>
<style>
  html,body{{margin:0;padding:0;background:{PAGE};}}
  body{{padding:22px 18px 30px;box-sizing:border-box;
        font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
        color:{INK};font-size:18px;line-height:1.8;
        -webkit-font-smoothing:antialiased;}}
  img{{display:block;}}
  @media (max-width:860px){{
    body{{padding:14px 12px 22px;}}
  }}
</style></head>
<body>{fragment}</body></html>'''


# --------------------------------------------------------------------------- CLI

def _ctx_from_args(a, report) -> dict:
    ctx = {
        "people": a.people or report.get("people") or 1,
        "budget": a.budget,
        "kcal_max": a.kcal_max,
        "sodium_max": a.sodium_max,
        "meal": a.meal,
        "prefer": a.prefer,
        "reason3": a.reason3,
        "freight": a.freight,
        "packing": a.packing,
        "stamp": a.stamp,
    }
    crit = report.get("criteria") or {}
    for k in ("budget", "kcal_max", "sodium_max", "people"):
        if ctx.get(k) in (None, 0) and crit.get(k):
            ctx[k] = crit[k]
    return ctx


def main():
    ap = argparse.ArgumentParser(description="麦当劳品牌卡片渲染（纯本地，不联网）")
    ap.add_argument("--report", required=True, help="mcd_planner 输出的 JSON；- 表示 stdin")
    ap.add_argument("--out", default="", help="输出 HTML 路径；不给则打印到 stdout")
    ap.add_argument("--fragment", action="store_true", help="只输出卡片片段，不包 HTML 骨架")
    ap.add_argument("--widget", action="store_true",
                    help="输出喂可视化工具的片段：无骨架、无投影、铺满容器宽度")
    ap.add_argument("--people", type=int, default=0)
    ap.add_argument("--budget", type=float)
    ap.add_argument("--kcal-max", type=float)
    ap.add_argument("--sodium-max", type=float)
    ap.add_argument("--meal", default="", help="餐段：早餐/午餐/下午茶/晚餐/夜宵")
    ap.add_argument("--prefer", default="", help="用户口味原话，如「咸香」「想吃辣的」")
    ap.add_argument("--reason3", default="", help="覆盖第三条依据（口味/场景）")
    ap.add_argument("--freight", type=float, help="外送运费，元")
    ap.add_argument("--packing", type=float, help="打包费，元")
    ap.add_argument("--stamp", default="", help="数据时间，默认取当前时间")
    ap.add_argument("--strict", action="store_true", help="命中禁用词时以非 0 退出")
    a = ap.parse_args()

    raw = sys.stdin.read() if a.report == "-" else open(a.report, encoding="utf-8").read()
    try:
        report = json.loads(raw)
    except ValueError as e:
        print(f"✗ report 不是合法 JSON：{e}", file=sys.stderr)
        return 1

    if not report.get("ok") and not report.get("plans"):
        print(f"✗ 规划失败，没有可渲染的方案：{report.get('error')}", file=sys.stderr)
        return 2

    ctx = _ctx_from_args(a, report)
    frag = render(report, ctx, widget=a.widget)
    if a.widget:
        frag = _widget_safe(frag)
    out_html = frag if (a.fragment or a.widget) else page(frag)

    hits = [w for w in BANNED if w in out_html]
    if hits:
        print(f"⚠ 文案禁用词命中：{'、'.join(hits)}，请改 --reason3 / --prefer 的措辞",
              file=sys.stderr)
        if a.strict:
            return 3

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(out_html)
        print(f"✓ 卡片已写入 {a.out}（{len(out_html)} 字符，方案 {len(report.get('plans') or [])} 套）")
    else:
        print(out_html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
