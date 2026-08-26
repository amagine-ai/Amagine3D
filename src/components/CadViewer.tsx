import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  AmbientLight,
  Box3,
  Color,
  DirectionalLight,
  GridHelper,
  Group,
  Mesh,
  MeshStandardMaterial,
  PerspectiveCamera,
  Scene,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
  type BufferGeometry,
  type Material,
  type Object3D,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { ThreeMFLoader } from 'three/examples/jsm/loaders/3MFLoader.js';

import styles from './CadViewer.module.css';
import { formatBytes } from '../lib/format';
import type { ArtifactSummary } from '../types';

type ViewName = 'front' | 'isometric' | 'top';
type ViewerState = 'empty' | 'error' | 'loading' | 'ready';

interface ViewerController {
  fit: () => void;
  setView: (view: ViewName) => void;
}

interface CadViewerProps {
  artifact?: ArtifactSummary;
  onStatusChange?: (status: string) => void;
}

function disposeMaterial(material: Material): void {
  for (const value of Object.values(material)) {
    if (value && typeof value === 'object' && 'dispose' in value) {
      const disposable = value as { dispose?: unknown };
      if (typeof disposable.dispose === 'function') disposable.dispose();
    }
  }
  material.dispose();
}

function disposeTree(root: Object3D): void {
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    object.geometry.dispose();
    const materials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    materials.forEach(disposeMaterial);
  });
}

function triangleCount(root: Object3D): number {
  let total = 0;
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    const geometry = object.geometry as BufferGeometry;
    total += geometry.index
      ? geometry.index.count / 3
      : (geometry.attributes.position?.count ?? 0) / 3;
  });
  return Math.round(total);
}

function directionForView(view: ViewName): Vector3 {
  switch (view) {
    case 'front':
      return new Vector3(0, -1, 0);
    case 'top':
      return new Vector3(0, 0, 1);
    case 'isometric':
      return new Vector3(1, -1, 0.8).normalize();
  }
}

async function parseModel(
  artifact: ArtifactSummary,
  buffer: ArrayBuffer,
): Promise<Object3D> {
  if (artifact.format === 'stl') {
    const geometry = new STLLoader().parse(buffer);
    geometry.computeVertexNormals();
    return new Mesh(
      geometry,
      new MeshStandardMaterial({
        color: new Color('#9ba7b3'),
        metalness: 0.04,
        roughness: 0.68,
      }),
    );
  }
  if (artifact.format === 'glb') {
    const gltf = await new GLTFLoader().parseAsync(buffer, '');
    return gltf.scene;
  }
  if (artifact.format === '3mf') return new ThreeMFLoader().parse(buffer);
  throw new Error('Browser preview supports STL, 3MF, and GLB.');
}

function ToolButton({
  children,
  disabled = false,
  onClick,
  pressed,
}: {
  children: ReactNode;
  disabled?: boolean;
  onClick: () => void;
  pressed?: boolean;
}) {
  return (
    <button
      aria-pressed={pressed}
      className={styles.toolButton}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  );
}

function StatePanel({
  detail,
  title,
  tone,
}: {
  detail: string;
  title: string;
  tone: 'empty' | 'error' | 'loading';
}) {
  return (
    <div className={styles.statePanel} data-tone={tone}>
      {tone === 'loading' ? <span className={styles.spinner} /> : null}
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

export function CadViewer({ artifact, onStatusChange }: CadViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<ViewerController | undefined>(undefined);
  const [bounds, setBounds] = useState<Vector3>();
  const [error, setError] = useState('');
  const [state, setState] = useState<ViewerState>('empty');
  const [triangles, setTriangles] = useState(0);

  const statusText =
    state === 'ready'
      ? `${triangles.toLocaleString()} triangles · ${artifact?.format === 'glb' ? 'display model' : 'print mesh'}`
      : state === 'loading'
        ? 'Reading model data…'
        : state === 'error'
          ? 'Model preview unavailable'
          : 'Waiting for model data';

  useEffect(() => {
    onStatusChange?.(statusText);
  }, [onStatusChange, statusText]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host || !artifact?.format) {
      controllerRef.current = undefined;
      setBounds(undefined);
      setTriangles(0);
      setState(artifact ? 'error' : 'empty');
      setError(
        artifact
          ? 'Browser preview supports STL, 3MF, and GLB display artifacts.'
          : '',
      );
      return;
    }

    const viewerHost = host;
    let disposed = false;
    let frame = 0;
    let model: Object3D | undefined;
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const scene = new Scene();
    const camera = new PerspectiveCamera(32, 1, 0.1, 100_000);
    camera.up.set(0, 0, 1);
    const renderer = new WebGLRenderer({
      alpha: true,
      antialias: true,
      canvas,
      powerPreference: 'high-performance',
    });
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.setClearAlpha(0);
    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = !reduceMotion;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;

    const computedStyles = getComputedStyle(document.documentElement);
    const gridColor =
      computedStyles.getPropertyValue('--color-canvas-grid').trim() || '#e8e8e8';
    const centerColor =
      computedStyles.getPropertyValue('--color-rule-strong').trim() || '#a1a1a1';
    const grid = new GridHelper(320, 32, centerColor, gridColor);
    grid.rotation.x = Math.PI / 2;
    scene.add(grid);

    const ambient = new AmbientLight(0xffffff, 1.7);
    const key = new DirectionalLight(0xffffff, 2.8);
    key.position.set(5, -6, 9);
    const fill = new DirectionalLight(0xdcecff, 1.1);
    fill.position.set(-6, 4, 3);
    scene.add(ambient, key, fill);

    function draw(): void {
      if (!disposed) renderer.render(scene, camera);
    }

    function animate(): void {
      if (disposed) return;
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(animate);
    }

    function resize(): void {
      const width = Math.max(1, viewerHost.clientWidth);
      const height = Math.max(1, viewerHost.clientHeight);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      if (reduceMotion) draw();
    }

    function fit(direction = directionForView('isometric')): void {
      if (!model) return;
      const modelBounds = new Box3().setFromObject(model);
      if (modelBounds.isEmpty()) return;
      const center = modelBounds.getCenter(new Vector3());
      const size = modelBounds.getSize(new Vector3());
      const radius = Math.max(size.x, size.y, size.z, 1);
      const distance = radius / Math.tan((camera.fov * Math.PI) / 360) * 0.78;
      controls.target.copy(center);
      camera.position.copy(center).add(direction.clone().multiplyScalar(distance));
      camera.near = Math.max(distance / 1_000, 0.01);
      camera.far = distance * 20;
      camera.updateProjectionMatrix();
      controls.update();
      draw();
    }

    controllerRef.current = {
      fit: () => fit(),
      setView: (view) => fit(directionForView(view)),
    };

    const observer = new ResizeObserver(resize);
    observer.observe(viewerHost);
    controls.addEventListener('change', draw);
    resize();
    if (!reduceMotion) frame = requestAnimationFrame(animate);

    const controller = new AbortController();
    setState('loading');
    setError('');
    void fetch(artifact.url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Unable to read model (${String(response.status)})`);
        return response.arrayBuffer();
      })
      .then((buffer) => parseModel(artifact, buffer))
      .then((loaded) => {
        if (disposed) {
          disposeTree(loaded);
          return;
        }
        const root = new Group();
        root.name = artifact.name;
        root.add(loaded);
        model = root;
        scene.add(root);
        setBounds(new Box3().setFromObject(root).getSize(new Vector3()));
        setTriangles(triangleCount(root));
        fit();
        setState('ready');
      })
      .catch((reason: unknown) => {
        if (disposed || (reason instanceof DOMException && reason.name === 'AbortError')) {
          return;
        }
        setError(reason instanceof Error ? reason.message : 'The model could not be rendered.');
        setState('error');
      });

    return () => {
      disposed = true;
      controller.abort();
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.removeEventListener('change', draw);
      controls.dispose();
      if (model) disposeTree(model);
      renderer.dispose();
      controllerRef.current = undefined;
    };
  }, [artifact]);

  const ready = state === 'ready';
  return (
    <section
      aria-label="CAD model viewer"
      className={styles.shell}
      data-viewer-status={state}
    >
      <header className={styles.header}>
        <div className={styles.modelIdentity}>
          <h2>{artifact?.name ?? 'Model preview'}</h2>
        </div>
        <div className={styles.viewerSummary} data-status={state}>
          <span aria-hidden="true" className={styles.statusMark} />
          <span>{statusText}</span>
        </div>
      </header>

      <div className={styles.stage} ref={hostRef}>
        <canvas
          aria-label="Interactive 3D preview. Drag to orbit, scroll to zoom."
          className={styles.canvas}
          ref={canvasRef}
          tabIndex={0}
        />
        <div className={styles.selectionTools} aria-label="Selection mode" role="group">
          <ToolButton disabled={!ready} onClick={() => undefined} pressed={ready}>
            Face
          </ToolButton>
          <ToolButton disabled onClick={() => undefined}>Edge</ToolButton>
          <ToolButton disabled onClick={() => undefined}>Point</ToolButton>
          <ToolButton disabled={!ready} onClick={() => undefined}>Orbit</ToolButton>
        </div>
        <div className={styles.viewTools} aria-label="Camera views" role="group">
          <ToolButton disabled={!ready} onClick={() => controllerRef.current?.fit()}>
            Fit
          </ToolButton>
          <ToolButton
            disabled={!ready}
            onClick={() => controllerRef.current?.setView('isometric')}
          >
            ISO
          </ToolButton>
          <ToolButton
            disabled={!ready}
            onClick={() => controllerRef.current?.setView('front')}
          >
            Front
          </ToolButton>
          <ToolButton
            disabled={!ready}
            onClick={() => controllerRef.current?.setView('top')}
          >
            Top
          </ToolButton>
        </div>

        {state === 'empty' ? (
          <StatePanel
            detail="Load a run with an STL, 3MF, or GLB preview artifact."
            title="No model loaded"
            tone="empty"
          />
        ) : null}
        {state === 'loading' ? (
          <StatePanel
            detail="Large meshes can take a moment to prepare."
            title="Loading geometry"
            tone="loading"
          />
        ) : null}
        {state === 'error' ? (
          <StatePanel detail={error} title="Model load failed" tone="error" />
        ) : null}

        {ready && bounds ? (
          <footer className={styles.readout}>
            <span>
              {[bounds.x, bounds.y, bounds.z].map((value) => value.toFixed(1)).join(' × ')} mm
            </span>
            <span>{formatBytes(artifact?.size ?? 0)}</span>
          </footer>
        ) : null}
      </div>
    </section>
  );
}
