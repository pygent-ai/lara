import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Code, Copy, Download, Workflow, X } from "lucide-react";
import { copyContextValue, useCopiedFeedback } from "./clipboard.js";
import { svgToPngBlob } from "./imageBlob.js";

// Visual parity with the previous hand-rolled renderer: the language label on
// fenced blocks and the copy button. ```mermaid fences instead render the
// diagram; they fall back to this plain code block until the source parses,
// so streaming output shows as code and never flashes an error.
export function MarkdownPre({ children }) {
  const code = Array.isArray(children) ? children[0] : children;
  const className = String(code?.props?.className || "");
  const language = /(?:^|\s)language-([\w+.#-]+)/.exec(className)?.[1] || "";
  const codeText = codeElementText(code?.props?.children).replace(/\n$/, "");
  if (language === "mermaid") {
    return <MermaidDiagram language={language} code={codeText}>{children}</MermaidDiagram>;
  }
  return <MarkdownCodeBlock language={language} codeText={codeText}>{children}</MarkdownCodeBlock>;
}

function MarkdownCodeBlock({ language, codeText, headAction, children }) {
  return (
    <pre className="markdown-code">
      {(language || codeText) && (
        <span className="markdown-code-head">
          {language && <span className="markdown-code-language">{language}</span>}
          <span className="markdown-code-actions">
            {headAction}
            <MarkdownCodeCopy text={codeText} />
          </span>
        </span>
      )}
      {children}
    </pre>
  );
}

// react-markdown hands the fenced body over as nested text nodes; flatten
// them back into the raw code string for the clipboard.
function codeElementText(value) {
  if (value == null || value === true || value === false) return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(codeElementText).join("");
  if (typeof value === "object" && value.props) return codeElementText(value.props.children);
  return "";
}

function MarkdownCodeCopy({ text }) {
  const [copied, markCopied] = useCopiedFeedback();
  return (
    <button
      type="button"
      className="markdown-code-copy"
      aria-label={copied ? "代码已复制" : "复制代码"}
      onClick={() => {
        copyContextValue(text);
        markCopied();
      }}
    >
      {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
    </button>
  );
}

function MermaidDiagram({ language, code, children }) {
  // view is the reader's preference; until the source parses (or after the
  // reader explicitly switched) the code fallback shows instead.
  const [view, setView] = useState("diagram");
  const [rendered, setRendered] = useState(null);
  const [notice, setNotice] = useState("");
  const [zoomUrl, setZoomUrl] = useState("");
  const [zoomScale, setZoomScale] = useState(1);
  const [zoomSize, setZoomSize] = useState(null);
  const noticeTimerRef = useRef(null);
  const frameRef = useRef(null);
  const lightboxRef = useRef(null);
  useEffect(() => () => window.clearTimeout(noticeTimerRef.current), []);
  useEffect(() => () => { if (zoomUrl) URL.revokeObjectURL(zoomUrl); }, [zoomUrl]);
  useEffect(() => {
    if (!zoomUrl) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") setZoomUrl("");
    };
    window.addEventListener("keydown", onKey);
    // React registers wheel as passive, so the non-passive listener goes on
    // the lightbox directly: wheel zooms instead of scrolling the lightbox.
    const lightbox = lightboxRef.current;
    const onWheel = (event) => {
      event.preventDefault();
      setZoomScale((current) => Math.min(8, Math.max(0.4, current * (event.deltaY < 0 ? 1.2 : 1 / 1.2))));
    };
    lightbox?.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      window.removeEventListener("keydown", onKey);
      lightbox?.removeEventListener("wheel", onWheel);
    };
  }, [zoomUrl]);
  useEffect(() => {
    let live = true;
    renderMermaid(code).then((svg) => {
      if (live && svg) setRendered({ code, svg });
    });
    return () => {
      live = false;
    };
  }, [code]);

  function flashNotice(text) {
    setNotice(text);
    window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setNotice(""), 1400);
  }

  async function diagramPngBlob() {
    const svg = frameRef.current?.querySelector("svg");
    if (!svg) throw new Error("diagram is not rendered");
    return svgToPngBlob(svg);
  }

  async function copyImage() {
    try {
      const blob = await diagramPngBlob();
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      flashNotice("已复制到剪贴板");
    } catch {
      flashNotice("复制失败");
    }
  }

  async function saveImage() {
    try {
      const url = URL.createObjectURL(await diagramPngBlob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `lara-mermaid-${Date.now()}.png`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      flashNotice("已保存图片");
    } catch {
      flashNotice("保存失败");
    }
  }

  async function openZoom() {
    try {
      setZoomScale(1);
      setZoomSize(null);
      setZoomUrl(URL.createObjectURL(await diagramPngBlob()));
    } catch {
      flashNotice("无法放大");
    }
  }

  // scale 1 keeps the default max-width/height fit; beyond that the box is
  // derived from the PNG's natural size (both axes, and the CSS caps off —
  // an uncapped width with a capped height would squash the image).
  function zoomedImageStyle() {
    if (!zoomSize || zoomScale === 1) return undefined;
    const fit = Math.min(
      (window.innerWidth * 0.88) / zoomSize.width,
      (window.innerHeight * 0.86) / zoomSize.height,
      1,
    );
    return {
      width: Math.round(zoomSize.width * fit * zoomScale),
      height: Math.round(zoomSize.height * fit * zoomScale),
      maxWidth: "none",
      maxHeight: "none",
    };
  }

  const diagramReady = rendered?.code === code;
  if (diagramReady && view === "diagram") {
    return (
      <div className="markdown-mermaid" ref={frameRef}>
        {/* mermaid serializes with securityLevel "strict", so the SVG is
            DOMPurify-sanitized and carries no active content. */}
        <div
          className="markdown-mermaid-graphic"
          dangerouslySetInnerHTML={{ __html: rendered.svg }}
          onClick={() => void openZoom()}
        />
        <span className="media-image-actions" onClick={(event) => event.stopPropagation()}>
          <button type="button" className="media-image-action" title="查看源码" aria-label="查看源码" onClick={() => setView("source")}>
            <Code size={13} aria-hidden="true" />
          </button>
          <button type="button" className="media-image-action" title="复制图片" aria-label="复制图片" onClick={() => void copyImage()}>
            <Copy size={13} aria-hidden="true" />
          </button>
          <button type="button" className="media-image-action" title="保存图片" aria-label="保存图片" onClick={() => void saveImage()}>
            <Download size={13} aria-hidden="true" />
          </button>
        </span>
        {notice && <span className="media-image-notice" role="status">{notice}</span>}
        {zoomUrl && createPortal(
          <div
            className="media-lightbox media-lightbox-zoomable"
            role="dialog"
            aria-modal="true"
            aria-label="查看大图"
            ref={lightboxRef}
            onClick={() => setZoomUrl("")}
            onDoubleClick={() => setZoomScale(1)}
          >
            <span
              className="media-lightbox-diagram"
              title="滚轮缩放 · 双击复位"
              onClick={(event) => event.stopPropagation()}
              onDoubleClick={(event) => {
                event.stopPropagation();
                setZoomScale(1);
              }}
            >
              <img
                src={zoomUrl}
                alt="mermaid 图表"
                onLoad={(event) => setZoomSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
                style={zoomedImageStyle()}
              />
            </span>
            <button
              type="button"
              className="media-image-action media-lightbox-close"
              aria-label="关闭大图"
              onClick={() => setZoomUrl("")}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>,
          document.body,
        )}
      </div>
    );
  }
  // Diagram still rendering or invalid: plain code block. A rendered diagram
  // the reader switched away from offers the switch back in the head.
  return (
    <MarkdownCodeBlock
      language={language}
      codeText={code}
      headAction={diagramReady && (
        <button type="button" className="markdown-code-view" title="查看图表" aria-label="查看图表" onClick={() => setView("diagram")}>
          <Workflow size={13} aria-hidden="true" />
        </button>
      )}
    >
      {children}
    </MarkdownCodeBlock>
  );
}

let mermaidPromise = null;
let renderCounter = 0;

function loadMermaid() {
  mermaidPromise ||= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      // Failed renders throw and clean up their temp DOM instead of drawing
      // an error-bomb SVG into the page; invalid sources just fall back.
      suppressErrorRendering: true,
      theme: "neutral",
    });
    return mermaid;
  });
  return mermaidPromise;
}

// mermaid keeps global config and temp DOM state across one render, so
// concurrent renders (streaming updates on several blocks) are serialized.
let renderQueue = Promise.resolve();

// Resolves to the sanitized SVG, or "" when the source is invalid (e.g. still
// streaming), so the caller keeps the code fallback.
async function renderMermaid(code) {
  const task = renderQueue.then(async () => {
    const mermaid = await loadMermaid();
    if (!(await mermaid.parse(code, { suppressErrors: true }))) {
      return "";
    }
    const { svg } = await mermaid.render(`lara-mermaid-${(renderCounter += 1)}`, code);
    return svg;
  });
  renderQueue = task.catch(() => {});
  try {
    return await task;
  } catch {
    return "";
  }
}
