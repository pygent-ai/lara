/**
 * Demo DeepSeek Harness plugin, used to verify the dsh-bridge end to end.
 *
 * Written against the official DSH plugin contract: `apply(ctx, config)` with
 * `inject = ['tools']`, registering through `ctx.tools.register(defineTool(...))`
 * exactly as documented in the "Develop a Custom Tool" tutorial.
 *
 * Load: node bridge.mjs demo-plugin/demo-dsh-tools-plugin.mjs
 */

import { defineTool } from "@deepseek-ai/dsh-tools";

export const name = "demo-word-stats";
export const inject = ["tools"];

export function apply(ctx) {
  ctx.tools.register(
    defineTool({
      name: "dsh_demo_word_stats",
      description:
        "Count words, lines, and characters of a text (DeepSeek Harness demo plugin).",
      parameters: {
        text: {
          type: "string",
          required: true,
          description: "Text to analyze",
        },
        upperLimit: {
          type: "integer",
          description:
            "Optional word-count cap; the result flags whether the text exceeds it",
        },
        explode: {
          type: "boolean",
          description:
            "When true the tool body throws, to exercise the error path in verification",
        },
      },
      async execute(args) {
        if (args.explode) {
          throw new Error("demo requested failure (explode=true)");
        }
        const trimmed = args.text.trim();
        const words = trimmed ? trimmed.split(/\s+/).length : 0;
        const lines = args.text ? args.text.split(/\r\n|\r|\n/).length : 0;
        return {
          words,
          lines,
          characters: args.text.length,
          overLimit: args.upperLimit != null ? words > args.upperLimit : false,
        };
      },
      output: {
        schema: {
          type: "object",
          additionalProperties: false,
          properties: {
            words: { type: "integer", description: "Whitespace-separated word count" },
            lines: { type: "integer", description: "Line count" },
            characters: { type: "integer", description: "Character count" },
            overLimit: { type: "boolean", description: "Whether words exceed upperLimit" },
          },
        },
        render(args, value) {
          const limit = value.overLimit ? " (over limit)" : "";
          return [
            {
              type: "text",
              text: `words=${value.words} lines=${value.lines} characters=${value.characters}${limit}`,
            },
          ];
        },
      },
    }),
  );
}
