# Orange Connex eFN 2026

Last updated: 2026-06-02

This document describes how CourieDelivery imports and calculates the Orange Connex / eFN AU 2026 rate workbook:

- Source file: `C:\Users\KenHu\Downloads\2026 AU eFN rate.xlsx`

## Source Workbook

The workbook has three sheets:

| Sheet | Purpose |
|---|---|
| `PFL SYD` | Origin Sydney price table. |
| `PFL MEL` | Origin Melbourne price table. |
| `PFL Zone` | Destination suburb/postcode/state to price-card zone mapping. |

The price sheets are fixed-rate weight band tables. They are not per-kg linear rate tables.

## Imported Rate Cards

| Rate card | Source sheet | Origin | Channel |
|---|---|---|---|
| `Orange Connex eFN SYD 2026` | `PFL SYD` | `SYD` | `orange_efn_syd_2026` |
| `Orange Connex eFN MEL 2026` | `PFL MEL` | `MEL` | `orange_efn_mel_2026` |

Rates are valid until `2026-12-31` according to the workbook note.

## Data Model Mapping

| Source concept | CourieDelivery model | Notes |
|---|---|---|
| Orange Connex carrier | `Carrier` | Reuse existing Orange carrier if present; normalize display name to `Orange Connex`. |
| eFN service by origin | `CarrierService` | Separate MEL/SYD services keep warehouse eligibility clean. |
| PFL rate table | `RateCard` | Tax mode `EX_GST`, effective `2026-01-01` to `2026-12-31`. |
| `PFL Zone` row | `RateZone` | `dest_zone = 价卡分区`. |
| Weight band price | `RateRule` | One rule per destination zone and weight band. `basic_charge = fixed band price`, `per_kg = 0`. |

## Weight Bands

The workbook defines these bands:

| Band | Max grams |
|---|---:|
| Up to 250g | 250 |
| Up to 500g | 500 |
| 501g-1kg | 1000 |
| 1.01kg-2kg | 2000 |
| 2.01kg-3kg | 3000 |
| 3.01kg-4kg | 4000 |
| 4.01kg-5kg | 5000 |
| 5.01kg-7kg | 7000 |
| 7.01kg-10kg | 10000 |
| 10.01kg-15kg | 15000 |
| 15.01kg-22kg | 22000 |
| 22.01kg-25kg | 25000 |

CourieDelivery rounds each article's unit weight up to the next gram before band selection.

```text
article_weight_grams = ceil(unit_weight_kg * 1000)
```

The quote calculation is article-based:

```text
line_price = fixed_price_for(article_weight_grams, destination_zone) * qty
base_freight = sum(line_price)
gst = base_freight * 0.10
final_price = base_freight + gst
```

## Zone Lookup

Lookup order:

1. Exact match by `postcode + suburb + state`.
2. Fallback by `postcode + state` only if every matching row has the same price-card zone.
3. If postcode/state maps to multiple zones and suburb does not match, return `NOT_AVAILABLE` with reason `ambiguous_destination_zone`.
4. If no zone row matches, use the workbook's `Rest of AU` price row.

The `PFL Zone` sheet contains 2,766 source rows and 2,762 unique suburb/postcode/state rows.

## Limits And Other Charges

Workbook limits:

- Maximum weight: `25kg`
- Maximum length: `105cm`
- Maximum volume: `0.088 m3`

If any article exceeds these limits, the calculator returns `NOT_AVAILABLE`.

Other charges from the workbook are documented but not auto-applied in the first version:

- Redeliver or Redirect: cost of shipping + `$2.13` admin
- Missing Manifest Fee: `$26.60 / parcel`
- Return to Sender: `$11.70 / parcel`

These need operational event data before they can be applied safely.

## Import Command

Dry run:

```powershell
.\.venv\Scripts\python.exe backend\manage.py import_orange_connex_rates --dry-run
```

Import:

```powershell
.\.venv\Scripts\python.exe backend\manage.py import_orange_connex_rates --configure-defaults
```

Useful options:

```powershell
--rate-file "C:\Users\KenHu\Downloads\2026 AU eFN rate.xlsx"
--default-platform-code PI2022080502320043121506
--default-warehouse-code BG01
```

`--configure-defaults` links only the default platform and warehouse. It does not enable Orange Connex for every sales platform automatically.

