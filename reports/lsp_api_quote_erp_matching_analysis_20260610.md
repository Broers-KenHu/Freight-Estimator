# LSP API Quote to ERP Matching Analysis

Date: 2026-06-10

## Purpose

Analyze whether LSP API quote rows can be matched back to ERP orders/shipments through LSP reference, shipment code, booking order, or tracking fields.

## Source Tables

- LSP quote header: `data_raw.lsp.lsp_openapi_quote_task`
- LSP quote task: `data_raw.lsp.lsp_quote_task`
- LSP booking: `data_raw.lsp.lsp_booking_order`
- ERP order: `data_raw.erp.hpoms_owner_order`
- ERP shipment detail: `data_raw.erp.hpoms_owner_order_shipment_detail`

## Confirmed Relationship

The previous importer only tried to match:

`lsp_quote_task.order_code -> erp.hpoms_scp_so_execute.from_order_no`

That path currently returns no matches for the OpenAPI quote set.

The usable path is:

`lsp_openapi_quote_task.quote_id -> lsp_quote_task.id`

then:

`lsp_quote_task.shipment_code -> lsp_booking_order.shipment_code`

then:

`lsp_booking_order.reference_no` or booking `shipment_code`
-> `erp.hpoms_owner_order.rd3_order_id / platform_reference_no / owner_order_no`

Package suffix normalization can be attempted for endings like `_1`, `_2`, `-P1`, but broad fuzzy matching should not be used.

## Current Counts After Backfill

- Local `LspApiQuoteSnapshot`: 25,405 rows
- Matched to local `HistoricalOrder`: 261 rows
- Rows with LSP booking tracking: 6,070
- Rows with ERP order number after matching: 261
- Rows with platform order number after matching: 261

## Source Mix

| LSP order prefix | Shipment pattern | Rows | Matched ERP orders | Tracking present | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| W-NSY | numeric | 19,099 | 0 | 0 | Standalone/API quote traffic; no ERP reference in available fields. |
| W-NSY | Axxx-date-seq | 4,202 | 11 | 4,009 | Booking exists for many rows, but ERP reference is usually absent. |
| LDX | other | 2,060 | 245 | 2,019 | Main ERP-matchable subset through booking reference/shipment code. |
| LDX | numeric | 43 | 5 | 42 | Small ERP-matchable subset. |
| W-NSY | other | 1 | 0 | 0 | No usable ERP bridge found. |

## Important Findings

- `lsp_quote_task.order_code` is usually an LSP/internal request order code, not an ERP order number.
- `lsp_quote_task.shipment_code` is the best bridge into `lsp_booking_order`.
- `lsp_booking_order.reference_no` is the best bridge into ERP order records.
- `lsp_booking_order.tracking_number` is useful display evidence, but it rarely matches ERP shipment tracking for these API quote rows.
- Most W-NSY rows should remain as historical API quote evidence unless LSP provides another ERP reference source.
- LDX rows are the main set that can currently be tied back to ERP order history.

## Implemented Importer Change

File: `backend/freight/management/commands/sync_lsp_api_quotes.py`

- Booking lookup now uses both LSP order code and shipment code.
- Booking payload no longer overwrites the original quote-task `lsp_order_code`.
- ERP matching now checks booking `reference_no` and booking `shipment_code` against ERP owner order references.
- Safe suffix normalization is applied for common package suffixes.

## Validation

- `python backend/manage.py check`: passed
- `python -m pytest freight/tests`: 43 passed
- Full LSP API quote backfill completed:
  - 25,405 snapshots updated
  - 370,886 quote option rows rebuilt
  - 261 snapshots matched to local ERP historical orders

