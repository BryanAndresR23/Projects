import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

function parseNdjson(ndjson) {
  return String(ndjson || "").split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
}

function normalized(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().replace(/[^A-Z0-9]+/g, " ").trim();
}

function loanKey(value) {
  let key = normalized(value).replace(/^CAF /, "CFA ");
  key = key.replace(/\s+EC$/, "");
  return key;
}

function canonical(value) {
  const text = normalized(value);
  if (!text || text === "N A") return "";
  if (/CITI/.test(text)) return "CITIBANK";
  if (/FED|FEDERAL/.test(text)) return "FEDERAL";
  if (/J P MORGAN|JP MORGAN|JPMORGAN/.test(text)) return "JPMORGAN";
  if (/ESPANA/.test(text)) return "BANCO DE ESPAÑA";
  if (/COMMERZ/.test(text)) return "COMMERZBANK";
  if (/FLAR/.test(text)) return "FLAR";
  return "";
}

function findHeader(headers, aliases) {
  const normalizedHeaders = headers.map(normalized);
  for (const alias of aliases) {
    const index = normalizedHeaders.findIndex((header) => header === alias || header.includes(alias));
    if (index >= 0) return index;
  }
  return -1;
}

function addCount(target, key, correspondent) {
  if (!key || !correspondent) return;
  target[key] ||= {};
  target[key][correspondent] = (target[key][correspondent] || 0) + 1;
}

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("Uso: correspondent_history_reader.mjs <xlsx> <json>");
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheets = parseNdjson((await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 6000 })).ndjson)
  .filter((item) => item.kind === "sheet" && /^20\d{2}$/.test(String(item.name || "")))
  .map((item) => item.name)
  .sort();

const rows = [];
const byLoan = {};
const byProfile = {};
const byLender = {};
const totals = {};
for (const sheetName of sheets) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const inspected = await workbook.inspect({ kind: "table", sheetId: sheetName, include: "id,name,range", maxChars: 6000 });
  const record = parseNdjson(inspected.ndjson).find((item) => item.kind === "table" && item.sheet === sheetName);
  if (!record) continue;
  const endAddress = String(record.address).split(":").pop();
  const endRow = Number(String(endAddress).match(/\d+$/)?.[0]);
  const endColumn = String(endAddress).replace(/\d+$/, "");
  const values = await Promise.resolve(sheet.getRange(`A1:${endColumn}${endRow}`).values);
  const headers = values[0] || [];
  const indexes = {
    loan: findHeader(headers, ["REFERENCE LOAN", "REFERENCIA PRESTAMO", "PRESTAMO"]),
    lender: findHeader(headers, ["LENDER", "ACREEDOR"]),
    borrower: findHeader(headers, ["BORROWER", "PRESTATARIO", "ORDENANTE"]),
    currency: findHeader(headers, ["CURRENCY", "MONEDA"]),
    correspondent: findHeader(headers, ["CORRESPONSAL"]),
  };
  if (indexes.loan < 0 || indexes.correspondent < 0) continue;
  for (let index = 1; index < values.length; index += 1) {
    const row = values[index] || [];
    const loan = String(row[indexes.loan] || "").trim().toUpperCase();
    const lender = indexes.lender >= 0 ? String(row[indexes.lender] || "").trim().toUpperCase() : "";
    const borrower = indexes.borrower >= 0 ? String(row[indexes.borrower] || "").trim().toUpperCase() : "";
    const currency = indexes.currency >= 0 ? String(row[indexes.currency] || "").trim().toUpperCase() : "";
    const correspondent = canonical(row[indexes.correspondent]);
    if (!loan || !correspondent) continue;
    const entry = { sheet: sheetName, row: index + 1, loan, loan_key: loanKey(loan), lender, borrower, currency, correspondent };
    rows.push(entry);
    addCount(byLoan, entry.loan_key, correspondent);
    addCount(byProfile, [normalized(lender), normalized(borrower), currency].join("|"), correspondent);
    addCount(byLender, [normalized(lender), currency].join("|"), correspondent);
    totals[correspondent] = (totals[correspondent] || 0) + 1;
  }
}

const result = {
  generated_at: new Date().toISOString(), source: inputPath, source_modified_ms: (await fs.stat(inputPath)).mtimeMs,
  sheets, rows: rows.length, totals, by_loan: byLoan, by_profile: byProfile, by_lender: byLender,
  recent: rows.slice(-60),
};
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, JSON.stringify(result, null, 2), "utf8");
process.stdout.write(JSON.stringify({ ok: true, output: outputPath, rows: rows.length, sheets, totals }));
