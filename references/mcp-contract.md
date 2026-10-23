# MCP 接口契约（只记文档没写、或写错的部分）

> 2026-10-22 对 `https://mcp.mcd.cn` 实调。接口签名与字段含义看工具 schema，这里只记**行为差异**。

## 1. 端点必须是根路径

| 地址 | 结果 |
|---|---|
| `https://mcp.mcd.cn` | ✅ 200 |
| `https://mcp.mcd.cn/mcp` | ❌ 404 |

配置里写成带 `/mcp` 后缀是接不上服务最常见的原因。

## 2. `content[].text` 是 Markdown 与 JSON 的混合体，必须 `raw_decode`

真实返回长这样（同一个 text 字段里）：

```
# API Response Information
## Response Structure
- **code**: 错误码 (Type: number)
- **data**: 餐品热量列表 (Type: string)
## Original Response
{"success":true,"code":200,...,"data":"[160]{...}"}## 数据传参规则
                                                        ^ 注意：没有换行分隔
```

`json.loads(blob[start:])` 会报 `Extra data: line 1 column 10281`。
正确做法是先 `blob.find('{"success"')` 定位，再 `JSONDecoder().raw_decode()` 精确切片 —— 见 `scripts/mcd_mcp.py:_extract_json`。

券类接口更极端：`content[].text` 前面是一大段给人看的 Markdown 表格说明，JSON 在最后。

**挂到宿主后（MCP 工具通道）外壳换了，但规律相同**：开头是 `# API Response Information` 加一整套字段说明，末尾 `## Original Response` 之后才是 JSON。所以「从 `{"success"` 起 `raw_decode`」这条规则在两条通道上都成立。稳妥写法用 `rfind` 而不是 `find`——字段说明里偶尔会出现与参数同形的片段：

```python
i = text.rfind('{"success"')
obj, _ = json.JSONDecoder().raw_decode(text[i:])
```

**注意**：`Extra data` 只会在脚本通道出现，MCP 工具通道的文本已由宿主整理过，直接读字段即可，不必手写解析。

## 3. 金额单位两套并存（最容易错 100 倍）

| 接口 | 字段 | 单位 | 示例 |
|---|---|---|---|
| `query-meals` | `currentPrice` / `originalPrice` | **元**（字符串） | `"33.5"` |
| `calculate-price` | `price` / `productPrice` / `discount` / `subtotal` | **分**（整数） | `3300` |

菜单是元、算价是分。混用一次就是 100 倍误差。

## 3b. 菜单标价 ≠ 单点实付（组合类普遍有差，最多差 ¥6.9）

**这是做预算判断时最危险的一条。** 2026-10-23 门店 `1950739` 实测：

| 商品 | 菜单 `currentPrice` | 单点实付 | 差 |
|---|---|---|---|
| 脆香油条组合 | ¥6.60 | **¥11.00** | +4.40 |
| 吉士蛋麦满分组合 | ¥7.20 | 拆开算 | — |
| 火腿扒麦满分组合 | ¥7.80 | **¥13.00** | +5.20 |
| 大脆鸡扒麦满分组合 | ¥8.10 | **¥13.50** | +5.40 |
| 猪柳蛋麦满分套餐 | ¥15.90 | **¥26.50** | +10.60 |
| 厚松饼猪柳蛋堡三件套 | ¥17.94 | **¥29.90** | +11.96 |

菜单价是**加价购起价**（得先买主品才享这个价），单点下单按原价结算。而 `query-meal-detail` 对这类商品返回 `currentPrice: null`。

**单点商品没有这个问题**，实测 `脆香油条 ¥8.00`、`圆角 ¥5.00`、`大脆鸡扒麦满分 ¥11.50` 菜单价与实付完全一致。

后果有两个，都会让方案出错：

1. 预算按菜单价判断 → 通过 → 核价后超支，等于给出买不起的方案
2. 排序偏向「看起来便宜」的组合，把真正便宜的单点挤掉

做法：拆出详情的商品，逐个调一次 `calculate-price`（免费）把 `price` 覆盖成实付，`scripts/mcd_planner.py:repair_unit_prices` 已实现并发版（29 个组合约 3 秒）。**不要覆盖 `originalPrice`**，那是真实划线价，改了会造出假「结构省」。

## 4. `query-meals` 返回两段结构

```jsonc
"categories": [ {"name":"人气热卖","meals":[{"code":"9900016076","tags":["限时特惠"]}]} ],  // 只给 code 引用
"meals": { "9900016076": {"name":"...","currentPrice":"24","originalPrice":"..."} }          // dict，不是 list
```

直接遍历 `meals` 会丢分类归属；直接遍历 `categories` 又拿不到价格。两边都要读，用 code 关联。
分类名可以当"这商品属于哪一类"的弱标签用（`scripts/nutrition_solver.py:parse_menu` 把 cat_name 塞进 tags）。

## 5. 菜单只返回「当前时段可售」

同一门店（`1950739`）2026-10-22 实测：

| 查询 | SKU |
|---|---|
| 当晚 19:25（不传 `reservationDate`） | **109** |
| 预约次日 08:00（`reservationDate="2026-10-23 08:00"`） | **71** |
| 两者交集 | **18** |

仅早餐可售 53 个（麦满分、厚松饼、粥），仅正餐可售 91 个（汉堡套餐、分享盒）。

**交集只有 18 个。** 做早餐或预约排餐必须传 `reservationDate`，否则会被判成"没这个商品"，
然后给用户推荐一顿晚饭。格式 `yyyy-MM-dd HH:mm`，非预约场景不传。

**`reservationDate` 必须一路透传到底，一个接口漏传就全盘失效。** 实测踩过：`query-meals`
传了、`query-meal-detail` 没传 → 预约午餐的 SKU code 在早餐时段不存在 → 30 个套餐全部拆解失败，
`combos_decomposed: 0`、营养覆盖从 49/106 掉到 19/106、求解出 0 个方案。
涉及三个接口：`query-meals`、`query-meal-detail`、`calculate-price`，`create-order` 也要带。

## 5b. 到店菜单 ≠ 外送菜单（2026-10-22 实测，门店 1950401）

同一门店、同一晚，只是 `orderType`/`beType` 不同：

| 查询 | SKU | 备注 |
|---|---|---|
| 到店 `orderType=1, beType=1` | 122 | 含「人气经典随心配」¥14.90 |
| 外送 `orderType=2, beType=2` + `beCode` | 125 | **随心配完全不在**，同款普遍贵 1 元 |

实测价差：麦香鸡 17.0 → 18.0；薯条 14.0 → 15.0；纯悦 7.5 → 8.0。

**后果**：拿 `orderType=1` 的菜单给外送用户算价，`calculate-price` 直接报
`60006 商品已售罄或不可售，换一个商品或检查 orderType/beType`。
这个错误码的真实含义是「**你这份菜单不属于这个 orderType**」，不是商品卖完了。

更隐蔽的是：`60006` 只在算价阶段才暴露，`query-meals` 换成外送后仍能正常返回数据，
所以很晚才发现价格算不出来。**凡是外送，`query-meals` 就必须带 `orderType=2, beType=2, beCode`。**

### 外送的真实附加成本

同店同地址实测（地址已脱敏）：

| 项 | 金额 |
|---|---|
| 运费 `deliveryPrice` | ¥6.00 |
| 打包费 `packingPrice` | ¥1.10（按商品数累加，V翅 0.80） |
| `tablewarePrice` | 0（`needTableware=false`） |

**「20 元预算」在到店和外送是两个完全不同的世界**：到店能买 ¥14.90 的双品套餐，
外送只够单点一个汉堡（吉士汉堡包 ¥16.50，合计 ¥23.60）。接需求时先确认取餐方式，
不要先按到店算价再套外送。

外送场景 `create-order` 还必须多传 `addressId` 与 `beCode`，且不传 `takeWayCode`。

## 6. 套餐详情：`code` 不是 `mealCode`

`query-meal-detail` 的参数名是 `code`。传 `mealCode` 直接失败。

返回结构：

```jsonc
{ "code": "...", "name": "麦香鱼中套餐", "supportModify": false,
  "rounds": [
    { "id": 1, "name": "麦香鱼",          "minQuantity":1, "maxQuantity":1,
      "choices": [ {"code":"1600","name":"麦香鱼","quantity":1,"isDefault":1} ] },
    { "id": 2, "name": "选择套餐内小食", "minQuantity":1, "maxQuantity":1,
      "choices": [ {"code":"4810","name":"中薯条","quantity":1,"isDefault":1},
                   {"code":"4820","name":"大薯条","quantity":0,"isDefault":0,"diffPrice":"+ ¥2"} ] }
  ] }
```

要点：

- **取 `quantity > 0` 的子项**作为"当前已选"。`rounds[].id` 是数字，回传给 `calculate-price` 时转成字符串。
- **`quantity > 0` 与 `isDefault == 1` 的关系**：12 个套餐实测两者完全一致（0 处分歧），但语义上 `quantity` 才是"已选数量"，所以以它为准。若将来出现分歧，以 `quantity` 为准并在卡片上注明。
- 加价用小计项看 `diffPrice`（字符串，如 `"+ ¥1"`），字段名不是 `addPrice`。
- `currentPrice` 在详情里是 `null` —— 详情不算价，价格只能来自 `query-meals` 或 `calculate-price`。
- 详情返回体后面也紧跟 `## 数据传参规则` 段落，同第 2 条，必须 `raw_decode`。

## 7. 套餐算价的传参结构

```jsonc
{ "storeCode":"1950739", "orderType":1, "beType":1,
  "items": [
    { "productCode": "9900005460", "quantity": 1,
      "roundList": [
        { "round": "1", "comboItemList": [ {"code":"1600","quantity":1} ] },
        { "round": "2", "comboItemList": [ {"code":"4810","quantity":1} ] },
        { "round": "3", "comboItemList": [ {"code":"3050","quantity":1} ] }
      ] } ] }
```

不传 `roundList` 也能算出价，但等于用默认子项，用户改过的搭配会被静默忽略。

## 8. 优惠券字段必须在 `items[]` 元素内部

`couponId` / `couponCode` 放在顶层无效。正确位置是每个商品项内部：

```jsonc
"items": [ { "productCode":"1600", "quantity":1, "couponId":"...", "couponCode":"..." } ]
```

同一 `couponId` 可对应多个 `couponCode`，展示时按 couponId 分组、取首个码。

## 9. 券接口返回形态不一致

- `query-store-coupons` / `available-coupons` 的 `content[].text` 是给人看的 Markdown 表格 + 尾部 JSON
- **无券时 `data: []`，不是报错**。空数组和接口故障必须分开处理
- `query-my-coupons` 在无券时直接返回 `# 您的优惠券列表\n暂无可用优惠券`，连 JSON 都没有

## 10. 营养表是 TOON 字符串，不是 JSON 数组

```jsonc
"data": "[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:\n  猪柳麦满分,null,1288,308,16,16,24,781,213\n  ..."
```

- 160 行、9 列、逗号分隔、2 空格缩进、缺失值为 `null`
- 实测有 **2 条重名**（`小杯玉米杯`、`纯牛奶（盒装）` 各出现两次，值相同），解析时要记录而不是静默覆盖
- `energyKcal` 可能为 `null` 而 `energyKj` 有值，用 `kJ × 0.239` 兜底并在卡片上注明
- 名称含全角括号（`纯牛奶（盒装）`），与菜单的 `纯牛奶(盒装)` 不一致，必须先 NFKC 归一化

## 11. 35 个工具的分组

| 组 | 工具 |
|---|---|
| 门店 | `query-nearby-stores` `delivery-query-stores` `delivery-query-addresses` `delivery-create-address` |
| 菜单与算价 | `query-meals` `query-meal-detail` `calculate-price` `list-nutrition-foods` `now-time-info` |
| 下单 | `create-order` `cancel-order` `query-order` `order-list` |
| 团餐 | `query-meal-assistance` `query-promotions` |
| 券 | `query-my-coupons` `query-store-coupons` `available-coupons` `auto-bind-coupons` `query-survey-coupon` |
| 积分商城 | `mall-points-products` `mall-product-detail` `mall-create-order` `mall-order-list` `mall-order-detail` `query-my-account` |
| 抽奖 | `draw-lottery` `query-lottery-info` `query-my-prizes` |
| 派对/活动 | `query-party-city` `query-party-store` `query-party-store-date` `query-party-store-session` `party-order-create` `campaign-calendar` |

配餐只用前五组里的只读工具。**积分商城、抽奖、派对不是"配餐"的范围**，别顺手调。

## 12. 会写进用户账号的调用

| 工具 | 副作用 |
|---|---|
| `create-order` | 真实下单、真实扣款、每日有额度上限 |
| `cancel-order` | 取消订单，**但不退还当日下单额度** |
| `delivery-create-address` | 往本人地址簿写新地址 |
| `mall-create-order` / `party-order-create` | 积分兑换 / 派对下单 |

没有用户明确确认不要调。`delivery-query-addresses` 是只读的，可以先查地址簿是否为空来判断能不能走外送。
