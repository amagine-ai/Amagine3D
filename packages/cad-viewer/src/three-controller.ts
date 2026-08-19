import {
  AmbientLight,
  Box3,
  BufferGeometry,
  Color,
  DirectionalLight,
  DoubleSide,
  GridHelper,
  Group,
  Line,
  LineBasicMaterial,
  Line3,
  Mesh,
  MeshStandardMaterial,
  PerspectiveCamera,
  Points,
  Raycaster,
  Scene,
  Sphere,
  SphereGeometry,
  SRGBColorSpace,
  Texture,
  Vector2,
  Vector3,
  WebGLRenderer,
  type ColorRepresentation,
  type Intersection,
  type Material,
  type Object3D,
} from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { ThreeMFLoader } from 'three/examples/jsm/loaders/3MFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

import { ViewerDomainError, toViewerFailure } from './errors';
import { measureSelections } from './measurement';
import {
  DEFAULT_VIEWER_LIMITS,
  type ViewerBounds,
  type ViewerController,
  type ViewerFormat,
  type ViewerLimits,
  type ViewerModel,
  type ViewerPart,
  type ViewerRegion,
  type ViewerSelection,
  type ViewerSelectionMode,
  type ViewerSnapshot,
  type ViewerSnapshotListener,
  type ViewerVector,
  type ViewerView,
  type ViewerViewport,
} from './types';

export type ThreeViewerTheme = {
  defaultPart: ColorRepresentation;
  grid: ColorRepresentation;
  gridCenter: ColorRepresentation;
  measurement: ColorRepresentation;
  selection: ColorRepresentation;
};

export const DEFAULT_THREE_VIEWER_THEME: ThreeViewerTheme = {
  defaultPart: '#aeb9b4',
  grid: '#d4d8d3',
  gridCenter: '#8d9892',
  measurement: '#167457',
  selection: '#d96d37',
};

export type ThreeViewerControllerOptions = {
  canvas: HTMLCanvasElement;
  limits?: Partial<ViewerLimits>;
  theme?: Partial<ThreeViewerTheme>;
};

const toTuple = (vector: Vector3): ViewerVector => [
  vector.x,
  vector.y,
  vector.z,
];

function disposeMaterial(material: Material): void {
  for (const value of Object.values(material)) {
    if (value instanceof Texture) value.dispose();
  }
  material.dispose();
}

export function disposeObjectTree(root: Object3D): void {
  root.traverse((object) => {
    if (
      object instanceof Mesh ||
      object instanceof Line ||
      object instanceof Points
    ) {
      object.geometry.dispose();
      const materials = Array.isArray(object.material)
        ? object.material
        : [object.material];
      for (const material of materials) disposeMaterial(material);
    }
  });
  root.removeFromParent();
  root.clear();
}

function countTriangles(root: Object3D): number {
  let count = 0;
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    const geometry = object.geometry;
    count += Math.floor(
      (geometry.index?.count ?? geometry.getAttribute('position')?.count ?? 0) /
        3,
    );
  });
  return count;
}

/**
 * Finds the logical objects that can be moved for a viewer-only exploded 3MF
 * layout. ThreeMFLoader wraps build items in groups, while meshes belonging to
 * one object can also be split by material; only group boundaries are treated
 * as separable parts so a colored body is never torn into material fragments.
 */
export function separatedLayoutObjects(
  root: Object3D,
  format: ViewerFormat,
): readonly Object3D[] {
  if (format !== '3mf') return [root];

  let assembly = root;
  while (true) {
    const children = assembly.children.filter(
      (child) => countTriangles(child) > 0,
    );
    if (
      children.length > 1 &&
      children.every((child) => !(child instanceof Mesh))
    ) {
      return children;
    }
    const onlyChild = children[0];
    if (
      children.length !== 1 ||
      onlyChild === undefined ||
      onlyChild instanceof Mesh
    ) {
      return [root];
    }
    assembly = onlyChild;
  }
}

/**
 * Creates a preview-only exploded row. Manufacturing coordinates remain in the
 * source artifacts; every displayed part receives an X interval separated by a
 * positive gap, the complete row is centred on X=0, and every part rests on Z=0.
 */
export function arrangeSeparatedParts(parts: readonly Object3D[]): void {
  const visible = parts.flatMap((part) => {
    part.updateMatrixWorld(true);
    const box = new Box3().setFromObject(part);
    if (box.isEmpty()) return [];
    return [{ part, box, size: box.getSize(new Vector3()) }];
  });
  if (visible.length < 2) return;

  const largestDimension = Math.max(
    ...visible.flatMap(({ size }) => [size.x, size.y, size.z]),
  );
  const gap = Math.max(largestDimension * 0.08, 1);
  const totalWidth =
    visible.reduce((sum, { size }) => sum + size.x, 0) +
    gap * (visible.length - 1);
  let cursor = -totalWidth / 2;

  for (const { part, box, size } of visible) {
    const center = box.getCenter(new Vector3());
    const targetCenterX = cursor + size.x / 2;
    part.position.add(
      new Vector3(targetCenterX - center.x, -center.y, -box.min.z),
    );
    part.updateMatrixWorld(true);
    cursor += size.x + gap;
  }
}

export function arrangePartsByExplodedOffsets(
  parts: readonly {
    object: Object3D;
    offset?: ViewerVector;
  }[],
): boolean {
  if (parts.length < 2 || parts.some(({ offset }) => offset === undefined)) {
    return false;
  }
  for (const { object, offset } of parts) {
    if (offset === undefined) return false;
    object.position.add(new Vector3().fromArray(offset));
    object.updateMatrixWorld(true);
  }
  return true;
}

export function viewerPartsForLayout(
  model: ViewerModel,
): readonly ViewerPart[] {
  return model.layout === 'separated' &&
    model.separatedParts !== undefined &&
    model.separatedParts.length > 0
    ? model.separatedParts
    : model.parts;
}

function boundsFromBox(box: Box3): ViewerBounds {
  return {
    min: toTuple(box.min),
    max: toTuple(box.max),
    size: toTuple(box.getSize(new Vector3())),
  };
}

function cloneSnapshot(snapshot: ViewerSnapshot): ViewerSnapshot {
  return {
    ...snapshot,
    regions: [...snapshot.regions],
    selections: [...snapshot.selections],
  };
}

function viewDirection(view: ViewerView): Vector3 {
  switch (view) {
    case 'front':
      return new Vector3(0, -1, 0);
    case 'isometric':
      return new Vector3(1, -1, 1).normalize();
    case 'right':
      return new Vector3(1, 0, 0);
    case 'top':
      return new Vector3(0, 0, 1);
  }
}

class ThreeViewerController implements ViewerController {
  readonly #canvas: HTMLCanvasElement;
  readonly #scene = new Scene();
  readonly #camera = new PerspectiveCamera(34, 1, 0.1, 10_000);
  readonly #renderer: WebGLRenderer;
  readonly #controls: OrbitControls;
  readonly #raycaster = new Raycaster();
  readonly #pointer = new Vector2();
  readonly #listeners = new Set<ViewerSnapshotListener>();
  readonly #limits: ViewerLimits;
  readonly #theme: ThreeViewerTheme;
  readonly #overlayRoot = new Group();
  #grid: GridHelper | undefined;
  #snapshot: ViewerSnapshot = {
    status: 'empty',
    triangleCount: 0,
    regions: [],
    selectionMode: 'face',
    selections: [],
  };
  #modelRoot: Group | undefined;
  #lastModel: ViewerModel | undefined;
  #disposed = false;
  #animationFrame: number | undefined;
  #pointerDown: readonly [number, number] | undefined;

  constructor(options: ThreeViewerControllerOptions) {
    this.#canvas = options.canvas;
    this.#limits = { ...DEFAULT_VIEWER_LIMITS, ...options.limits };
    this.#theme = { ...DEFAULT_THREE_VIEWER_THEME, ...options.theme };

    try {
      const context = this.#canvas.getContext('webgl2', {
        alpha: true,
        antialias: true,
        powerPreference: 'high-performance',
      });
      if (!context) {
        throw new Error(
          'Chrome did not provide a WebGL 2 context. Check hardware acceleration.',
        );
      }
      if (context.isContextLost() || !context.getContextAttributes()) {
        throw new Error(
          'Chrome created WebGL 2 but the GPU context was immediately lost. Close GPU-heavy tabs and check hardware acceleration.',
        );
      }
      const highPrecision = context.getShaderPrecisionFormat(
        context.VERTEX_SHADER,
        context.HIGH_FLOAT,
      );
      this.#renderer = new WebGLRenderer({
        canvas: this.#canvas,
        context,
        alpha: true,
        antialias: true,
        precision: highPrecision ? 'highp' : 'lowp',
        powerPreference: 'high-performance',
      });
    } catch (error) {
      throw new ViewerDomainError(
        'RendererUnavailable',
        `WebGL could not start. ${error instanceof Error ? error.message : 'Check Chrome hardware acceleration.'}`,
        true,
      );
    }
    this.#renderer.outputColorSpace = SRGBColorSpace;
    this.#renderer.setClearAlpha(0);
    this.#camera.up.set(0, 0, 1);
    this.#camera.position.set(80, -80, 65);

    this.#controls = new OrbitControls(this.#camera, this.#canvas);
    this.#controls.enableDamping = true;
    this.#controls.dampingFactor = 0.08;
    this.#controls.screenSpacePanning = true;
    this.#controls.addEventListener('change', this.#render);

    const ambient = new AmbientLight(new Color(0xffffff), 1.5);
    const key = new DirectionalLight(new Color(0xffffff), 2.7);
    key.position.set(4, -5, 8);
    const fill = new DirectionalLight(new Color(0xe5eee9), 1.4);
    fill.position.set(-6, 3, 2);
    this.#grid = this.#createGrid();
    this.#overlayRoot.name = 'viewer-overlays';
    this.#scene.add(ambient, key, fill, this.#grid, this.#overlayRoot);

    this.#canvas.addEventListener('pointerdown', this.#onPointerDown);
    this.#canvas.addEventListener('pointerup', this.#onPointerUp);
    this.#canvas.addEventListener('webglcontextlost', this.#onContextLost);
    this.#canvas.addEventListener(
      'webglcontextrestored',
      this.#onContextRestored,
    );
    this.#animate();
  }

  #assertActive(): void {
    if (this.#disposed) {
      throw new ViewerDomainError(
        'Disposed',
        'The viewer controller has been disposed.',
        false,
      );
    }
  }

  #publish(next: ViewerSnapshot): void {
    this.#snapshot = next;
    for (const listener of this.#listeners) listener(cloneSnapshot(next));
  }

  #createGrid(): GridHelper {
    const grid = new GridHelper(
      400,
      20,
      this.#theme.gridCenter,
      this.#theme.grid,
    );
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -0.01;
    grid.name = 'viewer-grid';
    const mat = grid.material as LineBasicMaterial;
    mat.transparent = true;
    mat.opacity = 0.25;
    return grid;
  }

  #discardContextResources(): void {
    if (this.#modelRoot) disposeObjectTree(this.#modelRoot);
    this.#modelRoot = undefined;
    disposeObjectTree(this.#overlayRoot);
    if (this.#grid) disposeObjectTree(this.#grid);
    this.#grid = undefined;
  }

  #render = (): void => {
    if (this.#disposed || this.#snapshot.status === 'context-lost') return;
    this.#renderer.render(this.#scene, this.#camera);
  };

  #animate = (): void => {
    if (this.#disposed) return;
    this.#controls.update();
    this.#render();
    this.#animationFrame = requestAnimationFrame(this.#animate);
  };

  #onPointerDown = (event: PointerEvent): void => {
    this.#pointerDown = [event.clientX, event.clientY];
  };

  #onPointerUp = (event: PointerEvent): void => {
    const pointerDown = this.#pointerDown;
    this.#pointerDown = undefined;
    if (this.#snapshot.selectionMode === 'off' || !pointerDown) return;
    const movement = Math.hypot(
      event.clientX - pointerDown[0],
      event.clientY - pointerDown[1],
    );
    if (movement > 4) return;
    this.#selectAt(event);
  };

  #onContextLost = (event: Event): void => {
    event.preventDefault();
    this.#controls.enabled = false;
    // Dispose objects while their original context is still the active (lost)
    // context. Keeping them until after restoration leaves Three.js disposal
    // listeners holding VAOs from the old context generation.
    this.#discardContextResources();
    this.#publish({
      ...this.#snapshot,
      status: 'context-lost',
      failure: {
        code: 'ContextLost',
        message:
          'Chrome paused the WebGL context. Close GPU-heavy tabs, then restore the preview.',
        recoverable: true,
      },
    });
  };

  #onContextRestored = (): void => {
    if (this.#disposed) return;
    this.#controls.enabled = true;
    this.#grid = this.#createGrid();
    this.#scene.add(this.#grid, this.#overlayRoot);
    const model = this.#lastModel;
    if (!model) {
      this.clear();
      return;
    }
    void this.load(model).catch(() => undefined);
  };

  async #parsePart(part: ViewerPart): Promise<Object3D> {
    if (part.format === 'stl') {
      const geometry = new STLLoader().parse(part.bytes);
      geometry.computeVertexNormals();
      const material = new MeshStandardMaterial({
        color: part.region?.hex ?? this.#theme.defaultPart,
        metalness: 0.04,
        roughness: 0.72,
        side: DoubleSide,
      });
      return new Mesh(geometry, material);
    }
    if (part.format === '3mf') {
      // 3MF and Amagine3D both use Z-up coordinates. ThreeMFLoader's example
      // rotates for Three.js' default Y-up scene, but this viewer deliberately
      // keeps Z-up, so the parsed assembly must remain unrotated.
      return new ThreeMFLoader().parse(part.bytes);
    }
    if (part.format === 'glb') {
      const gltf = await new Promise<{ scene: Group }>((resolve, reject) => {
        new GLTFLoader().parse(
          part.bytes,
          '',
          (result) => resolve({ scene: result.scene }),
          reject,
        );
      });
      return gltf.scene;
    }
    throw new ViewerDomainError(
      'UnsupportedFormat',
      `${part.name} is not a 3MF, STL, or binary GLTF model.`,
      false,
    );
  }

  #decoratePart(root: Object3D, part: ViewerPart): void {
    let meshIndex = 0;
    root.traverse((object) => {
      if (!(object instanceof Mesh)) return;
      object.userData['viewerPartId'] = part.id;
      object.userData['viewerEntityId'] = `${part.id}:mesh:${meshIndex}`;
      if (part.region) object.userData['viewerRegion'] = part.region;
      object.castShadow = false;
      object.receiveShadow = false;
      if (part.region && part.format !== 'stl') {
        const previousMaterials = Array.isArray(object.material)
          ? object.material
          : [object.material];
        for (const material of previousMaterials) disposeMaterial(material);
        object.material = new MeshStandardMaterial({
          color: part.region.hex,
          metalness: 0.04,
          roughness: 0.72,
          side: DoubleSide,
        });
      }
      meshIndex += 1;
    });
    root.userData['viewerPartId'] = part.id;
    if (part.region) root.userData['viewerRegion'] = part.region;
    root.name = part.name;
  }

  #selectAt(event: PointerEvent): void {
    if (!this.#modelRoot) return;
    const rect = this.#canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    this.#pointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.#raycaster.setFromCamera(this.#pointer, this.#camera);
    const hit = this.#raycaster
      .intersectObject(this.#modelRoot, true)
      .find((candidate) => candidate.object instanceof Mesh);
    if (!hit || !(hit.object instanceof Mesh)) {
      this.select(undefined);
      return;
    }
    const selection = this.#selectionFromHit(hit, this.#snapshot.selectionMode);
    if (selection) this.select(selection);
  }

  #selectionFromHit(
    hit: Intersection<Object3D>,
    mode: ViewerSelectionMode,
  ): ViewerSelection | undefined {
    if (mode === 'off' || !(hit.object instanceof Mesh) || !hit.face) {
      return undefined;
    }
    const object = hit.object;
    const partId = object.userData['viewerPartId'];
    if (typeof partId !== 'string') return undefined;
    const position = object.geometry.getAttribute('position');
    if (!position) return undefined;
    const indices = [hit.face.a, hit.face.b, hit.face.c] as const;
    const vertices = [
      object.localToWorld(
        new Vector3().fromBufferAttribute(position, indices[0]),
      ),
      object.localToWorld(
        new Vector3().fromBufferAttribute(position, indices[1]),
      ),
      object.localToWorld(
        new Vector3().fromBufferAttribute(position, indices[2]),
      ),
    ] as const;
    let entitySuffix: string;
    let point = hit.point.clone();
    if (mode === 'vertex') {
      let closestIndex: 0 | 1 | 2 = 0;
      for (const index of [1, 2] as const) {
        if (
          vertices[index].distanceToSquared(hit.point) <
          vertices[closestIndex].distanceToSquared(hit.point)
        ) {
          closestIndex = index;
        }
      }
      point = vertices[closestIndex].clone();
      entitySuffix = `vertex:${indices[closestIndex]}`;
    } else if (mode === 'edge') {
      const edges = [
        [0, 1],
        [1, 2],
        [2, 0],
      ] as const;
      const candidates = edges.map(([from, to]) => {
        const closest = new Line3(
          vertices[from],
          vertices[to],
        ).closestPointToPoint(hit.point, true, new Vector3());
        return { from, to, closest, distance: closest.distanceTo(hit.point) };
      });
      const closestEdge = candidates.reduce((best, candidate) =>
        candidate.distance < best.distance ? candidate : best,
      );
      point = closestEdge.closest;
      const edgeIndices = [
        indices[closestEdge.from],
        indices[closestEdge.to],
      ].sort((left, right) => left - right);
      entitySuffix = `edge:${edgeIndices.join('-')}`;
    } else {
      entitySuffix = `face:${hit.faceIndex ?? 0}`;
    }
    const region = object.userData['viewerRegion'] as ViewerRegion | undefined;
    return {
      entityId: `${partId}:${entitySuffix}`,
      kind: mode,
      partId,
      point: toTuple(point),
      ...(region ? { region } : {}),
    };
  }

  #rebuildOverlays(): void {
    while (this.#overlayRoot.children.length > 0) {
      const child = this.#overlayRoot.children[0];
      if (child) disposeObjectTree(child);
    }
    if (this.#snapshot.selections.length === 0) return;
    const size = this.#snapshot.bounds
      ? Math.max(...this.#snapshot.bounds.size)
      : 10;
    const markerRadius = Math.max(size * 0.012, 0.15);
    for (const selection of this.#snapshot.selections) {
      const marker = new Mesh(
        new SphereGeometry(markerRadius, 16, 12),
        new MeshStandardMaterial({ color: this.#theme.selection }),
      );
      marker.position.fromArray(selection.point);
      this.#overlayRoot.add(marker);
    }
    const measurement = this.#snapshot.measurement;
    if (measurement) {
      const geometry = new BufferGeometry().setFromPoints([
        new Vector3().fromArray(measurement.from),
        new Vector3().fromArray(measurement.to),
      ]);
      this.#overlayRoot.add(
        new Line(
          geometry,
          new LineBasicMaterial({ color: this.#theme.measurement }),
        ),
      );
    }
  }

  clear(): void {
    this.#assertActive();
    if (this.#modelRoot) disposeObjectTree(this.#modelRoot);
    this.#modelRoot = undefined;
    this.#lastModel = undefined;
    this.#publish({
      status: 'empty',
      triangleCount: 0,
      regions: [],
      selectionMode: this.#snapshot.selectionMode,
      selections: [],
    });
    this.#rebuildOverlays();
    this.#render();
  }

  dispose(): void {
    if (this.#disposed) return;
    this.#canvas.removeEventListener('pointerdown', this.#onPointerDown);
    this.#canvas.removeEventListener('pointerup', this.#onPointerUp);
    this.#canvas.removeEventListener('webglcontextlost', this.#onContextLost);
    this.#canvas.removeEventListener(
      'webglcontextrestored',
      this.#onContextRestored,
    );
    this.#controls.removeEventListener('change', this.#render);
    this.#controls.dispose();
    if (this.#animationFrame !== undefined) {
      cancelAnimationFrame(this.#animationFrame);
    }
    if (this.#modelRoot) disposeObjectTree(this.#modelRoot);
    disposeObjectTree(this.#overlayRoot);
    if (this.#grid) disposeObjectTree(this.#grid);
    this.#grid = undefined;
    this.#renderer.dispose();
    // React Strict Mode disposes and recreates effects on the same canvas in
    // development. Forcing the context to be lost here makes that immediate
    // recreation inherit a lost WebGL context. WebGLRenderer.dispose() already
    // releases the renderer's GPU resources without poisoning the canvas for a
    // subsequent controller.
    this.#listeners.clear();
    this.#lastModel = undefined;
    this.#modelRoot = undefined;
    this.#disposed = true;
    this.#snapshot = {
      status: 'disposed',
      triangleCount: 0,
      regions: [],
      selectionMode: this.#snapshot.selectionMode,
      selections: [],
    };
  }

  fit(): void {
    this.#assertActive();
    this.#fitWithDirection();
  }

  #fitWithDirection(direction?: Vector3): void {
    if (!this.#modelRoot) return;
    const box = new Box3().setFromObject(this.#modelRoot);
    if (box.isEmpty()) return;
    const sphere = box.getBoundingSphere(new Sphere());
    const radius = Math.max(sphere.radius, 0.5);
    const cameraDirection =
      direction ??
      this.#camera.position.clone().sub(this.#controls.target).normalize();
    if (cameraDirection.lengthSq() === 0)
      cameraDirection.copy(viewDirection('isometric'));
    const verticalFov = (this.#camera.fov * Math.PI) / 180;
    const distance = (radius / Math.sin(verticalFov / 2)) * 1.18;
    this.#controls.target.copy(sphere.center);
    this.#camera.position
      .copy(sphere.center)
      .add(cameraDirection.normalize().multiplyScalar(distance));
    this.#camera.near = Math.max(radius / 1_000, 0.01);
    this.#camera.far = Math.max(radius * 1_000, 1_000);
    this.#camera.updateProjectionMatrix();
    this.#controls.maxDistance = radius * 100;
    this.#controls.update();
    this.#render();
  }

  getSnapshot(): ViewerSnapshot {
    return cloneSnapshot(this.#snapshot);
  }

  async load(model: ViewerModel): Promise<void> {
    this.#assertActive();
    this.#lastModel = model;
    const renderParts = viewerPartsForLayout(model);
    const totalBytes = renderParts.reduce(
      (sum, part) => sum + part.bytes.byteLength,
      0,
    );
    if (renderParts.length === 0) {
      const error = new ViewerDomainError(
        'EmptyModel',
        'The model contains no renderable parts.',
        true,
      );
      this.#publish({
        ...this.#snapshot,
        status: 'error',
        failure: error.toFailure(),
      });
      throw error;
    }
    if (totalBytes > this.#limits.maxBytes) {
      const error = new ViewerDomainError(
        'ModelTooLarge',
        `The model is ${(totalBytes / 1024 / 1024).toFixed(1)} MB; the preview limit is ${(this.#limits.maxBytes / 1024 / 1024).toFixed(0)} MB. Export a simplified preview mesh and try again.`,
        true,
      );
      this.#publish({
        ...this.#snapshot,
        status: 'error',
        failure: error.toFailure(),
      });
      throw error;
    }

    const loadableSnapshot = { ...this.#snapshot };
    delete loadableSnapshot.failure;
    delete loadableSnapshot.measurement;
    this.#publish({
      ...loadableSnapshot,
      status: 'loading',
      selections: [],
    });
    const candidate = new Group();
    candidate.name = model.name;
    const separatedObjects: Array<{
      object: Object3D;
      offset?: ViewerVector;
    }> = [];
    try {
      for (const part of renderParts) {
        const object = await this.#parsePart(part);
        this.#decoratePart(object, part);
        candidate.add(object);
        separatedObjects.push(
          ...separatedLayoutObjects(object, part.format).map(
            (layoutObject) => ({
              object: layoutObject,
              ...(part.explodedOffset === undefined
                ? {}
                : { offset: part.explodedOffset }),
            }),
          ),
        );
      }
      if (model.layout === 'separated') {
        if (!arrangePartsByExplodedOffsets(separatedObjects)) {
          arrangeSeparatedParts(separatedObjects.map(({ object }) => object));
        }
      }
      const triangleCount = countTriangles(candidate);
      const box = new Box3().setFromObject(candidate);
      if (triangleCount === 0 || box.isEmpty()) {
        throw new ViewerDomainError(
          'EmptyModel',
          'The model has no visible triangles.',
          true,
        );
      }
      if (triangleCount > this.#limits.maxTriangles) {
        throw new ViewerDomainError(
          'ModelTooLarge',
          `The model has ${triangleCount.toLocaleString()} triangles; the preview limit is ${this.#limits.maxTriangles.toLocaleString()}. Export a simplified preview mesh and try again.`,
          true,
        );
      }

      const previousRoot = this.#modelRoot;
      this.#modelRoot = candidate;
      this.#scene.add(candidate);
      if (previousRoot) disposeObjectTree(previousRoot);
      const regions = renderParts.flatMap((part) =>
        part.region ? [part.region] : [],
      );
      this.#publish({
        status: 'ready',
        modelId: model.id,
        modelName: model.name,
        bounds: boundsFromBox(box),
        triangleCount,
        regions,
        selectionMode: this.#snapshot.selectionMode,
        selections: [],
      });
      this.#rebuildOverlays();
      this.#fitWithDirection(viewDirection('isometric'));
    } catch (error) {
      disposeObjectTree(candidate);
      const failure = toViewerFailure(error);
      this.#publish({
        ...this.#snapshot,
        status: 'error',
        failure,
      });
      throw error instanceof Error
        ? error
        : new ViewerDomainError('LoadFailed', failure.message, true);
    }
  }

  resize(viewport: ViewerViewport): void {
    this.#assertActive();
    const width = Math.max(1, Math.floor(viewport.width));
    const height = Math.max(1, Math.floor(viewport.height));
    const pixelRatio = Math.min(
      Math.max(
        viewport.devicePixelRatio ?? globalThis.devicePixelRatio ?? 1,
        1,
      ),
      2,
    );
    this.#renderer.setPixelRatio(pixelRatio);
    this.#renderer.setSize(width, height, false);
    this.#camera.aspect = width / height;
    this.#camera.updateProjectionMatrix();
    this.#render();
  }

  async retry(): Promise<void> {
    this.#assertActive();
    if (this.#snapshot.status === 'context-lost') {
      this.#renderer.forceContextRestore();
      return;
    }
    if (!this.#lastModel) {
      throw new ViewerDomainError(
        'EmptyModel',
        'There is no model to load again.',
        true,
      );
    }
    await this.load(this.#lastModel);
  }

  select(selection: ViewerSelection | undefined): void {
    this.#assertActive();
    const selections = selection
      ? [
          ...this.#snapshot.selections.filter(
            (candidate) => candidate.entityId !== selection.entityId,
          ),
          selection,
        ].slice(-2)
      : [];
    const measurement = measureSelections(selections);
    const unmeasuredSnapshot = { ...this.#snapshot };
    delete unmeasuredSnapshot.measurement;
    this.#publish({
      ...unmeasuredSnapshot,
      selections,
      ...(measurement ? { measurement } : {}),
    });
    this.#rebuildOverlays();
    this.#render();
  }

  setSelectionMode(mode: ViewerSelectionMode): void {
    this.#assertActive();
    this.#publish({ ...this.#snapshot, selectionMode: mode });
  }

  setView(view: ViewerView): void {
    this.#assertActive();
    this.#fitWithDirection(viewDirection(view));
  }

  subscribe(listener: ViewerSnapshotListener): () => void {
    this.#assertActive();
    this.#listeners.add(listener);
    listener(this.getSnapshot());
    return () => this.#listeners.delete(listener);
  }
}

export function createThreeViewerController(
  options: ThreeViewerControllerOptions,
): ViewerController {
  return new ThreeViewerController(options);
}
