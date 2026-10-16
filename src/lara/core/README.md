# Core

Stable, dependency-light helpers shared by every feature domain.

- `io.py`: JSON/JSONL, path validation, timestamps, and plain-data conversion.
- `process.py`: process ownership stamps and liveness checks for artifacts
  shared by multiple hosts.
- `redaction.py`: secret redaction.

Core must not import runtime, evaluation, repair, CLI, API, or UI code.
