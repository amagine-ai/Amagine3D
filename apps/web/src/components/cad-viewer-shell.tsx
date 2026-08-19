'use client';

import {
  createThreeViewerController,
  type ViewerController,
  type ViewerModel,
  type ViewerSelectionMode,
  type ViewerSnapshot,
  type ViewerView,
} from '@amagine3d/cad-viewer';
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';

import styles from './cad-viewer-shell.module.css';

const initialSnapshot: ViewerSnapshot = {
  status: 'empty',
  triangleCount: 0,
  regions: [],
  selectionMode: 'face',
  selections: [],
};

export type CadViewerShellProps = {
  model?: ViewerModel;
  emptyAction?: { label: string; run: () => void };
  controllerFactory?: (canvas: HTMLCanvasElement) => ViewerController;
  toolbarTarget?: Element | null;
};

// Viewer colours mirror the restrained light workbench palette while keeping
// selection and measurement states unmistakable against the neutral canvas.
const defaultControllerFactory = (canvas: HTMLCanvasElement) =>
  createThreeViewerController({
    canvas,
    theme: {
      defaultPart: '#9ba7b3',
      grid: '#d4dbe0',
      gridCenter: '#aeb8c0',
      measurement: '#4f789c',
      selection: '#d39134',
    },
  });

type ToolButtonProps = {
  children: ReactNode;
  disabled?: boolean;
  onClick: () => void;
  pressed?: boolean;
  state?: 'default' | 'error' | 'loading' | 'success';
};

function ToolButton({
  children,
  disabled = false,
  onClick,
  pressed,
  state = 'default',
}: ToolButtonProps) {
  return (
    <button
      aria-pressed={pressed}
      className={styles.toolButton}
      data-state={state}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  );
}

function StatePanel({
  action,
  detail,
  title,
  tone,
}: {
  action?: ReactNode;
  detail: string;
  title: string;
  tone: 'empty' | 'error' | 'loading';
}) {
  return (
    <div className={styles.statePanel} data-tone={tone}>
      {tone === 'loading' ? <span className={styles.spinner} /> : null}
      <strong>{title}</strong>
      <span>{detail}</span>
      {action}
    </div>
  );
}

export function CadViewerShell({
  model,
  emptyAction,
  controllerFactory = defaultControllerFactory,
  toolbarTarget,
}: CadViewerShellProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<ViewerController | undefined>(undefined);
  const [snapshot, setSnapshot] = useState(initialSnapshot);
  const [showLoading, setShowLoading] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    let controller: ViewerController;
    try {
      controller = controllerFactory(canvas);
    } catch (error) {
      setSnapshot({
        ...initialSnapshot,
        status: 'error',
        failure: {
          code: 'RendererUnavailable',
          message:
            error instanceof Error
              ? error.message
              : 'WebGL could not start. Check Chrome hardware acceleration.',
          recoverable: true,
        },
      });
      return;
    }
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe(setSnapshot);
    const resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return;
      controller.resize({
        width: entry.contentRect.width,
        height: entry.contentRect.height,
        devicePixelRatio: globalThis.devicePixelRatio,
      });
    });
    resizeObserver.observe(host);
    return () => {
      resizeObserver.disconnect();
      unsubscribe();
      controller.dispose();
      controllerRef.current = undefined;
    };
  }, [controllerFactory]);

  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller) return;
    if (!model) {
      controller.clear();
      return;
    }
    if (model.layout === 'separated') controller.setSelectionMode('off');
    void controller.load(model).catch(() => undefined);
  }, [model]);

  useEffect(() => {
    if (snapshot.status !== 'loading') {
      setShowLoading(false);
      return;
    }
    const timer = globalThis.setTimeout(() => setShowLoading(true), 150);
    return () => globalThis.clearTimeout(timer);
  }, [snapshot.status]);

  const command = (run: (controller: ViewerController) => void) => {
    const controller = controllerRef.current;
    if (controller) run(controller);
  };
  const chooseMode = (mode: ViewerSelectionMode) =>
    command((controller) => controller.setSelectionMode(mode));
  const chooseView = (view: ViewerView) =>
    command((controller) => controller.setView(view));

  const onCanvasKeyDown = (event: KeyboardEvent<HTMLCanvasElement>) => {
    const key = event.key.toLowerCase();
    if (key === 'f') command((controller) => controller.fit());
    else if (key === '1') chooseView('isometric');
    else if (key === '2') chooseView('front');
    else if (key === '3') chooseView('top');
    else return;
    event.preventDefault();
  };

  const ready = snapshot.status === 'ready';
  const isSeparated = model?.layout === 'separated';
  const bounds = snapshot.bounds?.size;
  const failure = snapshot.failure;
  const viewerStatus = ready
    ? isSeparated
      ? `${snapshot.triangleCount.toLocaleString()} triangles · viewer-only exploded layout`
      : `${snapshot.triangleCount.toLocaleString()} triangles · assembly coordinates`
    : snapshot.status === 'loading'
      ? 'Reading model data…'
      : 'Waiting for model data';
  const viewerSummary = (
    <div
      className={styles.viewerSummary}
      aria-live="polite"
      data-status={snapshot.status}
    >
      <span className={styles.statusMark} aria-hidden="true" />
      <span>{viewerStatus}</span>
    </div>
  );
  return (
    <section
      aria-label="CAD model viewer"
      className={styles.shell}
      data-external-toolbar={toolbarTarget === undefined ? undefined : 'true'}
      data-viewer-status={snapshot.status}
    >
      {toolbarTarget === undefined ? (
        <header className={styles.header}>
          <div className={styles.modelIdentity}>
            <h2>{snapshot.modelName ?? 'Model preview'}</h2>
          </div>
          {viewerSummary}
        </header>
      ) : toolbarTarget === null ? null : (
        createPortal(viewerSummary, toolbarTarget)
      )}

      <div className={styles.stage} ref={hostRef}>
        <canvas
          aria-label="Interactive 3D preview. Drag to orbit, scroll to zoom. Press F to fit."
          className={styles.canvas}
          onKeyDown={onCanvasKeyDown}
          ref={canvasRef}
          tabIndex={0}
        />

        <div
          className={styles.selectionTools}
          aria-label="Selection mode"
          role="group"
        >
          {(['face', 'edge', 'vertex', 'off'] as const).map((mode) => (
            <ToolButton
              disabled={!ready || isSeparated}
              key={mode}
              onClick={() => chooseMode(mode)}
              pressed={snapshot.selectionMode === mode}
            >
              {mode === 'vertex'
                ? 'Point'
                : mode === 'off'
                  ? 'Orbit'
                  : `${mode[0]?.toUpperCase()}${mode.slice(1)}`}
            </ToolButton>
          ))}
        </div>

        <div
          className={styles.viewTools}
          aria-label="Camera views"
          role="group"
        >
          <ToolButton
            disabled={!ready}
            onClick={() => command((item) => item.fit())}
          >
            Fit
          </ToolButton>
          <ToolButton disabled={!ready} onClick={() => chooseView('isometric')}>
            ISO
          </ToolButton>
          <ToolButton disabled={!ready} onClick={() => chooseView('front')}>
            Front
          </ToolButton>
          <ToolButton disabled={!ready} onClick={() => chooseView('top')}>
            Top
          </ToolButton>
        </div>

        {!isSeparated && snapshot.measurement ? (
          <output className={styles.measurement} aria-live="polite">
            <span>Selected distance</span>
            <strong>{snapshot.measurement.distanceMm.toFixed(2)} mm</strong>
          </output>
        ) : null}

        {snapshot.regions.length > 0 ? (
          <aside className={styles.regionLegend} aria-label="Color regions">
            <strong>Regions</strong>
            <ul>
              {snapshot.regions.map((region) => (
                <li key={region.id}>
                  <span
                    aria-hidden="true"
                    className={styles.regionSwatch}
                    style={{ backgroundColor: region.hex }}
                  />
                  <span>{region.name}</span>
                  <small>{region.colorName}</small>
                </li>
              ))}
            </ul>
          </aside>
        ) : null}

        {snapshot.status === 'empty' ? (
          <StatePanel
            action={
              emptyAction ? (
                <ToolButton onClick={emptyAction.run}>
                  {emptyAction.label}
                </ToolButton>
              ) : undefined
            }
            detail="Load a run with an STL or 3MF preview artifact."
            title="No model loaded"
            tone="empty"
          />
        ) : null}
        {snapshot.status === 'loading' && showLoading ? (
          <StatePanel
            detail="Large meshes can take a moment to prepare."
            title="Loading geometry"
            tone="loading"
          />
        ) : null}
        {snapshot.status === 'error' || snapshot.status === 'context-lost' ? (
          <StatePanel
            action={
              failure?.recoverable ? (
                <ToolButton
                  onClick={() =>
                    void controllerRef.current?.retry().catch(() => undefined)
                  }
                  state="error"
                >
                  Restore preview
                </ToolButton>
              ) : undefined
            }
            detail={failure?.message ?? 'The preview could not be rendered.'}
            title={
              snapshot.status === 'context-lost'
                ? 'WebGL context paused'
                : 'Model load failed'
            }
            tone="error"
          />
        ) : null}

        {ready && bounds ? (
          <footer className={styles.readout}>
            {isSeparated ? (
              <>
                <span>Display transforms only</span>
                <span>Measurements disabled</span>
              </>
            ) : (
              <>
                <span>
                  {bounds.map((value) => value.toFixed(1)).join(' × ')} mm
                </span>
                <span>{snapshot.selections.length}/2 selected</span>
              </>
            )}
          </footer>
        ) : null}
      </div>
    </section>
  );
}
