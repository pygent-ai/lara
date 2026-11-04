/**
 * Hand-rolled DSH plugin fixture: registers a tool WITHOUT the official
 * `defineTool` helper, passing a raw parameter spec map. This exercises the
 * bridge's second schema path (raw spec -> compile -> validateArgs) used by
 * plugins that skip `@deepseek-ai/dsh-tools`.
 */

export const name = "legacy-echo";

export function apply(ctx) {
  ctx.tools.register({
    name: "dsh_legacy_echo",
    description: "Echo a message back with an optional prefix (hand-rolled registration).",
    parameters: {
      message: { type: "string", required: true, description: "Text to echo" },
      prefix: { type: "string", description: "Optional prefix" },
    },
    output: {
      schema: {
        type: "object",
        additionalProperties: false,
        properties: { echoed: { type: "string" } },
      },
      render(args, value) {
        return [{ type: "text", text: value.echoed }];
      },
    },
    async execute(args) {
      return { echoed: args.prefix ? `${args.prefix}: ${args.message}` : args.message };
    },
  });
}
