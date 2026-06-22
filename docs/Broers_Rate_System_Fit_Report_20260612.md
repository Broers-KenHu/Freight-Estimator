# Broers Rate Package System Fit Report

生成日期：2026-06-12  
分析目录：`C:\Users\KenHu\Downloads\Broers rate`  
项目目录：`C:\Users\KenHu\.vscode\CourieDelivery`

## 1. 结论摘要

这批 Broers rate 资料可以分成三类：

| 分类 | Carrier / 资料 | 结论 |
|---|---|---|
| 已有系统计算逻辑，资料与当前实现一致或高度一致 | Direct Freight Express、OrangeConnex / PF Logistics | 系统已有 importer、calculator、文档和测试文件。DFE 的业务 sheet 与既有文件逐格一致；OrangeConnex 文件哈希与既有文件完全一致。数据库当前连接超时，实际导入状态需要 PostgreSQL 恢复后复核。 |
| 可以做进系统，但还不是当前系统已完成的 Broers 版本 | Hunter Express Broers、UBI Australia Post、Australia Post N0/Q0/V0 rate schedules | 资料结构足够，能够设计/实现为新的 Broers/UBI/Sunyee agent 下的 rate card calculator。Hunter 最接近可直接落地；Australia Post 需要新增固定重量段/zone calculator。 |
| 只能部分做或暂时不能做 | Allied Broers、EIZ API carriers、Shippit API carriers、UBI Aramex/TGE/Toll | Allied 有 rate table 但缺明确 postcode -> primary zone mapping，surcharge PDF 也不是可抽取文本；API carrier 缺少接口文档、认证方式和 request/response 样例；UBI Aramex/Toll/TGE 当前目录没有价卡文件。 |

重要判断：

- Broers、EIZ、UBI、SHIPPIT、OrangeConnex 应作为 `Agent` / rate owner 维度保留。同一个 carrier 在不同 agent 下可能价格不同，不能只按 carrier name 合并。
- “导入 rate table”不等于“可计算报价”。可计算报价至少需要 origin mapping、destination zone mapping、rate rule、fuel/surcharge/GST 规则和 profile rejection 规则。
- 这次 Broers 目录里 Allied/Hunter 的价卡不是现有 PostageCalculator SP 的同一来源，不能覆盖当前 `pc_*` channel，应新增 Broers 版本的 rate card/channel。
- 当前尝试连接 Django/PostgreSQL 查询 `RateCard` / `QuoteChannel` 失败，错误为 `connection timeout expired`。本报告的“已做”主要基于代码、文档、import command、文件哈希和 sheet 内容比对；数据库启用状态待复核。

## 2. 源文件读取结果

顶层源文件：

| 文件 | 内容 |
|---|---|
| `Australia Post Rate Table Update.zip` | Sunyee/EIZ Australia Post N0/Q0/V0 Parcel/Express PDF，加上 UBI Australia Post workbook。 |
| `Broers.zip` | Allied BROGRO、Hunter、Direct Freight Express rate files。 |
| `【PFL Broers价卡】2026 AU eFN rate (Domestic) _ Clean 20260224.xlsx` | OrangeConnex / PF Logistics eFN 2026 rate workbook。 |

展开后的关键结构：

| Carrier / 文件 | 关键 sheet / 表 | 结构摘要 |
|---|---|---|
| Allied `BROGRO - RATE CARD..xlsx` | `Road Rates` | 1,726 行，字段：`SERVICE`, `FROM ZONE`, `TO ZONE`, `BASIC`, `PER KG`, `MIN CHARGE`，175 个 from zone / 175 个 to zone。 |
| Allied | `Pallet Rates` | 3,331 行，含 `PT (Standard Pallet)`、`PT2 (Oversized Pallet)`。当前系统未采集 pallet count，不建议直接自动报价。 |
| Allied | `On Forward` | 16,940 行，含 postcode/suburb/state、on-forward basic/per kg；不是 primary zone mapping。 |
| Allied | `Additional Services` | 55 行，含 surcharge/code/range/rate/UOM。 |
| Allied | `Surcharge Summary.pdf` | 只能抽到标题 `AOE SURCHARGES effective 23.10.2023`，正文需要人工/OCR 复核。 |
| Hunter `Rate Card - Hunter Road Freight - BroersGroupPtyLtd - 20240920.xlsx` | `Sheet1` | 124 行，from zone 只有 `MELBOURNE` / `SYDNEY`，to zone 62 个，字段含 `Minimum Charge`, `Basic`, `Per KG`。`Item Rate` 相关列为空。 |
| Hunter `Zone List - ZoneListCommon_01082025000320.xlsx` | `Sheet1` | 16,549 行，字段含 `Suburb`, `State`, `Postcode`, `CustomerZone(Pricing Zones (Rate Region))`，可做 destination zone lookup。 |
| Hunter PDF | `HX - CHARGE FEE GUIDE`, `Surcharge` | 可抽取 charge weight、cubic factor 250、oversize factor 333、residential、length、uplift 等规则。 |
| Direct Freight Express | `Rate EX Mel`, `Ex SYD`, `Surcharge ` | 与既有 DFE proposal 业务 sheet 逐格一致；支持 MEL/SYD origin、KILO/PALLET、destination surcharge。 |
| DFE zone file | `Zone List - postcodes.csv` | 文件扩展名是 `.csv`，内容为 Excel workbook；与既有 DFE zone 文件哈希一致。 |
| UBI Australia Post | `Ex-SYD/MEL/PER/BNE Regular/Express` | 固定重量段 rate table，sheet 顶部标注 `Parcel Post (excl. GST)`，另有 9,091 行 postcode -> zone mapping。 |
| Sunyee/EIZ Australia Post PDF | N0/Q0/V0 Parcel/Express | PDF 明确写有 `GST inclusive`，包含 Local/Metro/Remote rate bands 和 basic/subsequent/per kg。 |
| OrangeConnex / PFL | `PFL SYD`, `PFL MEL`, `PFL Zone` | 与既有 `2026 AU eFN rate.xlsx` 哈希完全一致；固定重量段、MEL/SYD origin、2,766 行 zone mapping。 |

## 3. 与现有系统能力的匹配

系统当前具备的相关模型和能力：

| 模型 / 能力 | 适配情况 |
|---|---|
| `Agent` | 已存在，可表示 Broers、EIZ、UBI、SHIPPIT、OrangeConnex 等 quote/rate owner。 |
| `QuoteChannel.agent` | 已存在，同一 carrier 可以在不同 agent 下提供不同 quote source / calculator。 |
| `RateCard` | 已支持 `effective_from`, `effective_to`, `status`, `is_active`, `priority`, `tax_mode`, `gst_rate`, `cubic_factor`。 |
| `RateZone` | 支持 postcode/suburb/state 到 zone 的映射，也可用 `raw_payload` 保存 on-forward、drop/sort code 等。 |
| `RateRule` | 支持 linehaul/per kg/min charge，也可用 `raw_payload` 保存 weight band、pallet、special rows。 |
| `SurchargeRule` | 支持 fixed fee、ratio、condition JSON、weight/length/border/cubic/always match。 |
| `QuoteChargeLine` / breakdown / trace | 已支持显示 base、surcharge、fuel、GST、rate card、zone、not available reason。 |

系统当前已有计算器：

| Calculator | 当前来源 | 对 Broers package 的结论 |
|---|---|---|
| `HunterMel2023Calculator`, `HunterSydney2025Calculator` | PostageCalculator SP | 逻辑方向可复用，但 Broers 20240920 rate card 不是同一份数据，需要新增 Broers Hunter rate card/importer。 |
| `AlliedGro2023MelbourneCalculator`, `AlliedGro2023SydneyCalculator`, `AlliedB2C2025*` | PostageCalculator SP | 同名 carrier 已有逻辑，但 Broers BROGRO 缺 primary zone mapping，不能直接完整接入。 |
| `DirectFreightExpress2025Calculator` | DFE Feb 2025 proposal | 与本包 DFE 资料匹配。 |
| `OrangeConnexEfn2026Calculator` | OrangeConnex/PFL 2026 workbook | 与本包 PFL 文件完全匹配。 |
| Australia Post calculator | 尚无专用 calculator | UBI/Sunyee Australia Post 可以新增实现。 |
| API quote calculators for EIZ/SHIPPIT | 尚无真实 API connector | 需要接口文档、认证、账号、request/response 样例。 |

## 4. 逐项评估

### 1. Broers / Allied Express / 价卡计费 / Broers

当前状态：系统已有 Allied Express 的 PostageCalculator SP 计算器，但 Broers BROGRO 价卡还不能视为已完成。

可以导入：

- `Road Rates` 可导入为 Broers Allied GRO rate rules。
- `On Forward` 可导入为 destination on-forward reference。
- `Additional Services` 可导入为 surcharge reference。
- `Pallet Rates`, `Courier KLM`, `Taxi Truck TN/TA` 可先导入为 disabled/reference service，不建议直接参与 manual quote。

主要阻塞：

- 缺 postcode/suburb/state -> primary Allied zone (`N01`, `Vxx` 等) 的明确映射。`On Forward` 只有 on-forward 信息，不能替代 primary zone。
- `Surcharge Summary.pdf` 正文不可文本抽取，需要 OCR 或人工录入确认。
- 现有 Allied calculator 是 legacy PostageCalculator 逻辑，fuel 目前按 ratio 乘 inc-GST subtotal 的方式处理；Broers surcharge/fuel 需要确认是否同样适用。

建议：

1. 要求补充 Allied Broers zone list，或确认可复用当前 Allied SP zone mapping。
2. 对 `Surcharge Summary.pdf` 做 OCR/人工复核，把 surcharge code、threshold、fee、fuel ratio 录入 `SurchargeRule`。
3. 新建 `Broers Allied GRO 2023` rate card/channel，agent = Broers，不覆盖 `pc_allied_*`。
4. Pallet/taxi/courier local service 等到订单输入支持 pallet count、vehicle type、KLM 后再自动计算。

结论：部分可做。缺 zone mapping 前不能算“完整进入系统”。

### 2. Broers / Hunter Express / 价卡计费 / Broers

当前状态：系统已有 Hunter PostageCalculator SP 计算器，但 Broers Hunter 20240920 价卡还未作为独立 rate card 完成。

可以导入：

- Hunter rate workbook：`MELBOURNE` / `SYDNEY` 两个 origin，62 个 destination zone，`Minimum Charge + Basic + Per KG` 结构完整。
- Hunter zone list：16,549 行 postcode/suburb/state -> `CustomerZone(Pricing Zones)`，可直接做 destination lookup。
- Hunter charge guide / surcharge PDF：可抽取体积重和 surcharge 逻辑。

与现有 Hunter calculator 的匹配：

- 现有计算器已经支持 chargeable weight = max(dead, cubic)，默认 cubic factor 250。
- PDF 说明 oversize 可使用 cubic factor 333，现有计算器已有类似判断。
- 现有计算器支持 residential、length、uplift、fuel、WA fuel。
- workbook 中 `Item Rate` 相关列为空，实际可按 `basic + per kg * chargeable kg` 并套 minimum charge。

待确认：

- Broers 20240920 的 fuel rate、WA fuel、residential、length/uplift 金额应来自 PDF surcharge schedule 还是另有最新表。
- effective date 建议设为 `2024-09-20`，effective_to 需业务确认。
- 当前 Hunter source name 可能需要显示为 `Hunter Express`，但 carrier 可以复用系统已存在 Hunter carrier，并用 agent/channel 区分 Broers。

结论：可以做，优先级高。建议新增 `import_broers_hunter_rates` 或扩展 importer 支持 Broers source，不覆盖 PostageCalculator Hunter。

### 3. ESO / Direct Freight Express / 价卡计费 / Broers

当前状态：系统已有 DFE importer、calculator、文档。

核对结果：

- 本包 DFE zone file 与既有 `Zone List - postcodes 1.csv` 哈希一致。
- 本包 DFE rate workbook 和既有 source 文件哈希不同，但 `Rate EX Mel`, `Ex SYD`, `Surcharge ` 三个业务 sheet 逐格一致。
- 现有文档定义了 `DFE-EX-MEL-FEB-2025`, `DFE-EX-SYD-FEB-2025` 两个 rate card，calculator 为 `DirectFreightExpress2025Calculator`。

已实现逻辑：

- origin：EX MEL / EX SYD。
- zone lookup：postcode/suburb/state 精确匹配，postcode/state 唯一 fallback。
- chargeable kg：`ceil(max(actual_kg, cubic_kg))`，cubic factor 250。
- base：`basic + per_kg * chargeable_kg`，套 minimum charge。
- destination surcharge：`DFE_DEST`。
- fuel：`FS = 0.196`，配置在 surcharge rule。
- GST：EX GST -> add 10% GST。
- strict profile not-available：单件 > 30kg，最长边 > 120cm，两边 > 70cm。

结论：代码和资料层面已完成。数据库导入状态需要 PostgreSQL 恢复后用 `import_dfe_rates --dry-run` / 查询 `RateCard` 复核。

### 4. Sunyee(EIZ) / Australia Post / API 获取 / EIZ

当前状态：系统没有真实 EIZ Australia Post API connector。

资料情况：

- PDF 包含 N0/Q0/V0 Parcel/Express account rate schedule。
- PDF 标注 `GST inclusive`。
- PDF 里可抽到 Local/Metro/Remote 重量段、basic/subsequent/per kg。

可做方案：

- 如果业务允许用 rate table，不走 API，可新增 Australia Post rate-band calculator，分别按 account/service/origin 建 rate card。
- 如果必须 API 获取，则需要 EIZ/Australia Post API 文档、endpoint、auth、account code、请求字段、响应样例、错误码和调用频率限制。

结论：rate table 方案可以做但尚未做；API 方案缺接口资料，暂不能做完整 live quote。

### 5-9. EIZ / Allied Express, Aramex, Hunter Express, TNT / API 获取 / EIZ

当前状态：系统已有 `Agent` 维度，但没有 EIZ API quote connector。

当前目录没有这些 carrier 的 EIZ API 文档或价卡。

可以做的部分：

- 建立 agent = EIZ，carrier service/channel/API credential 的配置位置。
- 导入或展示历史 LSP API quote snapshot，作为历史报价证据。

不能完成的部分：

- 不能做实时 API quote。
- 不能验证 EIZ 返回价格是否正确。
- 不能生成真实 request/response parser。

需要资料：

- EIZ API endpoint、auth、账号、service code、request/response example、error codes。
- 每个 carrier 在 EIZ 下的 service mapping 和 tax/fuel/surcharge 表示方式。

结论：暂不能做进系统为可用报价，只能做配置壳和历史 quote 查看。

### 10. UBI / Aramex / 价卡计费 / Broers

当前目录未发现 Aramex rate card。

结论：不能从本包导入。需要 UBI Aramex rate workbook 或从 UBI invoice/rate reference 中定位正式价卡来源。

### 11-12. UBI / TGE(TOLL IPEC), TGE(TOLL B2C Priority) / 价卡计费 / Broers

当前目录未发现 TGE/Toll rate card。

用户备注里提到 “UBI 每个快递账单都含对应价卡，可在账单中查阅”，但这不在本次 Broers folder 内。

结论：不能从本包导入。需要 UBI Toll/IPEC/B2C Priority 的正式 rate card 或 invoice-derived rate reference，再判断是否能转成 `RateCard/RateRule/SurchargeRule`。

### 13. UBI / Australia Post / 价卡计费 / Broers

当前状态：系统没有 Australia Post table-rate calculator，但这份 workbook 足够支撑实现。

资料情况：

- `Ex-SYD Regular`, `Ex-MEL Regular`, `Ex-PER Regular`, `Ex-BNE Regular`
- `Ex-SYD Express`, `Ex-MEL Express`, `Ex-PER Express`, `Ex-BNE Express`
- `Charge Zone for Mapping`：9,091 行 postcode/state -> zone。
- sheet 顶部标注 `Parcel Post (excl. GST)`，可按 EX GST 处理。

建议实现：

- 新增 `AustraliaPostBandCalculator`。
- 每个 origin + service 建一个 `CarrierService` / `RateCard`：
  - SYD/MEL/PER/BNE x Regular/Express。
- `RateRule` 保存 fixed weight band price，`raw_payload` 保存 band label、max weight。
- 超过 22kg 使用 `Over 22kg Basic + Per Kg #` 逻辑。
- GST 按 `tax_mode = EX_GST` 加 10%。
- profile limit、remote/additional charges 需要从合同/PDF 或业务规则确认。

结论：可以做，当前还没做。优先级中高，因为文件结构完整。

### 14-16. SHIPPIT / Couriers Please, Smart Routing Bulky, Allied Express / API 获取 / SHIPPIT

当前状态：系统没有 Shippit live API connector。

当前目录没有 Shippit API 文档、credential 或 response sample。

结论：暂不能作为实时报价做进系统。可以先建 agent/channel/API credential 配置位，并接入历史 LSP API quote snapshot 展示。

### 17. OrangeConnex / PF Logistics / 价卡计费 / Broers

当前状态：系统已有 OrangeConnex importer、calculator、文档。

核对结果：

- 本包 `【PFL Broers价卡】2026 AU eFN rate...xlsx` 与既有 `C:\Users\KenHu\Downloads\2026 AU eFN rate.xlsx` SHA256 完全一致。
- 现有计算器为 `OrangeConnexEfn2026Calculator`。
- 已支持 `PFL MEL`, `PFL SYD`, `PFL Zone`。

已实现逻辑：

- 每件商品按 unit weight gram 选择固定重量段。
- 按 qty 汇总 article-based price。
- destination zone 先 exact postcode/suburb/state，再 postcode/state 唯一 fallback，找不到时用 `Rest of AU`。
- 严格 not-available：单件 > 25kg，最长边 > 105cm，体积 > 0.088m3。
- 其他事件类费用如 Missing Manifest / RTS 仅作 inactive/reference surcharge。

结论：代码和资料层面已完成。数据库导入状态需要 PostgreSQL 恢复后复核。

## 5. 建议实施顺序

### Phase 1：复核已完成项

1. PostgreSQL 恢复后查询 `RateCard` / `QuoteChannel`：
   - `dfe_ex_mel_2025`
   - `dfe_ex_syd_2025`
   - `orange_efn_mel_2026`
   - `orange_efn_syd_2026`
2. 跑 dry-run：
   - `import_dfe_rates --dry-run`
   - `import_orange_connex_rates --dry-run`
3. 用 2-3 个历史订单验证 breakdown：
   - origin warehouse state 只匹配对应 origin rate card。
   - outer result 只显示 inc GST total。
   - detail 中显示 base / surcharge / fuel / GST。

### Phase 2：新增 Broers Hunter

1. 新增 Broers agent/channel：
   - agent：Broers
   - carrier：Hunter Express / Hunter Road Freight
   - services：Broers Hunter MEL 2024、Broers Hunter SYD 2024
2. 导入 Hunter zone list 到 `RateZone`。
3. 导入 Hunter rate workbook 到 `RateRule`。
4. 从 surcharge PDF 建 `SurchargeRule`：
   - fuel
   - residential
   - length / excess length
   - uplift / oversize
   - WA special fuel if applicable
5. 新增或扩展 calculator，保持与现有 Hunter breakdown/trace 一致。

### Phase 3：新增 UBI Australia Post

1. 新增 Australia Post carrier/service/rate cards。
2. 导入 `Charge Zone for Mapping` 到 `RateZone`。
3. 导入 Regular/Express origin weight bands 到 `RateRule`。
4. 实现 Australia Post weight-band calculator。
5. 确认 fuel、remote、signature、manual manifest、return 等额外费用是否自动计算。

### Phase 4：Allied Broers

1. 先补资料：
   - Broers Allied postcode/suburb/state -> `Nxx/Vxx/...` primary zone mapping。
   - OCR 或人工录入 `Surcharge Summary.pdf`。
2. 再导入：
   - Road linehaul
   - On-forward
   - Additional Services
3. Pallet/taxi/courier KLM 暂存 reference，不参与自动报价。

### Phase 5：API carrier

对 EIZ/SHIPPIT API carrier，不建议先写 mock calculator。

需要先拿到：

- API 文档
- auth 方式
- credential
- endpoint
- service codes
- request/response sample
- error/not available reason
- quote amount 是否含 GST、fuel、surcharge

再实现：

- `ApiCredential.agent`
- API quote calculator
- request/response snapshot
- breakdown normalizer
- retry/timeout/error trace

## 6. 资料缺口清单

| 缺口 | 影响 |
|---|---|
| Allied Broers postcode -> primary zone mapping | 没有它无法按用户输入地址计算 Allied Road Rates。 |
| Allied surcharge PDF 可读表格/OCR | 无法可靠配置 fuel/surcharge/threshold。 |
| EIZ API 文档和 credentials | EIZ 下 Allied/Aramex/Hunter/TNT/Australia Post 不能做实时 API quote。 |
| SHIPPIT API 文档和 credentials | Shippit 下 Couriers Please/Smart Routing Bulky/Allied 不能做实时 API quote。 |
| UBI Aramex/TGE/Toll rate card | 当前无法导入这些 rate-table carrier。 |
| Pallet count / vehicle type / local courier inputs | Allied pallet/taxi/courier KLM 不能自动报价，只能保存参考。 |
| Australia Post surcharge/extra service business rules | 可做 base freight，但 remote/signature/manifest/return 等额外费用需要确认。 |

## 7. 验证状态

已完成：

- 解压并读取 Broers rate folder。
- 读取 Excel workbook sheet structure 和关键样例。
- 抽取可读 PDF 的费用条款关键词。
- 比对 DFE rate workbook 三个业务 sheet 与既有 source 文件，业务内容一致。
- 比对 DFE zone file 与既有 zone file，哈希一致。
- 比对 OrangeConnex/PFL workbook 与既有 source 文件，哈希一致。
- 对照系统现有 calculator/importer/docs/model 能力。

未完成：

- 当前 PostgreSQL 连接超时，无法直接验证本地 DB 内 `RateCard`、`QuoteChannel`、`SurchargeRule` 的实际行数和启用状态。
- 未执行新的导入动作。本报告是 fit assessment，不是导入结果报告。
- Allied surcharge PDF 没有完成 OCR 数字抽取。

## 8. 最终判断列表

| No. | Agent | Carrier | 当前判断 |
|---:|---|---|---|
| 1 | Broers | Allied Express | 部分可做。缺 primary zone mapping 和 OCR surcharge，不能算完整接入。 |
| 2 | Broers | Hunter Express | 可以做，尚未做 Broers 版本。建议优先实施。 |
| 3 | ESO | Direct Freight Express | 代码和资料层面已完成；DB 导入状态待复核。 |
| 4 | Sunyee(EIZ) | Australia Post | Rate table 可做；API quote 缺资料。 |
| 5 | EIZ | Allied Express | API 缺资料，暂不能做实时 quote。 |
| 6 | EIZ | Aramex | API 缺资料，暂不能做实时 quote。 |
| 7 | EIZ | Hunter Express | API 缺资料，暂不能做实时 quote。 |
| 9 | EIZ | TNT | API 缺资料，暂不能做实时 quote。 |
| 10 | UBI | Aramex | 当前目录无价卡，不能导入。 |
| 11 | UBI | TGE / Toll IPEC | 当前目录无价卡，不能导入。 |
| 12 | UBI | TGE / Toll B2C Priority | 当前目录无价卡，不能导入。 |
| 13 | UBI | Australia Post | 可以做，尚未做。需要新增 Australia Post band calculator。 |
| 14 | SHIPPIT | Couriers Please | API 缺资料，暂不能做实时 quote。 |
| 15 | SHIPPIT | Smart Routing Bulky | API 缺资料，暂不能做实时 quote。 |
| 16 | SHIPPIT | Allied Express | API 缺资料，暂不能做实时 quote。 |
| 17 | OrangeConnex | PF Logistics | 代码和资料层面已完成；DB 导入状态待复核。 |

