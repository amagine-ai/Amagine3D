# Third-party notices

Amagine3D source code is distributed under Apache License 2.0. Third-party
components keep their own licenses; the project license does not relicense
them.

## Source-distribution boundary

The public source repository contains dependency manifests and lock files, but
does not distribute `node_modules/`, `dist/`, `.venv/`, generated workspaces,
or platform wheels. npm and pip install those components separately under their
upstream licenses.

Anyone who distributes a built web application, installed Node.js dependency
tree, Python environment, desktop bundle, or container must generate and review
notices for that exact target. In particular, a Python binary distribution must
preserve the license material shipped in each wheel and review MPL-2.0
components such as certifi and LGPL-2.1 components such as Open CASCADE
Technology.

## Components used by the source repository

| Component | Version | License | Checked-in text or notice |
| --- | --- | --- | --- |
| PI coding agent | `0.84.2` | MIT | [`pi.txt`](../public/licenses/pi.txt) |
| Tavily JavaScript SDK | `0.7.7` | MIT | [`npm-production-licenses.json`](../public/licenses/npm-production-licenses.json) |
| IBM Plex Sans Variable | Fontsource `5.3.0`, font v23 | OFL-1.1 | [`ibm-plex-sans.txt`](../public/licenses/ibm-plex-sans.txt) |
| JetBrains Mono Variable | Fontsource `5.3.0`, font v24 | OFL-1.1 | [`jetbrains-mono.txt`](../public/licenses/jetbrains-mono.txt) |
| three.js | `0.182.0` | MIT | [`three.txt`](../public/licenses/three.txt) |
| React | `19.1.1` | MIT | [`react.txt`](../public/licenses/react.txt) |
| Express | `5.1.0` | MIT | [`express.txt`](../public/licenses/express.txt) |
| build123d | `0.11.1` | Apache-2.0 | [`build123d.txt`](../public/licenses/build123d.txt), [`build123d-notice.txt`](../public/licenses/build123d-notice.txt) |
| Open CASCADE Technology / CadQuery OCP | OCCT `7.9.3`, OCP `7.9.3.1.1` | LGPL-2.1 with Open CASCADE exception | [`opencascade-lgpl-2.1.txt`](../public/licenses/opencascade-lgpl-2.1.txt), [`opencascade-exception.txt`](../public/licenses/opencascade-exception.txt) |
| trimesh | `5.0.0` | MIT | [`trimesh.txt`](../public/licenses/trimesh.txt) |
| Rtree | `1.4.1` | MIT | [`rtree.txt`](../public/licenses/rtree.txt) |
| lib3mf | `2.5.0` | BSD-2-Clause | [`lib3mf.txt`](../public/licenses/lib3mf.txt) |

The complete production npm dependency graph and SPDX expressions are recorded
in [`npm-production-licenses.json`](../public/licenses/npm-production-licenses.json).
`npm run licenses:check` verifies the inventory policy, required license files,
and source-distribution boundary.

## build123d notice

build123d

Copyright (c) 2022–2025 The build123d Contributors

Licensed under the Apache License, Version 2.0. This project was originally
derived from portions of the CadQuery codebase but has since been extensively
refactored and restructured into an independent system. CadQuery is licensed
under the Apache License, Version 2.0. The verbatim upstream notice is available
in [`build123d-notice.txt`](../public/licenses/build123d-notice.txt).
