# Direct Freight Express 2025

Last updated: 2026-06-02

This document describes how CourieDelivery imports and calculates the Direct Freight Express rate proposal:

- Source rate proposal: `C:\Users\KenHu\Downloads\Direct Feight Express Rates Proposal EX SYD Ex MEL Feb 2025.xlsx`
- Source zone list: `C:\Users\KenHu\Downloads\Zone List - postcodes 1.csv`

The zone list file has a `.csv` extension, but the file content is an Excel workbook. The importer must detect and read it as XLSX.

## Import Scope

The DFE proposal contains two origin schedules:

| Rate card | Source sheet | Origin zone | Active calculator channel |
|---|---|---|---|
| `DFE EX MEL Feb 2025` | `Rate EX Mel` | `MELB` | `dfe_ex_mel_2025` |
| `DFE EX SYD Feb 2025` | `Ex SYD` | `SYDN` | `dfe_ex_syd_2025` |

The importer also reads:

- `Surcharge ` sheet destination surcharge table.
- DFE standard fuel levy from the rate proposal conditions. Current proposal value is `0.196`.
- Postcode/suburb/state zone mapping from the zone workbook.

The source workbook contains both `KILO` and `PALLET` rows. CourieDelivery imports both row types into the same origin rate card, but only the `KILO` quote channels are enabled by default. `PALLET` rows are retained for future use and remain disabled until order input supports pallet count/package type.

## Data Model Mapping

No new database schema is required for the first version.

| Source concept | CourieDelivery model | Notes |
|---|---|---|
| Direct Freight Express carrier | `Carrier` | Reuse existing Direct Freight carrier where possible. |
| KILO/PALLET service by origin | `CarrierService` | KILO active by default, PALLET disabled by default. |
| EX MEL / EX SYD proposal | `RateCard` | Status `ACTIVE`, tax mode `EX_GST`, cubic factor `250`. |
| Zone list row | `RateZone` | `dest_zone = CarrierZone`, with postcode/suburb/state and DropCode/SortCode in raw payload. |
| Rate row | `RateRule` | `to_zone = To`, `basic_charge = Basic Charge`, `per_kg = Rate`, `minimum_charge = Min Charge`. |
| Destination surcharge | `SurchargeRule` | Code `DFE_DEST`; matched by postcode and suburb. |
| Fuel levy | `SurchargeRule` | Code `FS`; ratio `0.196`; fuel applies to base freight plus fuel-applicable surcharges. |

## Zone Lookup

DFE depends on the supplied postcode zone list.

Lookup order:

1. Exact match by `postcode + suburb + state`.
2. Fallback by `postcode + state` only if every matching row has the same `CarrierZone`.
3. If postcode has multiple possible zones and the suburb does not match, return `NOT_AVAILABLE` with reason `ambiguous_destination_zone`.
4. If no row matches, return `NOT_AVAILABLE` with reason `rate_card_not_found`.

The checked source files currently show complete coverage:

- DFE rate table zones: 60.
- Zone list `CarrierZone` values: 60.
- Zone list imported rows after duplicate removal: 16,257.
- Missing zones from rate table: none.
- Rate zones without postcode mapping: none.

## Eligibility And Profile Rules

The proposal condition text says items outside this freight profile will be rejected:

- Any item greater than 30 kg.
- Two sides longer than 70 cm in the same parcel.
- Any item longer than 1.2 m.

CourieDelivery applies these as strict `NOT_AVAILABLE` rules in the first version.

The surcharge sheet includes long length and overweight surcharge descriptions, but that conflicts with the specific proposal profile text. Those rows are documented as reference/manual charges and are not applied automatically until the business confirms DFE accepts those parcels under this account/rate schedule.

## Chargeable Weight

For each quote:

```text
actual_kg = sum(unit_weight_kg * qty)
cubic_kg = sum(length_cm * width_cm * height_cm / 1,000,000 * 250 * qty)
chargeable_kg = ceil(max(actual_kg, cubic_kg))
```

The cubic conversion factor comes from the rate card and proposal rows. Current proposal value is `250`.

## Base Freight

For a KILO rule:

```text
base_freight = basic_charge + per_kg_rate * chargeable_kg
base_freight = max(base_freight, minimum_charge)
```

The matched rule is selected by:

- `RateRule.service = active DFE KILO service`.
- `RateRule.from_zone = origin zone`.
- `RateRule.to_zone = matched CarrierZone`.

## Surcharges

### Destination Surcharge

The destination surcharge table starts below the `DESTINATION SURCHARGE` heading in the `Surcharge ` sheet. Rows are imported with:

- `condition_json.postcode`
- `condition_json.suburb`
- `fee_amount`

The calculator applies the exact suburb/postcode fee when present. If there is no exact destination surcharge, the fee is `0`.

### Fuel Levy

Fuel levy is configured as a `SurchargeRule` with code `FS`.

```text
fuel = (base_freight + destination_surcharge) * fuel_rate
```

Current imported proposal value:

```text
fuel_rate = 0.196
```

This value must stay configurable from Pricing/Surcharges and must not be hard-coded into the calculator formula.

## GST And Total

DFE proposal rates are ex GST.

```text
total_ex_gst = base_freight + destination_surcharge + fuel
gst = total_ex_gst * 0.10
total_inc_gst = total_ex_gst + gst
```

## Breakdown And Trace Output

Every DFE quote result must expose:

- origin zone
- matched destination zone
- postcode/suburb/state lookup method
- actual kg
- cubic kg
- chargeable kg
- selected rate rule
- base freight
- destination surcharge
- fuel rate
- fuel amount
- GST
- final inc GST price
- profile rejection reason, if not available

These fields are stored in `QuoteCandidate.debug_breakdown`, `QuoteChargeLine`, and `QuoteTraceLog`.

## Import Command

Dry run:

```powershell
.\.venv\Scripts\python.exe backend\manage.py import_dfe_rates --dry-run
```

Import into CourieDelivery:

```powershell
.\.venv\Scripts\python.exe backend\manage.py import_dfe_rates --configure-defaults
```

Useful options:

```powershell
--rate-file "C:\Users\KenHu\Downloads\Direct Feight Express Rates Proposal EX SYD Ex MEL Feb 2025.xlsx"
--zone-file "C:\Users\KenHu\Downloads\Zone List - postcodes 1.csv"
--carrier-code 454
--default-platform-code PI2022080502320043121506
--default-warehouse-code BG01
```

`--configure-defaults` creates platform/warehouse carrier links for the default platform and warehouse only. It does not enable DFE for every sales platform automatically.

## Testing

Required checks:

1. Import dry-run returns expected source counts.
2. Exact postcode/suburb/state zone match returns the expected `CarrierZone`.
3. Ambiguous postcode-only lookup returns `NOT_AVAILABLE`.
4. Eligible KILO parcel calculates base, destination surcharge, fuel, GST, and final total.
5. Item profile violation returns `NOT_AVAILABLE`.
6. Freight Audit Matrix can show Direct Freight as `direct_freight`.
7. Breakdown and trace include zone, weights, surcharge, fuel, and rate-card details.
