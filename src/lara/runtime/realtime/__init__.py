"""Real-time interaction harness (D-RT series, see docs/design/realtime-interaction).

The agent works silently: content/thinking stay out of the user-facing stream and
``speak`` is the only channel the user sees. Modules planned under D-RT9:

- ``speak``          — the user-visible channel tool + renderer/source abstraction
- ``fork_reply``     — fork-snapshot single-call reply (tool_choice forced / fallback)
- ``envelope``       — conversation-exchange injection at the next tool-result boundary
- ``nudge``          — N-rounds-without-speak reminder
- ``turn_finalizer`` — end-of-turn bottom-line speak check

Wiring into the live pipeline lands after the 200-round acceptance run finishes;
until then this package is additive-only and imported by nothing.
"""
