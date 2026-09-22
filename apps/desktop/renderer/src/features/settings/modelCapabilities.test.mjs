import test from "node:test";
import assert from "node:assert/strict";
import { purposeCapabilities, updateCapability, templateOptions, capabilitiesEqual } from "./modelCapabilities.js";

test("purpose templates preserve limits without mutating current capabilities", () => {
  const current = purposeCapabilities("agentic", { limits: { context_tokens: 32000, max_output_tokens: 4000 } });
  for (const id of ["chat", "agentic", "understanding", "generation"]) {
    const next = purposeCapabilities(id, current);
    assert.deepEqual(next.limits, current.limits);
    assert.notEqual(next.limits, current.limits);
    assert.ok(next.streaming.output.every(type => next.modalities.output.includes(type)));
  }
  assert.equal(current.tools.call, true);
});
test("removing an output modality removes only its corresponding stream", () => {
  const value = purposeCapabilities("chat");
  value.modalities.output = ["text", "audio"];
  value.streaming.output = ["text", "audio"];
  const next = updateCapability(value, "modalities", "output", ["text"]);
  assert.deepEqual(next.streaming.output, ["text"]);
  assert.deepEqual(value.streaming.output, ["text", "audio"]);
});
test("disabling reasoning also disables controllability", () => {
  const value = purposeCapabilities("chat");
  value.reasoning = { supported: true, controllable: true };
  assert.deepEqual(updateCapability(value, "reasoning", "supported", false).reasoning, { supported: false, controllable: false });
  assert.equal(value.reasoning.controllable, true);
});

const CATALOG = {
  model_capabilities: [
    { provider: "deepseek", protocol: "openai_chat_completions", model_id: "deepseek-v4-pro", capabilities: { modalities: { input: ["text"], output: ["text"] }, streaming: { output: ["text"] }, tools: { call: true, choice: ["auto"], parallel: true }, structured_output: { json_object: true, json_schema: false }, reasoning: { supported: true, controllable: false }, limits: { context_tokens: 1000000, max_output_tokens: null } } },
    { provider: "openai", protocol: "openai_responses", model_id: "gpt-5.1", capabilities: { tools: { call: true }, limits: { context_tokens: 400000, max_output_tokens: 128000 } } },
    { provider: "openai", protocol: "openai_chat_completions", model_id: "gpt-5.1", capabilities: { tools: { call: false }, limits: { context_tokens: null, max_output_tokens: null } } },
  ],
};

test("template options rank same provider and protocol first with stable keys", () => {
  const options = templateOptions(CATALOG, { provider: "openai", protocol: "openai_chat_completions" });
  assert.deepEqual(options.map(item => item.key), [
    "openai/gpt-5.1/openai_chat_completions",
    "openai/gpt-5.1/openai_responses",
    "deepseek/deepseek-v4-pro/openai_chat_completions",
  ]);
  assert.equal(templateOptions(null, {}).length, 0);
});

test("capabilities equal requires both capability shape and limits", () => {
  const entry = CATALOG.model_capabilities[0];
  assert.equal(capabilitiesEqual(structuredClone(entry.capabilities), entry.capabilities), true);
  assert.equal(capabilitiesEqual({ ...entry.capabilities, limits: { context_tokens: 999, max_output_tokens: null } }, entry.capabilities), false);
  assert.equal(capabilitiesEqual({ ...entry.capabilities, tools: { call: false } }, entry.capabilities), false);
  assert.equal(capabilitiesEqual(purposeCapabilities("chat"), purposeCapabilities("chat")), true);
});

test("capabilities equal ignores object key order", () => {
  const entry = CATALOG.model_capabilities[0];
  const reordered = {
    limits: { max_output_tokens: null, context_tokens: 1000000 },
    reasoning: { controllable: false, supported: true },
    structured_output: { json_schema: false, json_object: true },
    tools: { parallel: true, choice: ["auto"], call: true },
    streaming: { output: ["text"] },
    modalities: { output: ["text"], input: ["text"] },
  };
  assert.equal(capabilitiesEqual(reordered, entry.capabilities), true);
});
