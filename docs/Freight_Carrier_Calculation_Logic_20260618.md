# Freight Carrier Calculation Logic - 2026-06-18

本文档说明 Freight Intelligence / CourieDelivery 当前已启用的表格费率计算逻辑。内容按当前代码实现整理，主要解释单货和多货时系统如何计算。

相关代码位置：

- 通用数据结构：`backend/freight/calculators/base.py`
- 报价引擎：`backend/freight/quote_engine.py`
- Hunter：`backend/freight/calculators/hunter_base.py`
- Allied GRO：`backend/freight/calculators/allied_gro_2023_melbourne.py`
- Allied B2C：`backend/freight/calculators/allied_b2c_2025_melbourne.py`
- Direct Freight Express：`backend/freight/calculators/direct_freight_express_2025.py`
- Orange Connex eFN：`backend/freight/calculators/orange_connex_efn_2026.py`
- UBI Team Global Express IPEC：`backend/freight/calculators/ubi_tge_ipec.py`

## 1. 当前启用的计算器

| Carrier | 当前服务/Rate Card | Calculator | Origin |
|---|---|---|---|
| Allied Express | Allied GRO 2023 Melbourne | Allied GRO 2023 | MEL/VIC |
| Allied Express | Allied GRO 2023 Sydney | Allied GRO 2023 | SYD/NSW |
| Allied Express | Allied B2C 2025 Melbourne | Allied B2C 2025 | MEL/VIC |
| Hunter Road Freight | Hunter MEL 2023 | Hunter Base | MEL/VIC |
| Hunter Road Freight | Hunter SYD Broers 20240920 | Hunter Base | SYD/NSW |
| Direct Freight Express | DFE KILO EX MEL 2025 | DFE 2025 | MEL |
| Direct Freight Express | DFE KILO EX SYD 2025 | DFE 2025 | SYD |
| Orange Connex | Orange Connex eFN MEL 2026 | Orange eFN 2026 | MEL |
| Orange Connex | Orange Connex eFN SYD 2026 | Orange eFN 2026 | SYD |
| Team Global Express | UBI TGE IPEC Road | UBI TGE IPEC | MEL1/SYD1 |

备注：

- API quote / LSP historical quote 不属于本文的“内部费率表计算”。API 结果以外部返回价格为准，系统只保存 request / response / selected option / historical option。
- UBI 是 Agent，不是 Carrier。UBI IPEC 的 Carrier 是 Team Global Express，Service 是 UBI TGE IPEC Road。

## 2. 通用计算概念

### 2.1 QuoteItem

每条商品线进入 calculator 时，已经被整理为 `QuoteItem`：

| 字段 | 含义 |
|---|---|
| `sku` | SKU 或手工输入 SKU 标识 |
| `qty` | 数量 |
| `unit_weight_kg` | 单件实重 kg |
| `length_cm` | 单件长 cm |
| `width_cm` | 单件宽 cm |
| `height_cm` | 单件高 cm |

单件体积：

```text
volume_m3 = length_cm * width_cm * height_cm / 1,000,000
```

Combo SKU 在进入 calculator 前会被系统展开或快照为可计算 SKU lines。Calculator 不关心它原来是 single SKU、combo SKU 还是手工尺寸，只关心最终的 item lines、数量、重量和尺寸。

### 2.2 RateCard / RateZone / RateRule / SurchargeRule

| 模型 | 用途 |
|---|---|
| `RateCard` | 费率版本、生效日期、origin、cubic factor、GST、calculator key |
| `RateZone` | 地址到 zone 的映射，如 postcode/suburb/state -> destination zone |
| `RateRule` | base freight 规则，如 origin zone + destination zone + weight -> basic/per kg/minimum |
| `SurchargeRule` | fuel、oversize、destination surcharge、home delivery 等附加费配置 |

### 2.3 Origin 过滤

报价前，系统会按 warehouse 推断 origin：

- 仓库 state/code/name/region 包含 `NSW`、`SYD`、`SYDNEY` -> SYD
- 仓库 state/code/name/region 包含 `VIC`、`MEL`、`MELBOURNE` -> MEL

因此从 MEL 发货时只会参与 MEL origin 的 channel/rate card；从 SYD 发货时只会参与 SYD origin 的 channel/rate card。`ALL warehouse` 会允许多个 origin 都参与报价。

### 2.4 单货和多货的核心区别

不同 carrier 的多货算法不一样，不能统一成一种：

| Carrier | 多货模式 |
|---|---|
| Hunter | 每个 SKU line 先算 `max(dead, cubic)`，再累加成整票计费重量 |
| Allied GRO | 全部货合并后算整票 dead/cubic，再选择一个 legacy surcharge |
| Allied B2C | base 按每件逐件计费，OWS 按整票计费重量触发 |
| DFE | 全部货合并后算总 dead 和总 cubic，取最大值作为整票计费重量 |
| Orange Connex eFN | 每件按单件实重进入固定重量档，逐件累加 |
| UBI TGE IPEC | 全部货合并后算总 dead 和总 cubic，取最大值作为整票计费重量 |

### 2.5 订单、Tracking 和审计口径

Manual Quote 里用户可以手工录入 SKU/尺寸，也可以按 ERP Order No / Platform Order No 带出订单信息。订单进入报价时，系统最终仍会转换成同一种 `QuoteItem` 列表：

```text
order/shipment lines -> SKU dimensions/weights -> QuoteItem lines -> calculator
```

因此 calculator 不直接读取 ERP 原表，而是根据同步后的订单、shipment、SKU 快照进行计算。这样可以保证历史订单复算时使用当时同步进系统的尺寸和数量，不会因为 WMS 或 ERP 后续改了 SKU 主数据而影响历史对账。

订单审计时有三个常见价格口径：

| 字段 | 含义 | GST 口径 |
|---|---|---|
| `ERP Est` | ERP 订单中的 `postage_shipping_estimated_amount` / `shipping_estimated_amount` | 前端外层显示含 GST 口径 |
| `System Est` | Freight Intelligence 用当前启用的 rate card / calculator 重新算出的价格 | 外层显示含 GST 总价，breakdown 内显示 ex GST / GST / inc GST |
| `Invoice Actual` | InvoiceReader 中按 tracking 匹配到的实际账单金额 | 对账统一用含 GST 总价 |

多 tracking 时要特别注意 ERP Est 的范围：

- 如果 ERP Est 是整单金额，系统会按 ERP Order 聚合后与多 tracking 的 invoice actual 总额比较。
- 如果 invoice 是按 tracking 拆分，明细页会按 tracking 排列每个包裹/consignment 的实际收费。
- 内部 rate table calculator 通常按输入的整票 item lines 计算一次；如果业务要求每个 tracking 单独计算，需要先把订单 lines 拆成 tracking groups，再分别调用 calculator。
- 当前 Freight Audit Matrix 的目标是同时展示：ERP Est、各个内部 carrier 计算结果、LSP/API historical quote、Invoice Actual，用来判断是 ERP 估算偏差、费率表偏差、API 返回偏差，还是账单异常。

## 3. Hunter Road Freight

适用服务：

- Hunter MEL 2023
- Hunter Sydney 2025

### 3.1 Zone 匹配

Hunter 使用 `RateZone` 匹配目的地：

1. 优先 exact：`postcode + suburb + state`
2. 然后 postcode/state 或 postcode range fallback
3. 匹配失败返回 `rate_card_not_found`

匹配成功后得到 `dest_zone`，再用 `dest_zone + chargeable weight` 找 `RateRule`。

### 3.2 单货计算

对一个 item line：

```text
length_m = length_cm / 100
width_m  = width_cm / 100
height_m = height_cm / 100
```

先判断是否 oversize：

```text
oversize =
  (length_cm > 120 and width_cm > 120)
  or height_cm > 180
  or ((length_cm > 120 or width_cm > 120) and unit_weight_kg > 59)
```

如果 oversize：

```text
cubic_factor = 333
```

否则：

```text
cubic_factor = 250
```

单件 cubic weight：

```text
unit_cubic_kg = length_m * width_m * height_m * cubic_factor
```

如果数量为 `qty`：

```text
line_dead_kg = unit_weight_kg * qty
line_cubic_kg = unit_cubic_kg * qty
line_chargeable_kg = max(line_dead_kg, line_cubic_kg)
```

单货只有一条 line 时：

```text
total_chargeable_kg = line_chargeable_kg
chargeable_kg_for_rate = ceil(total_chargeable_kg)
```

Base freight：

```text
base = max(minimum_charge, basic_charge + per_kg * chargeable_kg_for_rate)
```

### 3.3 多货计算

多货时 Hunter 不是先合并 dead/cubic 后取最大，而是每条 SKU line 先取最大：

```text
for each line:
  line_chargeable_kg = max(line_dead_kg, line_cubic_kg)

total_chargeable_kg = sum(line_chargeable_kg)
chargeable_kg_for_rate = ceil(total_chargeable_kg)
```

这样会比“总实重和总体积重取最大”更保守，因为每个 SKU line 的体积优势和重量优势不会互相抵消。

### 3.4 Surcharge

Hunter 当前计算三类 surcharge：

| Code | 触发依据 | 说明 |
|---|---|---|
| `RESI` | `max_item_charge_kg` | 住宅/大件相关配置，取所有单件中最大的 charge kg |
| `LEN` | `longest_len_m` | 长度 surcharge，超过某些区间可能标记 POA |
| `UPLF` | `total_chargeable_kg` | 按整票 chargeable weight 触发 |

其中：

```text
max_item_charge_kg = max(max(unit_weight_kg, unit_cubic_kg) for all item units)
longest_len_m = max(length_cm / 100 for all items)
surcharge_total = RESI + LEN + UPLF
```

### 3.5 Fuel 和 GST

Fuel basis：

```text
fuel_basis = base + surcharge_total
```

Fuel rate：

- 默认使用 `SurchargeRule` code `FS`
- 如果目的地 state 是 WA，则使用 `FS_WA`

```text
fuel = fuel_basis * fuel_rate
total_ex_gst = base + surcharge_total + fuel
gst = total_ex_gst * 0.10
total_inc_gst = total_ex_gst + gst
```

### 3.6 Breakdown

Hunter breakdown 会显示：

- Base
- Residential surcharge
- Length surcharge
- Uplift surcharge
- Fuel levy
- GST

## 4. Allied Express GRO 2023

适用服务：

- Allied GRO 2023 Melbourne
- Allied GRO 2023 Sydney

Sydney calculator 继承 Melbourne calculator，公式相同，差异来自 rate card、origin、zone/rule 数据。

### 4.1 Zone 匹配

使用通用 `find_zone`：

1. exact：`postcode + suburb + state`
2. postcode/state fallback
3. postcode range fallback

`RateZone.raw_payload.on_forward` 可能包含 on-forward delivery 信息。

### 4.2 单货计算

Allied GRO 用整票 dead/cubic 方式，即使单货也是走同一个公式：

```text
dead_kg = round0(sum(unit_weight_kg * qty))
cubic_kg = round0(sum(volume_m3 * 250 * qty))
chargeable_kg = max(dead_kg, cubic_kg)
```

`round0` 是四舍五入到整数 kg。

Base linehaul：

```text
linehaul = max(minimum_charge, basic_charge + per_kg * chargeable_kg)
```

如果 zone 有 on-forward：

```text
on_forward_base = on_forward.basic + on_forward.per_kg * chargeable_kg
```

否则 `on_forward_base = 0`。

### 4.3 多货计算

多货时先把所有 SKU lines 合并：

```text
dead_kg = round0(sum(each line dead))
cubic_kg = round0(sum(each line cubic))
chargeable_kg = max(dead_kg, cubic_kg)
```

Base linehaul 和 on-forward 都按这个整票 `chargeable_kg` 算。

### 4.4 Legacy surcharge 选择

Allied GRO 不把所有 surcharge 相加，而是计算多个候选后取最大值：

```text
chosen_surcharge = max(home_delivery, item_surcharge, two_person_crew)
```

#### Home delivery

```text
HDD = surcharge by dead_kg
HDC = surcharge by cubic_kg
home_delivery = HDD if dead_kg >= cubic_kg else HDC
```

如果有 on-forward：

```text
home_delivery = home_delivery * 2
```

#### Item surcharge

对每个 SKU line：

```text
longest = round0(item.longest_cm)
lsc = LSC surcharge by longest
ws = WS surcharge by longest
dhsl = DHSL surcharge by longest
dhsw = DHSW surcharge by unit_weight_kg
line_item_surcharge = max(lsc, ws, dhsl, dhsw) * qty
```

多货时：

```text
item_surcharge = sum(line_item_surcharge)
```

#### Two person crew

系统按每条 item line 的尺寸和重量判断 TPC，并取最大 TPC：

```text
if longest >= 240 and middle >= 130 and (dead_kg >= 76 or cubic_kg >= 151):
  TPC = 124.80
elif 190 <= longest <= 239 and middle >= 130 and (dead_kg >= 56 or cubic_kg >= 111):
  TPC = 78.00
elif 130 <= longest <= 189 and middle >= 90 and (dead_kg >= 47 or cubic_kg >= 92):
  TPC = 49.92
else:
  TPC = 0
```

多货时：

```text
two_person_crew = max(TPC of all lines)
```

### 4.5 Fuel 和 GST

Allied GRO 使用 legacy fuel ratio：

```text
subtotal = linehaul + on_forward_base + chosen_surcharge
total_inc_gst = subtotal * (1 + 0.10) * fuel_ratio
gst = subtotal * 0.10
fuel_amount = subtotal * (1 + 0.10) * (fuel_ratio - 1)
```

注意：

- `fuel_ratio` 是倍率，例如 `1.2679`，不是 `0.2679`。
- 当前 breakdown 中 fuel amount 是按含 GST subtotal 的倍率差计算，这是为贴合旧 PostageCalculator 逻辑。

### 4.6 Breakdown

Allied GRO breakdown 会显示：

- Allied linehaul
- Allied on-forward delivery
- Chosen legacy surcharge
- Fuel ratio
- GST

## 5. Allied Express B2C 2025

适用服务：

- Allied B2C 2025 Melbourne

代码也有 Sydney class，但当前启用 channel 里没有 Allied B2C Sydney。

### 5.1 Zone 匹配

使用通用 `find_zone` 匹配目的地 zone。

### 5.2 单货计算

Allied B2C 的 base 是 per-piece 模式。对单个 item：

```text
cubic_single_kg = volume_m3 * 250
chargeable_single_kg = max(unit_weight_kg, cubic_single_kg)
consignment_weight = chargeable_single_kg * qty
```

用整票 `consignment_weight` 找 `RateRule`，但 base 逐件计算：

```text
per_piece_base = max(basic_charge + per_kg * chargeable_single_kg, minimum_charge)
basic_total = per_piece_base * qty
```

OIS：

```text
rounded_border = round(item.longest_cm)
OIS = surcharge by rounded_border * qty
```

OWS：

```text
rounded_consignment_weight = round(consignment_weight)
OWS = surcharge by rounded_consignment_weight
```

总价：

```text
subtotal = basic_total + OIS + OWS
total = subtotal * (1 + fuel_ratio)
```

当前 Allied B2C calculator 不拆 GST：

```text
gst_amount = 0
total_ex_gst = total
total_inc_gst = total
```

### 5.3 多货计算

多货时：

```text
for each line:
  chargeable_single_kg = max(unit_weight_kg, volume_m3 * 250)
  consignment_weight += chargeable_single_kg * qty
```

然后：

- `RateRule` 用整票 `consignment_weight` 找
- `basic_total` 仍然按每件逐件算再累加
- OIS 按每条 SKU line 的最长边逐件乘 qty
- OWS 只按整票 `consignment_weight` 触发一次

### 5.4 Breakdown

Allied B2C breakdown 会显示：

- Allied B2C per-piece effective base
- OIS surcharge
- OWS surcharge
- Fuel ratio

## 6. Direct Freight Express

适用服务：

- DFE KILO EX MEL 2025
- DFE KILO EX SYD 2025

Pallet rows 已导入为未来参考/禁用服务，当前不自动计算 pallet。

### 6.1 Profile 限制

DFE 在正式计算前会做 strict not-available 检查：

| 条件 | 结果 |
|---|---|
| 缺重量、缺尺寸、qty <= 0 | `missing_dimension_or_weight` |
| 单件实重 > 30kg | `dfe_profile_item_over_30kg` |
| 最长边 > 120cm | `dfe_profile_length_over_120cm` |
| 最大两边都 > 70cm | `dfe_profile_two_sides_over_70cm` |

任何一条 item 触发都会整票 not available。

### 6.2 Zone 匹配

DFE 使用自己的 postcode 规范化，纯数字 postcode 会补齐 4 位，例如 `800` -> `0800`。

Zone lookup：

1. exact：`postcode + suburb + state`
2. 如果 exact 不存在，查 `postcode + state`
3. 如果同 postcode/state 只有一个 zone，则可用
4. 如果多个 zone，则返回 `ambiguous_destination_zone`
5. 找不到则返回 `rate_card_not_found`

### 6.3 单货计算

```text
actual_kg = unit_weight_kg * qty
cubic_kg = volume_m3 * 250 * qty
chargeable_kg = ceil(max(actual_kg, cubic_kg))
```

Base：

```text
base = max(minimum_charge, basic_charge + per_kg * chargeable_kg)
```

Destination surcharge：

1. 先找 exact `postcode + suburb`
2. 如果没有 exact，找 postcode 下所有 surcharge
3. 如果该 postcode 下所有 amount 相同，则使用该 amount
4. 否则不加 destination surcharge

Fuel：

```text
fuel_basis = base + destination_surcharge
fuel = fuel_basis * FS
```

GST：

```text
total_ex_gst = base + destination_surcharge + fuel
gst = total_ex_gst * 0.10
total_inc_gst = total_ex_gst + gst
```

### 6.4 多货计算

DFE 是整票合并模式：

```text
actual_kg = sum(unit_weight_kg * qty for all lines)
cubic_kg = sum(volume_m3 * 250 * qty for all lines)
chargeable_kg = ceil(max(actual_kg, cubic_kg))
```

多货不会逐件分别找 rate rule，而是使用整票 `chargeable_kg` 找一次 DFE rate rule。

### 6.5 Breakdown

DFE breakdown 会显示：

- DFE base
- DFE destination surcharge
- Fuel levy
- GST

## 7. Orange Connex eFN 2026

适用服务：

- Orange Connex eFN MEL 2026
- Orange Connex eFN SYD 2026

Orange Connex eFN 是固定重量档/逐件计费，不是 per kg linehaul。

### 7.1 Profile 限制

每个 item 必须满足：

| 条件 | 结果 |
|---|---|
| qty <= 0 或重量/尺寸缺失 | `missing_dimension_or_weight` |
| 单件实重 > 25kg | `orange_profile_item_over_25kg` |
| 最长边 > 105cm | `orange_profile_length_over_105cm` |
| 单件体积 > 0.088m3 | `orange_profile_volume_over_0_088m3` |

任何一条 item 触发都会整票 not available。

### 7.2 Zone 匹配

Orange zone lookup：

1. exact：`postcode + suburb + state`
2. 如果 exact 不存在，查 `postcode + state`
3. 如果 postcode/state 只有一个 zone，则使用该 zone
4. 如果 postcode/state 有多个 zone，则 `ambiguous_destination_zone`
5. 如果完全找不到 postcode/state，则使用 `Rest of AU`

### 7.3 单货计算

Orange 按单件实重进入重量档，不用体积重计价：

```text
article_weight_grams = ceil(unit_weight_kg * 1000)
band_weight_kg = article_weight_grams / 1000
```

用 `dest_zone + band_weight_kg` 找固定价格：

```text
unit_price = fixed band price
line_base = unit_price * qty
```

如果目的 zone 没有对应重量档，但 `Rest of AU` 有，则 fallback 到 `Rest of AU`。

GST：

```text
gst = base * 0.10
total_inc_gst = base + gst
```

当前 Orange calculator 不计算 fuel。

### 7.4 多货计算

Orange 是逐件累加：

```text
for each SKU line:
  article_weight_grams = ceil(unit_weight_kg * 1000)
  unit_price = rate(dest_zone, weight_band)
  line_base = unit_price * qty

base = sum(line_base)
```

多货不会把多个 SKU 的重量合并成一票再找重量档。每个 article 独立进入自己的重量档。

### 7.5 Breakdown

Orange breakdown 会为每条 SKU line 生成 base line，并显示：

- destination zone
- article weight grams
- band label
- unit price
- qty
- fallback_to_rest_of_au
- GST

## 8. UBI Team Global Express IPEC

适用服务：

- UBI TGE IPEC Road
- Rate versions：`UBI-IPEC-20250707`、`UBI-IPEC-20260420`
- Origins：`MEL1`、`SYD1`

### 8.1 Zone 来源

IPEC zone mapping 来源：

1. Public reference：Maropost/Neto `TollIPEC-ShippingZones.csv`
2. UBI Toll IPEC billing observed override

Public CSV 的 `Zone Code` 会被规范化：

```text
NT 3.81 -> NT
WQLD 60.52 -> WQLD
MEL1 -> MEL1
```

如果 UBI billing override 和 public CSV 冲突，系统优先使用 billing override。

### 8.2 Zone 匹配

IPEC zone lookup：

1. exact：`postcode + suburb + state`
2. 如果有多条 exact，优先 `UBI_BILLING_OBSERVED_OVERRIDE`
3. 如果 exact 不存在，查 `postcode + state`
4. 如果 postcode/state 只有一个 zone，则使用该 zone
5. 多 zone 则 `ambiguous_destination_zone`
6. 找不到则 `rate_card_not_found`

### 8.3 单货计算

IPEC 是整票计费模式。

单货：

```text
actual_kg = unit_weight_kg * qty
cubic_kg = volume_m3 * 250 * qty
chargeable_kg = ceil(max(actual_kg, cubic_kg))
```

Rate rule 中包含：

| 字段 | 含义 |
|---|---|
| `minimum_charge` | 最低收费 |
| `basic_charge` | 基础收费 |
| `per_kg` | freight charge per kg |
| `kg_included_in_basic` | basic 已包含 kg |

Base：

```text
rated_kg = max(0, chargeable_kg - kg_included_in_basic)
base = max(minimum_charge, basic_charge + per_kg * rated_kg)
```

Fuel：

```text
fuel = base * FS
```

当前导入默认 `FS = 0.099`，配置在 `SurchargeRule`，以后可以在 Pricing 页面维护。

GST：

```text
total_ex_gst = base + fuel
gst = total_ex_gst * 0.10
total_inc_gst = total_ex_gst + gst
```

### 8.4 多货计算

多货时 IPEC 先合并全部 SKU lines：

```text
actual_kg = sum(unit_weight_kg * qty for all lines)
cubic_kg = sum(volume_m3 * 250 * qty for all lines)
chargeable_kg = ceil(max(actual_kg, cubic_kg))
```

然后用整票 `chargeable_kg` 找一次 `RateRule`，不是逐件分别计价。

### 8.5 当前未计算项

当前 IPEC calculator 已计算：

- Base
- Fuel
- GST

当前没有自动计算：

- UBI additional fee 中的 penalty below 3kg / More to Pay
- redelivery / RTS / underticketing
- on-forwarding fee
- oversize fee

这些目前主要用于 invoice reconciliation 证据或后续 surcharge 开发。若要进入报价，需要建立可预测触发条件和 `SurchargeRule`。

### 8.6 Breakdown

IPEC breakdown 会显示：

- IPEC Road base
- Fuel levy
- GST

Debug 中会显示：

- origin zone
- destination zone
- actual kg
- cubic kg
- chargeable kg
- cubic factor
- minimum/basic/per kg/kg included
- fuel rate
- zone lookup method

## 9. 通用 Table Rate Calculator

当前主要 named carrier 都使用专用 calculator。通用 `TableRateCalculator` 仍保留给简单 rate card 使用。

通用逻辑：

```text
chargeable_weight = ceil(sum(max(dead, cubic) for each item))
base = max(minimum_charge, basic_charge + per_kg * chargeable_weight)
```

税务：

- 如果 `RateCard.tax_mode = INC_GST`，则 base 视为含 GST，系统反算 GST
- 否则 base 视为 ex GST，按 `RateCard.gst_rate` 加 GST

## 10. 单货 vs 多货对比示例

假设有两件货：

| SKU | Qty | Dead kg | Cubic kg |
|---|---:|---:|---:|
| A | 1 | 10 | 30 |
| B | 1 | 20 | 5 |

不同 carrier 的计费重量可能不同：

| Carrier | 计算方式 | Chargeable kg |
|---|---|---:|
| Hunter | `max(A dead, A cubic) + max(B dead, B cubic)` | 30 + 20 = 50 |
| DFE | `max(sum dead, sum cubic)` | max(30, 35) = 35 |
| IPEC | `max(sum dead, sum cubic)` | max(30, 35) = 35 |
| Allied GRO | `max(round(sum dead), round(sum cubic))` | max(30, 35) = 35 |
| Allied B2C | base 逐件，OWS 用整票 | A 按 30、B 按 20；OWS 用 50 |
| Orange Connex | 每件按单件实重进固定重量档 | A 用 10kg 档，B 用 20kg 档 |

这也是为什么同一个订单在不同快递中，单货和多货的差异可能非常大。

## 11. Review Points

以下是当前逻辑中建议业务确认或后续测试重点：

1. Allied GRO `two_person_crew` 当前按代码使用 `single_cubic = round0((item.volume_m3 * 250) / qty)`。如果 `volume_m3` 本身已经是单件体积，多数量时这里可能需要业务确认是否应除以 qty。
2. Allied GRO fuel 采用 legacy ratio，对含 GST subtotal 做倍率调整，和其他 carrier 的 fuel percentage 不同。
3. Allied B2C 当前不拆 GST，`total_ex_gst` 和 `total_inc_gst` 都等于含 fuel 后总价。这是现有代码行为，是否需要拆税要看源费率含税定义。
4. UBI IPEC oversize / on-forwarding 已有数据线索，但没有加入当前报价计算。加入前需要确认可预测触发条件和价格公式。
5. Orange Connex 按实重档逐件算，不使用体积重计价，但仍用尺寸做 not-available 限制。
