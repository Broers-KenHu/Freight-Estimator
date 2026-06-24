# InvoiceReader Order Match 与 CourieDelivery 匹配差异报告

生成时间：2026-06-24 12:11:58

本报告只读取 SQL Server `invoiceReader` 和 CourieDelivery PostgreSQL 的元数据、匹配键和聚合结果。报告不输出原始 invoice/order/tracking 明细值。

## 结论摘要

- 在 `invoiceReader` 中识别到最像“order match 结果表”的候选表为 `dbo.erp_match_results`，行数约 901,428。
- CourieDelivery 当前本地 `invoice_reconciliation_item` 总行数为 1,132,289，其中来自 InvoiceReader 的本地对账行按 `match_status` 见下方表格。
- 以 tracking 为主键对比：InvoiceReader 结果表唯一 tracking 781,075，CourieDelivery 唯一 tracking 879,506，交集 325,003。
- InvoiceReader 有、CourieDelivery 没有的 tracking：456,072；CourieDelivery 有、InvoiceReader 没有的 tracking：554,503。
- 同 tracking 但 ERP/order 映射不同的数量：142,264。
- tracking+order 成对匹配交集：172,819，InvoiceReader 独有 pair：457,433，CourieDelivery 独有 pair：737,256。

## InvoiceReader 候选结果表

| 候选表 | 行数 | 分数 | tracking 字段 | order 字段 | platform/ref 字段 | invoice 字段 | amount 字段 |
|---|---:|---:|---|---|---|---|---|
| `dbo.erp_match_results` | 901,428 | 198 | `detail_tracking` | `erp_owner_order_no` | `erp_rd3_order_id` | `invoice_no` | `detail_amount_inc_gst` |
| `dbo.fact_invoice_order_normalized` | 743,031 | 149 | `tracking_number` | `booking_order_id` | `reference_no` | `invoice_no` | `total_inc_gst` |
| `dbo.fact_lsp_order_sku` | 4,014,381 | 98 | `tracking_number` | `booking_order_id` | `reference_no` | `-` | `-` |
| `dbo.invoice_detail_ubi_rts` | 29 | 94 | `tracking_no` | `ioss_order_number` | `-` | `invoice_no` | `total_amount_in_gst` |
| `dbo.fact_lsp_order_reference` | 2,555,250 | 68 | `tracking_number` | `-` | `reference_no` | `-` | `-` |
| `dbo.fact_lsp_order_tracking` | 0 | 68 | `tracking_number` | `-` | `reference_no` | `-` | `-` |
| `dbo.invoice_detail_sunyee_manifest` | 404,765 | 64 | `estimated_charge_consignment` | `-` | `-` | `invoice_no` | `under_declared_amount` |
| `dbo.invoice_detail_ubi_toll_bill` | 75,233 | 64 | `tracking_no` | `-` | `-` | `invoice_no` | `freight_charge_amount` |
| `dbo.invoice_detail_eiz_shipment` | 50,577 | 64 | `tracking_number` | `-` | `-` | `invoice_number` | `tax_amount` |
| `dbo.invoice_detail_shippit_deliveries` | 37,094 | 64 | `tracking` | `-` | `-` | `invoice_no` | `amount` |
| `dbo.invoice_detail_sunyee_retrospect` | 26,150 | 64 | `article_id` | `-` | `-` | `invoice_no` | `declared_amount` |
| `dbo.cleaned_invoice_detail_eiz_shipment` | 23,799 | 64 | `tracking_number` | `-` | `-` | `invoice_number` | `tax_amount` |

> 说明：分数由表名和字段名推断，例如是否包含 `order`、`match`、`normalized`、`tracking`、`invoice`、`amount` 等。该分数用于定位候选表，不代表业务正确性。

## 选中表字段覆盖

选中表：`dbo.erp_match_results`

| 字段 | 非空行数 | 覆盖率 |
|---|---:|---:|
| `detail_tracking` | 900,934 | 99.95% |
| `erp_owner_order_no` | 744,991 | 82.65% |
| `erp_rd3_order_id` | 745,252 | 82.67% |
| `invoice_no` | 901,428 | 100.00% |
| `detail_amount_inc_gst` | 901,428 | 100.00% |

选中字段：

| 用途 | 字段 |
|---|---|
| Tracking | `detail_tracking` |
| ERP/Order | `erp_owner_order_no` |
| Platform/External Ref | `erp_rd3_order_id` |
| Invoice | `invoice_no` |
| Amount | `detail_amount_inc_gst` |
| Status | `-` |

本次本地对齐口径：

| InvoiceReader 字段 | 本地对齐字段 |
|---|---|
| `erp_owner_order_no` | `COALESCE(NULLIF(ess.erp_owner_order_no, ''), NULLIF(ess.erp_order_no, ''), NULLIF(iri.order_no, ''))` |
| `erp_rd3_order_id` | `COALESCE(NULLIF(ess.third_party_order_no, ''), NULLIF(ess.platform_order_no, ''))` |

## CourieDelivery 当前匹配结果

| 本地对象 | 行数 |
|---|---:|
| `invoice_reconciliation_item` | 1,132,289 |
| `invoice_charge_snapshot` | 189,041 |
| `erp_shipment_snapshot` | 341,361 |

InvoiceReader 本地对账行状态：

| match_status | 行数 |
|---|---:|
| `EXCEPTION` | 147,066 |
| `MATCHED` | 36,061 |
| `UNMATCHED` | 949,162 |

本地覆盖：

| 覆盖项 | 行数 |
|---|---:|
| 已关联 `ErpShipmentSnapshot` | 183,419 |
| 已关联 `InvoiceChargeSnapshot` | 189,041 |
| 有 ERP Est | 183,127 |
| 有 System Est | 427 |

## 集合差异

| 维度 | InvoiceReader | CourieDelivery | 交集 | InvoiceReader 独有 | CourieDelivery 独有 |
|---|---:|---:|---:|---:|---:|
| Unique tracking | 781,075 | 879,506 | 325,003 | 456,072 | 554,503 |
| Unique order | 595,917 | 891,003 | 158,196 | 437,721 | 732,807 |
| Tracking + order pair | 630,252 | 910,075 | 172,819 | 457,433 | 737,256 |
| Invoice + tracking pair | 794,538 | 189,041 | 181,782 | - | - |

## 同 tracking 的映射差异

| 差异类型 | 数量 | 解释 |
|---|---:|---|
| 同 tracking 但 ERP/order 映射不同 | 142,264 | 两边都能找到 tracking，但 order 集合没有交集，需要检查字段口径或 normalize 规则 |
| InvoiceReader 有 order，本地同 tracking 没 order | 13 | 本地可能只导入了 invoice charge，未匹配 ERP shipment |
| 本地有 order，InvoiceReader 同 tracking 没 order | 7,872 | InvoiceReader result 表可能缺 order match 或字段选择不对 |

## 金额差异检查

金额字段是根据字段名推断的 `detail_amount_inc_gst`，只作为辅助检查，不直接代表最终实际运费口径。

| 项目 | 数量 |
|---|---:|
| 可按 tracking 对比金额 | 325,003 |
| 金额一致，误差 <= 0.01 | 93,556 |
| 金额不同 | 231,447 |

## 初步判断

1. 如果 `dbo.erp_match_results` 是 InvoiceReader 已有的 order match 结果表，它的覆盖范围和 CourieDelivery 当前 tracking-based reconciliation 不完全一致。
2. CourieDelivery 目前的主路径仍然是 `InvoiceChargeSnapshot.tracking_no -> ErpShipmentSnapshot.tracking_no`，再用 carrier/channel/service 消歧。
3. InvoiceReader 结果表可作为对账辅助来源，但不建议直接替代当前逻辑，除非确认它的 order 字段、tracking 字段、金额字段、GST 口径和多行聚合规则。
4. 下一步建议抽样核对 `same tracking but different order` 的明细，确认差异来自字段选择、tracking normalization、multi-package 分组，还是 InvoiceReader 结果表的历史匹配规则。

## 建议动作

- 将 `dbo.erp_match_results` 作为候选辅助匹配源新增到设计文档，但先不要直接改写生产同步逻辑。
- 对 `InvoiceReader 独有 tracking` 做二次检查：是否来自当前系统未导入的 `invoice_detail_*` 表、tracking 格式差异、或历史 invoice 不在本地 batch 范围。
- 对 `CourieDelivery 独有 tracking` 做二次检查：是否来自 CSV/XLSX 手动上传、本地重新同步后的 ERP shipment、或 InvoiceReader result 表未覆盖的 carrier。
- 如果业务确认 InvoiceReader result 表更可信，可新增一个 `invoice_reader_order_match_snapshot` 本地表保存其结果，再和 `InvoiceChargeSnapshot`、`ErpShipmentSnapshot` 三方比对，不要在请求时直接连 `.8`。
