# 开发说明

给要改这个 skill 的人看。用户向的说明在 [README.md](README.md)。

---

## 目录结构

```
SKILL.md                  主文件：五步流程、三道门、铁律、故障速查（模型读这个）
_icon.png                 图标（512×512，透明圆角）
assets/icon.svg           图标矢量源文件
references/
  brand-visual.md         卡片视觉与文案调性：色板、字号、骨架、禁用词
  personas.md             六画像 → CLI 参数；口味/饮食偏好映射表
  mcp-contract.md         接口契约与实测坑（混合文本、单位两套、TOON、外送差异）
  nutrition-alignment.md  营养对齐四层规则、三种必须拒绝的情况
  store-lookup.md         查店五条硬事实、错误码归因、降级顺序
scripts/
  mcd_planner.py          主入口：查店→菜单→拆套餐→求解→核价→出卡，一条命令
  render_card.py          卡片渲染器：JSON → HTML，色板/字号/禁用词代码强制
  mcd_icons.py            21 类菜品图标 + 菜名归类，认不出时兜底金拱门
  nutrition_solver.py     求解器：营养对齐、套餐拆解、组合搜索、四重约束
  locate_store.py         查店排障：错误码归因、备用地标重试
  mcd_mcp.py              底层客户端：token、SSE、raw_decode、分元换算、只读缓存
tests/
  test_solver.py          46 条离线断言，不联网
  test_render.py          44 条离线断言，不联网
  fixtures/               实测快照（菜单、营养表、套餐详情）
```

---

## 命令行

```bash
# 一条命令出方案 + 卡片（--widget 给对话内嵌，--html 给独立页面）
python3 scripts/mcd_planner.py --city 雄安 --keyword 容东 --people 3 --budget 60 \
    --meal 晚餐 --widget /tmp/mcd_plan.html --brief

# 控卡单人餐
python3 scripts/mcd_planner.py --city 雄安 --keyword 容东 --people 1 \
    --kcal-max 700 --protein-min 25 --budget 35 --structure with_drink --html /tmp/mcd_plan.html

# 已知 storeCode
python3 scripts/mcd_planner.py --store 1950739 --budget 30

# 多人：3 人总预算 90，人手一份 / 分享装 / 差异化三条路并跑
python3 scripts/mcd_planner.py --store 1950739 --people 3 --budget 90

# 3 人聚餐、其中一位在减肥：700 是那一人的上限，其余两位按常规量
python3 scripts/mcd_planner.py --store 1950739 --people 3 --budget 130 \
    --meal 午餐 --kcal-max 700 --light-for 1 --exclude 辣

# 外送：order-type=2 时 be-type 也是 2，且必须带 beCode
python3 scripts/mcd_planner.py --store 1950401 --be-type 2 --order-type 2 \
    --be-code 195040102 --budget 20
```

输出一份 JSON（门店、约束、每套方案的商品／价格／营养／核价），`--html` 额外产出一张卡片。
`--meal` 必传：菜单按时段切，不传会把早餐菜单当晚餐算。

**二次渲染**：`--html` / `--widget` 会在卡片旁落一份 `.json` 存档。用户挑定某一套时用
`--from --pick`，只换渲染、不重算 —— 实测 8.76s → 0.02s。

```bash
python3 scripts/mcd_planner.py --store 1950401 --people 3 --budget 130 \
    --meal 午餐 --kcal-max 420 --light-for 1 --top-k 2 --html /tmp/plan.html

# 用户说「要第二套」
python3 scripts/mcd_planner.py --from /tmp/plan.html.json --pick 2 --html /tmp/plan_b.html
```

---

## 测试

```bash
python3 tests/test_solver.py     # 46 条，1.3s，不联网
python3 tests/test_render.py     # 44 条，不联网
```

两组都是纯离线断言，跑在 `tests/fixtures/` 的实测快照上，改求解器或卡片先跑这两组。

---

## 一次完整流程要多久

v1.0 跑过一次完整流程用了十几分钟。拆开看，慢的地方一个都不在网络：

| 环节 | v1.0 | 现在 | 怎么改的 |
|---|---|---|---|
| 拉数（门店+菜单+30 个套餐拆解+30 次核价+券） | 6.8s | 6.8s | 本来就是并发，没动 |
| 生成卡片 | 几分钟 | <20ms | 不让模型逐字写 HTML，改确定性渲染 |
| 读参考文档 | 60KB 五份全读 | 0 | 排障手册，正常流程不读 |
| 规划阶段调 MCP | 逐条调，一次菜单上千行 | 0 次 | 只跑脚本 |
| 追问 | 多轮挤牙膏 | 1 轮 4 题 | 一次问完 |
| 改条件重算（「换一批」） | 重拉全部 | 0.16s | 只读调用加 5 分钟缓存 |
| 选完方案再出图 | 重算 8.76s | 0.02s | `--from --pick` 读存档渲染 |

冷启动规划+出卡 8.5s，热缓存重算 0.16s，脚本自报耗时（JSON 里的 `timing`）。

卡片交给脚本还顺带解决了三件事：品牌色不会跑偏、禁用词由代码拦截、
数字只能来自 planner 的 JSON（模型没有机会编）。

---

## 卡片渲染

单卡 ≤432px，`flex-wrap` 铺两列：宽屏两套并排，窄屏（<860px）折成上下流式。
`--widget` 直接塞进对话里的可视化工具，该模式去投影、基宽收到 300px（宿主容器约 680px，
392 会被折成一列）、属性统一单引号免转义。约束太紧时先松营养上限、再松预算补第二套，
补出来的带「超预算」角标。

五层骨架（顺序固定）：

| 层 | 内容 | 关键约束 |
|---|---|---|
| L1 | 红底标题：`方案 A` + 角标 + 具象方案名 |「双层吉士高蛋白暴击套」，不是「方案A」当标题 |
| L2 | 餐品清单：套餐拆成单品逐行，图标 + 来源前缀 + 菜名 + 份数 + 千卡 | 名字 `nowrap` 截断 + `title`；查不到营养写「未收录」 |
| L3 | 价格看板：42px 实付 + 立省绿标 + 划线原价 + 人均 | 数字来自 `calculate-price`，运费打包另列 |
| L4 | 营养看板：合计 kcal / 蛋白 / 钠，超限标红 | 缺数据说「未收录」，不填 0 |
| L5 | 三条依据：算账 / 营养 / 口味或搭配 | 每条都指得回具体数字 |

餐品清单不出现套餐 SKU 名。套餐和随心配按 `parts` 摊成单品，一行一个（主食在前、
饮品垫后、甜品收尾），跨套餐重复出现时合并份数。份数全清单一致时标题写「人手一份 ×3」，
行内不再挂 ×3。单品营养查不到就显示「未收录」灰胶囊，不拿套餐合计摊到每行上。

差异化配餐（`--light-for N`）时清单分三组，每组带小标题和每人均值：

```
轻食  1 人            每人 369 kcal
  麦香鸡   369 kcal
  纯悦       0 kcal
照常  2 人            每人 614 kcal
  双层深海鳕鱼堡  485 kcal
  纯牛奶(盒装)    129 kcal
共享  大家分
  薯条  ≈289 kcal
```

底部看板跟着改成「轻食那位 369 kcal」而不是「人均 629 kcal」——
控卡的人看到 629 会以为自己也吃了 629。

**图标**不是官方产品图。菜单接口给的是 427×320 促销横幅（带角标文案、撞色底），
缩到 34px 只剩色块，所以自绘 21 类 SVG（汉堡三种、薯条、薯饼、鸡块、炸鸡盒、芝士角、
玉米、苹果、圆筒、新地、麦旋风、派、冷饮、热饮、盒装奶、纯悦、蘸酱、套餐纸袋），
认不出类别时上红底金拱门。`python3 scripts/mcd_icons.py out.html` 可导出 34/56/96 对照表。

**设计令牌**写死在 `render_card.py` 顶部，不跟随宿主主题：红 `#DA291C`、金 `#FFBC0D`、
奶油 `#FFF8E7`、炭黑 `#27251F`、中灰 `#8C857B`、省钱绿 `#1B873F`；外层圆角 18px、内层 12px。

三个兜底都在代码里，不靠自觉：图挂了换金拱门徽章、长菜名单行截断 + `title`、
卡片 `min-width:0`（flex 子项默认 `auto` 会把卡顶出屏幕，375px 下无横向溢出）。

---

## 几个实现取舍

**套餐必须拆。** 菜单里的套餐在营养表里没有条目。直接按名字查只命中 16.5%（18/109）；
调 `query-meal-detail` 拆成子项再累加，命中率到 87.2%（34/39）。不拆就是拿「麦香鱼」
的 325 kcal 去冒充「麦香鱼套餐」的 761 kcal。

**营养缺失不能当 0。** 缺数据的商品如果填 0 参与控卡计算，方案会看着合规、实际超标。
求解器把这类商品直接排除，卡片上标「未收录」。

**外送和到店是两套菜单。** 同一家店同一晚：到店 122 个 SKU 有「随心配」，外送 125 个
一个都没有，同款还贵 1 元。算价接口报 `60006 商品已售罄`，真实含义是「这份菜单不属于
这个 orderType」。

**其他**：四重约束（热量／蛋白／钠／预算）；营养指纹去重，防止多买 0 卡饮料凑单；
`calculate-price` 返回单位是**分**，不是元。

---

## 需求对齐

| 要求 | 落点 |
|---|---|
| 人数 | Gate 1 必问 → `--people N`。多人时预算是总额、营养约束是人均 |
| 预算 | Gate 1 必问 → `--budget`。到店价与外卖价是两套菜单，运费打包另计 |
| 口味与饮食偏好 | 口味映射 `--include/--exclude`，饮食偏好映射 `--kcal-max/--protein-min/--sodium-max/--objective` |
| 搭配清晰 | 逐件分行，主品/配品分层，份数写明；套餐必须展示拆解子项 |
| 总价有依据 | 逐项构成：商品 + 运费 + 打包 = 实付，不许只给一个数 |
| 选择有依据 | 每套一句排序理由，三条依据各自指得回具体商品或具体约束 |

两条不可让的规矩：

- 所有需要用户输入的地方一律走 `AskUserQuestion`，不在正文里写「请告诉我你的地址」
  让用户打字。单字段单题，题目里写清填写格式，选项位只放真实分支决策。
- 没被明确选择不下单。选项文字里必须出现「下单」两个字，`create-order` 全流程只调一次 ——
  麦当劳 MCP 单账号每日下单有硬上限（约 4 次），取消订单不退还额度。

---

## 未验证项

- `create-order` 未做真实订单验证 —— 会真实扣款。参数骨架按工具 schema 写，
  `takeWayCode` 取自 `calculate-price` 返回的 `takeWayList[].code`。
- 团餐阶梯优惠（满 300/500 折上折）不在求解器内，需在核价后单独叠加。
