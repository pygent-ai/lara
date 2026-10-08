const UPLOADS_PREFIX = ".lara/uploads/";

const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico"]);
const VIDEO_EXTENSIONS = new Set([".mp4", ".webm", ".mov", ".avi", ".mkv"]);
const AUDIO_EXTENSIONS = new Set([".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"]);

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

export function isUploadedAttachment(path) {
  return String(path || "").replace(/\\/g, "/").toLowerCase().startsWith(UPLOADS_PREFIX);
}

export function attachmentFileName(path) {
  return String(path || "").split(/[\\/]/).pop() || String(path || "");
}
