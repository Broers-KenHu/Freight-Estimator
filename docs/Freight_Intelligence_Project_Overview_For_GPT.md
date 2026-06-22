# Freight Intelligence 项目介绍与字段说明

生成日期：2026-06-11  
项目目录：`C:\Users\KenHu\.vscode\CourieDelivery`  
系统定位：公司内网使用的澳洲运费预估、费率管理、历史运费审计与账单复核系统。  

本文档用于给 GPT 或业务/技术评审人员快速理解当前系统已经具备什么、数据从哪里来、核心字段是什么，以及还可以继续补足哪些能力。

## 1. 系统目标

Freight Intelligence 的目标不是做一个面向客户的结账运费组件，而是做一个内部运营和财务使用的运费智能工作台：

- 在发货前或订单导入后，根据仓库、平台、目的地、SKU 尺寸重量、快递费率表/API，生成可解释的运费预估。
- 对每次报价保存明细 breakdown 和 trace，说明价格为什么这样算。
- 将 ERP 订单、WMS SKU/仓库、LSP 历史 API 报价、InvoiceReader 实际账单和 PostageCalculator 费率整合到本系统数据库。
- 对历史订单批量重算 Hunter、Allied、DFE、Orange Connex 等可用费率，比较 ERP Est、系统算法 Est、Invoice Actual 之间的差异。
- 帮助发现费率配置错误、zone mapping 错误、surcharge 漏算、快递乱收费、平台长期亏运费等问题。

## 2. 技术架构

- 后端：Django / Django REST Framework。
- 前端：React / Vite / Ant Design。
- 主数据库：PostgreSQL，业务库名为 `CourieDelivery`。
- 原始数据源库：PostgreSQL `data_raw`，包含 `wms`、`erp`、`lsp` 等 schema。
- 外部账单库：SQL Server `invoiceReader`。
- 外部历史费率/逻辑库：SQL Server `PostageCalculator`。
- 主要前端入口：`http://127.0.0.1:5173/`。
- 主要后端入口：`http://127.0.0.1:8000/api/`。

系统设计原则：

- UI 请求时不直接读远程 ERP/WMS/LSP/InvoiceReader，而是先同步或导入到 CourieDelivery 快照/模板表。
- 运费计算必须基于本系统内的 rate card、rate rules、surcharge rules、SKU/order snapshots 和 quote channel 配置。
- 历史回算必须保留当时使用的 rate card、calculator、zone、weight、surcharge、API response 等 trace。
- Master Data 和 Pricing 是内部配置页，应该紧凑、可搜索、可筛选、可批量配置。

## 3. 当前主要模块

### 3.1 登录与权限

系统已经有登录页，覆盖未认证访问。支持：

- 本地账号登录。
- Microsoft Entra 登录。
- 未来部署到公司内网后，可配置 Entra SSO 或 silent login。
- 管理员可维护用户、角色、权限覆盖。

关键对象：

- `UserProfile`
- `AuditLog`

核心字段：

- `email`
- `display_name`
- `role`
- `auth_provider`
- `entra_oid`
- `entra_upn`
- `entra_tid`
- `permission_overrides`
- `require_password_change`
- `is_active`
- `last_login_at`
- `last_auth_source`

角色意图：

- `ADMIN`：系统、权限、主数据、费率、同步、审计全权限。
- `PRICING_MANAGER`：费率、快递配置、报价验证。
- `OPS`：日常报价、订单导入、账单复核、审计。
- `READ_ONLY`：只读查看历史、配置和审计结果。

### 3.2 Master Data

Master Data 用于管理所有运费计算需要的基础数据。

已包含：

- Warehouses：仓库及地址，从 WMS 同步。
- Platforms：销售平台和快递/API 平台，从 ERP 同步。
- Agents：LSP/API agent，例如 Broers、EIZ、SHIPPIT、UBI、OrangeConnex。
- Carriers：快递主数据。
- Carrier Services：快递服务。
- Platform Carriers：平台可用快递/服务配置。
- Warehouse Carriers：仓库可用快递/服务配置。
- Warehouse Platforms：仓库和平台关系。
- SKU Master：single SKU 和 combo SKU。
- Invoice Sources：账单来源，按快递平台和路费账号识别 invoice。

重要业务规则：

- 平台有两种概念：
  - 销售平台：订单销售来源，例如 Shopify、Fantastic 等。
  - 快递/API 平台：用于调用 API 或 LSP 比价的 agent/platform。
- Carrier 在 UI 上应该显示 name，不应该显示难懂 code。
- 基础数据唯一 code 理想上由系统自增或同步源产生，不应要求普通用户手工填写。
- Platform 和 Warehouse 在 Manual Quote 中有 `ALL` 选项，用于不限制平台/仓库资格。
- 仓库 state 决定使用 MEL 还是 SYD 发货费率表。例如 NSW 仓库应优先参与 SYD rate card，VIC 仓库参与 MEL rate card。

### 3.3 SKU Master 与 Combo SKU

Single SKU 来源：

- `data_raw.wms.bas_sku`
- category 字段：`sku_Group2`
- 同步命令：`manage.py sync_sku_from_wms`

Combo SKU 来源：

- `data_raw.erp.hpoms_product_combo`
- `data_raw.erp.hpoms_product_combo_skus`
- combo type 数字必须从 ERP 字典表解析真实类型，不能猜。

Manual Quote 的 SKU 输入模式：

- SKU / Combo SKU：用户搜索并选择 SKU 或 combo SKU，只改 qty。
- Manual dimensions：用户可输入 SKU 作为备注/关联，但尺寸重量可以手工改。
- ERP / Platform Order：输入 ERP order no 或 platform order no 后，自动带出订单地址、平台、仓库、SKU、tracking、ERP Est、实际发货快递等信息，再计算报价。

当前 SKU 行需要支持：

- 搜索 SKU。
- 搜索 category。
- 下拉同时显示 single SKU 和 combo SKU。
- 下拉显示格式中 category 在前，SKU 对齐显示。
- 产品名完整显示，不应因为列宽被截断得不可读。

### 3.4 Pricing / Rate Card

Pricing 用于配置和导入费率。

核心对象：

- `RateCard`
- `RateZone`
- `RateRule`
- `SurchargeRule`
- `AdjustmentRule`
- `QuoteChannel`
- `ApiCredential`

当前支持的真实费率来源：

- PostageCalculator SQL Server 中已有 SP 逻辑。
- Hunter Express。
- Allied Express。
- Direct Freight Express Feb 2025 proposal。
- Orange Connex eFN 2026。

设计要求：

- Rate Card 有版本生效日期：
  - `effective_from`
  - `effective_to`
  - `is_active`
  - `priority`
  - `uploaded_by`
  - `approved_by`
  - `approved_at`
  - `activated_by`
  - `activated_at`
- 历史订单回算必须按订单日期选择当时生效的 rate card。
- 当前报价使用当前 active rate card。
- Rate Card 页面应显示 Active / Expired / Legacy 等状态。
- Fuel rate 不硬编码在 calculator 中，应该配置在 surcharge/rate config 里，例如 `0.21`、`0.28`。
- 配置时 fuel/surcharge 可以单独列举，计算结果只显示最终值，breakdown 中显示最终 fuel 金额。

### 3.5 Manual Quote

Manual Quote 是即时运费估算入口。

输入区域：

- Platform：默认 `ALL`。
- Warehouse：默认 `ALL`。
- Destination：
  - suburb 输入框。
  - 输入 suburb 时自动弹出 suburb / state / postcode 选项。
  - 选择后自动填充 state、postcode。
- Line entry mode：
  - SKU / Combo SKU。
  - Manual dimensions。
  - ERP / Platform Order。

SKU / Combo SKU 模式字段：

- `sku`
- `qty`
- `unit_weight_kg`
- `length_cm`
- `width_cm`
- `height_cm`
- `category`
- `is_combo`
- `components`
- `tracking`，如果来自 ERP order。

Manual dimensions 模式字段：

- `sku`，可选。
- `qty`
- `unit_weight_kg`
- `length_cm`
- `width_cm`
- `height_cm`

ERP / Platform Order 模式字段：

- `erp_order_no`
- `platform_order_no`
- `platform`
- `warehouse`
- `destination`
- `shipping_option`
- `erp_estimated_freight`
- `actual_carrier`
- `tracking_numbers`
- `sales_sku_items`
- `shipment_sku_items`
- `quote_items`
- `lsp_quote`，如果有匹配到历史 LSP API quote。

报价结果要求：

- 先按 available 排序，available 在前。
- available 内按 total inc GST 从低到高排序。
- 高亮最低价。
- 外层只显示 total inc GST，不显示 base/fuel 等细项。
- 每条结果提供 View Breakdown。
- breakdown 中只显示有数值的费用；没有触发或金额为 0 的 OIS/surcharge 不显示。

### 3.6 Quote Breakdown 与 Trace

每次报价不只保存总价，还保存每项费用。

Breakdown 示例：

- Base freight
- Fuel levy
- Residential surcharge
- Oversize surcharge
- Remote area surcharge
- Manual adjustment
- GST
- Final price

Trace 需要记录：

- 使用哪个仓库。
- 匹配哪个平台。
- 匹配哪个快递/服务。
- 使用哪个 rate card。
- 使用哪个 calculator。
- 匹配哪个 zone。
- 实际重量。
- 体积重量。
- 计费重量。
- 触发哪些 surcharge。
- 是否调用 API。
- API request / response summary。
- not available 原因。

关键对象：

- `QuoteRun`，DB table `quote_request`
- `QuoteCandidate`，DB table `quote_result`
- `QuoteChargeLine`，DB table `quote_result_breakdown`
- `QuoteTraceLog`，DB table `quote_trace_log`

### 3.7 Historical Orders / Order Imports

订单来源：

- `data_raw.erp.hpoms_owner_order`
- ERP manual order 相关表。
- `data_raw.erp.hpoms_owner_order_shipment_detail`

导入字段目标：

- ERP Order No。
- Platform Order No。
- 第三方/外部单号。
- Platform name。
- Warehouse code/name。
- Shipping option。
- ERP Est。
- Tracking，一个订单可能多个 tracking。
- Actual carrier / carrier channel / service provider。
- SKU 行。
- 目的地 suburb/state/postcode。

当前设计：

- 大量历史订单应同步进 CourieDelivery 后再使用。
- UI 列表要支持服务端模糊搜索，而不是只搜索当前页。
- 可按 tracking 查 ERP order。
- 订单量可到百万级，需要 PostgreSQL index 和分页查询。

### 3.8 LSP API Quotes

LSP API Quotes 用于保存历史 API 请求/返回价格。

来源：

- `data_raw.lsp.lsp_openapi_quote_task`
- `data_raw.lsp.lsp_quote_task`
- `data_raw.lsp.lsp_booking_order`
- `data_raw.lsp.lsp_quote_task_job_log`

当前确认的匹配逻辑：

- `lsp_openapi_quote_task.quote_id -> lsp_quote_task.id`
- 不要用 `lsp_quote_task.order_code` 直接匹配 ERP，通常不可靠。
- 首选桥：
  - `lsp_quote_task.shipment_code -> lsp_booking_order.shipment_code`
  - `lsp_booking_order.reference_no` 或 booking `shipment_code` -> ERP owner order reference
- `lsp_booking_order.tracking_number` 很少能直接匹配 ERP shipment tracking，只作为辅助证据。
- W-NSY/WSY01 大部分是 standalone/API/test-style quote traffic，无 ERP reference。
- LDX/LDX01 是目前主要可匹配 ERP 的子集。

在 Manual Quote 中，如果用户按 ERP order 搜索订单：

- 应显示当前系统算法报价。
- 如果能匹配 LSP API 历史报价，应显示一条可收起的 LSP 历史价格。
- 展开后显示当初 LSP 返回的各 carrier/agent 报价明细。

### 3.9 Invoice Reconciliation

Invoice Reconciliation 用于将快递实际账单与 ERP/系统预估对账。

来源：

- SQL Server `invoiceReader`
- Header source of truth：`dbo.invoice_header_local_freight`
- Detail source：`dbo.invoice_detail_*`

不要使用：

- `fact_invoice_order_normalized`
- `stg_*`
- `column_mapping`
- 维度/参考表作为实际 charge rows

对账逻辑：

- Invoice charge snapshot 按 tracking 导入。
- 用 tracking 匹配 `ErpShipmentSnapshot.tracking_no`。
- 如果一个 tracking 有多个 ERP 候选，用 carrier/channel/service 进一步 disambiguate。
- 匹配后获得 ERP order no、platform order no、platform、warehouse、carrier、ERP estimate。
- 比较：
  - ERP Est
  - System Est
  - Invoice Actual
  - variance amount
  - variance percent
  - dispute recommended

UI 要求：

- Review 结果密集展示。
- 字符缩小，适合大量字段。
- 支持 Excel 导出。
- 支持 matched / exception 分类。
- `matched` 表示 invoice 与 ERP shipment/order 成功对应。
- `exception` 表示缺 tracking、找不到 ERP、金额异常或需要人工处理。

### 3.10 Freight Audit Matrix

Freight Audit Matrix 是历史订单批量重算和跨 carrier 对比模块。

目标：

- 对 5000 单或更多历史订单批量跑系统中所有有真实计算方式的 carrier/channel。
- 列表字段：
  - order no
  - platform order no
  - tracking
  - platform
  - warehouse
  - destination
  - ERP Est
  - Invoice Actual
  - Hunter
  - Allied
  - DFE
  - Orange Connex
  - 其他可用 carrier
- 每个 carrier 列显示按费率表/API 算出的 inc GST 总价。
- 点击行打开详情页：
  - ERP Est 放在表头。
  - 各快递以卡片上下排列。
  - 多 tracking 时按 tracking 分组并排序。
  - 每个 tracking 展示 base、surcharge、fuel、GST、total 等明细。

计算模式：

- `CONSIGNMENT`：按 tracking/consignment 分别计算，再汇总到 owner-order 级别对比 ERP Est。
- `ORDER`：整单 SKU 合并计算。
- `ITEM`：单 SKU line 检查，不应直接与 order-level ERP Est 比较。

注意：

- ERP Est 目前按订单级别理解。
- ERP Est 从 ERP 导入时是 ex GST，UI 显示时要转为 inc GST。
- 系统算法 `total_inc_gst` 已经是含 GST。

## 4. 外部数据源与同步

### 4.1 WMS

数据库：`data_raw`  
Schema：`wms`

关键表：

- `bas_sku`
- `bsm_warehouse`

用途：

- 同步 single SKU 尺寸、重量、category。
- 同步仓库地址、state、postcode 等。

关键字段：

- `SKU`
- `weight`
- `length`
- `width`
- `height`
- `sku_Group2`
- `update time`
- warehouse code/name/address/state/postcode

计划：

- SKU 每天 AEDT 03:00 增量同步。
- 仓库按需或定期同步。

### 4.2 ERP

数据库：`data_raw`  
Schema：`erp`

关键表：

- `hpoms_platform_info`
- `hpoms_owner_order`
- `hpoms_order`
- `hpoms_owner_order_purchase_skus`
- `hpoms_owner_order_shipment_detail`
- `hpoms_manual_orders`
- `hpoms_manual_order_skus`
- `hpoms_product_combo`
- `hpoms_product_combo_skus`

用途：

- 同步销售平台。
- 同步历史订单。
- 同步订单 SKU。
- 同步 shipment/tracking。
- 同步 ERP Est。
- 同步 combo SKU 组件。
- 匹配 InvoiceReader。

重要字段：

- `rd3_order_id`
- `owner_order_no`
- `platform_reference_no`
- `platform_id`
- `wash_warehouse_code`
- `warehouse_owner_code`
- `shipping_option`
- `postage_shipping_estimated_amount`
- `shipping_estimated_amount`
- `tracking`
- `carrier`
- `carrier_channel`
- `service_providers`
- `carrier_channel_account`

### 4.3 LSP

数据库：`data_raw`  
Schema：`lsp`

关键表：

- `lsp_carrier`
- `lsp_carrier_channel`
- `lsp_carrier_agent`
- `lsp_booking_order`
- `lsp_openapi_quote_task`
- `lsp_quote_task`
- `lsp_quote_task_job`
- `lsp_quote_task_job_log`

用途：

- 一次性导入 carrier/agent/channel 基础信息。
- 同步历史 LSP API quote request/response。
- 保存历史 carrier-by-carrier quote options。
- 关联 ERP order 后，在 Manual Quote 的 ERP order 搜索中显示当初 LSP API 返回价格。

重要字段：

- `quote_id`
- `request_id`
- `req_json`
- `res_json`
- `order_code`
- `shipment_code`
- `reference_no`
- `tracking_number`
- `carrier_code`
- `carrier_agent_code`
- `carrier_strategy_code`
- `result_data`

### 4.4 InvoiceReader

数据库：SQL Server `invoiceReader`

关键表：

- `invoice_header_local_freight`
- `invoice_detail_*`

用途：

- 导入快递实际账单。
- 按 invoice source + freight account + tracking 聚合。
- 与 ERP shipment snapshot 匹配。

重要字段：

- invoice no
- invoice date
- invoice source
- freight account
- tracking no
- order reference
- carrier/service
- charge type
- actual freight
- source table
- source line count

### 4.5 PostageCalculator

数据库：SQL Server `PostageCalculator`

用途：

- 导入已有 Hunter / Allied 等 SP 费率和计算逻辑。
- 转换为 CourieDelivery rate card、rule、zone、surcharge template。

注意：

- LSP rate tables 不作为当前系统主费率来源。
- 用户当前选择的是以 CourieDelivery/PostageCalculator rate template 为准。

## 5. 当前已启用/导入的计算逻辑

### Hunter Express

来源：

- PostageCalculator SP。

特点：

- 按 rate card / zone / weight 计算。
- 支持 MEL/SYD 区分。
- 需要根据 warehouse state 决定参与哪套 origin rate card。

### Allied Express

来源：

- PostageCalculator SP。

特点：

- 支持 Allied GRO 2023 Melbourne/Sydney。
- 支持 Allied B2C 2025 Melbourne。
- 费率包含 linehaul、surcharge、fuel、GST 等。
- 多 tracking 时需要按 tracking 分组展示明细。

### Direct Freight Express

来源：

- `Direct Feight Express Rates Proposal EX SYD Ex MEL Feb 2025.xlsx`
- `Zone List - postcodes 1.csv`

特点：

- 以 kg rate 和 zone mapping 为基础。
- MEL/SYD 费率分开。
- fuel 配置在 surcharge rule，当前示例为 `0.196`。
- 有严格 not available 规则：
  - 单件重量 > 30kg。
  - 最长边 > 120cm。
  - 两边同时 > 70cm。

### Orange Connex eFN 2026

来源：

- `2026 AU eFN rate.xlsx`

特点：

- 按固定 article weight bands。
- MEL/SYD 分开。
- 使用 postcode/suburb/state zone。
- 有限制：
  - article weight > 25kg。
  - longest side > 105cm。
  - volume > 0.088m3。

## 6. 核心数据模型与字段清单

以下字段为当前系统中与业务逻辑直接相关的主表字段。`id`、`created_at`、`updated_at` 等通用审计字段未重复解释。

### 6.1 用户与权限

`UserProfile` / `freight_userprofile`

- `user`
- `entra_oid`
- `entra_upn`
- `entra_tid`
- `email`
- `display_name`
- `role`
- `auth_provider`
- `permission_overrides`
- `require_password_change`
- `last_auth_source`
- `is_active`
- `last_login_at`

`AuditLog` / `freight_auditlog`

- `actor`
- `action`
- `entity_type`
- `entity_id`
- `before_json`
- `after_json`

### 6.2 Master Data

`Warehouse` / `freight_warehouse`

- `code`
- `name`
- `address`
- `address2`
- `suburb`
- `postcode`
- `state`
- `country`
- `region`
- `contact_name`
- `telephone`
- `email`
- `timezone`
- `default_origin_zone`
- `active`
- `source_external_id`
- `source_system`
- `source_database`
- `source_schema`
- `source_table`
- `external_updated_at`
- `source_extracted_at`
- `last_synced_at`
- `sync_status`
- `sync_error`
- `source_payload_json`

`Platform` / `freight_platform`

- `code`
- `name`
- `company`
- `platform_type`
- `platform_role`
- `source_platform_type_code`
- `source_platform_type_name_en`
- `source_platform_type_name_zh`
- `platform_group_code`
- `platform_group_name_en`
- `platform_group_name_zh`
- `legal_name`
- `source_sort`
- `active`
- `default_origin_warehouse`
- `source_external_id`
- `source_system`
- `source_database`
- `source_schema`
- `source_table`
- `external_updated_at`
- `source_extracted_at`
- `last_synced_at`
- `sync_status`
- `sync_error`
- `source_payload_json`

`Agent` / `freight_agent`

- `code`
- `name`
- `agent_type`
- `active`
- `supports_api`
- `maintains_rate_cards`
- `lsp_status_code`
- `lsp_rate_type`
- `lsp_consign_agent_id`
- `channel_count`
- `carrier_count`
- `notes`
- `source_external_id`
- `source_system`
- `source_database`
- `source_schema`
- `source_table`
- `external_updated_at`
- `source_extracted_at`
- `last_synced_at`
- `sync_status`
- `sync_error`
- `source_payload_json`

`Carrier` / `freight_carrier`

- `code`
- `name`
- `carrier_type`
- `active`
- `support_api`
- `notes`
- `source_external_id`
- `source_system`
- `source_database`
- `source_schema`
- `source_table`
- `external_updated_at`
- `source_extracted_at`
- `last_synced_at`
- `sync_status`
- `sync_error`
- `source_payload_json`
- `lsp_status_code`
- `lsp_agent_code`
- `lsp_channel_code`
- `active_rate_rows`
- `active_quote_rate_rows`
- `active_api_accounts`

`CarrierService` / `freight_carrierservice`

- `carrier`
- `code`
- `name`
- `service_level`
- `active`

`PlatformCarrier` / `freight_platformcarrier`

- `platform`
- `carrier`
- `service`
- `enabled`
- `account_code`
- `priority`
- `quote_source`

`WarehouseCarrier` / `freight_warehousecarrier`

- `warehouse`
- `carrier`
- `service`
- `enabled`
- `account_code`
- `origin_zone`
- `cut_off_time`
- `max_weight_kg`
- `max_volume_m3`
- `notes`
- `updated_by`

`WarehousePlatform` / `freight_warehouseplatform`

- `warehouse`
- `platform`
- `enabled`
- `priority`
- `is_default`
- `valid_from`
- `valid_to`
- `notes`
- `updated_by`

`InvoiceSource` / `freight_invoicesource`

- `code`
- `name`
- `source_platform`
- `freight_account`
- `carrier`
- `carrier_service`
- `mapping_method`
- `active`
- `auto_created_carrier`
- `auto_created_service`
- `source_system`
- `source_database`
- `source_schema`
- `source_header_table`
- `source_detail_table`
- `last_synced_at`
- `source_payload_json`

### 6.3 SKU

`SKU` / `freight_sku`

- `sku`
- `description`
- `category`
- `unit_weight_kg`
- `length_cm`
- `width_cm`
- `height_cm`
- `carton_qty`
- `active`
- `source_system`
- `source_database`
- `source_schema`
- `source_table`
- `external_updated_at`
- `source_extracted_at`
- `last_synced_at`
- `sync_status`
- `sync_error`
- `source_payload_json`
- `is_combo`
- `combo_type`
- `combo_type_label`
- `combo_source_updated_at`

`SKUComboComponent` / `freight_skucombocomponent`

- `combo_sku`
- `component_sku`
- `component_qty`
- `combo_title`
- `combo_type`
- `combo_type_label`
- `active`
- `source_system`
- `source_updated_at`
- `source_extracted_at`
- `last_synced_at`
- `source_payload_json`

### 6.4 Pricing

`RateCard` / `freight_ratecard`

- `carrier`
- `service`
- `origin_warehouse`
- `name`
- `version`
- `version_label`
- `status`
- `effective_from`
- `effective_to`
- `is_active`
- `priority`
- `currency`
- `tax_mode`
- `gst_rate`
- `cubic_factor`
- `source_file`
- `legacy_source_object`
- `metadata_json`
- `uploaded_by`
- `approved_by`
- `approved_at`
- `activated_by`
- `activated_at`

`RateZone` / `freight_ratezone`

- `rate_card`
- `origin_zone`
- `dest_zone`
- `state`
- `suburb`
- `postcode`
- `postcode_from`
- `postcode_to`
- `deliverable`
- `raw_payload`

`RateRule` / `freight_raterule`

- `rate_card`
- `service`
- `from_zone`
- `to_zone`
- `state`
- `suburb`
- `postcode`
- `weight_min_kg`
- `weight_max_kg`
- `basic_charge`
- `per_kg`
- `minimum_charge`
- `maximum_charge`
- `rule_type`
- `priority`
- `raw_payload`

`SurchargeRule` / `freight_surchargerule`

- `carrier`
- `rate_card`
- `code`
- `rule_name`
- `min_threshold`
- `max_threshold`
- `ratio`
- `fee_amount`
- `match_dimension`
- `condition_json`
- `priority`
- `active`
- `raw_payload`

`AdjustmentRule` / `freight_adjustmentrule`

- `name`
- `active`
- `priority`
- `carrier`
- `rate_card`
- `platform`
- `service`
- `state`
- `suburb`
- `postcode`
- `zone_code`
- `sku_pattern`
- `action`
- `amount`
- `percent`
- `valid_from`
- `valid_to`
- `stop_processing`
- `notes`

`QuoteChannel` / `freight_quotechannel`

- `code`
- `name`
- `carrier`
- `service`
- `provider_type`
- `calculator_key`
- `quote_source`
- `enabled`
- `priority`
- `timeout_ms`
- `rate_card`
- `api_credential`
- `agent`
- `config_json`
- `valid_from`
- `valid_to`

`ApiCredential` / `freight_apicredential`

- `agent`
- `provider`
- `account_code`
- `base_url`
- `encrypted_secret`
- `active`
- `metadata_json`

### 6.5 Orders

`HistoricalOrder` / `freight_historicalorder`

- `order_no`
- `consignment_no`
- `platform`
- `warehouse`
- `order_date`
- `source_system`
- `source_order_type`
- `source_external_id`
- `source_updated_at`
- `erp_order_no`
- `erp_owner_order_no`
- `external_order_no`
- `platform_order_no`
- `shipping_option`
- `destination_address`
- `suburb`
- `postcode`
- `state`
- `actual_carrier`
- `actual_freight`
- `postage_shipping_estimated_amount`
- `source_estimated_freight`
- `source_estimated_carrier`
- `source_estimated_service`
- `raw_payload`

`HistoricalOrderItem` / `freight_historicalorderitem`

- `order`
- `sku`
- `description`
- `qty`
- `unit_weight_kg`
- `length_cm`
- `width_cm`
- `height_cm`
- `raw_payload`

`HistoricalOrderShipment` / `freight_historicalordershipment`

- `order`
- `source_external_id`
- `tracking_no`
- `carrier_name`
- `carrier_channel`
- `service_provider`
- `carrier_channel_account`
- `warehouse_code`
- `warehouse_owner_code`
- `package_no`
- `purchase_sku`
- `owner_purchase_sku`
- `qty`
- `status_code`
- `raw_payload`

`ErpShipmentSnapshot` / `erp_shipment_snapshot`

- `order`
- `source_system`
- `source_external_id`
- `tracking_no`
- `erp_order_no`
- `erp_owner_order_no`
- `third_party_order_no`
- `platform_order_no`
- `platform_code`
- `platform_name`
- `platform_company`
- `warehouse_code`
- `carrier_name`
- `carrier_channel`
- `service_provider`
- `carrier_channel_account`
- `shipping_option`
- `order_date`
- `source_updated_at`
- `estimated_freight`
- `estimate_source`
- `raw_payload`

### 6.6 LSP API Quote

`LspApiQuoteSnapshot` / `lsp_api_quote_snapshot`

- `historical_order`
- `platform`
- `carrier`
- `service`
- `source_system`
- `source_external_id`
- `quote_task_id`
- `request_id`
- `quote_id`
- `status`
- `status_summary`
- `quote_at`
- `source_created_at`
- `source_updated_at`
- `source_extracted_at`
- `lsp_order_code`
- `lsp_shipment_code`
- `warehouse_code`
- `strategy_code`
- `booking_tracking_no`
- `booking_carrier_code`
- `booking_freight`
- `erp_order_no`
- `erp_owner_order_no`
- `external_order_no`
- `platform_order_no`
- `source_order_id`
- `source_platform_id`
- `erp_estimated_freight`
- `erp_postage_estimated_freight`
- `predicted_carrier_code`
- `predicted_carrier_name`
- `predicted_service_code`
- `predicted_service_name`
- `predicted_shipping_cost`
- `predicted_carrier_shipping_cost`
- `owner_price`
- `predict_price`
- `package_count`
- `quote_option_count`
- `destination_suburb`
- `destination_state`
- `destination_postcode`
- `request_summary_json`
- `response_summary_json`
- `raw_response_json`

`LspApiQuoteOption` / `lsp_api_quote_option`

- `snapshot`
- `option_index`
- `carrier_code`
- `carrier_name`
- `courier_code`
- `courier_name`
- `service_code`
- `service_name`
- `can_shipping`
- `shipping_cost`
- `carrier_shipping_cost`
- `calc_mode`
- `remark`
- `raw_quote_json`

`LspQuoteTaskLogItem` / `lsp_quote_task_log_item`

- `snapshot`
- `source_system`
- `source_external_id`
- `quote_task_id`
- `quote_task_job_id`
- `item_index`
- `item_scope`
- `log_action`
- `log_status`
- `calc_mode`
- `rate_type`
- `carrier_agent_code`
- `carrier_codes`
- `carrier_strategy_code`
- `log_created_at`
- `log_updated_at`
- `agent_code`
- `carrier_code`
- `channel_code`
- `service_level`
- `can_shipping`
- `shipping_cost`
- `shipping_cost_with_tax`
- `surcharge`
- `estimated_days`
- `failed_reason`
- `raw_item_json`

### 6.7 Quote Request / Result / Trace

`QuoteRun` / `quote_request`

- `run_type`
- `source`
- `historical_order`
- `platform`
- `warehouse`
- `input_hash`
- `input_snapshot_json`
- `status`
- `started_at`
- `finished_at`
- `created_by`
- `error_message`

`QuoteCandidate` / `quote_result`

- `quote_run`
- `channel`
- `provider_type`
- `provider_name`
- `carrier`
- `service`
- `rate_card`
- `availability`
- `not_available_reason`
- `base_amount`
- `surcharge_amount`
- `fuel_amount`
- `adjustment_amount`
- `total_ex_gst`
- `gst_amount`
- `total_inc_gst`
- `eta_min_days`
- `eta_max_days`
- `rank`
- `raw_response_json`
- `debug_breakdown`

`QuoteChargeLine` / `quote_result_breakdown`

- `candidate`
- `line_type`
- `description`
- `amount_ex_gst`
- `gst_amount`
- `amount_inc_gst`
- `source_rule_id`
- `metadata_json`

`QuoteTraceLog` / `quote_trace_log`

- `quote_run`
- `candidate`
- `event_type`
- `step`
- `message`
- `details_json`

`ApiCallLog` / `freight_apicalllog`

- `provider`
- `request_hash`
- `masked_request`
- `response_json`
- `status_code`
- `duration_ms`
- `success`
- `error_message`

### 6.8 Invoice Reconciliation

`InvoiceReconciliationBatch` / `invoice_reconciliation_batch`

- `carrier`
- `carrier_service`
- `invoice_source`
- `name`
- `status`
- `source_file`
- `source_system`
- `source_external_id`
- `invoice_date`
- `total_rows`
- `matched_rows`
- `exception_rows`
- `uploaded_by`
- `report_json`

`InvoiceChargeSnapshot` / `invoice_charge_snapshot`

- `invoice_source`
- `source_system`
- `source_external_id`
- `source_key`
- `source_label`
- `source_table`
- `invoice_no`
- `invoice_date`
- `tracking_no`
- `order_reference`
- `source_platform`
- `freight_account`
- `carrier_name`
- `service_name`
- `charge_type`
- `amount_basis`
- `actual_freight`
- `source_line_count`
- `raw_payload`

`InvoiceReconciliationItem` / `invoice_reconciliation_item`

- `batch`
- `order`
- `quote_candidate`
- `erp_shipment_snapshot`
- `invoice_charge_snapshot`
- `carrier`
- `carrier_service`
- `invoice_source`
- `consignment_no`
- `order_no`
- `invoice_no`
- `invoice_date`
- `source_system`
- `source_external_id`
- `estimated_freight`
- `system_estimated_freight`
- `actual_freight`
- `variance_amount`
- `variance_percent`
- `system_variance_amount`
- `system_variance_percent`
- `match_status`
- `variance_type`
- `dispute_recommended`
- `reason`
- `system_estimate_reason`
- `raw_payload`

### 6.9 Freight Audit Matrix

`FreightAuditRow` / `freight_audit_row`

- `source_system`
- `source_external_id`
- `calculation_mode`
- `invoice_reconciliation_item`
- `erp_shipment_snapshot`
- `quote_run`
- `order_no`
- `tracking_no`
- `platform_code`
- `platform_name`
- `warehouse_code`
- `order_date`
- `suburb`
- `postcode`
- `state`
- `erp_estimated_freight`
- `invoice_actual_freight`
- `item_count`
- `total_qty`
- `status`
- `error_message`
- `raw_payload`

`FreightAuditResult` / `freight_audit_result`

- `row`
- `quote_channel`
- `quote_candidate`
- `carrier`
- `carrier_service`
- `carrier_key`
- `carrier_name`
- `service_name`
- `provider_type`
- `availability`
- `not_available_reason`
- `base_amount`
- `surcharge_amount`
- `fuel_amount`
- `adjustment_amount`
- `gst_amount`
- `total_inc_gst`
- `variance_to_erp`
- `variance_to_invoice`
- `rank`
- `raw_payload`

### 6.10 Import Job

`ImportJob` / `freight_importjob`

- `job_type`
- `status`
- `source_file`
- `total_rows`
- `success_rows`
- `error_rows`
- `progress`
- `report_json`
- `created_by`

## 7. 关键后端命令

主同步：

```powershell
python backend/manage.py sync_operational_data --full --order-batch-size 5000 --lsp-batch-size 1000 --log-batch-size 3000
python backend/manage.py sync_operational_data --incremental --order-batch-size 5000 --lsp-batch-size 1000 --log-batch-size 3000
```

索引检查：

```powershell
python backend/manage.py ensure_data_raw_sync_indexes --only-missing
python backend/manage.py check_postgres_optimization --show-missing
```

SKU/warehouse/platform/agent：

```powershell
python backend/manage.py sync_sku_from_wms
python backend/manage.py sync_warehouses_from_wms
python backend/manage.py sync_platforms_from_erp
python backend/manage.py sync_agents_from_lsp
```

订单与 shipment：

```powershell
python backend/manage.py sync_orders_from_erp
python backend/manage.py sync_orders_from_erp --shipments-only
```

LSP：

```powershell
python backend/manage.py sync_lsp_api_quotes --full
python backend/manage.py sync_lsp_quote_logs --full
```

Invoice：

```powershell
python backend/manage.py sync_invoices_from_sqlserver
python backend/manage.py sync_reconciliation_snapshots
```

Freight Audit：

```powershell
python backend/manage.py build_freight_audit_matrix --batch-id <id> --mode CONSIGNMENT --limit 5000 --order-batch-size 5000
```

Rate imports：

```powershell
python backend/manage.py import_postagecalculator_rates
python backend/manage.py import_dfe_rates
python backend/manage.py import_orange_connex_rates
```

## 8. 当前 UI 页面

主要页面：

- Login
- Dashboard
- Manual Quote
- Quote Runs
- Master Data
- Pricing
- Order Imports
- LSP API Quotes
- Invoice Reconciliation
- Freight Audit Matrix
- Quote Channels
- Users & Roles

UI 要求：

- 使用 Ant Design 风格。
- 配置页面应紧凑，适合大量字段和搜索筛选。
- 所有列表页标题下应有服务端模糊搜索框。
- 大屏/4K/常见笔记本分辨率下应避免字段名被挤压缩略。
- Manual Quote 和 Audit Detail 需要在多 tracking、多 fee 字段时保持可读。

## 9. 已知边界和待 GPT 思考的问题

### 9.1 数据匹配缺口

- W-NSY LSP API quote 大量无法匹配 ERP，因为源字段里没有清晰 ERP reference。
- 需要确认 LSP 是否另有字段可存 ERP order no / platform order no / shipment id。
- ERP Est 是 order-level 还是 tracking-level，需要用更多源数据证据确认。
- 多 tracking 订单对账时如何分摊 ERP Est 仍需要业务确认。

### 9.2 Rate / Calculator 缺口

- 当前真实 table calculator 主要覆盖 Hunter、Allied、DFE、Orange Connex。
- EIZ、Shippit、UBI、Australia Post 等 API/agent 逻辑还需要明确 live API 调用方式和 credential。
- 不同 carrier 的 fuel、remote、residential、oversize、tailgate、manual handling 等 surcharge 规则仍需逐一验证。
- Rate Card approval workflow 有字段，但审批 UI/流程可继续完善。

### 9.3 Invoice Reconciliation 缺口

- InvoiceReader 各 carrier detail table 的字段映射仍需持续校验。
- Dispute list 生成后，是否需要状态流转、备注、附件、邮件导出。
- Actual charge 是否全部统一为 inc GST，需要按每个 invoice source 验证。
- 一张 invoice 多 fee lines 聚合为一个 tracking charge 的规则需要业务确认。

### 9.4 Order / SKU 缺口

- SKU 尺寸重量质量需要监控，错误 SKU 会直接影响体积重。
- Combo SKU 展开后是否应该按实际 packed carton 还是组件尺寸累加，需要业务确认。
- ERP shipment SKU 与 sales SKU 不一致时，系统当前偏向 shipment SKU；规则是否适用于所有平台需要确认。
- Warehouse state 决定 MEL/SYD rate card 的规则需要扩展到更多 origin 区域。

### 9.5 权限与部署缺口

- Microsoft Entra SSO 需要服务器部署地址、App Registration、API scope、redirect URI。
- 公司内网 IP 可以使用 Entra SSO，但 redirect URI、证书/HTTPS、内网 DNS 策略需要 IT 配合。
- 本地 dev auth 不应生产启用。
- 用户权限已经有基础模型，但还需要按真实岗位梳理更细权限矩阵。

### 9.6 产品体验缺口

- Dashboard 目前可以继续加强为运营总览：
  - 今日报价数。
  - not available top reasons。
  - invoice variance top carriers。
  - rate card expiring soon。
  - SKU missing dimensions。
- Manual Quote 需要更清楚地展示：
  - 选择的 SKU 快照。
  - Combo 展开明细。
  - warehouse origin 影响。
  - LSP historical quote 与 system quote 的差异。
- Audit Matrix 需要考虑：
  - 大批量任务后台队列。
  - 进度条。
  - 可暂停/恢复。
  - 结果缓存。
  - Excel 导出。

## 10. 给 GPT 的评审提示

请从以下角度评估系统还缺什么：

1. 运费计算准确性：不同 carrier 的计费重量、体积重、minimum charge、fuel、surcharge、zone mapping 是否覆盖完整。
2. 数据完整性：ERP、WMS、LSP、InvoiceReader 哪些关键字段还没同步或没关联。
3. 对账价值：系统 Est、ERP Est、Invoice Actual 的比较是否足以发现问题。
4. 操作流程：定期同步、手动同步、异常处理、审批、dispute workflow 是否闭环。
5. 权限安全：角色、Entra SSO、本地账号、审计日志是否足以生产使用。
6. 性能：百万级订单、几十万 invoice charge、几百万 quote result 下，搜索、分页、批量审计是否可承受。
7. UI：配置页是否够高密度，报价/审计详情是否够解释性。
8. 可维护性：新 carrier/rate card/API 接入是否足够模板化。

## 11. UBI Agent Rate Package 设计入口

UBI 在系统中应作为 `Agent`，不是单一 carrier。UBI rate package 不做自动 email/文件夹监听更新；由管理员手动上传或手动运行导入命令。手动导入时，rate table 需要按规范化 sheet hash 做版本判断：

- 相同 hash：不更新费率，只记录本次文件已经见过。
- 不同 hash 且能识别 `effective_from`：创建新的 `RateCard` 版本，并按日期关闭旧版本。
- 不同 hash 但没有可信生效日期：进入人工 review，不参与报价。
- `additional_fee`、`oversize`、`redelivery`、`rts`、`underticketing` 只进入 Invoice Reconciliation / surcharge audit，不作为基础报价表。

专项分析和开发方案见：

- `docs\UBI_Agent_Rate_Table_Analysis_Development_Plan_20260617.md`
- `docs\UBI_Rate_Import_System_Development_Plan_20260617.md`
- `docs\UBI_Rate_Table_System_Readiness_Check_20260618.md`
