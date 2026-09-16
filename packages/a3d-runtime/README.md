# @amagine3d/a3d-runtime

Private runtime adapter between Amagine3D and the OpenAI Codex SDK.

This package owns:

- Codex client, model, provider, environment, and sandbox configuration;
- native Codex thread start/resume and streamed-turn execution;
- A3D runtime prompts and isolated workspace setup;
- conversion from vendor SDK events to the stable Amagine3D runtime contract;
- cancellation, idle timeout, and hard timeout supervision.

The package does not own HTTP transport, product-session JSON, uploads, or
artifact discovery. Thread persistence crosses the boundary through the
`threadId` input and `onThreadStarted` callback, so this package never imports
from the application `server/` or `src/` trees.

The root application links this private package with a local `file:` dependency.
Root build and test commands cover its TypeScript source and tests.
