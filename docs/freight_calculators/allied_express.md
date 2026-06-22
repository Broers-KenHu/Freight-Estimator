# Allied Express Calculation Logic

Last updated: 2026-05-28

## Scope

This document describes the current CourieDelivery implementation for Allied Express.

| Item | Value |
|---|---|
| Carrier code | `758` |
| Carrier name | Allied Express |
| Data source | SQL Server `192.168.72.8`, database `PostageCalculator` |
| GRO calculator | `backend/freight/calculators/allied_gro_2023_melbourne.py` |
| B2C calculator | `backend/freight/calculators/allied_b2c_2025_melbourne.py` |

## Enabled Services

| Service | Rate card | Source table | Source procedure | Calculator |
|---|---|---|---|---|
| `GRO_2023_MEL` | `SP-ALLIED-GRO-MEL-2023` | `Allied_Mel_Forward_Rate_Mapping2023` | `dbo.sp_AlliedGRO2023_order_Rate_Calculation` | `AlliedGro2023MelbourneCalculator` |
| `GRO_2023_SYD` | `SP-ALLIED-GRO-SYD-2023` | `Allied_Syd_Forward_Rate_Mapping2023` | `dbo.sp_AlliedGRO2023_Sydney_Rate_Calculation` | `AlliedGro2023SydneyCalculator` |
| `B2C_2025_MEL` | `SP-ALLIED-B2C-MEL-2025` | `Allied_Mel_Forward_Rate_Mapping2025` | `dbo.sp_AlliedB2C2025_order_Rate_Calculation` | `AlliedB2C2025MelbourneCalculator` |

GRO services use surcharge table `Allied_GRO_Item_Surcharge2023`.

B2C service uses surcharge table `Allied_B2C_Item_Surcharge2025`.

GRO also imports on-forward reference data from `Allied_GRO_Forward_Zone_Surcharge2023`.

## Imported Rate Template

The importer reads the PostageCalculator source table and creates:

- `RateCard`: one per service/version.
- `RateZone`: one row per deliverable destination row.
- `RateRule`: one row per unique combination of generated destination zone, origin zone, basic charge, per kg charge, minimum charge, and service.
- `SurchargeRule`: one row per surcharge threshold.

Allied zone mapping uses:

- `State`
- `Suburb`
- `Postcode`
- `From Zone`
- `To Zone`
- `Basic_Charge`
- `Per_Kilogram`
- `Minimun_Charge`

The app generates an internal `dest_zone` from source zone/rate values. For GRO, if a row in `Allied_GRO_Forward_Zone_Surcharge2023` matches `State + Postcode + Suburb`, that source row is snapshotted into the `RateZone.raw_payload.on_forward` field.

## Eligibility

Allied is quoted only when all of these are true:

- selected platform is active
- selected warehouse is active
- `WarehousePlatform` link is enabled
- the Allied service is enabled for the selected platform in `PlatformCarrier`
- the Allied service is enabled for the selected warehouse in `WarehouseCarrier`
- quote channel is enabled
- rate card is active and effective for the quote date

## Common Zone And Rule Lookup

The destination is normalized to uppercase, then matched in this order:

1. exact `postcode + suburb + state`
2. exact `postcode + state`
3. postcode range where `postcode_from <= postcode <= postcode_to`, optionally restricted by state

After zone match, a `RateRule` is selected by rate card, service, destination zone, chargeable weight, and priority.

Base rule formula:

```text
base = basic_charge + per_kg * chargeable_weight
base = max(minimum_charge, base)
base = min(maximum_charge, base) when maximum_charge exists
```

## Allied GRO 2023 Logic

This logic applies to:

- `GRO_2023_MEL`
- `GRO_2023_SYD`

### Weight Calculation

The calculator uses cubic factor `250`.

```text
dead_weight = round(sum(unit_weight_kg * qty))
cubic_weight = round(sum(length_cm * width_cm * height_cm / 1,000,000 * 250 * qty))
chargeable_weight = max(dead_weight, cubic_weight)
```

Rounding uses half-up whole-number rounding.

### Base Freight

The selected rate rule is applied to the consignment chargeable weight:

```text
linehaul = basic_charge + per_kg * chargeable_weight
linehaul = max(minimum_charge, linehaul)
```

### On-Forward Delivery

If the destination matched `Allied_GRO_Forward_Zone_Surcharge2023`, the calculator adds:

```text
on_forward_base = on_forward_basic + on_forward_per_kg * chargeable_weight
```

If no on-forward row matched:

```text
on_forward_base = 0
```

On-forward also doubles the home delivery surcharge:

```text
home_delivery_multiplier = 2 when on_forward matched, otherwise 1
```

### Item-Level Surcharges

For each item:

```text
longest = round(longest side in cm)
middle = round(middle side in cm)
qty = item qty
```

The calculator checks these surcharge codes:

| Code | Meaning | Input |
|---|---|---|
| `LSC` | Length surcharge | longest side |
| `WS` | Width surcharge | current implementation uses longest side |
| `DHSL` | Depot handling by length | longest side |
| `DHSW` | Depot handling by weight | unit dead weight |

For each item:

```text
item_surcharge_for_line = max(LSC, WS, DHSL, DHSW) * qty
```

The line values are summed into `item_surcharge`.

### Two Person Crew

The two-person crew surcharge is calculated by hard-coded legacy thresholds:

```text
124.80 when longest >= 240 and middle >= 130 and (dead >= 76 or cubic >= 151)
78.00  when 190 <= longest <= 239 and middle >= 130 and (dead >= 56 or cubic >= 111)
49.92  when 130 <= longest <= 189 and middle >= 90  and (dead >= 47 or cubic >= 92)
0      otherwise
```

The maximum two-person crew value across items is used.

### Home Delivery Surcharge

The calculator chooses either dead-weight or cubic-weight home delivery surcharge:

```text
home_delivery = HDD(dead_weight) when dead_weight >= cubic_weight
home_delivery = HDC(cubic_weight) when cubic_weight > dead_weight
home_delivery = home_delivery * home_delivery_multiplier
```

### Chosen Legacy Surcharge

Only the largest of these three surcharge buckets is charged:

```text
chosen_surcharge = max(home_delivery, item_surcharge, two_person_crew)
```

### Fuel, GST, And Total

Fuel ratio comes from surcharge code `FS`.

Current imported GRO fuel ratio:

```text
FS = 1.2679
```

The formula is:

```text
subtotal = linehaul + on_forward_base + chosen_surcharge
gst = subtotal * 10%
fuel_amount = subtotal * (1 + 10%) * (fuel_ratio - 1)
total_inc_gst = subtotal * (1 + 10%) * fuel_ratio
total_ex_gst = subtotal + fuel_amount
```

## Allied B2C 2025 Logic

This logic applies to:

- `B2C_2025_MEL`

### Weight Calculation

The calculator uses cubic factor `250`.

For each item:

```text
cubic_single = length_cm * width_cm * height_cm / 1,000,000 * 250
chargeable_single = max(unit_weight_kg, cubic_single)
consignment_weight += chargeable_single * qty
```

### Base Freight

The rate rule is selected using `consignment_weight`.

Then the base charge is calculated per piece:

```text
per_piece = basic_charge + per_kg * chargeable_single
per_piece = max(minimum_charge, per_piece)
basic_total += per_piece * qty
```

This means the consignment finds the rate band once, but each item line is charged per piece using its own single-piece chargeable weight.

### Oversize Item Surcharge

The calculator uses surcharge code `OIS`.

For each item:

```text
rounded_longest_side = round(longest side in cm)
ois_total += OIS(rounded_longest_side) * qty
```

### Overweight Surcharge

The calculator uses surcharge code `OWS`.

```text
rounded_consignment_weight = round(consignment_weight)
ows_total = OWS(rounded_consignment_weight)
```

### Fuel And Total

Fuel ratio comes from surcharge code `FS`.

Current imported B2C fuel ratio:

```text
FS = 0.27
```

The formula is:

```text
subtotal = basic_total + ois_total + ows_total
fuel_amount = subtotal * fuel_ratio
total = subtotal * (1 + fuel_ratio)
```

The current B2C calculator stores:

```text
total_ex_gst = total
gst_amount = 0
total_inc_gst = total
```

This mirrors the current Python implementation and should be reviewed if the business decides B2C outputs must be separated into ex-GST and GST components.

## Breakdown Lines

GRO writes:

- `BASE`: Allied linehaul
- `BASE`: Allied on-forward delivery
- `SURCHARGE`: chosen legacy surcharge
- `FUEL`: fuel ratio amount
- `GST`: GST

B2C writes:

- `BASE`: per-piece effective base
- `SURCHARGE`: OIS surcharge
- `SURCHARGE`: OWS surcharge
- `FUEL`: fuel ratio amount

The trace log records:

- selected warehouse and platform
- selected channel and calculator file
- matched rate card
- matched destination zone
- dead, cubic, and chargeable weights
- selected surcharge bucket
- fuel ratio
- not-available reason when applicable

## Current Notes

- The app does not call SQL Server stored procedures at quote time. The stored procedures are used as source-of-truth references; their table data is imported into CourieDelivery templates and evaluated by the Python calculators.
- `GRO_2023_MEL` and `B2C_2025_MEL` are configured for the current Melbourne default quote path.
- `GRO_2023_SYD` has an active rate card and enabled channel, but it will only appear for a platform/warehouse once the corresponding `PlatformCarrier` and `WarehouseCarrier` links are configured.

