// Paste/drop helpers for the chat composer. Pure duck-typing over
// DataTransfer so node --test can exercise them without a DOM.

export function filesFromDataTransfer(dataTransfer) {
  const items = dataTransfer?.items;
  if (!items) return [];
  const files = [];
  for (const item of items) {
    if (item.kind !== "file") continue;
    const file = item.getAsFile?.();
    if (file) files.push(file);
  }
  return files;
}

export function dataTransferHasFiles(dataTransfer) {
  return Array.from(dataTransfer?.types || []).includes("Files");
}
