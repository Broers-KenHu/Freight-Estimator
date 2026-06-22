# Broers Rate Package System Fit Report v3

生成日期：2026-06-12  
分析目录：`C:\Users\KenHu\Downloads\Broers rate`  
项目目录：`C:\Users\KenHu\.vscode\CourieDelivery`

## 1. 执行原则

按本次确认后的规则处理：

- Allied 2023 按 GRO 理解。
- Hunter 对应系统已有 Hunter。
- DFE 对应系统已有 DFE。
- 如果 Broers 文件与系统已有费率无差异，不新增费率表。
- 非 ROAD / 非快递服务不进入自动计算，例如 pallet、taxi truck、courier KLM、local courier 等。
- Surcharge 金额优先用 workbook / 数据库可结构化数据；PDF 主要用于确认 surcharge 组合逻辑。

## 2. 本次写入动作

已写入现有 `RateCard.metadata_json.broers_rate_package_verification`，只用于记录复核结果。

没有新增 rate card。  
没有改 `RateRule` 金额。  
没有改 `SurchargeRule` 金额。  
没有改 quote channel 启用状态。

| Rate card | 写入结果 |
|---|---|
| `SP-ALLIED-GRO-MEL-2023` | 标记 Broers Allied ROAD 已复核，无差异，不新增表。 |
| `SP-ALLIED-GRO-SYD-2023` | 标记 Broers Allied ROAD 已复核，无差异，不新增表。 |
| `DFE-EX-MEL-FEB-2025` | 标记 Broers DFE workbook 与现有 DFE 业务 sheet 一致，不新增表。 |
| `DFE-EX-SYD-FEB-2025` | 标记 Broers DFE workbook 与现有 DFE 业务 sheet 一致，不新增表。 |
| `SP-HUNTER-MEL-2023` | 标记 Broers Hunter MEL 与现有 Hunter MEL 2023 一致，不新增表。 |
| `SP-HUNTER-SYD-2025` | 标记 Broers Hunter SYD 与现有 Hunter Sydney 2025 不一致，未写入费率。 |

## 3. 费率对比结论

| Carrier | Broers source | Existing system card | 对比结论 | 处理 |
|---|---|---|---|---|
| Allied Express GRO ROAD | `BROGRO - RATE CARD..xlsx` | `SP-ALLIED-GRO-MEL-2023`, `SP-ALLIED-GRO-SYD-2023` | `N01` / `V01` origin 的 175 个 zone 全部匹配，费率无差异。 | 不新增 rate card，只写 verification metadata。 |
| Hunter Express MEL | `Rate Card - Hunter Road Freight - BroersGroupPtyLtd - 20240920.xlsx` | `SP-HUNTER-MEL-2023` | 16,549 条地址经 Broers Zone List 对齐后全部一致。 | 不新增 rate card，只写 verification metadata。 |
| Hunter Express SYD | 同上 | `SP-HUNTER-SYD-2025` | 16,549 条地址全部可匹配，但价格全部不同。 | 不写入费率，保留当前 2025 Sydney。 |
| Direct Freight Express | `Rate Card - Direct Feight Express Rates Proposal EX SYD Ex MEL Feb 2025.xlsx` | `DFE-EX-MEL-FEB-2025`, `DFE-EX-SYD-FEB-2025` | `Rate EX Mel`, `Ex SYD`, `Surcharge ` 三个业务 sheet 与现有导入源逐格一致。 | 不新增 rate card，只写 verification metadata。 |

## 4. Allied GRO 复核细节

### 4.1 ROAD rate / zone mapping

系统已有 Allied GRO 2023：

| Existing card | Origin | Address zone rows | Unique raw `To Zone` | Surcharge rules |
|---|---|---:|---:|---:|
| `SP-ALLIED-GRO-MEL-2023` | `V01` | 16,968 | 175 | 32 |
| `SP-ALLIED-GRO-SYD-2023` | `N01` | 16,968 | 175 | 32 |

Broers ROAD rate：

| Metric | Result |
|---|---:|
| ROAD rows | 1,725 |
| Unique from zones | 175 |
| Unique to zones | 175 |
| `To Zone` 与现有 2023 GRO mapping 交集 | 175 / 175 |
| Broers-only to zones | 0 |
| Existing-only to zones | 0 |

关键 origin 对比：

| Origin | Existing card | Existing to zones | Broers pairs | Matched zones | Same rate | Different rate |
|---|---|---:|---:|---:|---:|---:|
| `V01` | `SP-ALLIED-GRO-MEL-2023` | 175 | 175 | 175 | 175 | 0 |
| `N01` | `SP-ALLIED-GRO-SYD-2023` | 175 | 175 | 175 | 175 | 0 |

结论：Allied ROAD 不需要新增 Broers rate card。现有 GRO 2023 已覆盖。

### 4.2 Allied surcharge OCR / workbook / DB 匹配

`Surcharge Summary.pdf` 渲染后确认它主要是组合逻辑图，不是金额表：

- Business delivery `<= 2.4m`：MHF 与 LSC/WS 取较大。
- Business delivery `> 2.4m`：PDF 同时标记 MHF 和 LSC/WS，倾向理解为叠加，需业务确认。
- Home delivery `<= 2.4m`：DHS、LSC/WS、HD、2MC 取较大。
- Home delivery `> 2.4m`：PDF 单独标记 DHS，并对 LSC/WS、HD、2MC 取较大，倾向理解为 DHS + max(other group)，需业务确认。

金额表来自 `Additional Services`，与数据库匹配如下：

| Broers code | DB code | 状态 | 说明 |
|---|---|---|---|
| `LSC` | `LSC` | Match | Length surcharge 金额与阈值匹配。 |
| `WS` | `WS` | Match | Width surcharge 金额与阈值匹配。 |
| `HD` | `HDD` / `HDC` | Match | Home Delivery 按 dead/cubic 分拆后金额匹配。 |
| `2MC` | `2MCD` / `2MCC` | Match with duplicate | 金额匹配。DB 有一条重复的 `2MCC 92-111`，对结果影响很小，但可后续清理。 |
| `DHS` | `DHSW` / `DHSL` | Match by split code | 金额 15.34 匹配；DB 按重量和长度拆成两条规则。 |
| `MHF` | 无完全对应 code | Partial gap | Broers MHF 是 commercial manual handling，30kg+ 或 2.41m+，金额 15.34。DB 没有 MHF 独立 code。 |
| Fuel | `FS` | Configured | Broers 文件只说明 fuel surcharge 会变动，现有 DB 以 `FS` ratio 配置。 |

当前 Allied calculator 的风险：

- 现有逻辑是 `max(LSC, WS, DHSL, DHSW)`，再与 Home Delivery / Two Person Crew 做 max。
- PDF 对 `> 2.4m` 场景可能要求部分费用叠加，而不是全部取 max。
- 因为本次要求只计算快递 ROAD，非 ROAD 服务不自动算；但 MHF/DHS/HD/2MC 仍属于 ROAD surcharge，需要后续确认公式后再改代码。

本次未改 surcharge 计算公式，只记录匹配结果。

## 5. Hunter 复核细节

Broers Hunter Zone List：

| Metric | Result |
|---|---:|
| Zone map rows | 16,548 unique key rows |
| Existing Hunter address rows | 16,549 each card |
| Missing zone map | 0 |
| Missing rate | 0 |

### 5.1 MEL

| Existing card | Broers origin | Matched rows | Same rows | Different rows |
|---|---|---:|---:|---:|
| `SP-HUNTER-MEL-2023` | `MELBOURNE` | 16,549 | 16,549 | 0 |

结论：Broers Hunter MEL 与系统已有 Hunter MEL 完全一致，不新增费率表。

### 5.2 SYD

| Existing card | Broers origin | Matched rows | Same rows | Different rows |
|---|---|---:|---:|---:|
| `SP-HUNTER-SYD-2025` | `SYDNEY` | 16,549 | 0 | 16,549 |

样例差异：

| Destination | Existing Sydney 2025 | Broers SYD |
|---|---|---|
| `ABBOTSBURY NSW 2176` / Sydney zone | min 21.00, basic 8.93, per kg 0.17 | min 21.00, basic 9.6390, per kg 0.2100 |
| `ABERDARE NSW 2325` / NSW Regional Zone 4 | min 20.00, basic 13.38, per kg 0.54 | min 20.00, basic 14.78, per kg 0.58 |

结论：Broers Hunter SYD 不是当前系统 `SP-HUNTER-SYD-2025` 的同一价格，不应按无差异写入。当前保留 2025 Sydney。

如果业务要使用 Broers SYD 价格，有三个选择：

1. 替换现有 `SP-HUNTER-SYD-2025`，但会影响当前 Sydney 估算。
2. 新增一张 Broers Hunter SYD rate card，但这与“无差异不新增”的规则相反。
3. 暂不导入 Broers SYD，只记录差异，本次已采用。

## 6. DFE 复核细节

本包 DFE workbook 与系统既有 DFE source：

| Sheet | Shape | Cell differences |
|---|---:|---:|
| `Rate EX Mel` | 174 x 11 | 0 |
| `Ex SYD` | 152 x 11 | 0 |
| `Surcharge ` | 1515 x 3 | 0 |

DFE zone file 与既有 zone file SHA256 一致。

结论：DFE 不新增费率表，不改导入结果。

## 7. 非 ROAD 服务处理

以下内容保留为 reference，不进入自动 freight quote：

- Allied `Pallet Rates`
- Allied `Courier KLM`
- Allied `Taxi Truck TN`
- Allied `Taxi Truck TA`
- Allied pallet / skid / tail lift / hand unload / local courier 类费用

原因：系统当前报价输入主要基于 SKU 尺寸、重量、数量和地址；这些服务需要 pallet count、vehicle type、local KLM、预约/人工事件等 operational data。按本次要求，只计算快递 ROAD。

## 8. 输出文件

| 文件 | 说明 |
|---|---|
| `outputs\broers_rate_analysis\allied_broers_vs_pc2023_mapping_comparison.json` | Allied Broers ROAD vs existing Allied GRO 2023 对比。 |
| `outputs\broers_rate_analysis\hunter_broers_vs_pc_comparison.json` | Hunter Broers vs existing Hunter 对比。 |
| `outputs\broers_rate_analysis\allied_surcharge_broers_vs_db_mapping.json` | Allied surcharge workbook/PDF vs DB 规则匹配。 |
| `outputs\broers_rate_analysis\allied_surcharge_rendered\page1_3x.png` | Allied surcharge PDF 渲染图。 |

## 9. 最终结论

可以直接沿用已有系统费率、不新增费率表的项目：

- Allied GRO ROAD：现有 2023 GRO 覆盖，Broers ROAD 对 `N01` / `V01` 无差异。
- Hunter MEL：现有 Hunter MEL 2023 覆盖，无差异。
- DFE MEL/SYD：现有 DFE 覆盖，业务 sheet 无差异。

不能直接写入的项目：

- Hunter SYD：Broers SYD 与现有 Sydney 2025 有全量价格差异。
- Allied MHF / `>2.4m` surcharge 组合逻辑：金额大多可匹配，但 MHF 无独立 DB code，PDF 逻辑显示部分场景可能需要叠加，不应在未确认前改计算公式。

