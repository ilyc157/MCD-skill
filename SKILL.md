---
name: mcd-meal-planner
version: 1.2.0
display_name: 麦乐配 · 麦当劳点餐搭配
display_name_en: McDonald's Meal Planner
description: "麦当劳配餐与点餐全链路助手。当用户表达用餐意图、要求推荐麦当劳吃什么、点麦乐送或到店取餐、按人数/预算/热量/蛋白/忌口搭配餐品、用优惠券省钱、询问麦当劳餐品热量、或要下单时使用。会先追问必要信息，再基于实时菜单与官方营养数据求解搭配，用可视化卡片展示方案，用户选定后才下单。触发词：吃麦当劳、点麦当劳、麦乐送、来份套餐、不知道吃什么、控卡怎么点、帮我配一餐。"
description_zh: "麦当劳配餐与点餐全链路助手（基于麦当劳中国官方 MCP）。当用户表达用餐意图、要求推荐麦当劳吃什么、点麦乐送或到店取餐、按人数/预算/热量/蛋白/忌口搭配餐品、用优惠券省钱、询问麦当劳餐品热量、或要下单时使用。会先追问必要信息，再基于实时菜单与官方营养数据求解搭配，用可视化卡片展示方案，用户选定后才下单。"
description_en: "End-to-end McDonald's China meal planning and ordering via the official MCP. Ask follow-ups, solve combos against live menus and official nutrition, present a visual card, and only order after the user picks."
agent_created: true
---

# 麦当劳智能配餐

把"今天吃啥"这件事从头做到尾：**问清楚 → 算明白 → 看着选 → 你点头我才下单**。

数据全部来自麦当劳中国官方 MCP（`https://mcp.mcd.cn`，35 个工具），不用网页搜索、不用预置菜单。

---

## ⏱ 时间账：饿着的人等不了

用户说"点麦当劳"的时候是饿的。**从他说完需求，到看见方案，目标 60 秒内** ——
其中等人输入的时间不算，剩下全是你要守住的。

| 环节 | 预算 | 谁做 | 怎么做到 |
| --- | --- | --- | --- |
| 追问 | **1 轮**（≤4 题） | 模型 | 一次问完，别挤牙膏 |
| 规划 + 出卡 | 首次 ≤10s，选定/改条件 ≤1s | 脚本 | 一条命令出 JSON + HTML |
| 展示 | 1 次 | 模型 | `present_files` 打开卡片文件 |
| 选方案 | **1 轮** | 模型 | 选项里写明"下单"与代价 |
| 下单 | 1 次 | 模型 | `create-order`，只调一次 |

**四条禁令，都是实测出来的**（v1.0 一次完整流程跑了十几分钟，用户饿到骂人）：

1. **卡片必须用 `--html` 让脚本生成，不许自己写 HTML/SVG。**
   脚本出卡 <20ms。模型手写一版要几分钟，而且每次都不一样、品牌色跑偏。
2. **规划阶段只跑脚本，不许逐条调 `mcp__mcdonald__*`。**
   一次 `query-meals` 返回上千行 markdown，读进上下文会让后面每一步都变慢十几倍。
   MCP 工具留给"脚本报错要单点排查"和"最后下单"。
3. **不许预先读 `references/*.md`。** 那是排障手册。正常流程不用看，
   出错对照「故障速查」，或要改卡片样式时才翻 `brand-visual.md`。
4. **用户挑定方案后，用 `--from 存档 --pick N` 出卡，绝对不许重跑求解。**
   `--html` 会顺手在卡片旁落一份 `.json` 存档。重跑一遍要 8–9 秒（拉菜单+核价+读营养表），
   而解还是同一批解 —— 用户看到的就是"我选了 A，你又想了十分钟"。
   ```bash
   # 第一次：一次出两个，同时落存档（约 9s）
   ... --top-k 2 --html temp/plan.html
   # 用户选 B 之后：只换渲染，0.2s
   ... --from temp/plan.html.json --pick 2 --html temp/plan_pick.html
   ```
   **能用 1 个方案满足就别开 `--top-k 2`** —— 多摆一个选项，就多换一轮选择。

---

## 流程总览

```
Gate 1 追问：一次 4 题 → 拿到 人数/预算/口味/取餐方式/地点
   ↓
Step 2 一条命令：规划 + 出卡（scripts/mcd_planner.py --html）
   ↓
Gate 2 展示 + 选择：show_widget 内嵌卡片 → AskUserQuestion 让用户选
   ↓
Gate 3 下单：用户点了带"下单"的选项，才调 create-order
```

---

## Gate 1：追问（`AskUserQuestion`）

**一次问完（≤4 题，1 轮）。** 先看用户已经给了什么，缺哪项问哪项。

| 要素 | 缺了怎么办 |
| --- | --- |
| 人数 | 必问 → `--people`。多人时预算是**总额**，营养约束是**人均** |
| 预算 | 必问 → 决定能不能加饮品 |
| 口味 / 饮食偏好 | 必问 → 同时喂给 `--prefer`，卡片依据里会用到 |
| 取餐方式 | 必问 → 到店 `--order-type 1 --be-type 1`；外送 `2/2` 且需 `beCode` |
| 城市 / 地标 | 必问 → `--city --keyword`（两个都要，缺一报 600058） |
| 餐段 | 从时间推断（现在几点就是几点），跨餐段要 `--meal` |

**规则：**

1. **能不问就不问。** 到店自取**永远不需要**地址和手机号；用户已经说过的别再问。
2. **题目写清填写格式**，选项位只放真实分支。
   例：`配送地址？在下方输入框写：小区/大厦 + 楼栋门牌`。
3. 地址、手机号这类自由文本，**独立一题、一个输入框**，不要合并成一道题让人填三样。
4. 问不出来就给默认值（1 人 / 到店自取 / 不设预算），并在卡片口径里注明。

```
Q1 几个人吃？        [1 人] [2 人] [3-4 人] [5 人以上]
Q2 预算多少？        [20 元内] [30 元内] [40 元内] [不设限]
Q3 口味偏好？        [想吃辣] [清淡不辣] [甜口] [都行]
Q4 怎么取？          [到店自取] [麦乐送（需地址）] [得来速]
```

---

## Step 2：一条命令出方案 + 卡片

```bash
cd ~/.workbuddy/skills/mcd-meal-planner

# 到店：3 人晚餐 60 元，直接出卡片
python3 scripts/mcd_planner.py --city 雄安 --keyword 容东 \
    --people 3 --budget 60 --meal 晚餐 --prefer 想吃辣 \
    --html /tmp/mcd_plan.html --brief

# 控卡单人餐
python3 scripts/mcd_planner.py --city 雄安 --keyword 容东 \
    --people 1 --budget 25 --kcal-max 600 --structure with_drink \
    --meal 午餐 --html /tmp/mcd_plan.html --brief

# 已知 storeCode；外送；预约
python3 scripts/mcd_planner.py --store 1950739 --people 2 --budget 80 --meal 晚餐 \
    --be-type 2 --order-type 2 --be-code 195040102 --html /tmp/mcd_plan.html --brief
```

**这十条参数的含义（`--brief` 别省，它把核价明细砍掉，输出只剩结论）：**

| 参数 | 说明 |
| --- | --- |
| `--people` | 人数。≥2 时预算=总额、营养=人均，人手一份/分享装/差异化三条路并跑 |
| `--light-for N` | **同桌上 N 个人在控卡**（必须配 `--kcal-max`）。见下面「有人减肥怎么点」 |
| `--budget` | 预算上限，**按核价后的实付判**，不是菜单标价 |
| `--kcal-max` `--protein-min` `--sodium-max` | 营养约束，钠默认 1500mg |
| `--structure with_drink` | 强制主食 + 饮品；`full` = 主食 + 饮品/小食 |
| `--exclude 辣 牛肉` | 忌口关键词。**按整份拆开查**：套餐名不含辣、里面装着麦辣鸡翅的分享装，一样排除 |
| `--objective` | `value`（蛋白/元，默认）/`budget`/`kcal`/`sodium` |
| `--meal` | **必传**。早餐/午餐/下午茶/晚餐/夜宵 |
| `--prefer` | 用户口味原话，写进卡片第三条依据 |
| `--html PATH` | 生成品牌卡片；不给就只输出 JSON |
| `--no-cache` | 数据可疑时强制重拉（默认缓存 5 分钟，改条件重算 <1s） |

**输出**：一份 JSON（门店、约束、每套方案的商品/价格/营养/核价）+ 一个卡片 HTML。
**不要自己重算价格和热量** —— 脚本已经算过，手算必错。

### `--meal` 为什么必传

菜单是**按时段切**的。实测同一家店：早上 08:58 拿到 72 个 SKU，全是麦满分/油条/粥；
传 `reservationDate=当天 18:00` 后拿到 106 个 SKU，全是汉堡/套餐。
用户说"今晚吃"而现在还是早上，不传餐段就会**拿早餐菜单配晚餐**，
算出来是「油条 ×3」—— 价格没错，但没人这么吃。

脚本会自己判断：当前不在该餐段 → 自动补 `reservationDate` 取对应时段菜单，
并在卡片口径里写明"按 17:30 的菜单规划"。**你不用管，但 `--meal` 一定要给。**

### 有人说「在减肥 / 控卡 / 吃不下太多」

`--kcal-max` 是**人均**口径 —— 三个人传 700，等于要求三个人**每人都**吃 700 以内。
一个人减脂的时候这么算，另外两位会跟着一起啃减脂餐，实测输出「三个鳕鱼堡 + 三盒牛奶」，
一桌子没有一样能分着吃的东西。

这种时候多传一个 `--light-for 1`：

```bash
python3 scripts/mcd_planner.py --store 1950739 --people 3 --budget 130 --meal 午餐 \
    --kcal-max 700 --light-for 1 --exclude 辣 藤椒 麻 \
    --prefer "三个人聚餐，一位同事不吃辣、在减脂" --html /tmp/mcd.html --brief
```

语义变成：**700 是那一位的上限，其余人按常规量吃**（`--kcal-max × 1.35`）。
规划会给出「1 份轻食 + 2 份照常 + 可选一份共享小食」，卡片上分三组：
`轻食 1 人 / 每人 369 kcal`、`照常 2 人 / 每人 614 kcal`、`共享 大家分`。

判断口径：

| 用户怎么说 | 怎么传 |
| --- | --- |
| "我在控制体重"（一个人吃） | `--people 1 --kcal-max 500` |
| "我们仨一起吃，其中一个在减肥" | `--people 3 --kcal-max 700 --light-for 1` |
| "三个人都要清淡点" | `--people 3 --kcal-max 600`，**不要**传 `--light-for` |

不传 `--light-for` 时完全按人均语义跑，行为和以前一模一样。

---

## Gate 2：展示 + 让用户选

### 展示

卡片是脚本出的，你只负责**搬运**，不做设计：

```bash
python3 scripts/mcd_planner.py --store 1950739 --people 3 --budget 60 \
    --meal 晚餐 --widget /tmp/mcd_plan.html --brief
```

`--widget` 产出的是**纯片段**：无 HTML 骨架、无投影、属性全用单引号
（双引号塞进工具参数要逐个转义，几百处必错）。把该文件全文原样传给可视化工具：

```
show_widget(title="麦当劳配餐方案", widget_code=<该文件全文>)
```

**不要用 present_files 打开 HTML 文件** —— 那要把用户从对话里拽去预览面板，
还得关回来。卡片就该在对话里，跟文字一起读完。

同时用一两句话报结论：**几套、多少钱、差在哪**。别复述卡片内容。
例：「两套都在 60 以内。A ￥44.70，人均 429 kcal；B 多 ¥15 换 6g 蛋白，多一份冰淇淋。」

**卡片固定给两套，宽屏一行两个、窄屏上下折叠。** 脚本用 `--top-k 2` 求解；
约束太紧只解出一套时，`_ensure_two` 会先松营养上限、再松预算补第二套，
补出来的那套带「超预算」角标、`warnings` 里写明"是放宽后的备选"。
**用户要的是能比，不是能过。** 只给一套等于替他做了决定。

版式（五层：品牌标题 / 餐品清单（套餐拆成单品逐行）/ 价格看板 / 营养看板 / 三条依据）、设计令牌、
图挂兜底的细则在 `references/brand-visual.md`，**正常流程不用读** ——
脚本已经按它渲染好了。只有要改结构时才翻，改完跑 `tests/test_render.py`。

脚本会自查禁用词（"零负担""吃不胖"等），命中会在 JSON 的 `warnings` 里报。

### 让用户选

```
Q: 这两套怎么选？
   [方案 A，下单]  ¥44.70 · 人均 429 kcal · 蛋白 27g
   [方案 B，下单]  ¥59.70 · 人均 522 kcal · 蛋白 29g
   [换一批]        放宽条件再算
   [先不下单]      只看方案，我自己去 App 点
```

- **选项里必须出现"下单"两个字** —— 点它就是授权扣款。
- 多人方案写整单总价 + 人均。
- **到店场景把取餐方式并进这道题**，别为 `takeWayCode` 再开一轮：
  `[外带自提，下单]` / `[堂食，下单]` / `[先不下单]`。
- 用户选「换一批」→ **只改参数重跑，不许再问一轮需求**（有缓存，<1s 出新方案）。
- 用户挑定了某一套 → **不算第二遍**。上次出卡时旁边已落了 `.json` 存档，直接
  `--from <存档> --pick N --widget 新文件`，0.2s 拿到单套卡片。
  重算要 8–9 秒，且解不变，纯属让用户干等。

---

## Gate 3：下单

只有用户明确选了带"下单"的选项，才调 `create-order`。**只有这一次**。

```jsonc
{
  "storeCode": "1950739",
  "orderType": 1,          // 1 到店 / 2 外送
  "beType": 1,             // 1 到店取 / 2 麦乐送 / 5 得来速 / 6 团餐
  "takeWayCode": "eat-in", // orderType=1 必传，取自 calculate-price 的 takeWayList[].code
  "items": [
    { "productCode": "9900013304", "quantity": 3,
      "roundList": [                                  // 套餐必须带子项，否则算价对不上
        { "round": "1", "comboItemList": [{"code": "1120", "quantity": 1}] },
        { "round": "2", "comboItemList": [{"code": "521328", "quantity": 1}] }
      ] }
  ]
}
```

- `quantity` 用方案里的**真实份数**，别硬编码 1（多人单会腰斩）
- 外送要 `addressId` + `beCode`，`takeWayCode` 不传；团餐要 `gmServiceCode`
- `couponId` 放 **items 元素内部**，不是顶层
- 预约单 `reservationDate` 要和规划时**同一个值**（JSON 的 `criteria.reservation`）
- 完整字段以工具 schema 为准

**下单后必说三句**：支付链接 **15 分钟内有效**；手机打开链接或去 App「我的订单」支付；
商品/门店/取餐方式与确认的那套完全一致。

**额度红线**：每账号每日 `create-order` 约 4 次，`cancel-order` **不退额度**。
所以探索阶段只调 `calculate-price`（免费），绝不试单。

---

## 铁律

1. **所有用户输入走 `AskUserQuestion`**，不许在正文里写"请告诉我地址/几个人"让人打字回。
2. **没被明确选择，不下单。** "看看""推荐一下"就是不下单。
3. **不编价格。** 金额只来自 `query-meals`（元）或 `calculate-price`（分，除以 100）。
4. **不编营养。** 营养表没有就写"未收录"，不拿相似商品凑。
5. **不把 null 当 0。** 缺数据排除出控卡求解，不是填 0 继续算。
6. **`distance` 不是"离你多远"** —— 它是关键词解析点到门店的直线距离，同店实测能差 13 倍。
7. **返回 5 家门店不等于只有 5 家** —— 接口硬上限。
8. **不主动推销。** 用户只问热量，就只说热量。
9. **人数必须进求解。** 3 人 90 元给出一只汉堡，就是没传 `--people`。
10. **别手动改卡片。** 要调整就改参数重跑，或改 `scripts/render_card.py` 的模板。
11. **`--prefer` 只能填用户在对话里真说过的话。** 不许推断，不许为演示编一个。
    卡片上也不许出现「你说想吃 X」这类代述 —— 参数来源不可追溯，写成第一人称就是替他发言。
    口味那条只描述「这套里有什么、对不对得上」。

---

## 故障速查

| 现象 | 原因 | 修法 |
| --- | --- | --- |
| **整套流程跑很久** | 手写卡片 / 逐条调 MCP / 反复读文档 | 回看「时间账」三条禁令；一条命令搞定 |
| 晚餐给出了油条、粥、松饼 | 没传 `--meal`，拿到的是当前时段（早餐）菜单 | 传 `--meal 晚餐`，脚本会自动补 `reservationDate` |
| 查不到门店 | `city` 是行政区，不是业务城市 | 新区/开发区尤其注意，见 `references/store-lookup.md` |
| `600058` | `city` 和 `keyword` 缺一个 | 两个都补，文档里的"或"是误导 |
| `600050` | `searchType` 默认值 1（搜收藏） | 显式传 `2` |
| `600047` | 地址解析成功但周边无店 | **换 city**，不是换关键词 |
| `600057` | 门店已打烊 | 选 `businessStatus == true` 的门店 |
| 解析报 `Extra data` | 返回体是 markdown + JSON 拼接（仅脚本通道） | 用 `raw_decode`，见 `mcp-contract.md` 第 2 条 |
| 价格差 100 倍 | 菜单元 vs 算价分 | `calculate-price` 结果除以 100 |
| 给出超预算方案 | 用菜单标价判预算，组合类是加价购起价 | 脚本已二次校验，别自己再算一遍 |
| 预约时段拆解为 0 | `reservationDate` 没传给 `query-meal-detail` | 四个接口都要带：meals/detail/price/create |
| 算价报 `60006 商品不可售` | **多半是没传 `reservationDate`**，查的不是那份菜单 | 别急着换商品；先确认四个接口都带了预约时间 |
| 热量明显偏低 | 套餐没拆，或缺数据被当 0 | 看 `nutrition_coverage`，低于 60% 要警惕 |
| 多人方案只够一人吃 | 没传 `--people` | 传人数，走人手一份/分享装双路径 |
| 券字段不生效 | `couponId` 放到了顶层 | 放进 `items[]` 元素内部 |
| 数据像是旧的 | 本地缓存 5 分钟 | 加 `--no-cache` |
| 卡片价格全是 ¥0.00 | 先 `--brief` 再渲染，砍掉了 priced | 已修：先出卡后压缩 |

排障要单点查询时，MCP 工具调用顺序：`now-time-info`（判餐段）→ `query-nearby-stores`
（`beType=1, searchType=2, city, keyword`）→ `query-meals` → `list-nutrition-foods`
→ `query-meal-detail(code=...)` → `calculate-price` → `query-store-coupons`。
MCP 与脚本走同一个端点、同一份数据，只是返回文本外壳不同。

---

## 文件

| 文件 | 内容 |
| --- | --- |
| `scripts/mcd_planner.py` | **主入口**：查店→菜单→拆套餐→求解→核价→出卡，一条命令 |
| `scripts/render_card.py` | 品牌卡片渲染器：JSON → HTML，色板/字号/禁用词由代码强制 |
| `scripts/mcd_icons.py` | 21 类菜品图标（32×32 SVG）+ 菜名归类，认不出时红底金拱门 |
| `scripts/nutrition_solver.py` | 求解器：营养对齐、套餐拆解、组合搜索、四重约束 |
| `scripts/mcd_mcp.py` | 底层客户端：token、SSE、`raw_decode`、分元换算、只读缓存 |
| `scripts/locate_store.py` | 查店排障：错误码归因、备用地标重试 |
| `tests/test_solver.py` | 39 条离线断言，`python3 tests/test_solver.py`，1.4s |
| `references/brand-visual.md` | 卡片色板、字号、七层骨架、禁用词、三条依据写法 |
| `references/personas.md` | 六画像 → 参数映射、口味偏好映射表 |
| `references/mcp-contract.md` | 接口契约与 12 类坑（混合文本、两套单位、TOON 格式…） |
| `references/nutrition-alignment.md` | 营养对齐四层规则、三种必须拒绝的情况 |
| `references/store-lookup.md` | 查店五条硬事实、错误码归因、降级顺序 |
