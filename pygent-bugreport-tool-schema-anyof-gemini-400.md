# Bug Report: 工具 schema 用顶层 `anyOf` 表示可空参数,Gemini 系上游 functionDeclaration 严格校验直接 400 —— 带 tools 的每个请求必死,且 provider 原始错误体被丢弃无法事后定位

> **状态:已修复,归档于 2026-10-22。** pygent-ai 0.3.29 新增 `llm/_tool_schema.flatten_nullable_any_of`,`openai_chat_completions` 与 `openai_responses` 的工具投影均会展平可空 `anyOf`;`ModelAttempt` / `ModelProviderError` 新增 `provider_message` 保留 provider 字段级错误详情。本地对照验证(见下"环境"的 0.3.27):用标准 `bash` 工具走 `_tool_projection`,0.3.27 投影含 3 个 null-anyOf 节点(复现 400 触发形态),0.3.29 投影为 0 个且 `working_directory` 携带显式 `"type": "string"`。Gemini 端到端复现场景需真实凭据,未重放;待下一次 `gemini-3-pro-image`/az 实调确认。

## 环境

| 项 | 值 |
|---|---|
| pygent-ai | 0.3.27(PyPI,`pygent-ai[video]`) |
| Python | 3.13 |
| 协议 | `openai_chat_completions` |
| 部署形态 | Lara agent;第三方 OpenAI 兼容中转(az 连接,`https://az.gptplus5.com/v1`)转发至 Gemini 原生 API(模型 `gemini-3-pro-image`) |
| 对照组 | `glm-5.3-flash`(hw 连接,同协议)——同 schema 全部正常 |

## 摘要

pygent 工具参数的 JSON Schema 由 Pydantic 模型生成,`Optional[T] = None` 字段被投影为**顶层 `anyOf: [{type: T}, {type: null}]`,节点自身没有 `type` 字段**。`openai_compatible` 的 `_tool_projection` 对 `parameters` 原样透传,不做任何规范化。

Gemini 原生 API 的 functionDeclaration 校验要求**每个 schema 节点显式携带 `type`**,不接受顶层 `anyOf`。后果是:凡上游走 Gemini 校验的模型,只要请求带 tools(带工具的 agent turn 全部如此),**每个请求都在 attempt 1 被 400 拒绝**;而 `invalid_request` 类错误不可重试、不触发 fallback,整个 turn 直接终态 error。

叠加一个可诊断性缺陷:provider 的 400 响应体里**恰好有精确到字段级的错误信息**(`bash` 的 `parameters.working_directory`),但 pygent 只把归一化后的 `reason_code=invalid_parameter`(4xx 兜底分类)持久化进 run 事件,原始 message 全链路丢弃——事后排查只能重新抓包。

**补充定性**:同样的 wire schema 在 2026-10-16 曾对同一中转站、同一模型 8 次调用全部通过(见下文时间线)。客户端侧(pygent 工具定义、`_tool_projection`、Pydantic 2.13.4)在两时点间逐字节无差异,因此触发条件是**上游(Gemini 兼容层)校验行为在 10-16 至 10-22 之间收紧**;pygent 的 schema 形态则是使请求在该校验下必然失败的固有兼容性缺陷。

## 生产现场(Lara 集成,2026-10-22 实录)

会话 `chat-chat-20261022-102249-3b0e26`(项目 `5cb2265b…`,workspace `E:\Projects\design studio`),run `run-20261022-102249-663040-a8145e`。事件时间线(UTC,取自 run 的 `events.jsonl`):

| 时刻 | 事件 |
|---|---|
| 02:22:50.315 | `model.request`:group=`gemini-3-pro-image`,key=`model-6`,provider=`az` |
| 02:22:50.324 | `prompt.rendered`(静态 7 模块 + 动态 4 模块注入) |
| **02:22:52.342** | **`runtime.error`:`ModelCallError: model stream failed after retry and fallback: invalid_parameter (HTTP 400)`;attempts 仅 1 条:`{attempt: 1, error_kind: "invalid_request", http_status: 400, reason_code: "invalid_parameter", status: "failed"}`** |
| 02:22:52.348 | `model.response status=error` → run 终态 `error` |

同一模型组的另一 run(`run-20261022-102345-134674-e54798`,workspace `E:\Projects\lora`)于 02:23:45 以完全相同的签名失败。近 3 天统计:**`gemini-3-pro-image`/az 2 次调用 2 次失败;`glm-5.3-flash`/hw 115 次通过**——排除了会话与消息内容的特异性。

`reason_code=invalid_parameter` 具有误导性:它只是 `_reason_for_provider_failure`(`openai_compatible.py:1007-1008`)对"4xx 且 error body 的 `code`/`type` 不在 `_PROVIDER_ERROR_REASONS` 映射表(`:72`)中"的兜底分类,并不代表请求参数真的无效。

## 时间线:为什么 10-16 能通过

`gemini-3-pro-image`/az 全历史调用(自动扫描全部项目 run 事件):

| 日期(UTC) | 调用 | 结果 | 工具集 | pygent | 客户端 schema |
|---|---|---|---|---|---|
| 10-16 07:39–08:13 | 8 次 | 全部 passed | 13 个(含 `bash`、`read`,anyOf 在线) | 0.3.25 | 与 0.3.27 逐字节相同(见下) |
| 10-22 02:22 / 02:23 | 2 次 | 全部 400 | 31 个(新增 18 个 browser_* MCP 工具) | 0.3.27 | 同上 |

版本排除证据:

- `_bash.py`、`_files.py` 工具定义:0.3.25 与 0.3.27 diff 为空,anyOf 形态两版一致;
- `_tool_projection`:两版逐字节相同(纯透传,均无规范化);
- Pydantic 2.13.4:8 月安装至今未变(schema 生成器相同);
- `stream_options`:两版均有,且实测去掉它仍 400(非触发条件);
- 同期 `glm-5.3-flash`/hw 连接 115 次 run 全部通过——同一 pygent、同一 schema,排除了客户端侧普遍性回归。

结论:10-16 与 10-22 之间唯一随时间变化的服务端组件是 az 中转站对 `gemini-3-pro-image` 的上游实现(错误体 `"type": "upstream_error"` 及 `functionDeclaration` 措辞表明其转发目标为 Gemini 原生 API)。10-16 时它接受含顶层 `anyOf` 的 functionDeclaration(宽容转换层),10-22 起执行 Gemini 原生严格校验。工具集 13 → 31(browser MCP 工具加入)只是暴露面扩大,不是触发必要条件——实测仅 `bash` 一个工具即可独立触发 400。

## 根因

1. **Pydantic → JSON Schema 投影产生顶层 `anyOf`**。实测捕获的 wire 请求中,`bash` 工具的 `parameters`:

   ```json
   {
     "additionalProperties": false,
     "properties": {
       "command": {"title": "Command", "type": "string", "description": "…"},
       "working_directory": {
         "anyOf": [{"type": "string"}, {"type": "null"}],
         "default": null, "title": "Working Directory", "description": "…"
       },
       "timeout": {
         "anyOf": [{"minimum": 0, "type": "number"}, {"type": "null"}],
         "default": null, "title": "Timeout", "description": "…"
       }
     },
     "required": ["command"], "title": "BashArguments", "type": "object"
   }
   ```

   `read` 的 `pages`、`description` 等所有可空字段同构。Gemini 系上游拒绝此类节点:`parameters.working_directory` schema didn't specify the schema type field。

2. **`_tool_projection` 透传无规范化**(`openai_compatible.py:1488`):`parameters` 直接 `to_dict()` 进入请求体,协议层没有任何面向上游 schema 方言的适配点。

3. **错误分类与重试策略放大故障**:400 → `error_kind=invalid_request` → `retryable=false`、不尝试 fallback 模型;`max_attempts_per_model: 20` 形同虚设(attempt 1 即终局)。对"schema 被上游校验拒绝"这种确定性错误这是合理的,但意味着**只要 schema 不兼容,该模型组在 agent 场景下完全不可用**。

4. **可诊断性缺陷**:错误体 `{"error": {"message": "Unable to submit request because `bash` functionDeclaration `parameters.working_directory` schema didn't specify the schema type field. …", "type": "upstream_error", "code": 400}}` 被完整读到(分类函数截取了 body),但 attempts 记录只保留 `reason_code`/`http_status`,message 不进任何事件、日志或 `run_metadata`。本次事故的精确原因只能通过本地代理抓包重新拿到。

## 最小复现(已实测验证)

以本地抓包代理捕获 pygent 实际发出的完整报文(31 个工具、16KB system、2 条消息、`stream: true`、`stream_options.include_usage`),对上游端点回放并做变量二分:

| # | 请求变体 | 结果 |
|---|---|---|
| 1 | 真实报文原样回放 | **400**:`bash` → `parameters.working_directory` 未指定 type |
| 2 | 去掉 `stream_options` | 400(排除该变量) |
| 3 | 去掉 `tools`(保留完整 system/messages) | 200 OK(**证明 system/messages 无辜**) |
| 4 | 只扁平化 `bash` 一个工具的 anyOf | 400,报错**转移**到 `read` → `pages`(证明是模式性问题) |
| 5 | **全部工具扁平化 `anyOf: [T, null] → T`** | **200 OK,正常返回图片内容** |

对照实验(排除其余候选变量,均 200):纯文本无工具、单工具、33 个简单 schema 工具、`max_tokens: 8192` + stream、stream 无工具。

扁平化函数(复现脚本所用,语义:`anyOf` 恰含一个非 null 分支且两分支均有 `type` 时,以非 null 分支替换节点):

```python
def flatten(node):
    if isinstance(node, dict):
        ao = node.get("anyOf")
        if isinstance(ao, list) and len(ao) == 2:
            non_null = [b for b in ao if b.get("type") != "null"]
            if len(non_null) == 1 and all("type" in b for b in ao):
                merged = dict(node)
                merged.pop("anyOf")
                merged.update(flatten(non_null[0]))
                return merged
        return {k: flatten(v) for k, v in node.items()}
    if isinstance(node, list):
        return [flatten(x) for x in node]
    return node
```

## 期望行为

- `openai_chat_completions` 协议投影产出的工具 schema 对主流上游(OpenAI、Gemini 兼容层、各类中转)普遍可用:`Optional[T]` 不再以顶层 `anyOf` 进入 wire 请求;
- provider 错误响应体(至少 `error.message`,截断至 `_MAX_PROVIDER_ERROR_BYTES`)随 attempts 持久化到 run 事件,生产现场无需抓包即可定位;
- `invalid_parameter` 兜底分类不再吞掉"上游 schema 校验失败"这一确定性、可自修复的信息。

## 修复建议

1. **schema 规范化(根治)**:在 `_tool_projection` 内对 `parameters` 递归做 `anyOf [T, null] → T` 扁平化(即复现脚本中的 `flatten` 语义;`default: null`、`title`、`description` 原样保留,Gemini 均接受)。已实测该变换使同报文 400 → 200。若担心信息损失,可保留 `description` 中"可为空"的语义,或仅在检测到 `anyOf` 含 null 分支时执行。
2. **错误体持久化**:`_reason_for_provider_failure` 已解析 body;将 `error.message`(截断)并入 attempts 的 details,`ModelCallError` 消息可附带首个 provider message。
3. **回归测试**:对含 `Optional[T]` 字段的标准工具(bash/read/edit 等)断言生成的 wire schema 无顶层 `anyOf`;并加一条"schema 含任何顶层 `anyOf` 时 Gemini 类校验必拒"的文档性说明。
4. **讨论项**:是否需要 provider capability 声明(如 `schema.dialect`),由 Lara 侧 config 标注某连接走严格校验上游——在修复落地前,这是唯一的绕行通道(本项目 `model-6` 的 `tools.call: true` 声明本身合法,问题出在 wire schema,配置层无法绕过)。

## 补充上下文

- 复现产物:`C:\Users\Administrator\.lara\tmp\debug-home-20261022\`(捕获的完整请求 `captured-request.json`、脱敏请求头、抓包代理脚本),确认后可删除。
- 数据无损:同 prompt 对上游手工回放(扁平化后)可正常生图;`hw` 连接模型不受影响。
- 失败的两次 run 均为首轮(一次 1 条历史、一次 21 条历史),与上下文长度无关。
