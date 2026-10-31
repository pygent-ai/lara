// Rasterize images through a canvas into PNG bytes: clipboard write and
// downloads both need PNG, and SVG sources need explicit pixel dimensions
// before an <img> can decode them.

export function toPngBlob(blob, scale = 1) {
  const url = URL.createObjectURL(blob);
  return imageToPngBlob(url, scale).finally(() => URL.revokeObjectURL(url));
}

export async function imageToPngBlob(url, scale = 1) {
  const image = new window.Image();
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = () => reject(new Error("image decode failed"));
    image.src = url;
  });
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) => {
    canvas.toBlob((value) => (value ? resolve(value) : reject(new Error("canvas encode failed"))), "image/png");
  });
}

// The rendered mermaid SVG carries width="100%", which an <img> cannot
// rasterize; clone it with hard dimensions from its viewBox first. Chromium
// taints the canvas when a foreignObject SVG is loaded from a blob: URL, so
// the clone serializes into a data: URL instead.
export async function svgToPngBlob(svg, scale = 2) {
  const viewBox = svg.viewBox?.baseVal;
  if (!viewBox?.width || !viewBox?.height) {
    throw new Error("svg has no usable viewBox");
  }
  const clone = svg.cloneNode(true);
  clone.setAttribute("width", viewBox.width);
  clone.setAttribute("height", viewBox.height);
  const source = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(new XMLSerializer().serializeToString(clone))}`;
  return imageToPngBlob(source, scale);
}
