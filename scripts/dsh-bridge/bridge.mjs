#!/usr/bin/env node
/**
 * DeepSeek Harness plugin bridge.
 *
 * Loads DSH (Cordis) tool plugins and exposes every tool they register through
 * `ctx.tools.register(defineTool(...))` as one MCP stdio server, so the lora
 * agent can call them via a plain `mcp.servers` entry (stdio transport).
 *
 * Usage:
 *   node bridge.mjs <pluginSpec> [pluginSpec...]
 *
 * pluginSpec is either an npm package name resolvable from this directory or a
 * path to a plugin module. The module must export `apply(ctx, config)` in the
 * Cordis convention (named `apply`, `default.apply`, or a default function);
 * plugins that only contribute UI, themes, or host services register no tools
 * and are useless over this bridge.
 *
 * The bridge is a minimal Cordis host: it provides `ctx.tools.register`, a
 * logger, and a no-op event bus. Plugins using the official
 * `@deepseek-ai/dsh-tools` `defineTool` DSL get DSH-identical schema
 * compilation, argument validation, output validation, and rendering. Plugins
 * that require full kernel services (session, sandbox, agent scopes) cannot
 * run here and fail at load.
 *
 * All diagnostics go to stderr; stdout carries the MCP protocol only.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import {
  parameterSchemaSpecToJsonSchema,
  valueSchemaSpecToJsonSchema,
  validateArgs,
  validateJsonSchemaValue,
} from "@deepseek-ai/dsh-tools";
import { randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import { isAbsolute, resolve } from "node:path";

/** @type {Map<string, {definition: object, plugin: string, inputSchema: object, outputSchema: object}>} */
const tools = new Map();

function log(message) {
  process.stderr.write(`[dsh-bridge] ${message}\n`);
}

function isCompiledObjectSchema(node) {
  return Boolean(node) && typeof node === "object" && node.type === "object" && typeof node.properties === "object";
}

function fail(message) {
  process.stderr.write(`[dsh-bridge] ERROR: ${message}\n`);
  process.exit(1);
}

function makePluginContext(pluginName) {
  const listeners = new Map();
  const logger = {
    debug: (...parts) => log(`[${pluginName}] ${parts.join(" ")}`),
    info: (...parts) => log(`[${pluginName}] ${parts.join(" ")}`),
    warn: (...parts) => log(`[${pluginName}] WARN: ${parts.join(" ")}`),
    error: (...parts) => log(`[${pluginName}] ERROR: ${parts.join(" ")}`),
  };
  return {
    logger,
    log: logger,
    config: {},
    on(event, handler) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(handler);
      return () => listeners.get(event)?.delete(handler);
    },
    emit(event, ...args) {
      for (const handler of listeners.get(event) ?? []) {
        try {
          handler(...args);
        } catch (error) {
          logger.error(`event ${event} listener failed:`, error?.message ?? error);
        }
      }
    },
    tools: {
      register(definition) {
        if (!definition || typeof definition.name !== "string") {
          throw new Error("tool definition must have a string name");
        }
        if (typeof definition.execute !== "function") {
          throw new Error(`tool ${definition.name} must have an execute function`);
        }
        if (typeof definition.output?.render !== "function") {
          throw new Error(`tool ${definition.name} must have an output.render function`);
        }
        if (tools.has(definition.name)) {
          throw new Error(
            `duplicate tool name ${definition.name} (already registered by ${tools.get(definition.name).plugin})`,
          );
        }
        // Fail loudly at load: DSH compiles these once; an unusable schema or
        // output contract would only surface at call time otherwise.
        // defineTool hands back compiled JSON Schema nodes; hand-rolled
        // definitions pass the raw spec maps, which we compile here.
        const parameters = definition.parameters ?? {};
        const compiled = isCompiledObjectSchema(parameters);
        const inputSchema = compiled
          ? parameters
          : parameterSchemaSpecToJsonSchema(parameters);
        const outputRaw = definition.output.schema;
        const outputSchema = isCompiledObjectSchema(outputRaw)
          ? outputRaw
          : valueSchemaSpecToJsonSchema(outputRaw);
        tools.set(definition.name, {
          definition,
          plugin: pluginName,
          inputSchema,
          outputSchema,
          // validateArgs walks the raw spec map; pre-compiled definitions are
          // validated against their JSON Schema instead.
          parameterSpec: compiled ? null : parameters,
        });
        log(`[${pluginName}] registered tool ${definition.name}`);
        return () => tools.delete(definition.name);
      },
    },
  };
}

function resolvePluginModule(spec) {
  const looksLikePath = isAbsolute(spec) || /^[.]/.test(spec) || /[\\/]/.test(spec);
  if (!looksLikePath) return spec;
  const absolute = resolve(process.cwd(), spec);
  return pathToFileURL(absolute).href;
}

async function loadPlugin(spec) {
  const specifier = resolvePluginModule(spec);
  const mod = await import(specifier);
  const apply = mod.apply ?? mod.default?.apply ?? mod.default;
  if (typeof apply !== "function") {
    throw new Error("module exports no apply(ctx, config) function");
  }
  const pluginName = mod.name ?? mod.default?.name ?? spec;
  const before = tools.size;
  const ctx = makePluginContext(String(pluginName));
  await apply(ctx, ctx.config);
  const registered = tools.size - before;
  if (registered === 0) {
    log(`WARN: plugin ${pluginName} registered no tools (UI/theme/service-only plugin?)`);
  }
  log(`loaded plugin ${pluginName} (${registered} tool(s))`);
}

function toMcpContent(blocks, fallbackValue) {
  const source = Array.isArray(blocks) ? blocks : [{ type: "text", text: JSON.stringify(fallbackValue, null, 2) }];
  return source.map((block) => {
    if (block?.type === "text" && typeof block.text === "string") {
      return { type: "text", text: block.text };
    }
    return { type: "text", text: JSON.stringify(block, null, 2) };
  });
}

function errorResult(message) {
  return { isError: true, content: [{ type: "text", text: message }] };
}

async function callTool(name, rawArguments) {
  const entry = tools.get(name);
  if (!entry) {
    return errorResult(`unknown tool: ${name}`);
  }
  const { definition, outputSchema, parameterSpec, inputSchema } = entry;
  const args = rawArguments ?? {};
  const violations = parameterSpec
    ? validateArgs(parameterSpec, args)
    : validateJsonSchemaValue(inputSchema, args);
  if (violations.length > 0) {
    return errorResult(`invalid arguments for ${name}: ${violations.join("; ")}`);
  }
  let value;
  try {
    const exec = {
      callId: randomUUID(),
      rootCallId: randomUUID(),
      name,
      arguments: args,
      signal: typeof definition.timeoutMs === "number" && definition.timeoutMs > 0
        ? AbortSignal.timeout(definition.timeoutMs)
        : new AbortController().signal,
      deferContext() {},
      concludeTurn() {},
    };
    value = await definition.execute(args, exec);
  } catch (error) {
    return errorResult(`${name} failed: ${error?.message ?? String(error)}`);
  }
  const outputViolations = validateJsonSchemaValue(outputSchema, value);
  if (outputViolations.length > 0) {
    return errorResult(`${name} returned a value violating its output schema: ${outputViolations.join("; ")}`);
  }
  try {
    return { content: toMcpContent(definition.output.render(args, value), value) };
  } catch (error) {
    log(`WARN: ${name} output.render failed, falling back to JSON: ${error?.message ?? error}`);
    return { content: toMcpContent(null, value) };
  }
}

const specs = process.argv.slice(2);
if (specs.length === 0) {
  fail("usage: node bridge.mjs <pluginSpec> [pluginSpec...]");
}

for (const spec of specs) {
  try {
    await loadPlugin(spec);
  } catch (error) {
    fail(`failed to load plugin ${spec}: ${error?.message ?? error}`);
  }
}

const server = new Server({ name: "dsh-bridge", version: "0.1.0" }, { capabilities: { tools: {} } });
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [...tools.entries()].map(([name, entry]) => ({
    name,
    description: entry.definition.description ?? "",
    inputSchema: entry.inputSchema,
  })),
}));
server.setRequestHandler(CallToolRequestSchema, async (request) =>
  callTool(request.params.name, request.params.arguments),
);

await server.connect(new StdioServerTransport());
log(`serving ${tools.size} tool(s) from ${specs.length} plugin(s) over MCP stdio`);
