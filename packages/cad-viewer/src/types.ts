import type { ColorRegion, JsonValue } from '@amagine3d/cad-protocol';

export type ViewerFormat = '3mf' | 'glb' | 'stl';

export type ViewerRegion = Pick<
  ColorRegion,
  'colorName' | 'features' | 'filament' | 'hex' | 'id' | 'name'
> & {
  metadata?: Readonly<Record<string, JsonValue>>;
};

export type ViewerPart = {
  id: string;
  name: string;
  format: ViewerFormat;
  bytes: ArrayBuffer;
  region?: ViewerRegion;
  explodedOffset?: ViewerVector;
};

export type ViewerModel = {
  id: string;
  name: string;
  parts: readonly ViewerPart[];
  separatedParts?: readonly ViewerPart[];
  layout?: 'assembled' | 'separated';
};

export type ViewerVector = readonly [number, number, number];
export type ViewerSelectionKind = 'face' | 'edge' | 'vertex';
export type ViewerSelectionMode = ViewerSelectionKind | 'off';
export type ViewerView = 'front' | 'isometric' | 'right' | 'top';

export type ViewerSelection = {
  entityId: string;
  kind: ViewerSelectionKind;
  partId: string;
  point: ViewerVector;
  region?: ViewerRegion;
};

export type ViewerMeasurement = {
  id: string;
  from: ViewerVector;
  fromEntityId: string;
  to: ViewerVector;
  toEntityId: string;
  distanceMm: number;
};

export type ViewerBounds = {
  min: ViewerVector;
  max: ViewerVector;
  size: ViewerVector;
};

export type ViewerErrorCode =
  | 'ContextLost'
  | 'Disposed'
  | 'EmptyModel'
  | 'LoadFailed'
  | 'ModelTooLarge'
  | 'RendererUnavailable'
  | 'UnsupportedFormat';

export type ViewerFailure = {
  code: ViewerErrorCode;
  message: string;
  recoverable: boolean;
};

export type ViewerStatus =
  'context-lost' | 'disposed' | 'empty' | 'error' | 'loading' | 'ready';

export type ViewerSnapshot = {
  status: ViewerStatus;
  modelId?: string;
  modelName?: string;
  bounds?: ViewerBounds;
  triangleCount: number;
  regions: readonly ViewerRegion[];
  selectionMode: ViewerSelectionMode;
  selections: readonly ViewerSelection[];
  measurement?: ViewerMeasurement;
  failure?: ViewerFailure;
};

export type ViewerViewport = {
  width: number;
  height: number;
  devicePixelRatio?: number;
};

export type ViewerSnapshotListener = (snapshot: ViewerSnapshot) => void;

export interface ViewerController {
  clear(): void;
  dispose(): void;
  fit(): void;
  getSnapshot(): ViewerSnapshot;
  load(model: ViewerModel): Promise<void>;
  resize(viewport: ViewerViewport): void;
  retry(): Promise<void>;
  select(selection: ViewerSelection | undefined): void;
  setSelectionMode(mode: ViewerSelectionMode): void;
  setView(view: ViewerView): void;
  subscribe(listener: ViewerSnapshotListener): () => void;
}

export type ViewerLimits = {
  maxBytes: number;
  maxTriangles: number;
};

export const DEFAULT_VIEWER_LIMITS: ViewerLimits = {
  maxBytes: 128 * 1024 * 1024,
  maxTriangles: 2_000_000,
};
