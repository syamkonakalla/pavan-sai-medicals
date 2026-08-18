// Paste into script.google.com as Code.gs, replace FOLDER_ID with your Drive folder's ID,
// then Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone).
// Put the resulting /exec URL into main.py's apps_script_url.

var FOLDER_ID = "PUT_YOUR_DRIVE_FOLDER_ID_HERE";

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    var folder = DriveApp.getFolderById(FOLDER_ID);
    var bytes = Utilities.base64Decode(payload.data);
    var blob = Utilities.newBlob(bytes, payload.mimeType, payload.filename);
    var file = folder.createFile(blob);

    return ContentService.createTextOutput(JSON.stringify({
      status: "success",
      fileId: file.getId(),
      url: file.getUrl()
    })).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({
      status: "error",
      message: err.toString()
    })).setMimeType(ContentService.MimeType.JSON);
  }
}
