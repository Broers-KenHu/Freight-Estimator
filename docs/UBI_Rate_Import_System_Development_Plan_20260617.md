# UBI Rate Import Into Freight Intelligence - Development Plan

生成日期: 2026-06-17  
目标: 将 UBI agent 下的可计算费率表正式导入 Freight Intelligence，并实现对应 calculator。  
重要边界: 不做自动 email / 文件夹监听 / 定时自动更新。所有 UBI rate package 由管理员手动上传或通过命令手动导入，和当前其他 carrier rate 的维护方式保持一致。

## 1. 本次开发目标

本次只做三件事:

1. 把 UBI 可计算费率表导入系统标准 rate template。
2. 给 UBI 各渠道建立 calculator，让 Manual Quote / Freight Audit Matrix 可以算出真实价格。
3. 保留导入来源、hash、版本、生效日期和 breakdown trace，方便以后复核。

不做:

- 不做自动扫描 UBI invoice 邮件。
- 不做自动监听文件夹。
- 不做每次 invoice 到达后自动更新 rate card。
- 不自动覆盖当前 active rate card。
- 不把 penalty / oversize / RTS / underticketing 这类账单事件表导成基础 linehaul rate card。
- 当前只把可由尺寸提前判断的 oversize length 加入 `SurchargeRule` 或 calculator surcharge。
- `penalty below 3kg` / `More to Pay` 暂不进入报价计算，先只用于 Invoice Reconciliation 复核。

## 2. UBI 在系统中的建模方式

UBI 是 `Agent`。

UBI 下面的渠道需要分别映射到 carrier/service:

| UBI 渠道 | Agent | Carrier | Service | 用途 |
|---|---|---|---|---|
| eParcel Standard | UBI | Australia Post | UBI_EPARCEL_STANDARD | Australia Post eParcel 标准件 |
| eParcel Express | UBI | Australia Post | UBI_EPARCEL_EXPRESS | Australia Post eParcel express |
| Fastway / Aramex | UBI | Aramex/Fastway | UBI_FASTWAY | Fastway/Aramex road parcel |
| Toll IPEC Road | UBI | TGE Toll IPEC | UBI_TOLL_IPEC_ROAD | Toll IPEC Road Express |
| Toll Priority3 / B2C | UBI | TGE Toll Priority | UBI_TOLL_PRIORITY3_B2C | Toll Priority B2C / Priority3 |

`QuoteChannel` 按 agent + carrier + service + origin/strategy 建立，不因为重复导入同一个 workbook 而新增。

`RateCard` 按版本和生效日期建立。重复导入同样内容时不新增 rate card。

## 3. 导入范围

### 3.1 P1 优先导入

优先导入结构最清晰、最接近现有 `RateRule` 模型的渠道:

1. UBI Toll IPEC Road Express
   - `IPEC Rate 07.07.25`
   - `IPEC Rate 20.04.26`

2. UBI Fastway / Aramex
   - 旧 `Rate`
   - 新 `New Rate`
   - `Fastway Coverage`

原因:

- 两者都是 origin zone + destination zone + weight/rate 结构。
- 可以较快映射到 `RateZone` / `RateRule`。
- 比 eParcel 的多重量段、多附加说明更直接。

### 3.2 P2 导入

1. UBI eParcel Standard
   - `MEL eparcel 2024.3.18`
   - `MEL eparcel 2025.11.01`

2. UBI eParcel Express
   - `MEL express 2024.3.18`
   - `MEL express 2025.11.01`

3. UBI Toll Priority3 / B2C
   - Effective 2025-07-07
   - Effective 2026-04-20
   - 无日期版本只记录为 review，不激活

### 3.3 不导入为基础 linehaul，但需要区分可预估 surcharge

以下目录不进入基础 `RateRule` linehaul:

- `additional_fee`
- `oversize`
- `redelivery`
- `rts`
- `underticketing`

处理方式:

| 目录 | 是否可提前预估 | 系统处理 |
|---|---|---|
| `oversize` | 可以，前提是 SKU/parcel 尺寸准确 | 加入 UBI surcharge 计算，并在 invoice reconciliation 中复核实际收取 |
| `additional_fee` 中的 `Penalty` / `penalty below 3 kg` | 暂不做报价预估 | 只进入 Invoice Reconciliation 复核 |
| `additional_fee` 中的 `More to Pay` | 暂不做报价预估 | 只进入 Invoice Reconciliation 复核 |
| `redelivery` | 通常不能提前知道 | 只进入 invoice reconciliation |
| `rts` | 通常不能提前知道 | 只进入 invoice reconciliation |
| `underticketing` | 事后稽核类 | 只进入 invoice reconciliation，除非能还原稳定触发规则 |

`oversize` 样本中有 `ParcelLength`、`ParcelWidth`、`ParcelHeight`、`LabelCost`、`service_type=Oversize Length Surcharge`，多数单项费用为 ex GST 15。该类费用可以由尺寸阈值预判，应进入报价 breakdown。

`penalty below 3 kg` 样本中有:

- `Weight`
- `Actual Weight`
- `Actual Length`
- `Actual Width`
- `Actual Height`
- `Billed Weight`
- `Charge Code`
- `Fuel Surcharge(ex GST)`
- `Security Management Charge`
- `Additional Charge`
- `service_type=Penalty`

这说明它不是普通 linehaul，而是申报重量/尺寸与 carrier 实测重量/尺寸不一致时产生的补收费。当前阶段先不加入报价计算，避免在没有完整公式时误报价格；只在 invoice reconciliation 中用于解释 actual charge 差异。

## 4. 数据库改动方案

### 4.1 RateCard 增加 agent

建议给 `RateCard` 增加可空 FK:

```text
agent -> Agent, null=True
```

原因:

- 同一个 carrier 在不同 agent 下可能价格不同。
- UBI/Fastway 和 Broers/Fastway 不能只靠 carrier 区分。
- `QuoteChannel.agent` 已存在，RateCard 也应该能表达 rate owner。

查询逻辑:

- QuoteChannel 有 agent 时，只优先匹配同 agent 的 RateCard。
- RateCard.agent 为空时作为 legacy/global card。

### 4.2 RateCard metadata_json 记录导入证据

每个 UBI rate card 的 `metadata_json` 至少记录:

```json
{
  "agent_code": "UBI",
  "ubi_role_key": "ubi_toll_ipec_road_rate",
  "source_package": "ubi_invoices_all 1.zip",
  "source_sheet": "IPEC Rate 20.04.26",
  "source_sheet_hash": "sha256...",
  "source_rows": 86,
  "source_cols": 16,
  "effective_from_source": "sheet_name",
  "imported_by_command": "import_ubi_rate_package"
}
```

### 4.3 最小导入日志模型

因为不做自动更新，导入追踪可以保持轻量。

新增 `RateImportBatch`:

- `id`
- `agent`
- `source_file_name`
- `source_file_hash`
- `import_mode`: `DRY_RUN` / `COMMIT`
- `status`: `SUCCESS` / `FAILED`
- `summary_json`
- `created_at`
- `created_by`

新增 `RateImportSheetSnapshot`:

- `batch`
- `agent`
- `role_key`
- `sheet_name`
- `normalized_hash`
- `effective_from_candidate`
- `decision`: `UNCHANGED` / `IMPORT` / `REVIEW` / `IGNORED`
- `rate_card`
- `preview_json`

不需要做完整文件监听队列，也不需要做自动任务表。

## 5. 后端导入命令

### 5.1 Analyze 命令

命令:

```powershell
python backend\manage.py analyze_ubi_rate_package --zip "C:\Users\KenHu\Downloads\ubi_invoices_all 1.zip"
```

作用:

- 只分析，不写 Pricing 数据。
- 读取 zip 内 workbook。
- 识别可导入渠道。
- 计算 normalized hash。
- 输出哪些是:
  - 已存在
  - 新版本
  - 缺少生效日期，需要人工确认
  - adjustment-only，忽略导入

输出:

- 控制台 summary。
- JSON report。
- CSV report。

### 5.2 Import 命令

命令:

```powershell
python backend\manage.py import_ubi_rate_package --zip "C:\Users\KenHu\Downloads\ubi_invoices_all 1.zip" --commit
```

可选参数:

```powershell
--dry-run
--only toll_ipec
--only fastway
--only eparcel
--only priority3
--effective-from ubi_fastway_new=2025-08-01
--activate
--deactivate-previous
```

规则:

- 默认 `--dry-run`。
- 没有 `--commit` 不写入 RateCard/RateRule。
- 没有可信 effective date 的版本不激活。
- 相同 hash 不重复导入。
- `--activate` 才把导入卡设为 ACTIVE。
- `--deactivate-previous` 才自动关闭同 role key 的旧版本。

这样可以避免误操作，也符合当前系统其他 rate 的维护方式。

## 6. RateCard / Rule 导入映射

### 6.1 Toll IPEC Road

RateCard:

- `UBI-TOLL-IPEC-ROAD-2025-07-07`
- `UBI-TOLL-IPEC-ROAD-2026-04-20`

RateRule:

- `from_zone` = `sce_zone`，例如 `SYD1` / `MEL1`
- `to_zone` = `dest_zone`
- `minimum_charge` = `Minimum Charge`
- `basic_charge` = `BasicChargeAmount`
- `per_kg` = `FreightChargeAmount`
- `cubic_factor` = `Cubic Conversion`
- `raw_payload.KG Included In Basic` 保留

Calculator:

- chargeable kg = `ceil(max(actual_kg, cubic_kg))`
- base = `max(minimum_charge, basic_charge + per_kg * chargeable_kg)`
- GST 在系统统一计算
- fuel 走 `SurchargeRule`，不写死

### 6.2 Fastway / Aramex

RateCard:

- `UBI-FASTWAY-MEL-LEGACY`
- `UBI-FASTWAY-MEL-SYD-PER-2025-08-01`

RateZone:

- 来源 `Fastway Coverage`
- `postcode`
- `suburb`
- `state`
- `dest_zone` = RF

RateRule:

- `from_zone` = origin，例如 `MEL` / `SYD` / `PER`
- `to_zone` = RF destination
- fixed weight break 进入 `raw_payload.weight_breaks`
- `per_kg` 或 `raw_payload.per_500g_after_5_5kg` 保存超过 5.5kg 的加收

Calculator:

- destination 通过 coverage 找 RF。
- 0.3kg 到 5.5kg 用固定 break。
- 超 5.5kg 按每 500g 加收。
- fuel 走 `SurchargeRule`。

### 6.3 eParcel Standard / Express

RateCard:

- `UBI-EPARCEL-STANDARD-MEL-2024-03-18`
- `UBI-EPARCEL-STANDARD-MEL-2025-11-01`
- `UBI-EPARCEL-EXPRESS-MEL-2024-03-18`
- `UBI-EPARCEL-EXPRESS-MEL-2025-11-01`

RateZone:

- 来源 `zone mapping`
- `postcode`
- `dest_zone` = UBI Zone

RateRule:

- 每个 destination zone 保存 weight band matrix。
- 固定价 band 放在 `raw_payload.weight_breaks`。

Calculator:

- postcode -> UBI zone。
- actual/cubic 取计费重量。
- 选择对应 weight band。
- 超出最高 band 的处理必须按 workbook 说明实现，未确认前超过范围返回 not available。

### 6.4 Toll Priority3 / B2C

RateCard:

- `UBI-TOLL-PRIORITY3-B2C-2025-07-07`
- `UBI-TOLL-PRIORITY3-B2C-2026-04-20`
- 无日期版本只建 DRAFT/REVIEW，不参与报价

RateZone:

- 来源 `Priority Zone Finder`
- `postcode`
- `dest_zone` = PriorityZone

RateRule:

- `from_zone` = `From`
- `to_zone` = `To`
- `raw_payload.Org`
- `minimum_charge`
- `basic_charge`
- `per_kg`
- `raw_payload.weight_breaks`

Calculator:

- postcode -> PriorityZone。
- origin + destination zone 找 rule。
- 按 min/basic/kilo thereafter/weight breaks 计算。

### 6.5 UBI 可预估 surcharge

#### Oversize Length Surcharge

来源:

- `ubi_invoices_all/oversize`

导入方式:

- 不建基础 `RateCard`。
- 建 `SurchargeRule`，绑定 UBI agent + 对应 carrier/service/rate card。
- `raw_payload` 保存来源样本和触发字段。

建议规则:

- 如果最长边超过 UBI/Fastway 当前合同阈值，则收取 oversize length surcharge。
- 从样本看触发长度最小约为 106 cm，多数收费为 ex GST 15；正式导入前需要把阈值和金额作为可配置项，不硬编码。
- 若未来发现不同 service/origin/period 金额不同，用 `effective_from/effective_to` 和 `rate_card` 绑定。

Breakdown:

```text
Oversize length surcharge: $15.00
```

Trace:

- longest_side_cm
- threshold_cm
- surcharge_rule_id
- source_folder=oversize
- source_service_type=Oversize Length Surcharge

#### Penalty Below 3kg / More To Pay

来源:

- `ubi_invoices_all/additional_fee`

当前决定:

- 暂不进入报价计算。
- 暂不在 Manual Quote / Freight Audit Matrix 中加入 predicted penalty。
- 保留在 Invoice Reconciliation / surcharge audit 中，用于解释实际账单差异。

后续如果要启用，需要先确认:

- carrier 处罚公式。
- label 申报重量的系统来源。
- billed weight / actual weight / volumetric weight 的取值优先级。
- fuel/security/additional charge 的拆分方式。

## 7. Calculator 文件规划

新增:

```text
backend\freight\calculators\ubi_toll_ipec.py
backend\freight\calculators\ubi_fastway.py
backend\freight\calculators\ubi_eparcel.py
backend\freight\calculators\ubi_toll_priority3.py
```

注册到:

```text
backend\freight\calculators\registry.py
```

calculator key:

```text
ubi_toll_ipec
ubi_fastway
ubi_eparcel
ubi_toll_priority3
```

每个 calculator 必须输出:

- availability
- not_available_reason
- base
- surcharge
- fuel
- gst
- total_inc_gst
- chargeable_kg
- destination zone
- rate_card
- source rule id
- breakdown lines

## 8. 前端配置和展示

### 8.1 Pricing 页面

Rate Card 列表需要能看到:

- Agent
- Carrier
- Service
- Version
- Effective from
- Effective to
- Status
- Source sheet hash
- Imported at

### 8.2 Quote Channel 页面

新增 UBI quote channels:

- UBI Toll IPEC Road
- UBI Fastway
- UBI eParcel Standard
- UBI eParcel Express
- UBI Toll Priority3

显示上要明确:

- Agent = UBI
- Carrier = 真实承运商
- Service = UBI 下的服务

### 8.3 Manual Quote / Audit Matrix

结果排序继续沿用当前逻辑:

- available 在前
- inc GST total 从低到高
- 最低价高亮

Breakdown 需要显示:

- Agent
- Carrier
- Service
- RateCard version
- Base
- Fuel
- Surcharge
- GST
- Final inc GST

## 9. 测试计划

### 9.1 P1 单元测试

- Toll IPEC 2025 样本计算。
- Toll IPEC 2026 样本计算。
- Fastway old Rate 样本计算。
- Fastway New Rate 样本计算。
- Oversize Length Surcharge 尺寸触发测试。
- 相同 hash 重复导入不新增 RateCard。
- 无 `--commit` 时不写入 Pricing。
- adjustment-only sheet 不生成 RateCard。

### 9.2 P2 集成测试

- 导入 UBI Toll IPEC 后，Manual Quote 能返回 UBI Toll 价格。
- 导入 UBI Fastway 后，Manual Quote 能返回 UBI Fastway 价格。
- Freight Audit Matrix 可以按历史订单跑 UBI channels。
- quote date 在不同版本区间时选择正确 RateCard。
- warehouse origin 只匹配对应 origin rate。

### 9.3 P3 UI/E2E

- Pricing 列表能搜索 UBI / carrier / version。
- QuoteChannel 显示 Agent。
- Breakdown 中能看到 UBI RateCard 和费用来源。
- Oversize 能在 breakdown 中解释触发原因。

## 10. 推荐开发顺序

### 第一阶段: 最小可用导入

1. 给 `RateCard` 增加 `agent` 字段。
2. 新增轻量导入日志模型。
3. 新增 UBI parser 基础框架。
4. 实现 `analyze_ubi_rate_package`。
5. 实现 `import_ubi_rate_package --dry-run/--commit`。

### 第二阶段: P1 可计算渠道

1. 导入 Toll IPEC Road。
2. 实现 `ubi_toll_ipec` calculator。
3. 导入 Fastway。
4. 实现 `ubi_fastway` calculator。
5. 添加单元测试和 Manual Quote 验证。

### 第三阶段: P2 可计算渠道

1. 导入 eParcel Standard / Express。
2. 实现 `ubi_eparcel` calculator。
3. 导入 Toll Priority3。
4. 实现 `ubi_toll_priority3` calculator。
5. 添加 Audit Matrix 验证。

### 第四阶段: 前端完善

1. Pricing 列表显示 Agent。
2. QuoteChannel 列表显示 Agent。
3. Breakdown 显示 UBI agent/rate card/source hash。
4. 可选增加手动上传分析页面，但不是必须。

## 11. 验收标准

本功能完成后应满足:

- UBI 作为 Agent 可在 Master Data 中看到。
- UBI 下的 Toll IPEC / Fastway / eParcel / Priority3 可作为 QuoteChannel 启用。
- 手动导入同一份 zip 不重复创建相同 rate card。
- 不同版本 rate card 按 effective date 存在系统里。
- Manual Quote 可以算出 UBI 渠道真实结果。
- Freight Audit Matrix 可以跑 UBI 渠道历史订单估算。
- Breakdown 能解释每个 UBI 报价来自哪个 rate card、哪个 zone、哪个 weight/rule。
- oversize 进入 surcharge 计算，而不是基础 linehaul。
- penalty below 3kg / More to Pay 暂不进入报价计算，只用于账单复核。
- redelivery / RTS / underticketing 没有被错误导入成基础报价。
