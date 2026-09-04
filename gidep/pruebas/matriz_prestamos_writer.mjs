import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";


function parseNdjson(ndjson) {
  return String(ndjson || "")
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .flatMap((line) => {
      try {
        return [JSON.parse(line)];
      } catch {
        return [];
      }
    });
}


function rowFromAddress(address) {
  const end = String(address || "").split(":").pop();
  const match = String(end || "").match(/(\d+)$/);
  if (!match) throw new Error(`No se pudo interpretar el rango de tabla: ${address}`);
  return Number(match[1]);
}


function asDate(value, label) {
  const text = String(value || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    throw new Error(`${label} debe tener formato AAAA-MM-DD.`);
  }
  const [year, month, day] = text.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (Number.isNaN(date.getTime())) throw new Error(`${label} no es una fecha válida.`);
  return date;
}


async function openWorkbook(inputPath) {
  return SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
}


async function tableRecord(workbook, sheetName) {
  const inspected = await workbook.inspect({
    kind: "table",
    sheetId: sheetName,
    include: "id,name,range",
    maxChars: 6000,
  });
  const records = parseNdjson(inspected.ndjson).filter(
    (record) => record.kind === "table" && record.sheet === sheetName,
  );
  if (records.length !== 1) {
    throw new Error(
      `Se esperaba una sola tabla en la hoja ${sheetName}; se encontraron ${records.length}.`,
    );
  }
  return records[0];
}


async function exactMatches(workbook, term) {
  const normalized = String(term || "").trim().toUpperCase();
  if (!normalized) return [];
  const inspected = await workbook.inspect({
    kind: "match",
    searchTerm: normalized,
    options: { useRegex: false, maxResults: 100 },
    maxChars: 10000,
  });
  return parseNdjson(inspected.ndjson).filter(
    (record) =>
      record.kind === "match" &&
      String(record.value || "").trim().toUpperCase() === normalized,
  );
}


async function inspectCommand(inputPath) {
  const workbook = await openWorkbook(inputPath);
  const sheets = parseNdjson(
    (
      await workbook.inspect({
        kind: "sheet",
        include: "id,name",
        maxChars: 6000,
      })
    ).ndjson,
  ).filter((record) => record.kind === "sheet");
  const currentYear = String(new Date().getFullYear());
  const selected = sheets.some((sheet) => sheet.name === currentYear)
    ? currentYear
    : String(sheets.at(-1)?.name || "");
  const table = await tableRecord(workbook, selected);
  return {
    ok: true,
    workbook: inputPath,
    sheets: sheets.map((sheet) => sheet.name),
    active_sheet: selected,
    table_address: table.address,
    data_rows: Math.max(0, rowFromAddress(table.address) - 1),
  };
}


async function appendCommand(inputPath, entryPath, outputPath, previewPath = "") {
  const entry = JSON.parse(await fs.readFile(entryPath, "utf8"));
  const required = [
    "entry_date",
    "reference_loan",
    "refer_swift",
    "lender",
    "borrower",
    "currency",
    "value_date",
    "status",
    "user",
  ];
  const missing = required.filter((key) => !String(entry[key] ?? "").trim());
  if (missing.length) throw new Error(`Faltan campos obligatorios: ${missing.join(", ")}`);

  const workbook = await openWorkbook(inputPath);
  const sheetName = String(entry.value_date).slice(0, 4);
  const sheet = workbook.worksheets.getItem(sheetName);
  const tableInfo = await tableRecord(workbook, sheetName);
  const currentLastRow = rowFromAddress(tableInfo.address);
  const newRow = currentLastRow + 1;

  const swiftMatches = await exactMatches(workbook, entry.refer_swift);
  if (swiftMatches.length) {
    throw new Error(
      `DUPLICADO: la referencia ${entry.refer_swift} ya existe en ${swiftMatches[0].sheet}!${swiftMatches[0].address}.`,
    );
  }
  if (entry.local_operation) {
    const localMatches = await exactMatches(workbook, entry.local_operation);
    if (localMatches.length) {
      throw new Error(
        `DUPLICADO: la operación ${entry.local_operation} ya existe en ${localMatches[0].sheet}!${localMatches[0].address}.`,
      );
    }
  }

  const amountUsd = entry.amount_usd === null || entry.amount_usd === ""
    ? null
    : Number(entry.amount_usd);
  const amountOther = entry.amount_other === null || entry.amount_other === ""
    ? null
    : Number(entry.amount_other);
  if (amountUsd !== null && !Number.isFinite(amountUsd)) throw new Error("Amount USD no es numérico.");
  if (amountOther !== null && !Number.isFinite(amountOther)) throw new Error("Amount Other Currencies no es numérico.");
  if (amountUsd === null && amountOther === null) throw new Error("Debe existir un monto USD u otra moneda.");

  const table = sheet.tables.items[0];
  table.rows.add(null, [[
    asDate(entry.entry_date, "Fecha Ingreso SGI"),
    String(entry.reference_loan).trim(),
    String(entry.refer_swift).trim().toUpperCase(),
    String(entry.local_operation || "").trim().toUpperCase() || null,
    String(entry.lender).trim().toUpperCase(),
    String(entry.borrower).trim().toUpperCase(),
    amountUsd,
    amountOther,
    String(entry.currency).trim().toUpperCase(),
    String(entry.correspondent || "").trim().toUpperCase() || null,
    entry.other_currency_accounting_date
      ? asDate(entry.other_currency_accounting_date, "Contabilizar Otras Monedas")
      : null,
    asDate(entry.value_date, "Value Date"),
    String(entry.status).trim(),
    String(entry.notes || "").trim() || null,
    String(entry.user).trim().toLowerCase(),
    null,
    null,
  ]]);

  // artifact-tool conserva la tabla, pero no siempre hereda los formatos de
  // número de la fila anterior al agregar una fila. Los fijamos expresamente.
  sheet.getRange(`A${newRow}`).format.numberFormat = "d-mmm";
  sheet.getRange(`G${newRow}`).format.numberFormat = '_("$"* #,##0.00_);_("$"* \\(#,##0.00\\);_("$"* "-"??_);_(@_)';
  sheet.getRange(`H${newRow}`).format.numberFormat = "#,##0.00";
  sheet.getRange(`K${newRow}:L${newRow}`).format.numberFormat = "d-mmm";

  sheet.getRange(`P${newRow}`).formulas = [[
    `="Favor procesar con fecha valor " & TEXT(L${newRow},"dd/mm/yyyy") & IF(D${newRow}<>""," Operaciones No. "," Operacion No. ") & C${newRow} & IF(D${newRow}<>""," y " & D${newRow},"")`,
  ]];
  sheet.getRange(`Q${newRow}`).formulas = [[
    `="Atendido mediante " & IF(D${newRow}<>"","mensajes SWIFT ","mensaje SWIFT ") & C${newRow} & IF(D${newRow}<>""," y " & D${newRow},"") & " de " & TEXT(A${newRow},"dd") & " de " & CHOOSE(MONTH(A${newRow}),"enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre") & " de " & YEAR(A${newRow})`,
  ]];

  const added = await workbook.inspect({
    kind: "region",
    sheetId: sheetName,
    range: `A${newRow}:Q${newRow}`,
    include: "values,formulas",
    maxChars: 10000,
    tableMaxRows: 2,
    tableMaxCols: 17,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "final formula error scan",
    maxChars: 12000,
  });
  const errorRecords = parseNdjson(errors.ndjson).filter((record) => record.kind === "match");
  if (errorRecords.length) {
    throw new Error(`La verificación detectó ${errorRecords.length} error(es) de fórmula.`);
  }

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);

  if (previewPath) {
    const preview = await workbook.render({
      sheetName,
      range: `A${Math.max(1, newRow - 4)}:Q${newRow}`,
      scale: 1.7,
      format: "png",
    });
    await fs.mkdir(path.dirname(previewPath), { recursive: true });
    await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
  }

  const receipt = {
    ok: true,
    output: outputPath,
    sheet: sheetName,
    row: newRow,
    reference: entry.refer_swift,
    verification: parseNdjson(added.ndjson),
    formula_errors: 0,
  };
  await fs.writeFile(`${outputPath}.receipt.json`, JSON.stringify(receipt), "utf8");
  return receipt;
}


async function repairCommand(inputPath, entryPath, outputPath, previewPath = "") {
  const entry = JSON.parse(await fs.readFile(entryPath, "utf8"));
  const workbook = await openWorkbook(inputPath);
  const sheetName = String(entry.value_date).slice(0, 4);
  const sheet = workbook.worksheets.getItem(sheetName);
  const matches = (await exactMatches(workbook, entry.refer_swift)).filter(
    (record) => /^C\d+$/.test(String(record.address || "")),
  );
  if (matches.length !== 1) {
    throw new Error(`Se esperaba una sola fila para ${entry.refer_swift}; se encontraron ${matches.length}.`);
  }
  const row = rowFromAddress(matches[0].address);
  sheet.getRange(`A${row}`).values = [[asDate(entry.entry_date, "Fecha Ingreso SGI")]];
  sheet.getRange(`L${row}`).values = [[asDate(entry.value_date, "Value Date")]];
  sheet.getRange(`A${row}`).format.numberFormat = "d-mmm";
  sheet.getRange(`L${row}`).format.numberFormat = "d-mmm";
  sheet.getRange(`G${row}`).format.numberFormat = '_("$"* #,##0.00_);_("$"* \\(#,##0.00\\);_("$"* "-"??_);_(@_)';
  sheet.getRange(`H${row}`).format.numberFormat = "#,##0.00";

  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    maxChars: 12000,
  });
  const errorRecords = parseNdjson(errors.ndjson).filter((record) => record.kind === "match");
  if (errorRecords.length) throw new Error(`La verificación detectó ${errorRecords.length} error(es) de fórmula.`);

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
  if (previewPath) {
    const preview = await workbook.render({ sheetName, range: `A${Math.max(1, row - 4)}:Q${row}`, scale: 1.7, format: "png" });
    await fs.mkdir(path.dirname(previewPath), { recursive: true });
    await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
  }
  return { ok: true, output: outputPath, sheet: sheetName, row, formula_errors: 0 };
}


async function main() {
  const [command, inputPath, third, fourth, fifth] = process.argv.slice(2);
  if (!command || !inputPath) {
    throw new Error("Uso: matriz_prestamos_writer.mjs inspect <xlsx> | append <xlsx> <entry.json> <output.xlsx> [preview.png]");
  }
  const result = command === "inspect"
    ? await inspectCommand(inputPath)
    : command === "append"
      ? await appendCommand(inputPath, third, fourth, fifth || "")
      : command === "repair"
        ? await repairCommand(inputPath, third, fourth, fifth || "")
      : (() => { throw new Error(`Comando no reconocido: ${command}`); })();
  process.stdout.write(JSON.stringify(result));
}


main().catch((error) => {
  process.stderr.write(JSON.stringify({ ok: false, error: String(error?.message || error) }));
  process.exitCode = 2;
});
