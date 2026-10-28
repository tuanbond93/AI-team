/**
 * Xuất 2 tab Booking và Raw ra CSV + manifest vào một thư mục Google Drive của chính owner.
 * Chạy bằng tài khoản công ty của owner (quyền Viewer là đủ). Không gửi dữ liệu ra ngoài Google,
 * không dùng cookie/API ẩn. App local (ops_team.drive_import) kiểm hash, header và nguồn đã ghim.
 *
 * Cài: Project Settings → Script Properties, đặt các khóa dưới đây (lấy đúng giá trị trong /plan của bot):
 *   BOOKING_SPREADSHEET_ID, BOOKING_GID, BOOKING_TAB, BOOKING_RANGE   (vd. 'DC PHÚ THỌ'!A1:AK10000)
 *   RAW_SPREADSHEET_ID, RAW_GID, RAW_TAB, RAW_RANGE                   (vd. 'raw_current'!A1:Y100000)
 *   FOLDER_ID   (thư mục Drive được Google Drive for desktop đồng bộ về máy chạy bot)
 * Rồi chạy installTrigger() một lần và cấp quyền khi Google hỏi.
 */
var WINDOW_START = '12:35';  // giờ Việt Nam; báo cáo 13:00 cần bản xuất mới trong 15 phút
var WINDOW_END = '12:58';
var TZ = 'Asia/Ho_Chi_Minh';

function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'scheduledExport') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('scheduledExport').timeBased().everyMinutes(5).create();
}

function scheduledExport() {
  var now = Utilities.formatDate(new Date(), TZ, 'HH:mm');
  if (now < WINDOW_START || now > WINDOW_END) return;  // tiết kiệm quota ngoài khung giờ
  exportNow();
}

function exportNow() {
  var p = PropertiesService.getScriptProperties().getProperties();
  var folder = DriveApp.getFolderById(required_(p, 'FOLDER_ID'));
  var exportedAt = Utilities.formatDate(new Date(), TZ, "yyyy-MM-dd'T'HH:mm:ssXXX");
  var files = {
    booking: exportTab_(folder, 'booking.csv', p, 'BOOKING', 37),
    raw: exportTab_(folder, 'raw.csv', p, 'RAW', 25)
  };
  // Manifest ghi cuối cùng: app chỉ nhập khi hash trong manifest khớp file đã đồng bộ.
  writeFile_(folder, 'supra-export-manifest.json',
    JSON.stringify({schema: 'supra-drive-export-v1', exported_at: exportedAt, files: files}));
}

function exportTab_(folder, name, p, prefix, columns) {
  var id = required_(p, prefix + '_SPREADSHEET_ID'), gid = Number(required_(p, prefix + '_GID'));
  var tab = required_(p, prefix + '_TAB'), range = required_(p, prefix + '_RANGE');
  var sheet = SpreadsheetApp.openById(id).getSheets().filter(function (s) { return s.getSheetId() === gid; })[0];
  if (!sheet || sheet.getName() !== tab) throw new Error('Sai tab/gid cho ' + prefix);
  var a1 = range.replace(/^.*!/, '');
  var values = sheet.getRange(a1).getDisplayValues();
  if (values[0].length !== columns) throw new Error('Range ' + prefix + ' phải đúng ' + columns + ' cột');
  var last = values.length;
  while (last > 1 && values[last - 1].every(function (v) { return v === ''; })) last--;
  values = values.slice(0, last);
  var rows = values.slice(1).filter(function (r) { return r.some(function (v) { return v !== ''; }); }).length;
  var csv = values.map(function (r) { return r.map(csvCell_).join(','); }).join('\r\n') + '\r\n';
  writeFile_(folder, name, csv);
  return {name: name, spreadsheet_id: id, tab: tab, gid: gid, range: range, sha256: sha256_(csv), rows: rows};
}

function csvCell_(v) {
  v = String(v);
  return /[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}

function sha256_(text) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8)
    .map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
}

function writeFile_(folder, name, content) {
  var existing = folder.getFilesByName(name);
  if (existing.hasNext()) existing.next().setContent(content);
  else folder.createFile(name, content, name.slice(-4) === '.csv' ? 'text/csv' : 'application/json');
}

function required_(p, key) {
  if (!p[key]) throw new Error('Thiếu Script Property ' + key);
  return p[key];
}
