#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""菜品图标：32×32 viewBox 的扁平矢量，按菜品类别取图。

为什么不用官方产品图
-------------------
菜单接口 `image` 字段给的是 427×320 的促销横幅 —— 带「随心配1+1」「100%纯牛肉」
这类角标文案、粉蓝撞色背景。缩到卡片上 34px 见方，只剩一团色块加几个字，
比没有图更糟。所以图标自己画：形状少、面积大、颜色是品牌色，34px 下还认得出。

兜底是金拱门
-----------
认不出类别的商品（新出的限定款之类）不画问号、不画通用汉堡，直接上金拱门：
用户看到的是「麦当劳的东西，具体哪款没图标」，而不是「这软件不认识这道菜」。

图标清单（21 类，覆盖本店 106 个 SKU + 早餐时段）
------------------------------------------------
主食：burger_beef（双层吉士/巨无霸/安格斯）、burger_chicken（麦辣/板烧/麦香鸡）、
      burger_fish（麦香鱼/深海鳕鱼）
小食：fries / hashbrown（心形薯饼）/ nuggets / fried_chicken（翅、脆汁鸡、鸡排）
      cheese_stick / corn / apple / sauce
甜品：cone / sundae / mcflurry / pie
饮品：drink_cold / drink_hot / carton（盒装奶、果汁）/ water
其他：tray（套餐/组合/分享装）、logo（兜底）

写路径注意：一个 path 必须是一整个字符串字面量。跨行拆成两个 f-string 时，
行尾数字会和下行数字粘在一起（`1.6-4` + `0-3.5` → `-40-3.5`），形状直接坏掉 ——
圆筒第一版就这么坏的，画出来只剩一条细描边。
"""

from __future__ import annotations

# 品牌色（与 render_card 同源，这里独立定义避免循环引用）
RED = "#DA291C"
GOLD = "#FFBC0D"
CREAM = "#FFF8E7"
WHITE = "#FFFFFF"
LINE = "#EADFC8"
SUB = "#8C857B"

BUN = "#F2B45C"
BUN_D = "#DE9A3F"
PATTY = "#7C4A28"
PATTY_D = "#653A1E"
CHEESE = "#FFC72C"
LETTUCE = "#5FA83A"
CRUST = "#E9AF5B"
CRUST_D = "#C98A3C"
CREAMY = "#FBEFD6"          # 奶油色：纯白在白色底板上等于隐形，暖一档才立得住
CREAM_EDGE = "#DCC08E"

# 金拱门：simple-icons 的 mcdonalds 路径（24×24），比手画的更准
ARCHES = ("M17.243 3.006c2.066 0 3.742 8.714 3.742 19.478H24c0-11.588-3.042-20.968-"
          "6.766-20.968-2.127 0-4.007 2.81-5.248 7.227-1.241-4.416-3.121-7.227-5.231-7.227"
          "C3.031 1.516 0 10.888 0 22.476h3.014c0-10.763 1.658-19.47 3.724-19.47 "
          "2.066 0 3.741 8.05 3.741 17.98h2.997c0-9.93 1.684-17.98 3.75-17.98Z")


def _arches(x: float, y: float, w: float, color: str) -> str:
    """把 24×24 的金拱门缩放到 (x, y) 处、宽 w。"""
    k = w / 24.0
    return (f'<path transform="translate({x:g} {y:g}) scale({k:.4f})" '
            f'fill="{color}" d="{ARCHES}"/>')


ICONS: dict[str, str] = {}

# ---------------------------------------------------------------- 主食：三种汉堡
# 靠「肉饼」区分：牛肉是棕色厚饼、鸡腿堡是金黄宽饼配生菜、鱼堡是浅色方饼出边
ICONS["burger_beef"] = (
    f'<path d="M4 15.4C4 10 9.4 6.6 16 6.6s12 3.4 12 8.8v.8H4z" fill="{BUN}"/>'
    f'<ellipse cx="11.6" cy="11.6" rx="1.5" ry=".9" fill="#FFE3AE"/>'
    f'<ellipse cx="16.4" cy="10.5" rx="1.5" ry=".9" fill="#FFE3AE"/>'
    f'<ellipse cx="21" cy="12" rx="1.5" ry=".9" fill="#FFE3AE"/>'
    f'<rect x="3.2" y="16.2" width="25.6" height="2.6" rx=".7" fill="{CHEESE}"/>'
    f'<rect x="3.2" y="18.8" width="25.6" height="3.6" rx=".9" fill="{PATTY}"/>'
    f'<rect x="4.2" y="22.4" width="23.6" height="1.7" rx=".8" fill="{PATTY_D}"/>'
    f'<rect x="3.6" y="24.1" width="24.8" height="2.8" rx="1.4" fill="{BUN_D}"/>'
)

ICONS["burger_chicken"] = (
    # 鸡腿堡的包不带芝麻，肉饼比包宽、露出生菜的波浪边
    f'<path d="M4 15.4C4 10 9.4 6.6 16 6.6s12 3.4 12 8.8v.8H4z" fill="{BUN}"/>'
    f'<path d="M3 15.4h26v1.2c-1.7 0-1.7 1.9-3.4 1.9s-1.7-1.9-3.4-1.9-1.7 1.9-3.4 1.9 '
    f'-1.7-1.9-3.4-1.9-1.7 1.9-3.4 1.9-1.7-1.9-3.4-1.9S4.7 16.6 3 16.6z" fill="{LETTUCE}"/>'
    f'<rect x="2.2" y="18.2" width="27.6" height="4.2" rx="1.7" fill="{CRUST}"/>'
    f'<rect x="2.2" y="21.6" width="27.6" height="1.2" rx=".6" fill="{CRUST_D}"/>'
    f'<circle cx="9.4" cy="20.2" r=".9" fill="{CRUST_D}"/>'
    f'<circle cx="16" cy="20" r=".9" fill="{CRUST_D}"/>'
    f'<circle cx="22.6" cy="20.2" r=".9" fill="{CRUST_D}"/>'
    f'<rect x="3.6" y="22.8" width="24.8" height="2.6" rx="1.3" fill="{BUN_D}"/>'
)

ICONS["burger_fish"] = (
    f'<path d="M4 15.4C4 10 9.4 6.6 16 6.6s12 3.4 12 8.8v.8H4z" fill="{BUN}"/>'
    f'<rect x="2.6" y="15.8" width="26.8" height="2.2" rx=".6" fill="{CHEESE}"/>'
    f'<rect x="2.6" y="18" width="26.8" height="4.4" rx="1.3" fill="#FBEBC8"/>'
    f'<rect x="2.6" y="22.4" width="26.8" height="1.3" rx=".65" fill="#D8B87E"/>'
    f'<rect x="3.6" y="23.7" width="24.8" height="2.6" rx="1.3" fill="{BUN_D}"/>'
)

# ---------------------------------------------------------------- 小食
ICONS["fries"] = (
    f'<rect x="9.4" y="7.4" width="3.4" height="9" rx="1.2" fill="{GOLD}"/>'
    f'<rect x="13.6" y="4.6" width="3.4" height="11.8" rx="1.2" fill="{CHEESE}"/>'
    f'<rect x="17.8" y="6.2" width="3.4" height="10.2" rx="1.2" fill="{GOLD}"/>'
    f'<rect x="22" y="8.4" width="3.4" height="8" rx="1.2" fill="{CHEESE}"/>'
    f'<path d="M7.6 14h17l-1.9 12.6c-.2 1.35-1.35 2.4-2.7 2.4h-7.8 '
    f'c-1.35 0-2.5-1.05-2.7-2.4z" fill="{RED}"/>'
    + _arches(12.6, 16.6, 6.8, GOLD)
)

ICONS["hashbrown"] = (
    # 心形薯饼
    f'<path d="M16 27.6c-6.6-4.4-10.8-7.8-10.8-12.2C5.2 12 7.9 9.8 11 9.8 '
    f'c1.9 0 3.6 1 4.9 2.7 1.3-1.7 3-2.7 4.9-2.7 3.1 0 5.8 2.2 5.8 5.6 '
    f'0 4.4-4.2 7.8-10.6 12.2z" fill="{CRUST}"/>'
    f'<circle cx="11.4" cy="15.4" r="1" fill="{CRUST_D}"/>'
    f'<circle cx="16" cy="13.2" r="1" fill="{CRUST_D}"/>'
    f'<circle cx="20.6" cy="15.6" r="1" fill="{CRUST_D}"/>'
    f'<circle cx="16" cy="19.4" r="1" fill="{CRUST_D}"/>'
)

ICONS["nuggets"] = (
    f'<circle cx="10.4" cy="12.6" r="3.6" fill="{CRUST}" stroke="{CRUST_D}" stroke-width="1"/>'
    f'<circle cx="16" cy="10.4" r="3.6" fill="{CRUST}" stroke="{CRUST_D}" stroke-width="1"/>'
    f'<circle cx="21.6" cy="12.6" r="3.6" fill="{CRUST}" stroke="{CRUST_D}" stroke-width="1"/>'
    f'<path d="M6.4 16.4h19.2l-1.9 10c-.25 1.35-1.4 2.4-2.8 2.4H11.1 '
    f'c-1.4 0-2.55-1.05-2.8-2.4z" fill="{WHITE}" stroke="{LINE}" stroke-width="1.1"/>'
    f'<path d="M9 21.4h14l-.6 4.2H9.6z" fill="{RED}"/>'
)

ICONS["fried_chicken"] = (
    # 麦辣鸡翅 / 薄皮 V 翅 / 脆汁鸡 / 那么大鸡排：红盒装炸鸡。
    # 画过四版鸡腿 —— 34px 下肉块加骨头一律读成棒棒糖或放大镜，盒子最不容易跑偏。
    f'<ellipse cx="11.6" cy="12.4" rx="5.2" ry="4.2" fill="{CRUST}"/>'
    f'<ellipse cx="20.4" cy="11.2" rx="5.6" ry="4.4" fill="{CRUST}"/>'
    f'<circle cx="10.4" cy="11" r="1" fill="{CRUST_D}"/>'
    f'<circle cx="21.4" cy="9.8" r="1" fill="{CRUST_D}"/>'
    f'<path d="M4.8 15.8h22.4l-2 10.8c-.26 1.4-1.5 2.4-2.9 2.4H9.7 '
    f'c-1.4 0-2.64-1-2.9-2.4z" fill="{RED}"/>'
    f'<path d="M9 21.2h14l-.6 4.4H9.6z" fill="#F6E7C8"/>'
)

ICONS["cheese_stick"] = (
    # 马苏里拉拉丝芝士条：画芝士角。棒子形状在 34px 下像两根香肠，角一眼就是芝士。
    f'<path d="M5.4 19.4L21.4 6.6c1.6-1.3 4-.2 4 1.9v12.6c0 1.1-.9 2-2 2H7.2 '
    f'c-1.8 0-2.8-2-1.8-3.3z" fill="{CHEESE}" stroke="#E9A63C" stroke-width="1"/>'
    f'<circle cx="14.4" cy="18.4" r="1.5" fill="#F0AE4E"/>'
    f'<circle cx="19.6" cy="14.4" r="1.2" fill="#F0AE4E"/>'
)

ICONS["corn"] = (
    f'<circle cx="12" cy="11.4" r="2.1" fill="{GOLD}"/>'
    f'<circle cx="16" cy="9.8" r="2.1" fill="{CHEESE}"/>'
    f'<circle cx="20" cy="11.2" r="2.1" fill="{GOLD}"/>'
    f'<circle cx="13.4" cy="15.4" r="2.1" fill="{CHEESE}"/>'
    f'<circle cx="17.8" cy="15.2" r="2.1" fill="{GOLD}"/>'
    f'<circle cx="16" cy="18.6" r="2.1" fill="{CHEESE}"/>'
    f'<path d="M8.2 14.6h15.6l-1.7 11.4c-.2 1.35-1.35 2.4-2.7 2.4h-6.8 '
    f'c-1.35 0-2.5-1.05-2.7-2.4z" fill="{CREAMY}" stroke="{LINE}" stroke-width="1.1"/>'
)

ICONS["apple"] = (
    f'<path d="M16 10.4c3.9 0 7 3.1 7 7.6 0 4.4-3.1 8-7 8s-7-3.6-7-8c0-4.5 3.1-7.6 7-7.6z" '
    f'fill="{RED}"/>'
    f'<path d="M16 10.6c-.2-2 .8-3.6 2.6-4.4" stroke="#7C4A28" stroke-width="1.6" '
    f'stroke-linecap="round" fill="none"/>'
    f'<path d="M18.6 7.6c1.8-2 4.4-2 5.6-1.4-.6 1.8-2.8 3.2-5.6 1.4z" fill="{LETTUCE}"/>'
    f'<path d="M13.6 14.2c1.2-1.2 2.8-1.6 4-1.2" stroke="#F08A7E" stroke-width="1.4" '
    f'stroke-linecap="round" fill="none"/>'
)

ICONS["sauce"] = (
    f'<ellipse cx="16" cy="12.6" rx="6.8" ry="2.6" fill="#C7301F"/>'
    f'<path d="M9.2 12.6h13.6l-1.5 12c-.17 1.35-1.3 2.4-2.65 2.4h-5.3 '
    f'c-1.35 0-2.48-1.05-2.65-2.4z" fill="{WHITE}" stroke="{LINE}" stroke-width="1.1"/>'
    f'<path d="M10.2 16.4h11.6" stroke="{LINE}" stroke-width="1.2"/>'
)

# ---------------------------------------------------------------- 甜品
ICONS["cone"] = (
    # 一球奶油 + 蛋筒：上球后画，压住下球的描边，省掉布尔运算
    f'<circle cx="16" cy="12.6" r="6.6" fill="{CREAMY}" stroke="{CREAM_EDGE}" stroke-width="1.2"/>'
    f'<circle cx="16" cy="7.6" r="4.4" fill="{CREAMY}" stroke="{CREAM_EDGE}" stroke-width="1.2"/>'
    f'<path d="M11.2 16.4h9.6L16 28.6z" fill="{CRUST}"/>'
    f'<path d="M13.4 16.6l2 9.4M18.6 16.6l-2 9.4" stroke="{CRUST_D}" stroke-width=".9"/>'
)

ICONS["sundae"] = (
    # 新地：矮杯 + 厚奶油 + 巧克力酱走线
    f'<path d="M8.2 15.4c0-4.3 3.5-7.8 7.8-7.8s7.8 3.5 7.8 7.8z" fill="{CREAMY}" '
    f'stroke="{CREAM_EDGE}" stroke-width="1.2"/>'
    f'<path d="M10.2 12.6c1.5-1 3-.2 4.4-.4s2.6-1.2 3.8-.4 2.2.4 3.2.6" stroke="#6B3E22" '
    f'stroke-width="2.4" stroke-linecap="round" fill="none"/>'
    f'<path d="M8.2 15.2h15.6l-1.7 10.2c-.22 1.3-1.33 2.3-2.65 2.3h-6.9 '
    f'c-1.32 0-2.43-1-2.65-2.3z" fill="{CREAMY}" stroke="{LINE}" stroke-width="1.1"/>'
)

ICONS["mcflurry"] = (
    f'<rect x="14.6" y="3" width="2" height="10" rx="1" transform="rotate(8 15.6 8)" '
    f'fill="#C9C1AF"/>'
    f'<rect x="16.4" y="2.4" width="4.6" height="4.4" rx="1.4" transform="rotate(8 18.7 4.6)" '
    f'fill="#C9C1AF"/>'
    f'<ellipse cx="16" cy="13" rx="6.4" ry="1.9" fill="{CREAMY}" stroke="{CREAM_EDGE}" '
    f'stroke-width="1"/>'
    f'<rect x="11.4" y="11.4" width="2.4" height="1.8" rx=".5" fill="#6B3E22"/>'
    f'<rect x="17.4" y="11.6" width="2.2" height="1.6" rx=".5" fill="#A8552B"/>'
    f'<path d="M9.6 13.6h12.8l-1.4 12.8c-.15 1.4-1.3 2.4-2.7 2.4h-4.6 '
    f'c-1.4 0-2.55-1-2.7-2.4z" fill="{WHITE}" stroke="{LINE}" stroke-width="1.1"/>'
    f'<rect x="9.9" y="17.8" width="12.2" height="3.2" fill="{RED}"/>'
)

ICONS["pie"] = (
    # 切片：左边酥皮、右边流心。长条版本在小尺寸下读成一条面包，反而误导。
    f'<path d="M4.6 12.4h3.2l14.4 9.4c1.5 1 .8 3.4-1 3.4H4.6c-1.3 0-2.4-1.1-2.4-2.4v-8 '
    f'c0-1.3 1.1-2.4 2.4-2.4z" fill="{CRUST}"/>'
    f'<path d="M2.2 12.4h5.6v12.8H4.6c-1.3 0-2.4-1.1-2.4-2.4z" fill="{CRUST_D}"/>'
    f'<path d="M10.6 17.6h6.4M10.6 20.6h4.6" stroke="{CRUST_D}" stroke-width="1.2" '
    f'stroke-linecap="round"/>'
)

# ---------------------------------------------------------------- 饮品
ICONS["drink_cold"] = (
    f'<rect x="18.4" y="2.6" width="2.4" height="9" rx="1.2" transform="rotate(12 19.6 7.1)" '
    f'fill="{RED}"/>'
    f'<rect x="6.8" y="9.2" width="18.4" height="3.4" rx="1.7" fill="#EDE4D0" stroke="{LINE}" '
    f'stroke-width="1"/>'
    f'<path d="M8 12.4h16l-1.6 14.4c-.16 1.35-1.3 2.4-2.65 2.4h-7.5 '
    f'c-1.35 0-2.49-1.05-2.65-2.4z" fill="{WHITE}" stroke="{LINE}" stroke-width="1.1"/>'
    f'<path d="M8.7 17.2h14.6l-.6 4.6H9.3z" fill="{RED}"/>'
)

ICONS["drink_hot"] = (
    f'<path d="M12.6 6.4c-1-1.3 1-2 0-3.4M19.4 6.4c-1-1.3 1-2 0-3.4" stroke="#CFC7B6" '
    f'stroke-width="1.4" stroke-linecap="round" fill="none"/>'
    f'<rect x="8.4" y="7.8" width="15.2" height="3.2" rx="1.6" fill="{PATTY}"/>'
    f'<path d="M9.2 10.8h13.6l-1.4 13c-.14 1.3-1.26 2.3-2.6 2.3h-5.6 '
    f'c-1.34 0-2.46-1-2.6-2.3z" fill="{WHITE}" stroke="{LINE}" stroke-width="1.1"/>'
    f'<rect x="9.8" y="15.4" width="12.4" height="3.6" fill="#8B5A33"/>'
)

ICONS["carton"] = (
    f'<path d="M10.4 10.6l2.6-3.4h6l2.6 3.4z" fill="#F3EBD9" stroke="{LINE}" stroke-width="1"/>'
    f'<path d="M10.4 10.6h11.2v14.2c0 1.3-1 2.2-2.3 2.2h-6.6c-1.3 0-2.3-.9-2.3-2.2z" '
    f'fill="{WHITE}" stroke="{LINE}" stroke-width="1.1"/>'
    f'<rect x="10.4" y="15" width="11.2" height="3.8" fill="{RED}"/>'
    f'<rect x="10.4" y="19.4" width="11.2" height="1.2" fill="{GOLD}"/>'
)

ICONS["water"] = (
    f'<rect x="13.6" y="4" width="4.8" height="2.8" rx="1" fill="#7FB4CC"/>'
    f'<rect x="14.4" y="6.8" width="3.2" height="2" fill="#BFE3F2"/>'
    f'<path d="M11.8 8.8h8.4c1.2 0 2 1 2 2.2v13c0 1.7-1.3 2.8-2.9 2.8h-6.6 '
    f'c-1.6 0-2.9-1.1-2.9-2.8v-13c0-1.2.8-2.2 2-2.2z" fill="#DCEFF7" stroke="#A9D3E4" '
    f'stroke-width="1.1"/>'
    f'<rect x="10.8" y="14.6" width="10.4" height="4.6" fill="{WHITE}"/>'
)

# ---------------------------------------------------------------- 其他
ICONS["tray"] = (
    # 套餐/组合/分享装：红纸袋 + 金拱门，一眼「这是套的」
    f'<path d="M7 9.4h18l.9 15.6c.1 1.4-1 2.6-2.4 2.6H8.5c-1.4 0-2.5-1.2-2.4-2.6z" '
    f'fill="{RED}"/>'
    f'<rect x="6" y="9.4" width="20" height="2.8" rx="1.4" fill="#F6E7C8"/>'
    + _arches(8.8, 13.4, 14.4, GOLD)
)

ICONS["logo"] = _arches(4, 8, 24, GOLD)


# ---------------------------------------------------------------- 归类
# 顺序即优先级：套餐标记先判（「龙焰鸡腿堡三件套」是套不是单品），
# 「鸡块」先于「酱」（「蘸酱韩式甜辣酱鸡块」是鸡块不是酱）。
_RULES: list[tuple[str, tuple[str, ...]]] = [
    # 盒装奶/果汁排在纸袋前面：tray 的「盒」会把「纯牛奶(盒装)」抢走，
    # 画成一个纸袋 —— 原来就是这么错的
    ("carton", ("牛奶", "盒装", "果汁")),
    ("tray", ("随心配", "四件套", "三件套", "套餐", "分享", "小食盘", "随心拼",
              "双人餐", "亲子", "百宝袋", "欢乐盒", "双全盒", "任选", "四宫格")),
    ("cone", ("圆筒", "甜筒")),
    ("sundae", ("新地",)),
    ("mcflurry", ("麦旋风", "旋风")),
    ("pie", ("派",)),
    ("hashbrown", ("薯饼",)),
    ("fries", ("薯条", "扭扭薯")),
    ("corn", ("玉米杯", "玉米")),
    ("apple", ("苹果片",)),
    ("nuggets", ("鸡块", "麦乐鸡")),
    ("cheese_stick", ("芝士条", "芝士棒", "芝士棍")),
    ("fried_chicken", ("鸡翅", "V翅", "脆汁鸡", "炸鸡", "鸡排", "翅")),
    ("sauce", ("酱",)),
    ("water", ("纯悦", "矿泉水", "饮用水")),
    ("drink_hot", ("咖啡", "红茶", "拿铁", "美式", "热巧")),
    ("drink_cold", ("可乐", "雪碧", "怡泉", "+C", "美汁源", "柠柠", "橙橙",
                    "柠檬红茶", "麦炫酷", "冰")),
    ("burger_fish", ("麦香鱼", "鳕鱼", "鱼排", "三文鱼", "双虾")),
    ("burger_beef", ("巨无霸", "安格斯", "双牛", "吉士", "不素之霸", "培根蔬萃",
                     "牛肉堡", "牛堡")),
    ("burger_chicken", ("鸡腿堡", "麦辣", "板烧", "麦香鸡", "鸡肉堡", "龙焰",
                        "咸蛋黄", "蛋月堡", "堡")),
]


def pick(name: str) -> str:
    """按菜名归类。认不出就返回 logo —— 不猜、不硬套一个汉堡糊弄。"""
    n = (name or "").strip()
    if not n:
        return "logo"
    for key, words in _RULES:
        if any(w in n for w in words):
            return key
    return "logo"


def svg(key: str, size: int = 38) -> str:
    body = ICONS.get(key) or ICONS["logo"]
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 32 32" '
            f'aria-hidden="true" focusable="false">{body}</svg>')


def _attr(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def badge(name: str, size: int = 38) -> str:
    """图标底板：白底 + 1px 描边，统一 38px 见方；认不出类别时红底金拱门。"""
    key = pick(name)
    logo = key == "logo"
    bg = RED if logo else WHITE
    pad = 0 if logo else 1
    inner = svg(key, size - pad * 2)
    return (f'<div title="{_attr(name)}" style="width:{size}px;height:{size}px;'
            f'flex:0 0 {size}px;border-radius:10px;background:{bg};'
            f'border:1px solid {LINE};overflow:hidden;display:flex;'
            f'align-items:center;justify-content:center;">{inner}</div>')


def contact_sheet() -> str:
    """开发自检用：所有图标铺一张表，34 / 56 / 96 三档并排看。"""
    cells = []
    for key in ICONS:
        cells.append(
            f'<div style="display:inline-flex;flex-direction:column;align-items:center;'
            f'gap:6px;margin:10px 14px;">'
            f'<div style="display:flex;align-items:flex-end;gap:10px;">'
            f'<div style="background:{WHITE};border:1px solid {LINE};border-radius:10px;'
            f'padding:2px;">{svg(key, 34)}</div>'
            f'<div style="background:{WHITE};border:1px solid {LINE};border-radius:14px;'
            f'padding:4px;">{svg(key, 56)}</div>'
            f'<div style="background:{WHITE};border:1px solid {LINE};border-radius:20px;'
            f'padding:6px;">{svg(key, 96)}</div></div>'
            f'<div style="font-size:13px;color:{SUB};">{key}</div></div>')
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
            "body{margin:0;padding:20px;background:#F2E9D8;"
            'font-family:-apple-system,"PingFang SC",sans-serif;}</style></head><body>'
            + "".join(cells) + "</body></html>")


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/icons.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(contact_sheet())
    print(f"{len(ICONS)} 个图标 → {out}")
