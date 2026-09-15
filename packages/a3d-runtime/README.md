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

## Automatic gateway image compatibility

A configured `LLM_BASE_URL` or `OPENAI_BASE_URL` automatically enables a narrow,
per-turn Responses adapter. It lifts typed `input_image` objects out of function
and custom-tool output arrays into following user image messages, after the tool
call group closes. Tool text stays at tool authority; generated provenance text
marks the images as tool data rather than a new user request. No model-name or
host-name detection, additional option, model probe, or retry is used to choose
this representation. Native direct connections keep their existing transport.

The adapter binds only to loopback with a random per-turn bearer token and sends
Responses and native context-compaction requests only to the configured upstream.
The SDK receives the local token rather
than the upstream API key. Request JSON is buffered for inspection, but unchanged
requests preserve their original bytes and compression; SSE and other responses
stream with backpressure, without decoding or accumulating them. Redirects are not
followed. Turn completion, failure and cancellation close listeners and connections.
This transport does not select CAD stages, alter acceptance, or mark visual review
complete: `probeVision` still checks actual image perception.
