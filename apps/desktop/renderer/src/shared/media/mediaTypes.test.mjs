import test from "node:test";
import assert from "node:assert/strict";

import { attachmentDisplayName, attachmentFileName, attachmentMediaType, isDocumentAttachment, isUploadedAttachment } from "./mediaTypes.js";

test("multimedia attachments classify by extension", () => {
  assert.equal(attachmentMediaType(".lara/uploads/abc-image.png"), "image");
  assert.equal(attachmentMediaType("E:\\shot.JPG"), "image");
  assert.equal(attachmentMediaType("media/clip.webm"), "video");
  assert.equal(attachmentMediaType("media/clip.MOV"), "video");
  assert.equal(attachmentMediaType("media/voice.ogg"), "audio");
  assert.equal(attachmentMediaType("docs/report.pdf"), "file");
  assert.equal(attachmentMediaType("src/main.py"), "file");
  assert.equal(attachmentMediaType("noextension"), "file");
  assert.equal(attachmentMediaType(""), "file");
});

test("uploaded attachment paths recognize the workspace uploads area", () => {
  assert.equal(isUploadedAttachment(".lara/uploads/abc-image.png"), true);
  assert.equal(isUploadedAttachment(".lara\\uploads\\abc-image.png"), true);
  assert.equal(isUploadedAttachment(".LARA/uploads/abc-image.png"), true);
  assert.equal(isUploadedAttachment("shots/a.png"), false);
  assert.equal(isUploadedAttachment(".lara/uploads-backup/a.png"), false);
  assert.equal(isUploadedAttachment(""), false);
});

test("attachment file names survive mixed separators", () => {
  assert.equal(attachmentFileName(".lara/uploads/8efe283d6b70-image.png"), "8efe283d6b70-image.png");
  assert.equal(attachmentFileName("E:\\Projects\\lara\\notes.txt"), "notes.txt");
});

test("display names drop the stored upload id prefix but keep workspace names", () => {
  assert.equal(attachmentDisplayName(".lara/uploads/cf13446b10a7-OPERATOR-HANDOFF.private.md"), "OPERATOR-HANDOFF.private.md");
  assert.equal(attachmentDisplayName(".lara/uploads/8efe283d6b70-image.png"), "image.png");
  // Legacy uploads stored without the id prefix pass through unchanged.
  assert.equal(attachmentDisplayName(".lara/uploads/notes.txt"), "notes.txt");
  // Workspace attachments keep their own base name, even when it looks like an id prefix.
  assert.equal(attachmentDisplayName("shots/abc123def456-notes.md"), "abc123def456-notes.md");
  assert.equal(attachmentDisplayName(""), "");
});

test("document attachments detect text-like extensions", () => {
  assert.equal(isDocumentAttachment(".lara/uploads/9a54b9d005d2-pygent_b1643c6_review.md"), true);
  assert.equal(isDocumentAttachment("E:\\docs\\readme.MARKDOWN"), true);
  assert.equal(isDocumentAttachment("notes.txt"), true);
  assert.equal(isDocumentAttachment("docs/report.pdf"), true);
  assert.equal(isDocumentAttachment(".lara/uploads/abc-image.png"), false);
  assert.equal(isDocumentAttachment("media/clip.webm"), false);
  assert.equal(isDocumentAttachment("src/main.py"), false);
  assert.equal(isDocumentAttachment("noextension"), false);
  assert.equal(isDocumentAttachment(""), false);
});
