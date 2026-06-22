# IPEC Zone Mapping Source Check - 2026-06-18

## Summary

Found a usable public Toll IPEC postcode/suburb zone mapping candidate:

- Source article: Maropost/Neto "Team Global Express IPEC Shipping Setup"
- Downloaded file: `https://neto.com.au/assets/docs/8007/TollIPEC-ShippingZones.csv`
- Local raw copy: `outputs/ipec_zone_lookup/TollIPEC-ShippingZones.csv`
- Local normalized copy: `outputs/ipec_zone_lookup/TollIPEC-ShippingZones.normalized.csv`

This source is not a direct Team Global Express account-specific rate document, but the zone naming matches the UBI Toll IPEC rate table zone set after normalizing suffixes such as `WQLD 60.52` to `WQLD`.

## Website Findings

Maropost/Neto explicitly documents an IPEC shipping zone import workflow and links an IPEC zones CSV. The same page states that postcodes and nearby suburbs are grouped into shipping zones, then rate costs are associated with those zones.

A separate public Toll IPEC zone map PDF was also found. It uses the same historical Toll IPEC zone vocabulary, for example `NCQLD`, `WQLD`, `CNSW`, `SEQLD`, `IVIC`, `OVIC`, `SYD`, `MEL`, `PER`, `ADL`, and postcode ranges.

## CSV Structure

Raw headers:

- `Country`
- `Courier`
- `From Post Code`
- `To Post Code`
- `Zone Code`
- `Zone Name`
- `City/Suburb`

Observed raw row count:

- 16,361 rows
- 2,876 unique postcodes
- 14,826 unique suburbs
- 63 raw zone code strings
- 42 normalized IPEC destination zones

Normalization rule:

- Extract the leading zone token from `Zone Code`.
- Examples:
  - `MEL1` -> `MEL1`
  - `NT 3.81` -> `NT`
  - `WQLD 60.52` -> `WQLD`

The 42 normalized zones exactly cover the UBI IPEC rate table destination zones:

`ABX`, `ADL`, `ASP`, `BNE`, `BRM`, `CBR`, `CCQLD`, `CNS`, `CNSW`, `CWA`, `DRW`, `HBA`, `HRNSW`, `IVIC`, `LST`, `MEL1`, `MEL2`, `MKY`, `MTG`, `NCNSW`, `NCQLD`, `NT`, `NTL`, `NWA`, `NWSA`, `OSQLD`, `OVIC`, `PER`, `RNSW`, `ROK`, `SCNSW`, `SEQLD`, `SESA`, `SWA`, `SYD1`, `SYD2`, `TAS`, `TSV`, `WNSW`, `WOL`, `WQLD`, `YORK`.

## Validation Against UBI Toll IPEC Billing

Compared the normalized public CSV against UBI `toll/*.xlsx` billing rows where billing includes `City/Suburb`, `Postcode`, and `To Zone`.

Results:

- UBI Toll workbooks checked: 42
- Billing rows checked: 70,813
- Exact postcode + suburb found in CSV: 70,111
- Exact match to billing `To Zone`: 69,834
- Postcode-unique fallback found: 600
- Postcode-unique fallback match: 588
- Missing postcode in CSV: 102
- Mismatches: 289

Overall:

- Matched rows: 70,422 / 70,813 = about 99.45%
- Covered rows: 70,711 / 70,813 = about 99.86%

Top mismatch themes:

- `SYD1` vs `SYD2`
- `DRW` vs `NT`
- `MEL2` vs `MEL1`
- `SWA` vs `PER`
- `ABX` vs `OVIC`

These are likely boundary/suburb updates or customer/account-specific differences. They should be handled as review exceptions or explicit overrides, not silently ignored.

## LSP Findings

`data_raw.lsp` was checked beyond the previously synced local tables.

Relevant findings:

- `lsp_carrier_rate` contains `AU.TGE.PRIORITY.PRO` rate rows for MEL/SYD origins, but not IPEC zone mapping.
- `lsp_carrier_zone` contains no Toll/IPEC/TGE zone rows.
- `lsp_platform_zone` exists but currently has 0 rows.
- `lsp_openapi_quote_task`, `lsp_quote_task`, `lsp_quote_task_job`, and `lsp_quote_task_job_log` contain TGE/IPEC historical quote records, but no reusable postcode/suburb -> IPEC zone master data.
- `lsp_carrier_onforwarding` contains `UBI.AU2AU.IPEC` on-forwarding fee rows with from/to postcode and suburb. This is surcharge/on-forwarding reference data, not a base IPEC zone master.
- `lsp_tracking` and `mv_shipment_detail` contain actual shipment/location rows. These are historical operational facts, not master mapping.

## Recommendation

Use the Maropost/Neto `TollIPEC-ShippingZones.csv` as the initial IPEC zone mapping candidate, but import it as reviewed/reference-driven data rather than official TeamGE account master data.

Suggested implementation:

1. Add an IPEC import command that creates `RateZone` rows from the normalized CSV.
2. Store source metadata on each `RateZone.raw_payload`:
   - `source_url`
   - `raw_zone_code`
   - `raw_zone_name`
   - `normalized_zone`
   - `source_confidence = PUBLIC_NETO_REFERENCE`
3. Import UBI IPEC price rows as DRAFT/REVIEW first.
4. Add override support for mismatches found from UBI billing:
   - exact postcode + suburb override
   - source `UBI billing observed To Zone`
   - effective date if inferable from invoice date
5. Only enable full Manual Quote calculation after calculator test cases pass for:
   - CSV mapping lookup
   - billing mismatch override
   - MEL1/SYD1 origin selection by warehouse state
   - `max(minimum_charge, basic + freight_charge * chargeable_kg)`
   - fuel/GST/oversize breakdown

## Current Decision

The previous "Toll IPEC has no complete mapping" conclusion should be softened:

- There is no complete IPEC zone mapping inside the current CourieDelivery database.
- There is no complete IPEC zone mapping inside LSP `lsp_carrier_zone`.
- A public Maropost/Neto Toll IPEC zone CSV is available and aligns strongly with UBI IPEC billing.
- It is good enough for a DRAFT import and calculator development, with mismatch/override review before production activation.

## Implementation Applied

Implemented on 2026-06-18:

- Import command: `backend/freight/management/commands/import_ubi_ipec_rates.py`
- Calculator: `backend/freight/calculators/ubi_tge_ipec.py`
- Tests: `backend/freight/tests/test_ubi_tge_ipec.py`

Command run:

```powershell
python manage.py import_ubi_ipec_rates --open-all-access
```

Import result:

- Rate cards created/updated: 4
  - `UBI-IPEC-20250707-MEL1`
  - `UBI-IPEC-20250707-SYD1`
  - `UBI-IPEC-20260420-MEL1`
  - `UBI-IPEC-20260420-SYD1`
- Rate rules: 168
- Zone rows per rate card: 16,460
- Billing override rows added to public mapping: 99
- Missing zone mappings after public CSV + billing overrides: 0
- Open access links added:
  - 37 platform-carrier links
  - 8 warehouse-carrier links

Calculator behavior:

- Destination zone lookup:
  1. exact postcode + suburb + state
  2. postcode + state only when it resolves to one zone
  3. UBI billing override rows take precedence over public CSV rows
- Chargeable kg: `ceil(max(actual_kg, cubic_kg))`
- Cubic factor: 250
- Base formula: `max(minimum_charge, basic_charge + freight_charge_per_kg * max(0, chargeable_kg - kg_included_in_basic))`
- Fuel: configurable `SurchargeRule` code `FS`, imported default `0.099`
- GST: `RateCard.gst_rate`, currently `0.10`

Verification:

- Dry run showed both IPEC versions had zero missing destination-zone mappings.
- Targeted tests passed:

```powershell
python -m pytest freight\tests\test_ubi_tge_ipec.py freight\tests\test_direct_freight_express.py freight\tests\test_orange_connex.py
```

Result: `8 passed`.
