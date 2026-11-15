# CLI

Thin command-line adapter over Lara application services.

- `main.py`: root parser, command dispatch, JSON rendering, and CLI errors.
- `sessions.py`: session CRUD, one-shot turns (`session run`, the scheduler's
  task runner) and interactive chat; turns go through the SDK
  `LocalExecutionHost.deliver_user_message`.
- `scheduler.py`: the SDK scheduler's command face — daemon (`run`), signed
  task/control/event submissions, task and run listings.
- `credentials.py`: credential subcommands.

Business logic belongs in the corresponding feature package, not in command handlers.
