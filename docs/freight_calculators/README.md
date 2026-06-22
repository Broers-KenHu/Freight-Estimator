# Freight Calculator Documentation

Last updated: 2026-06-12

This folder documents the currently enabled freight calculation logic in CourieDelivery.

The live rate source is mainly SQL Server `192.168.72.8` database `PostageCalculator`, with approved local rate-package overrides. LSP rate staging data and LSP-derived rate templates have been purged from the application database. The PostageCalculator importer protects approved local overrides by default; use `--overwrite-approved-overrides` only when replacing an approved local source is intentional.

## Current Data Boundary

Kept:

- `RateCard`, `RateZone`, `RateRule`, and `SurchargeRule` rows where `metadata_json.source = "PostageCalculator"`.
- Approved local override rows, currently Hunter Sydney rates where `metadata_json.source = "BroersRatePackage"`.
- `QuoteChannel` rows where `quote_source` is `PostageCalculator` or an approved local rate source such as `BroersRatePackage`.
- Operational master data such as platforms, warehouses, SKU master, combo SKU snapshots, platform-carrier links, and warehouse-carrier links.

Removed:

- `LspRateTableCurrent`
- `LspRateTableArchive`
- LSP-derived `RateCard` rows
- LSP-derived `RateRule`, `RateZone`, and `SurchargeRule` rows
- `ImportJob` rows with type `LSP_RATE_TABLE_IMPORT`

The cleanup command is:

```powershell
.\.venv\Scripts\python.exe backend\manage.py purge_lsp_rate_data
```

## Current Enabled Quote Channels

| Channel | Carrier code | Service | Rate card | Calculator |
|---|---:|---|---|---|
| `pc_hunter_mel_2023` | `road_freight` | `HUNTER_MEL_2023` | `SP-HUNTER-MEL-2023` | `HunterMel2023Calculator` |
| `pc_hunter_syd_2025` | `road_freight` | `HUNTER_SYD_2025` | `SP-HUNTER-SYD-2025` | `HunterSydney2025Calculator` |
| `pc_allied_gro_2023_mel` | `758` | `GRO_2023_MEL` | `SP-ALLIED-GRO-MEL-2023` | `AlliedGro2023MelbourneCalculator` |
| `pc_allied_gro_2023_syd` | `758` | `GRO_2023_SYD` | `SP-ALLIED-GRO-SYD-2023` | `AlliedGro2023SydneyCalculator` |
| `pc_allied_b2c_2025_mel` | `758` | `B2C_2025_MEL` | `SP-ALLIED-B2C-MEL-2025` | `AlliedB2C2025MelbourneCalculator` |

DFE Feb 2025 proposal rates can be imported from the local proposal workbook and zone list:

| Channel | Carrier | Service | Rate card | Calculator |
|---|---|---|---|---|
| `dfe_ex_mel_2025` | Direct Freight Express | `DFE_KILO_EX_MEL_2025` | `DFE-EX-MEL-FEB-2025` | `DirectFreightExpress2025Calculator` |
| `dfe_ex_syd_2025` | Direct Freight Express | `DFE_KILO_EX_SYD_2025` | `DFE-EX-SYD-FEB-2025` | `DirectFreightExpress2025Calculator` |

Orange Connex eFN 2026 rates can be imported from the local rate workbook:

| Channel | Carrier | Service | Rate card | Calculator |
|---|---|---|---|---|
| `orange_efn_mel_2026` | Orange Connex | `ORANGE_EFN_MEL_2026` | `ORANGE-EFN-MEL-2026` | `OrangeConnexEfn2026Calculator` |
| `orange_efn_syd_2026` | Orange Connex | `ORANGE_EFN_SYD_2026` | `ORANGE-EFN-SYD-2026` | `OrangeConnexEfn2026Calculator` |

Hunter Sydney note:

- The channel code remains `pc_hunter_syd_2025` for compatibility.
- On 2026-06-12, the existing `SP-HUNTER-SYD-2025` rate card was overwritten with Broers Hunter SYD 20240920 rates after full comparison showed the local Broers SYD source should be the source of truth.
- Backup JSON was written under `outputs\broers_rate_analysis\hunter_sydney_before_broers_apply_*.json`.
- Future `import_postagecalculator_rates` runs skip this approved local override unless `--overwrite-approved-overrides` is supplied.

## Common Quote Flow

Manual Quote and historical quote reuse the same backend `QuoteEngine`.

1. The request is normalized into a `QuoteContext`.
2. If SKU mode is used, SKU dimensions and weight are snapshotted from `SKU`.
3. If combo SKU mode is used, the parent combo SKU is expanded into its component SKUs from `SKUComboComponent`; each component receives a calculation snapshot.
4. The engine checks platform and warehouse eligibility:
   - platform must be active
   - warehouse must be active
   - `WarehousePlatform` must be enabled
   - the service must be enabled in both `PlatformCarrier` and `WarehouseCarrier`
   - `QuoteChannel` must be enabled and date-valid
5. The channel's active `RateCard` is selected by carrier, service, effective date, status, and priority.
6. The carrier calculator performs zone lookup, chargeable weight calculation, base freight, surcharge, fuel, GST, and final total.
7. Global `AdjustmentRule` rows are applied after the carrier calculator.
8. The result is saved to:
   - `quote_request`
   - `quote_result`
   - `quote_result_breakdown`
   - `quote_trace_log`

## Courier Documents

- [Hunter Road Freight](./hunter_road_freight.md)
- [Allied Express](./allied_express.md)
- [Direct Freight Express 2025](./direct_freight_express_2025.md)
- [Orange Connex eFN 2026](./orange_connex_efn_2026.md)
