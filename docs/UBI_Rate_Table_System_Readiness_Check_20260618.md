# UBI Rate Table System Readiness Check

生成日期: 2026-06-18  
来源包: `C:\Users\KenHu\Downloads\ubi_invoices_all 1.zip`  
相关分析:

- `outputs\ubi_rate_analysis\ubi_rate_analysis_summary.json`
- `outputs\ubi_rate_analysis\ubi_rate_sheet_versions.csv`
- `docs\UBI_Rate_Import_System_Development_Plan_20260617.md`

## 1. 当前系统状态

数据库检查结果:

| 对象 | 状态 |
|---|---|
| `Agent=UBI` | 已存在，code 为 `ubi` |
| UBI RateCard | 暂无 |
| UBI QuoteChannel | 暂无 |

结论:

- UBI 文件已经完成分析，但尚未正式导入到 Pricing。
- 当前系统还不能直接计算 UBI 报价。
- 需要按开发方案新增 parser、import command、calculator 和 quote channels。

## 2. 总体结论

UBI 费率表整体可以进入系统，但不是所有渠道都已经完全满足“直接导入即可报价”的要求。

| 渠道 | 是否满足系统需求 | 主要原因 |
|---|---|---|
| UBI Fastway / Aramex | 基本满足 | 有 coverage mapping、rate matrix、origin、weight break；需要确认生效日期和 fuel/oversize 配置 |
| UBI eParcel Standard / Express | 基本满足 | 有 postcode zone mapping、rate matrix、MLID；但当前只看到 MEL lodgement/origin |
| UBI Toll Priority3 / B2C | 基本满足 | 有 postcode zone finder、MEL/SYD origin、rate rows、effective date；无日期版本不能启用 |
| UBI Toll IPEC Road | 部分满足 | 价格表完整，但缺少完整 postcode/suburb -> Toll dest zone 主映射 |
| UBI Oversize | 可作为 surcharge 加入 | 有尺寸字段和费用字段，可按最长边/尺寸规则预估 |
| UBI penalty below 3kg / More to Pay | 暂不做报价计算 | 依赖申报重量和 carrier 实测差异，公式未确认，先用于账单复核 |

## 3. 系统导入需求核对

### 3.1 Master Data

| 需求 | 状态 | 说明 |
|---|---|---|
| Agent | 满足 | `Agent=UBI` 已存在 |
| Carrier | 需要创建/匹配 | Australia Post、Aramex/Fastway、TGE Toll IPEC、TGE Toll Priority 需要按现有 carrier 规范匹配 |
| CarrierService | 需要创建 | 需要创建 `UBI_EPARCEL_STANDARD`、`UBI_EPARCEL_EXPRESS`、`UBI_FASTWAY`、`UBI_TOLL_IPEC_ROAD`、`UBI_TOLL_PRIORITY3_B2C` |
| QuoteChannel.agent | 满足 | 系统已有字段 |
| RateCard.agent | 当前缺失 | 建议新增字段，否则只能暂放 `metadata_json.agent_code` |

### 3.2 RateCard 生命周期

| 需求 | 状态 | 说明 |
|---|---|---|
| version | 满足 | 可用 sheet 名和 effective date 生成 |
| effective_from | 部分满足 | eParcel、Toll IPEC、Priority3 有明确日期；Fastway New Rate 需要人工确认 2025-08-01；旧 Fastway 需要人工确认 |
| effective_to | 满足 | 可由新版本导入时手动关闭旧版本 |
| status/is_active | 满足 | 现有模型支持 |
| priority | 满足 | 现有模型支持 |
| source hash | 满足 | 可放入 `metadata_json.source_sheet_hash` |

## 4. 各渠道明细

### 4.1 UBI Fastway / Aramex

检查结果:

| 项目 | 结果 |
|---|---|
| Rate matrix | 有 |
| 旧 `Rate` origin | MEL，29 个 destination |
| `New Rate` origin | SYD / PER / MEL，各 29 个 destination |
| Coverage mapping | 4,860 个 postcode/suburb rows |
| Coverage states | ACT、NSW、QLD、SA、TAS、VIC、WA |
| Destination zones | 29 个 RF zone |
| Weight break | 0.3kg 到 5.5kg，超过后 `Per 500g +` |

系统适配:

- 可以导入 `RateZone`，postcode/suburb/state -> RF。
- 可以导入 `RateRule`，origin + RF + weight break。
- 需要专用 calculator，因为它不是简单 `basic + per_kg * chargeable_kg`，而是 fixed break + per 500g thereafter。
- `New Rate` 支持多 origin，适合系统按 warehouse state/origin 过滤。

缺口:

- Fastway 旧 `Rate` 没有明确 effective date。
- `New Rate` 没有直接写在 sheet 名中；可根据首个带 `New Rate` 的 billing closed time `2025-08-01` 作为候选，但需要人工确认。
- fuel rate 需要从合同或账单确认后配置成 `SurchargeRule`。
- NT 不在 coverage states 中，报价时应返回 not available，除非后续拿到覆盖表。

结论:

- 满足导入系统需求，P1 可优先做。

### 4.2 UBI eParcel Standard / Express

检查结果:

| 项目 | 结果 |
|---|---|
| Standard versions | `MEL eparcel 2024.3.18`、`MEL eparcel 2025.11.01` |
| Express versions | `MEL express 2024.3.18`、`MEL express 2025.11.01` |
| Zone mapping | 9,092 postcode rows |
| UBI zones | 38 个 |
| MLID mapping | 有，23 行新版、16 行旧版 |
| Weight bands | 有 |
| Over 22kg | 旧表有 Basic / Per Kg；新版也有超重/附加说明结构 |

系统适配:

- postcode -> UBI zone 可导入 `RateZone`。
- weight band matrix 可进入 `RateRule.raw_payload.weight_breaks`。
- 需要专用 calculator，因为它是 zone + weight band 固定价，不是现有通用 table rate。

缺口:

- 当前 rate sheet 都是 MEL lodgement/origin，没有看到 SYD lodgement/origin 的 eParcel rate。
- Zone mapping 只有 postcode，没有 suburb/state 精确粒度；如果 postcode 唯一映射 zone，可以接受。
- Over 22kg 和新版附加说明需要 parser 单测确认。

结论:

- 满足 MEL origin 的导入系统需求。
- 如果 NSW/SYD 仓库也要走 UBI eParcel，需要额外确认是否使用同一 MEL lodgement rate，还是缺少 SYD rate sheet。

### 4.3 UBI Toll IPEC Road

检查结果:

| 项目 | 结果 |
|---|---|
| Versions | `IPEC Rate 07.07.25`、`IPEC Rate 20.04.26` |
| Rate rows | 每版 84 条 |
| Origins | SYD1 42 条、MEL1 42 条 |
| Destination zones | 42 个 |
| Cubic conversion | 250 |
| Price fields | Minimum Charge、BasicChargeAmount、FreightChargeAmount、KG Included In Basic |

系统适配:

- 价格结构非常适合导入 `RateRule`。
- calculator 可以按 `max(minimum, basic + per_kg * chargeable_kg)` 实现。
- origin 可以按 warehouse state 匹配 MEL/SYD。

缺口:

- 缺少完整 postcode/suburb -> Toll `To Zone` 主映射。
- Billing sheet 中有 `To Zone`，可抽取到 2,078 个历史出现过的 postcode，发现 36 个 postcode 映射冲突。这只能用于复核或临时补洞，不适合作为完整报价 zone master。
- 已复核之前提供的 `lsp_carrier_rate_reference_2026-06-05/csv/lsp_carrier_zone.csv`：该文件包含 Hunter Road Freight、UBI AUPOST、PF Logistics 等 zone/rate 数据，但没有 Toll/IPEC/TGE 的 carrier zone rows。
- 已复核之前提供的 `lsp_quote_response_location_analysis_2026-06-05`：只确认 LSP API quote response 中存在 `predict_carrier_code=toll` 的历史报价记录，不包含 postcode/suburb -> Toll `To Zone` 的主数据表。
- 已复核当前系统库：`RateCard/RateZone` 中没有 Toll/IPEC/TGE/Priority 正式报价 mapping；invoice 侧只有 UBI Toll/Toll P3 的账单来源和历史明细，不是报价用 zone master。
- 已复核 `data_raw.lsp` 原始库：`lsp_carrier_rate` 有 `AU.TGE.PRIORITY.PRO` 的 MEL/SYD rate rows，但 `lsp_carrier_zone` 对 Toll/IPEC/TGE 为 0 行，`lsp_platform_zone` 为空表。
- 不建议直接借用 Toll Priority3 的 `Priority Zone Finder` 给 IPEC：Priority zone finder 有 53 个 zone，IPEC 有 42 个 dest zone，两者只重合 16 个 zone，`NSW1/QLD1/VIC1` 等 Priority 分区与 `CNSW/SEQLD/IVIC` 等 IPEC 分区不是同一套命名。
- 2026-06-18 补充网站复核：Maropost/Neto 公开 IPEC setup 文档链接了 `TollIPEC-ShippingZones.csv`。该 CSV 有 16,361 行、2,876 个 postcode、14,826 个 suburb；把 `Zone Code` 前缀规范化后，正好覆盖 UBI IPEC rate table 的 42 个 dest zone。与 UBI Toll IPEC billing 70,813 行比对，70,422 行匹配，覆盖率约 99.86%，匹配率约 99.45%。建议作为 DRAFT mapping 导入，并把 UBI billing mismatch 做 override/review。
- 要正式用于 Manual Quote，可以先使用 Maropost/Neto CSV 作为 DRAFT zone finder；生产启用前建议让业务确认来源可接受，或从 LSP/UBI/TeamGE 获得账号级正式 mapping，并保留 UBI billing mismatch override。

结论:

- 费率价格表满足系统需求。
- 地址到 zone 的 mapping 不完整，因此不能直接全面上线报价。
- 可以先用于 invoice/audit 中已知 `To Zone` 的订单复算，或先上线为受限渠道。

### 4.4 UBI Toll Priority3 / B2C

检查结果:

| 项目 | 结果 |
|---|---|
| 明确版本 | Effective 2025-07-07、Effective 2026-04-20 |
| 无日期版本 | 1 个 hash，需 review |
| Zone finder | 6,400 postcode rows |
| Origins | MEL 51 条、SYD 51 条 |
| Destination zones | 51 个 |
| Rate fields | Min Charge、Basic、Kilo Rate Thereafter、Kilos Included、weight breaks |

系统适配:

- postcode -> PriorityZone 可导入 `RateZone`。
- MEL/SYD origin 满足 warehouse state 过滤。
- 需要专用 calculator 处理 Priority3 的 min/basic/kilo thereafter/weight break。

缺口:

- 无日期版本不能激活。
- 需要确认 cubic/chargeable weight 规则。

结论:

- 明确日期的两个版本满足导入系统需求。
- 无日期版本只保存为 DRAFT/REVIEW。

## 5. Oversize 是否应进入计算

结论: 应进入计算。

证据:

- `oversize` 表中有 `ParcelLength`、`ParcelWidth`、`ParcelHeight`、`LabelCost`、`service_type=Oversize Length Surcharge`。
- 样本中多数 `LabelCost` 为 ex GST 15。
- 触发样本的长度集中在 100cm 以上，最长边可作为主要判断条件。

建议:

- 不导入成基础 `RateRule`。
- 导入成 `SurchargeRule` 或 calculator 内部 surcharge config。
- 条件先做成可配置，不硬编码:
  - `longest_side_cm >= threshold`
  - `fee_amount = 15`
  - 可按 service/rate_card/effective date 区分

Breakdown 示例:

```text
Oversize length surcharge: $15.00
```

Trace 必须记录:

- longest_side_cm
- threshold_cm
- surcharge_rule_id
- source folder = `oversize`
- source service_type = `Oversize Length Surcharge`

## 6. Penalty Below 3kg 是否进入计算

结论: 当前不进入计算。

原因:

- 它依赖 declared weight、actual weight、billed weight、actual dimensions、charge code、fuel/security/additional charge 等组合。
- 样本表明它更像 carrier 事后复测/补收费，不是稳定的基础报价规则。
- 在公式未确认前加入报价会制造误差。

处理方式:

- 保留为 Invoice Reconciliation evidence。
- 不进入 Manual Quote。
- 不进入 Freight Audit Matrix system estimate。
- 不在 breakdown 显示 predicted penalty。

## 7. 最终判断

UBI 费率表满足系统导入的主体需求，但需要按渠道分阶段处理。

可以优先导入:

1. Fastway / Aramex
2. Toll Priority3 / B2C
3. eParcel Standard / Express for MEL origin

谨慎导入:

1. Toll IPEC Road
   - 价格表可以导入。
   - 但完整 zone mapping 缺失，需要补 zone finder 后才能全面报价。

可以加入计算的 surcharge:

1. Oversize Length Surcharge

暂不加入计算:

1. Penalty below 3kg
2. More to Pay
3. Redelivery
4. RTS
5. Underticketing

## 8. 建议下一步

1. 先实现 UBI import parser 的 dry-run，输出每个渠道的 row counts、hash、effective date、zone mapping 覆盖率。
2. P1 导入 Fastway + oversize surcharge，因为它的 coverage 和 rate matrix 最完整。
3. P2 导入 Toll Priority3 和 eParcel。
4. Toll IPEC 先导入价格表为 DRAFT，等拿到完整 postcode zone mapping 后再启用报价。
5. 所有 UBI channel 的 outer result 只显示 inc GST total，breakdown 显示 base/fuel/oversize/GST。
