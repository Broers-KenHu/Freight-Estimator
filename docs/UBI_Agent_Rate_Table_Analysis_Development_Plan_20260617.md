# UBI Agent Rate Table Analysis And Development Plan

生成日期: 2026-06-17  
来源文件: `C:\Users\KenHu\Downloads\ubi_invoices_all 1.zip`  
分析输出:

- `outputs\ubi_rate_analysis\ubi_rate_analysis_summary.json`
- `outputs\ubi_rate_analysis\ubi_rate_sheet_inventory.json`
- `outputs\ubi_rate_analysis\ubi_rate_sheet_versions.csv`

## 1. 业务结论

UBI 应该建模为 `Agent`，不是单一 carrier。UBI 下面有多个渠道和承运服务，同一个 invoice workbook 里经常同时包含实际账单明细和当期费率表。

这批压缩包一共分析了 267 个 Excel workbook，没有解析失败。按目录分布如下:

| 目录 | 文件数 | 系统处理定位 |
|---|---:|---|
| `eparcel` | 47 | 可进入报价引擎，含 Australia Post eParcel / Express rate card、zone mapping、MLID origin mapping |
| `fastway` | 51 | 可进入报价引擎，含 Fastway/Aramex rate matrix、coverage mapping |
| `toll` | 42 | 可进入报价引擎，含 Toll IPEC Road Express rate card |
| `toll_priority3` | 36 | 可进入报价引擎，含 Toll Priority B2C/Priority3 rate card、zone finder |
| `additional_fee` | 9 | 不作为基础 linehaul；penalty / More to Pay 当前暂不进报价，只作为账单复核来源 |
| `oversize` | 41 | 不作为基础 linehaul；可按尺寸预判的 oversize length surcharge 应加入报价计算，并继续作为 invoice 复核来源 |
| `redelivery` | 1 | 不作为基础报价表，作为 redelivery surcharge 复核来源 |
| `rts` | 7 | 不作为基础报价表，作为 return-to-sender charge 复核来源 |
| `underticketing` | 33 | 不作为基础报价表，作为 underticketing 差异复核来源 |

核心判断:

- 相同 rate sheet 不能重复创建 RateCard，只应记录一次 snapshot 命中。
- 不同 rate sheet 需要按 `effective_from` 生成新 RateCard 版本。
- UBI invoice 附带的 adjustment/penalty 文件不应混入基础 linehaul rate。当前只把可由尺寸提前判断的 oversize length 加入报价 surcharge；penalty below 3kg / More to Pay 暂不做报价计算。
- `QuoteChannel` 应稳定代表 agent + carrier + service + origin/strategy，费率变化由底层 `RateCard` 版本承载。

## 2. 识别到的 UBI 费率版本

以下版本是用规范化 sheet 内容 hash 比较得出。规范化过程会忽略 workbook 文件名差异，按 sheet 内容判断是否真的发生费率变化。

### 2.1 UBI eParcel

| Role key | 出现次数 | 不同版本数 | 版本判断 |
|---|---:|---:|---|
| `ubi_eparcel_standard_rate` | 75 | 2 | `MEL eparcel 2024.3.18`、`MEL eparcel 2025.11.01` |
| `ubi_eparcel_express_rate` | 75 | 2 | `MEL express 2024.3.18`、`MEL express 2025.11.01` |
| `ubi_eparcel_zone_mapping` | 47 | 1 | zone mapping 相同 |
| `ubi_eparcel_mlid_origin_mapping` | 47 | 2 | MLID origin mapping 发生过变化 |

说明:

- 2025-11-01 后的 workbook 内同时携带旧版和新版 eParcel/Express rate sheet，所以不能只按 workbook 文件数判断版本。
- 新版 eParcel rate table 结构更宽，包含更细的重量段和额外费用说明。
- `zone mapping` 可以作为 postcode 到 UBI zone 的主映射。
- `MLID` 需要作为 lodgement/origin 映射保存，因为 UBI invoice 中会通过 MLID 或 facility 表达发货区域。

建议导入为:

- `Agent`: `UBI`
- `Carrier`: `Australia Post`
- `CarrierService`: `UBI_EPARCEL_STANDARD`, `UBI_EPARCEL_EXPRESS`
- `RateCard`:
  - `UBI-EPARCEL-STANDARD-MEL-2024-03-18`
  - `UBI-EPARCEL-STANDARD-MEL-2025-11-01`
  - `UBI-EPARCEL-EXPRESS-MEL-2024-03-18`
  - `UBI-EPARCEL-EXPRESS-MEL-2025-11-01`

### 2.2 UBI Fastway / Aramex

| Role key | 出现次数 | 不同版本数 | 版本判断 |
|---|---:|---:|---|
| `ubi_fastway_rate_matrix` | 51 | 1 | 原始 `Rate` sheet，旧版主表 |
| `fastway_rate_table` | 45 | 1 | `New Rate` sheet，后续 workbook 开始出现 |
| `ubi_fastway_rate_formula` | 51 | 1 | `Rate for Formula`，适合校验，不建议作为主导入源 |
| `ubi_fastway_coverage_mapping` | 51 | 1 | postcode/suburb 到 RF 区域映射 |

说明:

- 早期 6 个 Fastway workbook 只有 `Rate`。
- 后续 45 个 workbook 同时有 `New Rate` 和 `Rate`。
- `Rate` 旧表只包含 MEL origin 的费率行。
- `New Rate` 包含 SYD、PER、MEL 三个 origin，每个 origin 29 条 destination rate。
- 首个携带 `New Rate` 的样本账单明细 closed time 是 2025-08-01，因此 2025-08-01 可作为候选生效日期，但正式导入时仍应允许人工确认。
- Fastway 账单中出现 fuel charge，rate sheet 标明价格不含 GST 和 fuel levy，因此 fuel 应作为 `SurchargeRule` 或 rate version config 管理。

建议导入为:

- `Agent`: `UBI`
- `Carrier`: `Aramex/Fastway`
- `CarrierService`: `UBI_FASTWAY`
- `RateCard`:
  - `UBI-FASTWAY-MEL-LEGACY`
  - `UBI-FASTWAY-MEL-SYD-PER-2025-08-01`

### 2.3 UBI Toll IPEC Road Express

| Role key | 出现次数 | 不同版本数 | 版本判断 |
|---|---:|---:|---|
| `ubi_toll_ipec_road_rate` | 46 | 2 | `IPEC Rate 07.07.25`、`IPEC Rate 20.04.26` |

结构特征:

- `service_category`: Road Express
- `sce_zone`: SYD1 / MEL1
- `dest_zone`: destination zone
- `Minimum Charge`
- `BasicChargeAmount`
- `FreightChargeAmount`
- `KG Included In Basic`
- `Cubic Conversion`: 250

说明:

- 2025-07-07 版本出现 40 次。
- 2026-04-20 版本出现 6 次。
- 每个版本均包含 SYD1 和 MEL1 origin，各 42 个 destination zone。
- 该表结构和现有 `RateRule` 非常接近，适合优先接入。

建议导入为:

- `Agent`: `UBI`
- `Carrier`: `TGE Toll IPEC`
- `CarrierService`: `UBI_TOLL_IPEC_ROAD`
- `RateCard`:
  - `UBI-TOLL-IPEC-ROAD-2025-07-07`
  - `UBI-TOLL-IPEC-ROAD-2026-04-20`

### 2.4 UBI Toll Priority3 / B2C Priority

| Role key | 出现次数 | 不同版本数 | 版本判断 |
|---|---:|---:|---|
| `ubi_toll_priority3_b2c_rate` | 34 | 3 | Effective 2025-07-07、Effective 2026-04-20、另有 1 个无明确日期版本 |
| `ubi_toll_priority3_zone_mapping` | 36 | 1 | Priority Zone Finder 相同 |

结构特征:

- `Service Name`: TGE Priority B2C 3PL
- `Service Code`: AU.TGE.PRIORITY.PRO
- `Facility`: SYD/MEL
- `From`, `To`, `Org`
- `Min Charge`
- `Basic`
- `Kilo Rate Thereafter`
- `Kilos Included`
- Weight break columns
- `GST Exclusive`

说明:

- 2025-07-07 版本出现 28 次。
- 2026-04-20 版本出现 4 次。
- 另有 2 次出现的 hash 没有明确 effective date，需要人工 review。不能自动覆盖已有版本。
- `Priority Zone Finder` 是 postcode 到 PriorityZone 的主映射。

建议导入为:

- `Agent`: `UBI`
- `Carrier`: `TGE Toll Priority`
- `CarrierService`: `UBI_TOLL_PRIORITY3_B2C`
- `RateCard`:
  - `UBI-TOLL-PRIORITY3-B2C-2025-07-07`
  - `UBI-TOLL-PRIORITY3-B2C-2026-04-20`
  - 无日期版本进入 `DRAFT_REVIEW`，人工确认后再激活

## 3. 不建议作为基础 linehaul 的文件

以下目录不应该生成基础 linehaul `RateCard`:

- `additional_fee`
- `oversize`
- `redelivery`
- `rts`
- `underticketing`

处理原则:

- `oversize` 有 ParcelLength / ParcelWidth / ParcelHeight / LabelCost，可根据 SKU/parcel 尺寸提前判断，应作为 UBI surcharge 加入报价计算。
- `additional_fee` 中的 penalty below 3kg / More to Pay 有 declared weight、actual weight、actual dimensions、billed weight、charge code、fuel/security/additional charge。它不是基础运费，当前暂不进入报价计算，只作为 Invoice Reconciliation 复核数据。
- `redelivery`、`rts`、`underticketing` 多数依赖事后运输事件或 carrier 稽核，只进入 Invoice Reconciliation / surcharge audit。

## 4. 现有系统适配情况

现有 Freight Intelligence 已具备以下基础:

- `Agent` 已是 Master Data，适合表示 UBI。
- `QuoteChannel.agent` 已存在，可表达同一 carrier 在不同 agent 下有不同报价。
- `RateCard` 已有 `effective_from`、`effective_to`、`status`、`is_active`、`priority`、`metadata_json`。
- `QuoteEngine` 会按 quote date 和 origin 匹配 active RateCard。
- `RateZone`、`RateRule`、`SurchargeRule` 足够承载大多数 UBI 表结构。

当前缺口:

- `RateCard` 没有直接的 `agent` FK。可以短期放在 `metadata_json.agent_code`，但长期建议补 `agent` 字段。
- 没有统一的 rate package ingestion / sheet fingerprint 表。
- 没有 UBI eParcel/Fastway/Toll/Priority3 的专用 parser。
- 没有 UI 用来比较“本次 invoice 附带 rate sheet 与系统已有版本是否相同”。
- 没有把 adjustment workbook 作为 surcharge reconciliation source 的独立入口。

## 5. 推荐数据模型

### 5.1 扩展现有模型

`RateCard`

- 新增可选字段: `agent`
- 继续使用:
  - `carrier`
  - `service`
  - `effective_from`
  - `effective_to`
  - `status`
  - `is_active`
  - `priority`
  - `metadata_json`

`metadata_json` 建议记录:

```json
{
  "agent_code": "UBI",
  "role_key": "ubi_toll_ipec_road_rate",
  "source_sheet_hash": "sha256...",
  "source_schema_hash": "sha256...",
  "source_package": "ubi_invoices_all 1.zip",
  "source_sheet": "IPEC Rate 20.04.26",
  "effective_from_source": "sheet_name",
  "requires_manual_review": false
}
```

### 5.2 新增导入追踪模型

建议新增三个模型:

`RateImportBatch`

- `id`
- `agent`
- `source_name`
- `source_path`
- `source_sha256`
- `imported_by`
- `imported_at`
- `mode`: `ANALYZE`, `COMMIT`
- `status`: `PENDING`, `COMPLETED`, `FAILED`
- `summary_json`

`RateImportFile`

- `batch`
- `folder`
- `file_name`
- `file_sha256`
- `workbook_sheet_count`
- `invoice_no_candidate`
- `invoice_date_candidate`
- `raw_payload_json`

`RateImportSheetSnapshot`

- `batch`
- `file`
- `agent`
- `carrier`
- `service`
- `role_key`
- `sheet_name`
- `sheet_role`: `RATE_TABLE`, `ZONE_MAPPING`, `INVOICE_DETAIL`, `ADJUSTMENT_ONLY`
- `normalized_hash`
- `schema_hash`
- `row_count`
- `column_count`
- `effective_from_candidate`
- `effective_from_source`
- `matched_rate_card`
- `decision`: `UNCHANGED`, `NEW_VERSION`, `MANUAL_REVIEW`, `IGNORED`
- `decision_reason`
- `raw_preview_json`

这样可以实现:

- 相同 hash 不更新 RateCard，只记录本次 invoice package 又见到了同一版。
- 不同 hash 生成新版本或进入 review。
- 任何系统报价都能追溯到源文件、sheet 和 hash。

## 6. 导入流程设计

### 6.1 Analyze 模式

命令:

```powershell
python backend\manage.py analyze_ubi_rate_package --zip "C:\Users\KenHu\Downloads\ubi_invoices_all 1.zip"
```

行为:

1. 解压或流式读取 zip。
2. 按目录和 sheet 名识别 UBI channel。
3. 对候选 rate/mapping sheet 做规范化。
4. 计算 `normalized_hash`。
5. 与已有 `RateImportSheetSnapshot` 和 `RateCard.metadata_json.source_sheet_hash` 比较。
6. 输出:
   - unchanged sheets
   - new version candidates
   - missing effective date warnings
   - ignored invoice adjustment sheets

### 6.2 Commit 模式

命令:

```powershell
python backend\manage.py import_ubi_rate_package --zip "C:\Users\KenHu\Downloads\ubi_invoices_all 1.zip"
```

行为:

1. 只处理 analyze 结果中 `NEW_VERSION` 且有可信 `effective_from` 的 rate sheet。
2. 生成或更新 `Agent=UBI`。
3. 生成或匹配 carrier/service。
4. 创建 `RateCard`、`RateZone`、`RateRule`、`SurchargeRule`。
5. 新版本激活后，自动把同 role key 的旧 active RateCard `effective_to` 设为新版本生效日前一天。
6. 无明确日期的版本保持 `DRAFT` 或 `MANUAL_REVIEW`，不参与报价。

### 6.3 UI 设计

新增页面: `Pricing > Rate Imports > UBI`

功能:

- 上传 zip。
- 显示按 channel 分组的分析结果。
- 列出:
  - role key
  - carrier/service
  - sheet name
  - detected effective date
  - hash
  - occurrence count
  - decision
  - matched existing RateCard
- 支持人工选择 effective_from。
- 支持 approve/commit。
- 支持查看 source preview。
- 支持下载分析 CSV。

## 7. 计算逻辑落地方案

### 7.1 eParcel

输入:

- postcode/suburb/state
- weight
- service: standard or express
- origin/lodgement zone

规则:

- 通过 `zone mapping` 找 destination zone。
- 通过 eParcel/Express rate table 找 weight band。
- 费用为固定 weight band price。
- 超过表内最高重量时，需要按 workbook 中 >22kg 或说明规则处理，导入前必须补 parser 单元测试。
- GST 在系统统一加，因为表内标注为 ex GST。
- fuel/administrative fee 不能硬编码，作为 `SurchargeRule` 或 inactive reference rule 维护。

### 7.2 Fastway

输入:

- origin zone
- postcode/suburb/state
- actual/cubic/chargeable weight

规则:

- 通过 `Fastway Coverage` 找 RF/destination。
- 用 origin + RF + chargeable weight 匹配 rate matrix。
- 0.3 到 5.5 kg 用固定 weight break。
- 超过 5.5 kg 按 `Per 500g +` 计算加收。
- `New Rate` 支持 SYD/PER/MEL origin，旧 `Rate` 只支持 MEL。
- fuel 作为 surcharge version config，不写死到 calculator。

### 7.3 Toll IPEC Road Express

输入:

- origin zone: SYD1 / MEL1
- destination zone
- actual/cubic/chargeable weight

规则:

- cubic factor = 250。
- chargeable kg = max(actual kg, cubic kg)。
- linehaul = BasicChargeAmount + FreightChargeAmount * chargeable kg。
- total base = max(Minimum Charge, linehaul)。
- 表内 `KG Included In Basic` 当前样本为 0，但 parser 应保留字段，防止未来版本变化。
- fuel 和 additional billing surcharge 独立配置。

### 7.4 Toll Priority3 / B2C

输入:

- origin
- postcode to PriorityZone
- chargeable weight

规则:

- 通过 `Priority Zone Finder` 找 destination priority zone。
- 通过 From/To/Org 找 rate row。
- 使用 `Min Charge`、`Basic`、`Kilo Rate Thereafter`、`Kilos Included` 和 weight break 计算。
- 无 effective date 的版本不自动启用。

## 8. 测试计划

P1 单元测试:

- 相同 UBI package 重复导入，RateCard 数量不增加。
- 同 role key 新 hash 创建新 RateCard。
- 新 RateCard 生效后，旧 RateCard 自动设置 `effective_to`。
- 无 effective date 的版本进入 `MANUAL_REVIEW`。
- adjustment-only workbook 不生成 RateCard。

P1 计算测试:

- eParcel 2024 和 2025 weight band 样本。
- Fastway old MEL 和 New Rate SYD/PER/MEL 样本。
- Toll IPEC 2025 和 2026 样本。
- Toll Priority3 2025 和 2026 样本。

P2 集成测试:

- Manual Quote 指定 quote date 能选中正确 UBI RateCard。
- Freight Audit Matrix 能显示 UBI agent 下的 channel result。
- Breakdown 显示 base、fuel、surcharge、GST、source rate card。
- Invoice reconciliation 能把 adjustment workbook 的实际 charge 与订单 tracking 关联。

P3 UI/E2E:

- 上传 zip 后能看到 unchanged/new/manual review 分组。
- approve 后 RateCard 出现在 Pricing 列表。
- 再次上传同一 zip 显示 unchanged。

## 9. 推荐实施优先级

P1:

1. 建 RateImportBatch / File / SheetSnapshot。
2. 做 UBI package analyze command。
3. 做 Toll IPEC Road parser 和 calculator。
4. 做 Fastway parser 和 calculator。
5. 做 Rate Import UI 的只读分析页。

P2:

1. 做 eParcel parser 和 calculator。
2. 做 Toll Priority3 parser 和 calculator。
3. 做 commit/approve 流程。
4. 将 adjustment workbook 接入 Invoice Reconciliation charge detail。

P3:

1. 做版本差异可视化 diff。
2. 做 carrier invoice 实收 vs UBI rate card 重算校验。
3. 可选增加手动上传分析页面；不做自动 email/文件夹监听导入。

## 10. 当前最终判断

这批 UBI 附带 rate table 中，能进入 Freight Intelligence 的基础报价表包括:

- UBI eParcel Standard: 2 个版本。
- UBI eParcel Express: 2 个版本。
- UBI Fastway: 2 个版本，旧 `Rate` 和新 `New Rate`。
- UBI Toll IPEC Road Express: 2 个版本。
- UBI Toll Priority3/B2C: 2 个明确版本，另 1 个无日期版本需人工 review。

不应进入基础报价表、但应进入账单复核的包括:

- additional fee
- oversize
- redelivery
- return to sender
- underticketing

开发上应先做“分析/版本判断/人工确认”能力，再做正式导入。UBI 后续如果再提供 rate table，由管理员手动上传或手动运行导入命令，系统只在这次手动导入流程中判断:

- 完全相同: 不更新，只记录已见过。
- 内容不同且日期明确: 创建新 RateCard 版本。
- 内容不同但日期不明确: 进入人工 review，不影响当前报价。
