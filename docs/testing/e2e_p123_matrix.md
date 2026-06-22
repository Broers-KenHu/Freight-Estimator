# P1/P2/P3 E2E Test Matrix

Last updated: 2026-06-12

The frontend E2E suite uses Playwright and a deterministic mocked API. It validates the full React application in a browser while avoiding failures caused by live database drift.

## P1 Critical Business Flow

- Manual Quote default manual-dimension quote.
- Quote result sorting: available rows first, lowest inc-GST price highlighted.
- Quote result outer table shows total inc GST only; base/fuel/surcharge/GST appear inside breakdown.
- Breakdown filters zero-value charge lines.
- Trace tab exposes warehouse, zone, and calculation details.
- ERP / Platform Order lookup brings ERP order, platform order, tracking, ERP carrier, ERP estimate inc GST, LSP quote context, and SKU snapshots into Manual Quote.
- Invoice Reconciliation review drawer compares invoice actual inc GST, system estimate inc GST, and ERP estimate converted to inc GST.
- Invoice Reconciliation Excel export action.
- Freight Audit Matrix row and detail drawer, including tracking-grouped carrier cards and build request payload.

## P2 Operational Configuration

- Rate Card list renders current solved rate templates: Allied, Hunter Broers SYD, DFE.
- Rate Card search is server-side, not only current page filtering.
- Carrier Master displays carrier names, including normalized `Direct Freight Express`.
- Rate Card create drawer exposes version, effective dates, GST, and metadata fields.

## P3 Usability And Layout

- SKU autocomplete displays category and SKU aligned with `||` separator.
- SKU autocomplete can show combo SKU candidates and full product description.
- Manual Quote primary controls remain usable at 1366x768, 1920x1080, and 3840x2160.
- Freight Audit detail drawer remains wide enough on notebook viewport and keeps tracking detail sections readable.

## Commands

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\frontend
npm run test:e2e
npm run test:e2e:p1
npm run test:e2e:p2
npm run test:e2e:p3
```
