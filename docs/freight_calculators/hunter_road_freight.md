# Hunter Road Freight Calculation Logic

Last updated: 2026-06-12

## Scope

This document describes the current CourieDelivery implementation for Hunter Road Freight.

| Item | Value |
|---|---|
| Carrier code | `road_freight` |
| Carrier name | Hunter Road Freight |
| Data source | PostageCalculator plus approved Broers Hunter SYD override |
| Backend base calculator | `backend/freight/calculators/hunter_base.py` |

## Enabled Services

| Service | Rate card | Source table | Source procedure | Calculator |
|---|---|---|---|---|
| `HUNTER_MEL_2023` | `SP-HUNTER-MEL-2023` | `Hunter_Mel_Forward_Rate_Mapping2023` | `dbo.sp_Hunter_MEL_2023_Rate_Calculation` | `HunterMel2023Calculator` |
| `HUNTER_SYD_2025` | `SP-HUNTER-SYD-2025` | Broers Hunter SYD 20240920 applied over existing card | `apply_broers_hunter_sydney_rates` | `HunterSydney2025Calculator` |

Both services use Hunter surcharge rules imported from `HX_Item_Surcharge_Ref`, plus configurable Hunter fuel surcharge rows.

Important Sydney source note:

- The Sydney quote channel code remains `pc_hunter_syd_2025` so historical UI/configuration does not need to change.
- On 2026-06-12, Broers Hunter SYD 20240920 was compared against the previous Hunter Sydney 2025 rates.
- All 16,549 destination rows mapped successfully, but all 16,549 prices differed.
- Per user instruction, Broers Hunter SYD is now the source of truth for the existing `SP-HUNTER-SYD-2025` card.
- The previous Sydney 2025 rate data was backed up to `outputs\broers_rate_analysis\hunter_sydney_before_broers_apply_*.json`.
- Future `import_postagecalculator_rates` runs skip this approved local override unless `--overwrite-approved-overrides` is supplied.

## Imported Rate Template

The importer reads the PostageCalculator source table and creates CourieDelivery template rows:

- `RateCard`: one per service/version.
- `RateZone`: one row per deliverable destination row.
- `RateRule`: one row per unique combination of generated destination zone, origin zone, basic charge, per kg charge, minimum charge, and service.
- `SurchargeRule`: one row per source surcharge row from `HX_Item_Surcharge_Ref`.

Hunter zone mapping uses:

- `state`
- `Suburb`
- `postcode`
- `rate_card_type`
- `basic`
- `per_kg`
- `minimum_charge`

The app generates an internal `dest_zone` from source zone/rate values, then links each postcode/suburb/state row to that generated zone.

## Eligibility

Hunter is quoted only when all of these are true:

- selected platform is active
- selected warehouse is active
- `WarehousePlatform` link is enabled
- the Hunter service is enabled for the selected platform in `PlatformCarrier`
- the Hunter service is enabled for the selected warehouse in `WarehouseCarrier`
- quote channel is enabled
- rate card is active and effective for the quote date

If one of those links is missing, the channel is not considered selectable and the quote result will show a not-available reason at eligibility level.

## Weight And Cubic Logic

For each item line:

1. Convert dimensions from cm to metres.
2. Check the Hunter oversize rule:
   - length > 120 cm and width > 120 cm, or
   - height > 180 cm, or
   - length > 120 cm or width > 120 cm, and unit dead weight > 59 kg
3. Use cubic factor:
   - normal: `250`
   - oversize: `333`
4. Calculate unit cubic weight:

```text
unit_cubic_weight = length_m * width_m * height_m * cubic_factor
```

5. Calculate line weights:

```text
line_dead_weight = unit_weight_kg * qty
line_cubic_weight = unit_cubic_weight * qty
line_chargeable_weight = max(line_dead_weight, line_cubic_weight)
```

6. Sum all line chargeable weights.
7. Round the consignment chargeable weight up to the next whole kg.

```text
chargeable_weight_for_rate = ceil(sum(line_chargeable_weight))
```

The calculator also tracks:

- `max_item_charge_kg`: maximum of unit dead weight and unit cubic weight across all items.
- `longest_len_m`: maximum item length in metres.

These are used for residential and length surcharges.

## Zone Lookup

The destination is normalized to uppercase, then matched in this order:

1. exact `postcode + suburb + state`
2. exact `postcode + state`
3. postcode range where `postcode_from <= postcode <= postcode_to`, optionally restricted by state

If no zone is found, the result is not available with reason `rate_card_not_found` and trace stage `zone_lookup`.

## Base Freight

After zone lookup, the calculator finds a `RateRule` by:

- matching the rate card
- matching the Hunter service or a service-null fallback rule
- matching `to_zone`
- checking chargeable weight between `weight_min_kg` and `weight_max_kg`
- ordering by priority

Base freight formula:

```text
base = basic_charge + per_kg * chargeable_weight_for_rate
base = max(minimum_charge, base)
base = min(maximum_charge, base) when maximum_charge exists
```

## Surcharges

Hunter currently calculates these surcharge types:

| Code | Meaning | Input value |
|---|---|---|
| `FS` | Base fuel levy ratio | Always matched for this Hunter rate card |
| `FS_WA` | WA fuel levy ratio | Used instead of `FS` when destination state is `WA` |
| `RESI` | Residential surcharge | `max_item_charge_kg` |
| `LEN` | Length surcharge | `longest_len_m` |
| `UPLF` | Uplift surcharge | raw total chargeable weight before ceil |

Surcharge matching is threshold based. The implementation checks:

```text
min_threshold is null or value >= min_threshold
max_threshold is null or value < max_threshold
```

For `LEN`, if the matched rule name contains `GE_6`, the result carries a POA flag in the breakdown metadata.

## Fuel And GST

Hunter fuel is configured in `Pricing -> Surcharges` as rate-card surcharge rows:

| Code | Ratio | Meaning |
|---|---:|---|
| `FS` | `0.21` | Base fuel levy |
| `FS_WA` | `0.28` | WA fuel levy |

The calculator reads those ratios from `SurchargeRule`, so changing fuel no longer requires a code change.
`FS` and `FS_WA` are maintained as separate surcharge rows. At quote time the calculator selects one final fuel rate and writes one fuel line to the breakdown.

For non-WA destinations:

```text
fuel_rate = FS
fuel = (base + surcharge_total) * fuel_rate
```

For WA destinations:

```text
fuel_rate = FS_WA
fuel = (base + surcharge_total) * fuel_rate
```

GST:

```text
gst = (base + surcharge_total + fuel) * 10%
```

Final total:

```text
total_ex_gst = base + surcharge_total + fuel
total_inc_gst = total_ex_gst + gst
```

## Breakdown Lines

The quote result writes these lines to `quote_result_breakdown`:

- `BASE`: Hunter zone and chargeable kg
- `SURCHARGE`: Residential surcharge
- `SURCHARGE`: Length surcharge
- `SURCHARGE`: Uplift surcharge
- `FUEL`: Final selected fuel levy percentage
- `GST`: GST

The trace log also records:

- selected warehouse and platform
- selected channel and calculator file
- matched rate card
- matched destination zone
- actual, cubic, and chargeable weights
- triggered surcharge lines
- not-available reason when applicable

## Current Notes

- The app does not call the SQL Server stored procedure at quote time. The stored procedure is used as the source-of-truth reference; its table data is imported into CourieDelivery templates and evaluated by the Python calculator.
- `HUNTER_MEL_2023` is configured for the current Melbourne default quote path.
- `HUNTER_SYD_2025` has an active rate card and enabled channel, but it will only appear for a platform/warehouse once the corresponding `PlatformCarrier` and `WarehouseCarrier` links are configured.
