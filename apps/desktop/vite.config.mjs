import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  optimizeDeps: {
    // App.jsx is intentionally loaded after the startup shell so backend
    // warm-up and renderer loading overlap. Runtime dependency discovery can
    // reload the page while Chromium is fetching that module, aborting it.
    // Pre-bundle the external renderer graph up front and disable late
    // discovery so the first App.jsx request remains stable.
    noDiscovery: true,
    include: [
      "lucide-react",
      "mdast-util-gfm-footnote",
      "mdast-util-gfm-strikethrough",
      "mdast-util-gfm-table",
      "mdast-util-gfm-task-list-item",
      "mermaid",
      "micromark-extension-gfm-footnote",
      "micromark-extension-gfm-strikethrough",
      "micromark-extension-gfm-table",
      "micromark-extension-gfm-task-list-item",
      "react",
      "react-dom",
      "react-dom/client",
      "react-markdown",
    ],
  },
});
