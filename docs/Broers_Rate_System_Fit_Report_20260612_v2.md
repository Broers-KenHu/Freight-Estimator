# Broers Rate Package System Fit Report v2

生成日期：2026-06-12  
分析目录：`C:\Users\KenHu\Downloads\Broers rate`  
项目目录：`C:\Users\KenHu\.vscode\CourieDelivery`  
更新重点：复核 Allied 缺失的 postcode primary zone mapping 是否可复用系统中 2023 Allied GRO 路费表数据。

## 1. 本次更新结论

本次复核后，Allied Broers 的判断从“缺 primary zone mapping，不能完整接入”更新为：

> Allied Broers 的 ROAD 价卡可以使用现有 PostageCalculator Allied GRO 2023 数据作为 postcode/suburb/state -> primary `To Zone` mapping。对当前主要仓库 origin `N01` 和 `V01`，Broers Road Rates 与 2023 GRO rate data 的 zone 集合和价格均完全一致。

仍然不能直接自动完成的部分：

- Allied `Surcharge Summary.pdf` 仍然无法文本抽取，只能抽到标题，需要 OCR 或人工录入后才能可靠配置 surcharge/fuel。
- Allied `Pallet Rates`、`Courier KLM`、`Taxi Truck TN/TA` 需要订单输入支持 pallet count、vehicle type、KLM/local courier 场景，否则只能先作为 reference/disabled service。
- Broers Road Rates 不是完整 175x175 矩阵。只有 `N01`, `V01`, `Q01`, `S01`, `W01` 五个主 origin 各覆盖全部 175 个 destination zone；其他 origin 只覆盖少量 lane。

## 2. 数据库复核状态

PostgreSQL 当前可连接，已复核关键 rate card / quote channel。

| Rate card | Carrier | Active | Zones | Rules | Surcharges | Source |
|---|---|---:|---:|---:|---:|---|
| `SP-ALLIED-GRO-MEL-2023` | Allied Express | Yes | 16,968 | 189 | 32 | PostageCalculator |
| `SP-ALLIED-GRO-SYD-2023` | Allied Express | Yes | 16,968 | 189 | 32 | PostageCalculator |
| `DFE-EX-MEL-FEB-2025` | Direct Freight Parcel | Yes | 16,257 | 169 | 1,460 | DirectFreightExpressProposal |
| `DFE-EX-SYD-FEB-2025` | Direct Freight Parcel | Yes | 16,257 | 150 | 1,460 | DirectFreightExpressProposal |
| `ORANGE-EFN-MEL-2026` | Orange Connex | Yes | 2,762 | 408 | 6 | OrangeConnexEfn2026 |
| `ORANGE-EFN-SYD-2026` | Orange Connex | Yes | 2,762 | 408 | 6 | OrangeConnexEfn2026 |

已启用 quote channel：

| Channel | Enabled | Rate card |
|---|---:|---|
| `pc_allied_gro_2023_mel` | Yes | `SP-ALLIED-GRO-MEL-2023` |
| `pc_allied_gro_2023_syd` | Yes | `SP-ALLIED-GRO-SYD-2023` |
| `dfe_ex_mel_2025` | Yes | `DFE-EX-MEL-FEB-2025` |
| `dfe_ex_syd_2025` | Yes | `DFE-EX-SYD-FEB-2025` |
| `orange_efn_mel_2026` | Yes | `ORANGE-EFN-MEL-2026` |
| `orange_efn_syd_2026` | Yes | `ORANGE-EFN-SYD-2026` |

备注：Direct Freight carrier 在当前 DB 中显示为 `Direct Freight Parcel`，但 rate card/channel 指向 DFE 计算逻辑。后续可以统一 display name 为 `Direct Freight Express`，避免前端显示歧义。

## 3. Allied 2023 Mapping 复核

### 3.1 现有 2023 mapping 数据

系统中 PostageCalculator Allied GRO 2023 已导入两张主卡：

| Version | Origin | Address zone rows | Unique address rows | Raw from zone | Unique raw to zones |
|---|---|---:|---:|---|---:|
| `SP-ALLIED-GRO-MEL-2023` | Melbourne | 16,968 | 16,968 | `V01` | 175 |
| `SP-ALLIED-GRO-SYD-2023` | Sydney | 16,968 | 16,968 | `N01` | 175 |

每个 raw `To Zone` 只有一组价格，不存在同一 zone 多套 base/per kg/min 的冲突。这意味着 2023 数据可以被拆成两个部分：

- 地址映射：`postcode + suburb + state -> raw To Zone`
- 价格规则：`origin zone + raw To Zone -> basic / per kg / minimum`

Broers 新价卡可以复用第一部分地址映射，再用 Broers workbook 的 Road Rates 作为价格规则。

### 3.2 Broers Road Rates 对齐结果

Broers `BROGRO - RATE CARD..xlsx` / `Road Rates` 读取结果：

| Metric | Result |
|---|---:|
| ROAD rows | 1,725 |
| Unique from zones | 175 |
| Unique to zones | 175 |
| `To Zone` 与 2023 Allied GRO mapping 交集 | 175 / 175 |
| Broers-only to zones | 0 |
| 2023 mapping-only to zones | 0 |

对当前最重要的两个 origin：

| Origin | 2023 source card | 2023 to zones | Broers pairs for origin | Matched zones | Rate same as 2023 | Different rates |
|---|---|---:|---:|---:|---:|---:|
| `V01` | `SP-ALLIED-GRO-MEL-2023` | 175 | 175 | 175 | 175 | 0 |
| `N01` | `SP-ALLIED-GRO-SYD-2023` | 175 | 175 | 175 | 175 | 0 |

结论：

- 如果当前 warehouse state 是 VIC，Allied Broers 可以使用 `V01` origin。
- 如果当前 warehouse state 是 NSW，Allied Broers 可以使用 `N01` origin。
- 对这两个 origin，Broers ROAD linehaul 价格与系统中 2023 GRO 已导入价格完全一致。
- 所以 Allied ROAD 的 primary zone mapping 缺口已经可以用 2023 GRO 数据解决。

### 3.3 其他 origin 覆盖

Broers Road Rates 中全覆盖 175 个 destination zone 的 origin：

| Origin | 说明 |
|---|---|
| `N01` | Sydney / NSW 主 origin |
| `V01` | Melbourne / VIC 主 origin |
| `Q01` | Queensland 主 origin |
| `S01` | South Australia 主 origin |
| `W01` | Western Australia 主 origin |

其他 origin 大多只有 5 条 lane，不适合作为普通 warehouse origin 自动报价。

因此，后续可以按仓库 state 映射 origin：

| Warehouse state | Allied origin zone |
|---|---|
| NSW / ACT | `N01` |
| VIC / TAS | `V01` |
| QLD | `Q01` |
| SA / NT | `S01` |
| WA | `W01` |

其中 NSW/VIC 已经与 2023 GRO 数据做了完整价格一致性校验；QLD/SA/WA 还需要确认是否有对应 PostageCalculator source mapping 或可复用同一 `To Zone` 地址映射。

## 4. Allied Broers 建议落地方案

### 4.1 新增 rate card/channel，而不是覆盖当前 `pc_allied_*`

建议保留当前 PostageCalculator channel，同时新增 Broers agent 下的 Allied ROAD channel：

| Object | 建议 |
|---|---|
| Agent | `Broers`，`maintains_rate_cards = true` |
| Carrier | 复用 `Allied Express` |
| Service | `BROERS_ALLIED_GRO_ROAD_2025` 或 `ALLIED_BROGRO_ROAD_2025` |
| Rate card | `BROERS-ALLIED-GRO-ROAD-2025` |
| Source | `Broers.BROGRO.RoadRates` |
| Calculator | 可复用 Allied GRO calculator 或创建 `AlliedBroersGroRoad2025Calculator` |
| Tax mode | EX GST |
| Effective from | 2025-11-10，来自 workbook 顶部日期 |
| Surcharge | 暂先复用现有 Allied GRO 2023 surcharge，或待 OCR 后导入 Broers surcharge |

### 4.2 推荐的数据导入方式

导入时不要把 2023 价格直接复制成 Broers 价格，而应明确分层：

1. 从 PostageCalculator Allied GRO 2023 读取地址映射：
   - `state`
   - `suburb`
   - `postcode`
   - raw `To Zone`
2. 用 Broers Road Rates 导入价格：
   - `FROM ZONE`
   - `TO ZONE`
   - `BASIC`
   - `PER KG`
   - `MIN CHARGE`
3. 生成 Broers rate card 的 `RateZone`：
   - address fields 来自 2023 mapping
   - `dest_zone` 使用 raw `To Zone`
   - `raw_payload.source_mapping_card` 记录来自 `SP-ALLIED-GRO-MEL-2023` / `SP-ALLIED-GRO-SYD-2023`
4. 生成 Broers `RateRule`：
   - `from_zone = N01/V01/...`
   - `to_zone = raw To Zone`
   - `basic_charge/per_kg/minimum_charge` 来自 Broers workbook
5. calculator 按 warehouse state 选择 allowed origin：
   - 只让对应 origin 的 rate card / rule 参与报价。

### 4.3 Calculator 注意点

现有 Allied GRO calculator 当前使用内部 `dest_zone` 由价格哈希生成，例如 `ABX_50BCA7D1`。Broers 新方案建议直接使用 raw zone code，例如 `ABX`、`N01`、`V01`，这样更容易和 Broers workbook 对账。

建议新增一个 Broers calculator 或改造通用 Allied calculator 支持：

- `RateZone.dest_zone = raw To Zone`
- `RateRule.to_zone = raw To Zone`
- `RateRule.from_zone = origin zone`
- quote 时同时匹配 `from_zone + to_zone`
- breakdown 显示 raw origin / destination zone，不显示哈希 zone。

这样将来排查时能直接看到：

```text
Origin zone: V01
Destination zone: ABX
Base: 8.84
Per kg: 0.31
Minimum: 10.71
```

### 4.4 Surcharge 处理策略

短期可行：

- 先复用现有 Allied GRO 2023 的 32 条 surcharge rule，让 ROAD quote 可跑。
- 报告和 UI 中标记 surcharge source = `PostageCalculator Allied_GRO_Item_Surcharge2023`。

中期正确方案：

- 对 `Surcharge Summary.pdf` 做 OCR 或人工录入。
- 与 `Additional Services` sheet 交叉核对。
- 建立 Broers 独立 `SurchargeRule`，source = `Broers BROGRO Surcharge Summary effective 23.10.2023`。

不能现在自动化的费用：

- Pallet surcharge / oversized pallet
- Taxi truck
- Courier KLM
- AET additional services
- 需要人工事件、车种或 pallet count 的费用

## 5. 逐项判断更新

| No. | Agent | Carrier | v1 判断 | v2 判断 |
|---:|---|---|---|---|
| 1 | Broers | Allied Express | 部分可做，缺 primary zone mapping | ROAD 可以做。Primary zone mapping 可复用 2023 Allied GRO；surcharge/OCR 和 pallet/taxi/local courier 仍需补。 |
| 2 | Broers | Hunter Express | 可以做，尚未做 Broers 版本 | 不变。建议优先做 Broers Hunter importer/calculator。 |
| 3 | ESO | Direct Freight Express | 代码和资料层面已完成；DB 待复核 | 已复核 DB：MEL/SYD rate card 和 channel 均 active/enabled。 |
| 4 | Sunyee(EIZ) | Australia Post | Rate table 可做；API quote 缺资料 | 不变。PDF 是 GST inclusive，需单独建 Australia Post account/service calculator 或等待 API 资料。 |
| 5 | EIZ | Allied Express | API 缺资料 | 不变。不能用 Broers/PC table 替代 EIZ API。 |
| 6 | EIZ | Aramex | API 缺资料 | 不变。 |
| 7 | EIZ | Hunter Express | API 缺资料 | 不变。 |
| 9 | EIZ | TNT | API 缺资料 | 不变。 |
| 10 | UBI | Aramex | 当前目录无价卡 | 不变。 |
| 11 | UBI | TGE / Toll IPEC | 当前目录无价卡 | 不变。 |
| 12 | UBI | TGE / Toll B2C Priority | 当前目录无价卡 | 不变。 |
| 13 | UBI | Australia Post | 可以做，尚未做 | 不变。建议新增 Australia Post band calculator。 |
| 14 | SHIPPIT | Couriers Please | API 缺资料 | 不变。 |
| 15 | SHIPPIT | Smart Routing Bulky | API 缺资料 | 不变。 |
| 16 | SHIPPIT | Allied Express | API 缺资料 | 不变。 |
| 17 | OrangeConnex | PF Logistics | 代码和资料层面已完成；DB 待复核 | 已复核 DB：MEL/SYD rate card 和 channel 均 active/enabled。 |

## 6. 新的实施优先级

### P0：Allied Broers ROAD 接入验证

现在可以进入实施设计：

1. 建 Broers agent。
2. 建 Broers Allied ROAD rate card/channel。
3. 复用 2023 Allied GRO 地址映射生成 Broers `RateZone`。
4. 从 Broers workbook `Road Rates` 导入 `RateRule`。
5. calculator 使用 raw `from_zone + to_zone` 匹配。
6. 先复用现有 Allied GRO surcharge 或标记 surcharge pending。
7. 用 `SOUTH MELBOURNE VIC 3205`、`WOY WOY NSW 2256`、`ALBURY NSW 2640` 等样例验证 quote/breakdown。

### P1：Hunter Broers

Hunter 仍然是资料最完整的新价卡：

- 有 rate workbook。
- 有 zone list。
- 有可读 surcharge/fee guide。
- 与现有 Hunter calculator 逻辑相近。

建议作为下一个完整新增 calculator/importer。

### P2：UBI Australia Post

适合新增 band calculator，但需要确认：

- UBI workbook EX GST 还是所有账户统一税模式。
- 超过 22kg basic/per kg 公式。
- remote/signature/return/manual 等额外费用是否自动计算。

### P3：API 类 carrier

EIZ / SHIPPIT 的 API quote 仍然需要 API 文档和 credential。当前不建议用 mock 或其他 agent 的价卡替代。

## 7. 本次生成的分析文件

| 文件 | 说明 |
|---|---|
| `outputs\broers_rate_analysis\allied_pc_2023_mapping_summary.json` | Allied 2023 mapping 汇总。 |
| `outputs\broers_rate_analysis\allied_pc_2023_rates_by_zone.json` | Allied 2023 每个 raw To Zone 的价格三元组。 |
| `outputs\broers_rate_analysis\allied_broers_vs_pc2023_mapping_comparison.json` | Broers ROAD 与 2023 mapping/price 的对齐结果。 |

## 8. 最终结论

Allied Broers 的 ROAD 价卡现在可以进入系统实施阶段。原来最大的缺口 `postcode primary zone mapping` 已经可以由 2023 Allied GRO 数据解决，并且 `N01` / `V01` 两个主 origin 的 Broers Road Rates 与当前 2023 GRO 已导入价格完全一致。

剩余风险不在 primary zone mapping，而在：

- surcharge/fuel 是否完全按 Broers surcharge PDF；
- pallet/taxi/local courier 这类非 ROAD 服务是否需要自动计算；
- QLD/SA/WA 仓库 origin 是否马上启用；
- 是否要新增 Broers 独立 channel，避免和现有 `pc_allied_gro_2023_*` 混在一起。

