# Freight Carrier Calculation Logic - 2026-06-23

本文档整理 Freight Intelligence 当前已经启用的运费估算通道，并按 `Agent / Courier / Service / QuoteChannel` 说明单货、多货、surcharge、fuel、GST 和审计口径。内容来自当前数据库启用的 `QuoteChannel`、对应 `RateCard`，以及后端 calculator 源码。

相关代码位置：

- 通用 calculator 结构：`backend/freight/calculators/base.py`
- 报价引擎：`backend/freight/quote_engine.py`
- 通道 eligibility：`backend/freight/services/channel_eligibility.py`
- Rate card 选择：`backend/freight/services/rate_card_selector.py`
- Freight Audit Matrix：`backend/freight/management/commands/build_freight_audit_matrix.py`

## 1. 当前启用估算通道

| Agent | Courier | Service | QuoteChannel | Provider | Rate Card / Version | Calculator |
|---|---|---|---|---|---|---|
| UBI | Team Global Express | UBI TGE IPEC Road | `ubi_tge_ipec_mel1` | TABLE | UBI TGE IPEC Road MEL1 2026-04-20 / `UBI-IPEC-20260420-MEL1` | `UbiTgeIpecCalculator` |
| UBI | Team Global Express | UBI TGE IPEC Road | `ubi_tge_ipec_syd1` | TABLE | UBI TGE IPEC Road SYD1 2026-04-20 / `UBI-IPEC-20260420-SYD1` | `UbiTgeIpecCalculator` |
| System / Local rate table | Allied Express | Allied B2C 2025 Melbourne | `pc_allied_b2c_2025_mel` | TABLE | Allied B2C 2025 Melbourne PostageCalculator SP / `SP-ALLIED-B2C-MEL-2025` | `AlliedB2C2025MelbourneCalculator` |
| System / Local rate table | Allied Express | Allied GRO 2023 Melbourne | `pc_allied_gro_2023_mel` | TABLE | Allied GRO 2023 Melbourne PostageCalculator SP / `SP-ALLIED-GRO-MEL-2023` | `AlliedGro2023MelbourneCalculator` |
| System / Local rate table | Allied Express | Allied GRO 2023 Sydney | `pc_allied_gro_2023_syd` | TABLE | Allied GRO 2023 Sydney PostageCalculator SP / `SP-ALLIED-GRO-SYD-2023` | `AlliedGro2023SydneyCalculator` |
| System / Local rate table | Direct Freight Parcel | DFE KILO EX MEL 2025 | `dfe_ex_mel_2025` | TABLE | DFE EX MEL Feb 2025 / `DFE-EX-MEL-FEB-2025` | `DirectFreightExpress2025Calculator` |
| System / Local rate table | Direct Freight Parcel | DFE KILO EX SYD 2025 | `dfe_ex_syd_2025` | TABLE | DFE EX SYD Feb 2025 / `DFE-EX-SYD-FEB-2025` | `DirectFreightExpress2025Calculator` |
| System / Local rate table | Hunter Road Freight | Hunter MEL 2023 | `pc_hunter_mel_2023` | TABLE | Hunter MEL 2023 PostageCalculator SP / `SP-HUNTER-MEL-2023` | `HunterMel2023Calculator` |
| System / Local rate table | Hunter Road Freight | Hunter Sydney 2025 | `pc_hunter_syd_2025` | TABLE | Hunter SYD Broers 20240920 / `SP-HUNTER-SYD-2025` | `HunterSydney2025Calculator` |
| System / Local rate table | Orange Connex | Orange Connex eFN MEL 2026 | `orange_efn_mel_2026` | TABLE | Orange Connex eFN MEL 2026 / `ORANGE-EFN-MEL-2026` | `OrangeConnexEfn2026Calculator` |
| System / Local rate table | Orange Connex | Orange Connex eFN SYD 2026 | `orange_efn_syd_2026` | TABLE | Orange Connex eFN SYD 2026 / `ORANGE-EFN-SYD-2026` | `OrangeConnexEfn2026Calculator` |

说明：

- `UBI` 是 Agent，不是 courier。UBI 维护的是 Team Global Express IPEC Road 的价卡。
- EIZ / SHIPPIT / Sunyee 等 API 或历史 LSP quote 数据可以进入审计和历史对比，但当前没有真实 API calculator 被纳入上述启用估算通道。
- Manual Quote 会按 platform / warehouse / carrier eligibility 过滤通道；Freight Audit Matrix 可以指定 `quote_channel_code` 或 `carrier_keyword` 横向复算历史订单。

## 2. 通用计算入口

系统不会在 calculator 内直接读取 ERP/WMS 原表。报价前先把订单、SKU、combo SKU 和手工尺寸统一成 `QuoteItem`：

| 字段 | 含义 |
|---|---|
| `sku` | SKU 或手工输入标识 |
| `qty` | 数量 |
| `unit_weight_kg` | 单件实重 kg |
| `length_cm / width_cm / height_cm` | 单件尺寸 cm |

通用体积：

```text
volume_m3 = length_cm * width_cm * height_cm / 1,000,000
```

Combo SKU 在进入 calculator 前会通过同步来的 combo 组成快照展开或聚合为可计算的 item lines。Calculator 只看最终 item lines、qty、重量、尺寸和目的地。

## 3. Channel / Rate Card 选择规则

### 3.1 Manual Quote

Manual Quote 使用 `QuoteEngine.quote_manual`：

1. 根据 `platform_code` 找启用平台，`ALL` 表示所有启用平台。
2. 根据 `warehouse_code` 找启用仓库，`ALL` 表示所有启用仓库。
3. 必须存在启用的 `WarehousePlatform`。
4. `PlatformCarrier` 和 `WarehouseCarrier` 的启用 service 取交集。
5. 只保留启用 `QuoteChannel`，并按仓库 origin 过滤 MEL / SYD。
6. Rate card 必须 `ACTIVE + is_active`，且满足 `effective_from/effective_to`。

### 3.2 Origin 过滤

仓库 state/code/name/region 中包含以下词时会被识别：

| Origin | 识别词 |
|---|---|
| MEL | `VIC`, `MEL`, `MELB`, `MELBOURNE` |
| SYD | `NSW`, `SYD`, `SYDN`, `SYDNEY` |

MEL 仓库只参与 MEL channel/rate card；SYD 仓库只参与 SYD channel/rate card。`ALL warehouse` 会允许多个 origin 都参与。

### 3.3 Freight Audit Matrix

Freight Audit Matrix 使用 `QuoteEngine.quote_selected_channels`。它会绕过 platform/warehouse carrier eligibility，用指定或启用的 calculator 对同一历史订单横向复算。

常用命令：

```powershell
python manage.py build_freight_audit_matrix --batch-id 221 --mode CONSIGNMENT --limit 5000 --order-batch-size 5000
python manage.py build_freight_audit_matrix --batch-id 221 --source-config HUNTER --carrier-keyword pc_hunter_mel_2023 --mode CONSIGNMENT --limit 5000
```

## 4. GST 和金额显示口径

| 价格字段 | 来源 | 显示口径 |
|---|---|---|
| ERP Est | ERP `postage_shipping_estimated_amount` / `shipping_estimated_amount` | ERP 原值按 ex GST 保存，前端外层显示为 `ERP Est * 1.10` |
| System Est | 当前 calculator/rate card 重新计算 | 外层显示含 GST `total_inc_gst`；breakdown 显示 base/surcharge/fuel/GST |
| Invoice Actual | InvoiceReader 实收 | 对账统一按含 GST 实收金额 |

外层列表不显示 base/fuel 等内部项，详情和 breakdown 才显示费用构成。

## 5. Hunter Road Freight

启用服务：

- `pc_hunter_mel_2023` / Hunter MEL 2023
- `pc_hunter_syd_2025` / Hunter Sydney 2025

### 5.1 Zone 和 Rate Rule

使用通用 `RateZone`：

1. `postcode + suburb + state` exact match
2. `postcode + state` fallback
3. postcode range fallback

命中后用 `dest_zone + chargeable_weight` 找 `RateRule`。

### 5.2 单货计算

每个 item 先判断 oversize：

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

单 line：

```text
unit_cubic_kg = length_m * width_m * height_m * cubic_factor
line_dead_kg = unit_weight_kg * qty
line_cubic_kg = unit_cubic_kg * qty
line_chargeable_kg = max(line_dead_kg, line_cubic_kg)
chargeable_kg_for_rate = ceil(sum(line_chargeable_kg))
```

Base：

```text
base = max(minimum_charge, basic_charge + per_kg * chargeable_kg_for_rate)
```

### 5.3 多货计算

Hunter 是逐 line 先取较大值，再汇总：

```text
total_chargeable_kg = sum(max(line_dead_kg, line_cubic_kg) for each SKU line)
chargeable_kg_for_rate = ceil(total_chargeable_kg)
```

这比“总实重 vs 总体积重取最大”更保守，因为不同 SKU 的实重优势和体积优势不会互相抵消。

### 5.4 Surcharge / Fuel / GST

当前启用 surcharge：

| Code | 依据 | 当前逻辑 |
|---|---|---|
| `RESI` | 单件最大 charge kg | residential fee |
| `LEN` | 最长边 m | length surcharge；GE_6 会标记 POA flag |
| `UPLF` | 整票 chargeable kg | uplift surcharge |
| `FS` | always | 普通 fuel levy，当前配置 21% |
| `FS_WA` | WA destination | WA fuel levy，当前配置 28% |

```text
surcharge_total = RESI + LEN + UPLF
fuel_basis = base + surcharge_total
fuel = fuel_basis * selected_fuel_rate
total_ex_gst = base + surcharge_total + fuel
gst = total_ex_gst * 0.10
total_inc_gst = total_ex_gst + gst
```

## 6. Allied Express GRO 2023

启用服务：

- `pc_allied_gro_2023_mel`
- `pc_allied_gro_2023_syd`

Sydney calculator 继承 Melbourne calculator，公式相同，差异来自 rate card、zone 和 rule 数据。

### 6.1 Zone / Weight

```text
dead_kg = round(sum(unit_weight_kg * qty))
cubic_kg = round(sum(volume_m3 * 250 * qty))
chargeable_kg = max(dead_kg, cubic_kg)
```

用 `dest_zone + chargeable_kg` 找 rate rule。

### 6.2 Base

```text
linehaul = max(minimum_charge, basic_charge + per_kg * chargeable_kg)
```

如果 `RateZone.raw_payload.on_forward.matched = true`：

```text
on_forward_base = on_forward.basic + on_forward.per_kg * chargeable_kg
base = linehaul + on_forward_base
```

否则 `on_forward_base = 0`。

### 6.3 Surcharge

Allied GRO 会计算多类 legacy surcharge，但最终只取一个最高/选中项：

| 类型 | 依据 |
|---|---|
| LSC | item longest side |
| WS | item width/long side规则 |
| DHSL | item longest side |
| DHSW | item unit weight |
| Home Delivery HDD/HDC | 整票 dead/cubic |
| Two Person Crew | longest, middle, dead kg, cubic kg |

```text
item_surcharge = sum(max(LSC, WS, DHSL, DHSW) * qty for each item)
home_delivery = HDD(dead) if dead >= cubic else HDC(cubic)
if on_forward matched:
  home_delivery = home_delivery * 2
chosen_surcharge = max(home_delivery, item_surcharge, two_person_crew)
```

### 6.4 Fuel / GST

Allied GRO 的 fuel 配置是 ratio，不是单独百分比加法。当前 `FS` 配置为 `1.2679`。

```text
subtotal = linehaul + on_forward_base + chosen_surcharge
total_inc_gst = subtotal * 1.10 * fuel_ratio
gst = subtotal * 0.10
fuel_amount = subtotal * 1.10 * (fuel_ratio - 1)
```

注意：breakdown 中 fuel 已经按最终 ratio 展开，不需要再人工联动计算。

## 7. Allied Express B2C 2025

启用服务：

- `pc_allied_b2c_2025_mel`

### 7.1 单货 / 多货

Allied B2C 是逐件 base，但 OWS 按整票重量：

```text
single_cubic_kg = volume_m3 * 250
chargeable_single = max(unit_weight_kg, single_cubic_kg)
consignment_weight = sum(chargeable_single * qty)
```

先用 `consignment_weight` 找 rate rule。

逐件 base：

```text
per_piece = max(basic_charge + per_kg * chargeable_single, minimum_charge)
basic_total = sum(per_piece * qty)
```

### 7.2 Surcharge / Fuel

| Code | 依据 | 说明 |
|---|---|---|
| `OIS` | 单件最长边，按每件 qty 累计 | oversized item surcharge |
| `OWS` | 整票 chargeable weight | overweight surcharge |
| `FS` | always | 当前配置 `0.27` |

```text
ois_total = sum(OIS(longest_side) * qty)
ows_total = OWS(round(consignment_weight))
subtotal = basic_total + ois_total + ows_total
total = subtotal * (1 + fuel_ratio)
```

当前 calculator 将 `total` 作为 `total_inc_gst`，`gst_amount = 0`，表示 imported B2C rate/fuel 口径已经按该价卡逻辑处理，不再额外加 GST。

## 8. Direct Freight Express

启用服务：

- `dfe_ex_mel_2025`
- `dfe_ex_syd_2025`

### 8.1 Profile 限制

DFE calculator 先检查包裹 profile，不满足时直接 `NOT_AVAILABLE`：

| 限制 | not available reason |
|---|---|
| 重量或尺寸缺失 / qty <= 0 | `missing_dimension_or_weight` |
| 单件实重 > 30 kg | `dfe_profile_item_over_30kg` |
| 最长边 > 120 cm | `dfe_profile_length_over_120cm` |
| 最长边 > 70 cm 且第二长边 > 70 cm | `dfe_profile_two_sides_over_70cm` |

### 8.2 Zone

DFE 使用独立 zone lookup：

1. postcode 会补齐 4 位，例如 `800` -> `0800`
2. exact：`postcode + suburb + state`
3. fallback：如果 `postcode + state` 只对应一个唯一 zone，则使用该 zone
4. 多个 zone 时返回 `ambiguous_destination_zone`

### 8.3 单货 / 多货

DFE 是整票合并后计算：

```text
actual_kg = sum(unit_weight_kg * qty)
cubic_kg = sum(volume_m3 * cubic_factor * qty)
chargeable_kg = ceil(max(actual_kg, cubic_kg))
```

`cubic_factor` 来自 `RateCard.cubic_factor`，默认 250。

Base：

```text
base = max(minimum_charge, basic_charge + per_kg * chargeable_kg)
```

### 8.4 Destination Surcharge / Fuel / GST

DFE 有大量 `DFE_DEST` surcharge，按 postcode/suburb 或 postcode 匹配：

1. exact：`condition_json.postcode + condition_json.suburb`
2. fallback：如果同 postcode 的 surcharge 金额唯一，则使用该金额
3. 金额不唯一时不自动套用，避免错误收费

Fuel：

```text
fuel_basis = base + destination_surcharge
fuel = fuel_basis * FS
total_ex_gst = base + destination_surcharge + fuel
gst = total_ex_gst * 0.10
total_inc_gst = total_ex_gst + gst
```

当前 DFE `FS` 配置来自 rate card，示例配置为 19.6%。

## 9. Orange Connex eFN

启用服务：

- `orange_efn_mel_2026`
- `orange_efn_syd_2026`

### 9.1 Profile 限制

每件 article 必须满足：

| 限制 | not available reason |
|---|---|
| 重量/尺寸/qty 缺失或 <= 0 | `missing_dimension_or_weight` |
| 单件实重 > 25 kg | `orange_profile_item_over_25kg` |
| 最长边 > 105 cm | `orange_profile_length_over_105cm` |
| 单件体积 > 0.088 m3 | `orange_profile_volume_over_0_088m3` |

### 9.2 Zone

Orange 使用 postcode/suburb/state：

1. exact：`postcode + suburb + state`
2. fallback：如果 `postcode + state` 只有一个唯一 zone，则使用该 zone
3. 如果没有候选，则使用 `Rest of AU`
4. 多个候选 zone 时返回 `ambiguous_destination_zone`

### 9.3 单货 / 多货

Orange eFN 是逐 article 固定重量档：

```text
article_weight_grams = ceil(unit_weight_kg * 1000)
band_weight_kg = article_weight_grams / 1000
unit_price = RateRule(to_zone, band_weight_kg).basic_charge
line_base = unit_price * qty
base = sum(line_base)
```

如果目的 zone 找不到对应重量档，且 zone 不是 `Rest of AU`，会尝试 fallback 到 `Rest of AU`。

### 9.4 GST

Orange 当前无 fuel/surcharge：

```text
total_ex_gst = base
gst = base * 0.10
total_inc_gst = base + gst
```

## 10. UBI Agent / Team Global Express IPEC

启用服务：

- Agent：UBI
- Courier：Team Global Express
- Service：UBI TGE IPEC Road
- Channels：`ubi_tge_ipec_mel1`, `ubi_tge_ipec_syd1`

### 10.1 Zone

IPEC 使用独立 zone lookup：

1. postcode 补齐 4 位
2. exact：`postcode + suburb + state`
3. fallback：`postcode + state` 只有一个唯一 zone 时使用
4. 多个候选 zone 时返回 `ambiguous_destination_zone`
5. 如果有 override mapping，优先使用 override

Rate rule 需要匹配：

```text
from_zone + to_zone + chargeable_kg
```

### 10.2 单货 / 多货

UBI TGE IPEC 是整票合并后计算：

```text
actual_kg = sum(unit_weight_kg * qty)
cubic_kg = sum(volume_m3 * cubic_factor * qty)
chargeable_kg = ceil(max(actual_kg, cubic_kg))
```

`cubic_factor` 来自 `RateCard.cubic_factor`，默认 250。

Base：

```text
included_kg = RateRule.raw_payload.kg_included_in_basic
rated_kg = max(0, chargeable_kg - included_kg)
base = max(minimum_charge, basic_charge + per_kg * rated_kg)
```

### 10.3 Fuel / GST

当前 UBI IPEC fuel 是 `SurchargeRule` code `FS`，配置为 9.9%。

```text
fuel = base * FS
total_ex_gst = base + fuel
gst = total_ex_gst * rate_card.gst_rate
total_inc_gst = total_ex_gst + gst
```

### 10.4 当前限制

- 已做的是 UBI TGE IPEC Road。
- UBI Fastway / Aramex / eParcel / Toll Priority 等如要作为系统估算通道，需要各自的 rate card importer、zone mapping 和 calculator 启用后才进入当前清单。
- UBI invoice 实收仍可进入 Invoice Reconciliation，但不代表已存在可复算的内部 calculator。

## 11. Breakdown / Trace / Audit

每个可用报价会保存：

- `QuoteRun`
- `QuoteCandidate`
- `QuoteChargeLine`
- `QuoteTraceLog`
- Freight Audit 时额外保存 `FreightAuditRow` 和 `FreightAuditResult`

Breakdown 中只显示有金额或有实际触发意义的费用项；未触发或 0 的 OIS/surcharge 不应在前端详情里干扰用户。

Freight Audit Matrix 建议工作流：

1. 先看顶部 estimate-enabled carrier list。
2. 点某个 channel 的 `Review`。
3. 下方只看该 channel 的历史订单、tracking、ERP Est、System Est、Invoice Actual。
4. 点行打开详情，逐 tracking 查看 base、surcharge、fuel、GST 和 item calculation。

## 12. 当前未启用为内部估算的 Agent / Courier

以下来源可以出现在订单、InvoiceReader、LSP API quote 或历史报价中，但当前不属于“内部 rate table calculator 已启用”的清单：

| Agent / Source | Courier | 当前状态 |
|---|---|---|
| EIZ / Sunyee | Australia Post, Allied, Aramex, Hunter, TNT 等 | 主要作为 API/historical quote 或 invoice source；缺真实 API connector 或启用的 table calculator |
| SHIPPIT | Couriers Please, Smart Routing Bulky, Allied API 等 | 可同步历史/API 数据；未作为内部 calculator 启用 |
| UBI | Fastway/Aramex/eParcel/Toll Priority/其他 UBI invoice channel | 可进入 invoice reconciliation；内部估算需单独导入价卡和启用 calculator |
| LSP rate table raw data | 多 carrier | LSP 原始 rate data 已不作为当前系统估算主来源；系统估算以当前 RateCard/QuoteChannel 为准 |

## 13. 快速核对清单

开发或对账时，判断一个 courier 是否“已经能系统估算”，以这几个条件为准：

1. `Carrier.active = true`
2. `CarrierService.active = true`
3. 存在启用 `QuoteChannel.enabled = true`
4. `QuoteChannel.provider_type = TABLE` 或已实现真实 API provider
5. `calculator_key` 能加载
6. rate card 当前有效：`status=ACTIVE`, `is_active=true`, effective date 覆盖 quote date
7. 目的地 zone 能匹配
8. SKU/手工输入有重量和尺寸
9. 对应 profile 限制没有触发 not available

如果 Freight Audit 中看到 `NOT_AVAILABLE`，优先查看 detail 中的 `not_available_reason` 和 `debug_breakdown.stage`。
