import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const projectRoot = "C:/Users/KenHu/.vscode/CourieDelivery";
const inputPath = path.join(projectRoot, "outputs_invoice_couriers_raw.json");
const outputDir = path.join(projectRoot, "outputs", "invoice_courier_summary_20260602");
const outputPath = path.join(outputDir, "invoiceReader_courier_summary_20260602.xlsx");

const raw = JSON.parse((await fs.readFile(inputPath, "utf8")).replace(/^\uFEFF/, ""));

const asNumber = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const amountForSource = (sourceKey) => {
  const rows = raw.details.filter((row) => row.source_key === sourceKey);
  const amounts = rows
    .map((row) => asNumber(row.amount_inc_gst_total_configured))
    .filter((value) => value !== null);
  return amounts.length ? amounts.reduce((sum, value) => sum + value, 0) : null;
};

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const couriers = workbook.worksheets.add("Courier Sources");
const details = workbook.worksheets.add("Detail Tables");
const headers = workbook.worksheets.add("Header Contacts");

function writeTable(sheet, startCell, headers, rows) {
  const matrix = [headers, ...rows];
  sheet.getRange(startCell).write(matrix);
  const rowCount = matrix.length;
  const colCount = headers.length;
  const range = sheet.getRangeByIndexes(0, 0, rowCount, colCount);
  range.format.borders = { preset: "all", style: "thin", color: "#E5E7EB" };
  sheet.getRangeByIndexes(0, 0, 1, colCount).format = {
    fill: "#111827",
    font: { bold: true, color: "#FFFFFF" },
  };
  sheet.freezePanes.freezeRows(1);
  return range;
}

function setWidths(sheet, widths) {
  widths.forEach((width, index) => {
    sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidthPx = width;
  });
}

const courierRows = raw.couriers.map((row) => [
  row.source_key,
  row.courier_name,
  row.contact_name,
  row.mapping_status,
  row.gst_basis,
  row.existing_detail_tables,
  row.row_count,
  row.invoice_count,
  row.consignment_count,
  row.freight_account_count,
  row.service_count,
  amountForSource(row.source_key),
  row.detail_tables,
]);

summary.getRange("A1").values = [["invoiceReader Courier Source Summary"]];
summary.getRange("A1:M1").merge();
summary.getRange("A1").format = {
  fill: "#111827",
  font: { bold: true, color: "#FFFFFF", size: 16 },
};
summary.getRange("A2:M2").merge();
summary.getRange("A2").values = [[
  "Generated from SQL Server invoiceReader using documented invoice detail mappings. Courier source count is based on detail tables with rows; header contact count is shown separately.",
]];
summary.getRange("A2").format = { font: { color: "#475569" }, wrapText: true };

const summaryRows = [
  ["Generated at", raw.summary.generated_at],
  ["Database", raw.summary.database],
  ["Distinct courier sources with detail rows", raw.summary.distinct_courier_count_with_rows],
  ["Distinct header contacts/vendors", raw.headers.length],
  ["Existing detail tables with rows", raw.summary.total_existing_detail_tables],
  ["Total invoice detail rows", raw.summary.total_detail_rows],
  ["Configured source definitions", raw.summary.source_definition_count],
];
summary.getRange("A4:B10").values = summaryRows;
summary.getRange("A4:A10").format = { fill: "#F8FAFC", font: { bold: true, color: "#334155" } };
summary.getRange("B4:B10").format = { font: { color: "#0F172A" } };
summary.getRange("A4:B10").format.borders = { preset: "all", style: "thin", color: "#E5E7EB" };
summary.getRange("B6:B10").format.numberFormat = "#,##0";

const topRows = raw.couriers
  .map((row) => [row.courier_name, row.row_count])
  .sort((a, b) => b[1] - a[1]);
summary.getRange("D4:E4").values = [["Courier source", "Detail rows"]];
summary.getRange("D5:E16").values = topRows;
summary.getRange("D4:E4").format = { fill: "#111827", font: { bold: true, color: "#FFFFFF" } };
summary.getRange("D4:E16").format.borders = { preset: "all", style: "thin", color: "#E5E7EB" };
summary.getRange("E5:E16").format.numberFormat = "#,##0";
const chart = summary.charts.add("bar", summary.getRange("D4:E16"));
chart.title = "Detail Rows by Courier Source";
chart.hasLegend = false;
chart.xAxis = { axisType: "textAxis" };
chart.yAxis = { numberFormatCode: "#,##0" };
chart.setPosition("G4", "M22");
setWidths(summary, [280, 150, 24, 210, 110, 24, 120, 120, 120, 120, 120, 120, 120]);

writeTable(
  couriers,
  "A1",
  [
    "Source Key",
    "Courier Source",
    "Header Contact",
    "Mapping Status",
    "GST Basis",
    "Detail Tables",
    "Rows",
    "Invoices",
    "Consignments",
    "Freight Accounts",
    "Services",
    "Configured Amount Total",
    "Detail Table Names",
  ],
  courierRows,
);
couriers.getRange("G2:L13").format.numberFormat = "#,##0";
couriers.getRange("L2:L13").format.numberFormat = "$#,##0.00";
setWidths(couriers, [185, 210, 260, 110, 90, 100, 90, 90, 110, 125, 90, 145, 520]);
couriers.tables.add("A1:M13", true, "CourierSourcesTable");

const detailRows = raw.details.map((row) => [
  row.source_key,
  row.courier_name,
  row.detail_table,
  row.table_exists,
  row.mapping_status,
  row.amount_basis,
  row.amount_column,
  row.row_count,
  row.invoice_count,
  row.consignment_count,
  row.freight_account_count,
  row.service_count,
  row.amount_inc_gst_total_configured,
  row.sample_freight_accounts,
  row.sample_services,
]);
writeTable(
  details,
  "A1",
  [
    "Source Key",
    "Courier Source",
    "Detail Table",
    "Exists",
    "Mapping Status",
    "Amount Basis",
    "Amount Column",
    "Rows",
    "Invoices",
    "Consignments",
    "Freight Accounts",
    "Services",
    "Configured Amount Total",
    "Sample Freight Accounts",
    "Sample Services",
  ],
  detailRows,
);
details.getRange(`H2:M${detailRows.length + 1}`).format.numberFormat = "#,##0";
details.getRange(`M2:M${detailRows.length + 1}`).format.numberFormat = "$#,##0.00";
setWidths(details, [180, 210, 270, 70, 110, 90, 150, 90, 90, 110, 125, 90, 145, 320, 500]);
details.tables.add(`A1:O${detailRows.length + 1}`, true, "DetailTablesTable");

const headerRows = raw.headers.map((row) => [
  row.contact_name || "(blank)",
  row.header_rows,
  row.invoice_count,
  row.header_amount_total,
]);
writeTable(headers, "A1", ["Header Contact", "Header Rows", "Invoice Count Field", "Header Amount Total"], headerRows);
headers.getRange(`B2:D${headerRows.length + 1}`).format.numberFormat = "#,##0";
headers.getRange(`D2:D${headerRows.length + 1}`).format.numberFormat = "$#,##0.00";
setWidths(headers, [320, 110, 140, 160]);
headers.tables.add(`A1:D${headerRows.length + 1}`, true, "HeaderContactsTable");

for (const sheet of [summary, couriers, details, headers]) {
  sheet.showGridLines = false;
  const used = sheet.getUsedRange();
  used.format.font = { name: "Arial", size: 10 };
  used.format.verticalAlignment = "Top";
}

await fs.mkdir(outputDir, { recursive: true });

const preview = await workbook.render({
  sheetName: "Summary",
  autoCrop: "all",
  scale: 1,
  format: "png",
});
await fs.writeFile(path.join(outputDir, "summary_preview.png"), new Uint8Array(await preview.arrayBuffer()));

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, summary: raw.summary, headerContactCount: raw.headers.length }));
