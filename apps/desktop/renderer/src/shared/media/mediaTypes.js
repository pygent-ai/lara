const UPLOADS_PREFIX = ".lara/uploads/";

const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico"]);
const VIDEO_EXTENSIONS = new Set([".mp4", ".webm", ".mov", ".avi", ".mkv"]);
const AUDIO_EXTENSIONS = new Set([".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"]);
const DOCUMENT_EXTENSIONS = new Set([".md", ".markdown", ".mdx", ".txt", ".pdf"]);

// Multimedia attachments render as thumbnails or icons instead of file-name
// text; everything else keeps the generic paperclip chip.
export function attachmentMediaType(path) {
  const name = attachmentFileName(path);
  const extension = name.slice(name.lastIndexOf(".")).toLowerCase();
  if (name.lastIndexOf(".") === -1 || extension === name) return "file";
  if (IMAGE_EXTENSIONS.has(extension)) return "image";
  if (VIDEO_EXTENSIONS.has(extension)) return "video";
  if (AUDIO_EXTENSIONS.has(extension)) return "audio";
  return "file";
}

export function isDocumentAttachment(path) {
  const name = attachmentFileName(path);
  const dot = name.lastIndexOf(".");
  if (dot === -1) return false;
  return DOCUMENT_EXTENSIONS.has(name.slice(dot).toLowerCase());
}

export function isUploadedAttachment(path) {
  return String(path || "").replace(/\\/g, "/").toLowerCase().startsWith(UPLOADS_PREFIX);
}

export function attachmentFileName(path) {
  return String(path || "").split(/[\\/]/).pop() || String(path || "");
}

// Stored uploads are saved as "<12-hex id>-<name>" so repeated pastes of the
// same file name do not collide; chips display the original name instead of
// the storage name. Workspace attachments keep their own base name.
const UPLOAD_ID_PREFIX = /^[0-9a-f]{12}-/;

export function attachmentDisplayName(path) {
  const name = attachmentFileName(path);
  return isUploadedAttachment(path) ? name.replace(UPLOAD_ID_PREFIX, "") : name;
}
