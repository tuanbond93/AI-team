import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';
const root = new URL('../', import.meta.url);
const output = new URL('outputs/chief-auditor/', root);
const { tables, headers } = JSON.parse(await fs.readFile(new URL('.test-tmp-chief-template/demo.json', root), 'utf8'));
const wb = Workbook.create();
await fs.mkdir(output, { recursive: true });
for (const [name, columns] of Object.entries(headers)) {
  const sh = wb.worksheets.add(name);
  sh.showGridLines = false;
  const rows = tables[name].map(row => columns.map(c => row[c] ?? null));
  const area = sh.getRangeByIndexes(0, 0, rows.length+1, columns.length);
  area.values = [columns, ...rows];
  area.format.font = { name: 'Arial', size: 11, color: '#17304C' };
  area.format.rowHeight = 29;
  area.format.columnWidth = 18;
  area.format.verticalAlignment = 'center';
  area.format.wrapText = true;
  const header = sh.getRangeByIndexes(0, 0, 1, columns.length);
  header.format.fill = '#17304C';
  header.format.font = { name: 'Arial', size: 11, bold: true, color: '#FFFFFF' };
  header.format.rowHeight = 48;
  header.format.horizontalAlignment = 'center';
  sh.freezePanes.freezeRows(1);
  sh.freezePanes.freezeColumns(1);
  for (let col=0; col<columns.length; col++) {
    const range = sh.getRangeByIndexes(1, col, rows.length, 1);
    range.format.font = { name: 'Arial', size: 11, color: '#1455B0' };
    if (['weight','pieces','cartons','capacityKg','vehicleRateVnd','rateVnd','ratePerKgVnd','amountVnd','outsideCostsVnd','marginTargetPct','minKg','maxKg','stage'].includes(columns[col])) {
      range.setNumberFormat(['weight','capacityKg','minKg','maxKg'].includes(columns[col]) ? '#,##0.00;(#,##0.00);0.00' : '#,##0;(#,##0);0');
      range.format.horizontalAlignment = 'right';
    } else range.format.horizontalAlignment = 'left';
    if (['scopeId','roundingPolicy'].includes(columns[col])) sh.getRangeByIndexes(0,col,rows.length+1,1).format.columnWidth = 28;
    if (['populationComplete','completenessConfirmed'].includes(columns[col])) sh.getRangeByIndexes(0,col,rows.length+1,1).format.columnWidth = 26;
  }
  if (name === 'orders') {
    sh.getRange('E2:E100').dataValidation = { rule: { type: 'list', values: ['B2B','B2C'] } };
    sh.getRange('G2:G100').dataValidation = { rule: { type: 'list', values: ['g','kg'] } };
    sh.getRange('J2:J100').dataValidation = { rule: { type: 'list', values: ['pieces','cartons'] } };
  }
}
wb.recalculate();
for (const name of Object.keys(headers)) {
  const image = await wb.render({ sheetName: name, autoCrop: 'all', scale: 1, format: 'png' });
  await fs.writeFile(new URL(name+'.png', output), new Uint8Array(await image.arrayBuffer()));
}
console.log((await wb.inspect({kind:'sheet', include:'id,name', maxChars:1200})).ndjson);
console.log((await wb.inspect({kind:'match', searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!', options:{useRegex:true,maxResults:10}, maxChars:1000})).ndjson);
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(fileURLToPath(new URL('SYNTHETIC_CHIEF_INPUT.xlsx', output)));
