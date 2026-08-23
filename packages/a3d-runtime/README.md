# @amagine3d/a3d-runtime

Local adapter package for the PI coding-agent SDK used by Amagine3D.

The package owns vendor-facing concerns:

- model/provider registration and environment parsing;
- PI session and resource-loader creation;
- project skill discovery;
- writable-workspace tool restrictions;
- the small set of PI session APIs used by the server persistence layer.

Application HTTP routes should import from `@amagine3d/a3d-runtime` rather than
from `@earendil-works/pi-coding-agent`. Request validation, artifact discovery,
uploads, and CAD visual-audit policy remain in `server/` because they are
Amagine3D application behavior rather than runtime-adapter behavior.

The package is private and linked into the root application with a local `file:`
dependency, so normal root commands (`npm install`, `npm test`, `npm run build`)
cover it without a separate publish step.
