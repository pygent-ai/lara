# API Docs

The local FastAPI layer lives in `src/lara_api` and is exposed through the `lara-api` console script.

Start here:

- [Local API Service](local-service.md): route-level API documentation for the desktop shell.
- `contracts/openapi/lara-api.json`: generated OpenAPI contract for typed clients.

Implemented route groups:

- Health: `GET /health`
- Projects and settings: `GET /projects`, `PATCH /projects/order`, `GET /settings`
- Sessions: `GET /sessions`, `POST /sessions`, `GET /sessions/groups`, `PATCH /sessions/groups/order`, `GET /sessions/{session_id}`, `DELETE /sessions/{session_id}`
- Chat stream: `POST /chat/stream`
- Traces: `GET /traces/{session_id}/{case_run_id}`
