// remark-gfm replacement that drops only its autolink-literal extension.
// That extension scans from "http://" to the next whitespace, so a URL glued
// to CJK text swallows the closing "**" into the link and the bold pair can
// never match (rendered as literal asterisks with an over-long link).
// Tables, strikethrough, task lists, and footnotes still come from the GFM
// micromark extensions below; URLs are linkified by this module instead, with
// explicit boundaries: markdown markers (*_~), CJK punctuation, and quotes
// never become part of a URL, and trailing punctuation/unbalanced brackets
// are trimmed the way GFM does.
import { gfmFootnoteFromMarkdown } from "mdast-util-gfm-footnote";
import { gfmFootnote } from "micromark-extension-gfm-footnote";
import { gfmStrikethrough } from "micromark-extension-gfm-strikethrough";
import { gfmTable } from "micromark-extension-gfm-table";
import { gfmTaskListItem } from "micromark-extension-gfm-task-list-item";
import { gfmStrikethroughFromMarkdown } from "mdast-util-gfm-strikethrough";
import { gfmTableFromMarkdown } from "mdast-util-gfm-table";
import { gfmTaskListItemFromMarkdown } from "mdast-util-gfm-task-list-item";

// RFC-style URL characters minus markup hazards: no "*", "_", "~" would keep
// common underscored paths out, so they are allowed here and trimmed from the
// tail instead; "<", ">", quotes, backticks, and any non-ASCII (hence CJK)
// character end the match.
const URL_PATH_CHARS = "[A-Za-z0-9%/$&'()+,;=?#!.:@\\[\\]*_~-]";
const DOMAIN_LABEL = "[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?";
const URL_PATTERN = new RegExp(
  `https?://${DOMAIN_LABEL}(?:\\.${DOMAIN_LABEL})*(?::\\d{1,5})?(?:[/?#]${URL_PATH_CHARS}*)?`
    + `|www\\.${DOMAIN_LABEL}(?:\\.${DOMAIN_LABEL})+(?::\\d{1,5})?(?:[/?#]${URL_PATH_CHARS}*)?`,
  "g",
);
const EMAIL_PATTERN = /[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}/g;
const TRAILING_TRIM = "*_~.,;:!?";

export default function remarkGfmNoAutolink() {
  const data = this.data();
  const add = (field, value) => {
    (data[field] || (data[field] = [])).push(value);
  };
  // One extension per entry: the combiners walk entries with for..in, so a
  // nested array would be silently ignored.
  add("micromarkExtensions", gfmFootnote());
  add("micromarkExtensions", gfmStrikethrough());
  add("micromarkExtensions", gfmTable());
  add("micromarkExtensions", gfmTaskListItem());
  add("fromMarkdownExtensions", gfmFootnoteFromMarkdown());
  add("fromMarkdownExtensions", gfmStrikethroughFromMarkdown());
  add("fromMarkdownExtensions", gfmTableFromMarkdown());
  add("fromMarkdownExtensions", gfmTaskListItemFromMarkdown());
  return linkifyAst;
}

function linkifyAst(tree) {
  linkifyChildren(tree);
}

function linkifyChildren(node) {
  const children = node.children;
  if (!Array.isArray(children)) {
    return;
  }
  for (let index = children.length - 1; index >= 0; index -= 1) {
    const child = children[index];
    if (child.type === "text") {
      const parts = linkifyText(String(child.value || ""));
      if (parts.length > 1) {
        children.splice(index, 1, ...parts);
      }
      continue;
    }
    // Links never nest, so existing link text is left untouched. Code lives
    // in leaf nodes (code/inlineCode), which text walking never reaches.
    if (child.type !== "link" && child.type !== "linkReference") {
      linkifyChildren(child);
    }
  }
}

function linkifyText(value) {
  const parts = [];
  let cursor = 0;
  for (const match of value.matchAll(URL_PATTERN)) {
    const start = match.index;
    const raw = match[0];
    if (raw.startsWith("www.") && start > 0 && /[A-Za-z0-9./]/.test(value[start - 1])) {
      continue;
    }
    const url = trimTrailingPunctuation(raw);
    if (start > cursor) {
      parts.push({ type: "text", value: value.slice(cursor, start) });
    }
    parts.push({
      type: "link",
      title: null,
      url: raw.startsWith("www.") ? `http://${url}` : url,
      children: [{ type: "text", value: url }],
    });
    cursor = start + url.length;
  }
  if (!parts.length) {
    return linkifyEmails([{ type: "text", value }]);
  }
  if (cursor < value.length) {
    parts.push({ type: "text", value: value.slice(cursor) });
  }
  return linkifyEmails(parts);
}

function linkifyEmails(parts) {
  const out = [];
  for (const part of parts) {
    if (part.type !== "text") {
      out.push(part);
      continue;
    }
    let cursor = 0;
    for (const match of part.value.matchAll(EMAIL_PATTERN)) {
      if (match.index > cursor) {
        out.push({ type: "text", value: part.value.slice(cursor, match.index) });
      }
      out.push({
        type: "link",
        title: null,
        url: `mailto:${match[0]}`,
        children: [{ type: "text", value: match[0] }],
      });
      cursor = match.index + match[0].length;
    }
    if (cursor < part.value.length) {
      out.push({ type: "text", value: part.value.slice(cursor) });
    }
  }
  return out;
}

function trimTrailingPunctuation(url) {
  let end = url.length;
  for (;;) {
    const ch = url[end - 1];
    if (end > 0 && TRAILING_TRIM.includes(ch)) {
      end -= 1;
      continue;
    }
    if (ch === ")" || ch === "]") {
      const opening = ch === ")" ? "(" : "[";
      const segment = url.slice(0, end);
      if (countChar(segment, ch) > countChar(segment, opening)) {
        end -= 1;
        continue;
      }
    }
    break;
  }
  return url.slice(0, end);
}

function countChar(text, ch) {
  let count = 0;
  for (const current of text) {
    if (current === ch) {
      count += 1;
    }
  }
  return count;
}
