# @amagine3d/a3d-runtime

Private runtime adapter between Amagine3D and the OpenAI Codex SDK.

This package owns:

- Codex client, model, provider, environment, and sandbox configuration;
- native Codex thread start/resume and streamed-turn execution;
- A3D runtime prompts and isolated workspace setup;
- conversion from vendor SDK events to the stable Amagine3D runtime contract;
- cancellation, idle timeout, and hard timeout supervision.

The package does not own product HTTP routes, product-session JSON, uploads, or
artifact discovery. Thread persistence crosses the boundary through the
`threadId` input and `onThreadStarted` callback, so this package never imports
from the application `server/` or `src/` trees.

The root application links this private package with a local `file:` dependency.
Root build and test commands cover its TypeScript source and tests.

## Tavily search transport

When Web research is enabled and `TAVILY_API_KEY` is configured, the runtime
selects the local `a3d search` backend instead of provider-hosted search. The
runtime never issues a search automatically; the LLM decides whether to search
and supplies the query and options from the current task semantics. Each
turn gets an authenticated loopback broker that can only submit validated
requests to Tavily's fixed Search endpoint. The account key remains in the
runtime process; Codex shell commands receive only the temporary turn
capability, which is revoked when the turn settles.

Without a Tavily key, enabled Web research retains Codex hosted search for
providers that implement it. `CODEX_WEB_SEARCH_ENABLED=false` disables both
backends. Standalone operators may run `a3d search` with an explicitly exported
`TAVILY_API_KEY`; managed turns never fall back from their broker to that key.
