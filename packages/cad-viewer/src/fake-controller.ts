import { ViewerDomainError, toViewerFailure } from './errors';
import { measureSelections } from './measurement';
import type {
  ViewerController,
  ViewerModel,
  ViewerSelection,
  ViewerSelectionMode,
  ViewerSnapshot,
  ViewerSnapshotListener,
  ViewerView,
  ViewerViewport,
} from './types';

export type FakeViewerController = ViewerController & {
  readonly calls: readonly string[];
  readonly releasedModelIds: readonly string[];
};

function estimateTriangleCount(model: ViewerModel): number {
  const decoder = new TextDecoder();
  return model.parts.reduce((total, part) => {
    if (part.format !== 'stl') return total;
    const text = decoder.decode(part.bytes);
    return total + (text.match(/\bfacet\s+normal\b/gu)?.length ?? 0);
  }, 0);
}

export function createFakeViewerController(): FakeViewerController {
  const calls: string[] = [];
  const releasedModelIds: string[] = [];
  const listeners = new Set<ViewerSnapshotListener>();
  let lastModel: ViewerModel | undefined;
  let disposed = false;
  let snapshot: ViewerSnapshot = {
    status: 'empty',
    triangleCount: 0,
    regions: [],
    selectionMode: 'face',
    selections: [],
  };

  const publish = (next: ViewerSnapshot) => {
    snapshot = next;
    for (const listener of listeners) listener(snapshot);
  };

  const assertActive = () => {
    if (disposed) {
      throw new ViewerDomainError(
        'Disposed',
        'The viewer controller has been disposed.',
        false,
      );
    }
  };

  const controller: FakeViewerController = {
    calls,
    releasedModelIds,
    clear() {
      assertActive();
      calls.push('clear');
      if (lastModel) releasedModelIds.push(lastModel.id);
      lastModel = undefined;
      publish({
        status: 'empty',
        triangleCount: 0,
        regions: [],
        selectionMode: snapshot.selectionMode,
        selections: [],
      });
    },
    dispose() {
      if (disposed) return;
      calls.push('dispose');
      if (lastModel) releasedModelIds.push(lastModel.id);
      lastModel = undefined;
      disposed = true;
      listeners.clear();
      snapshot = {
        status: 'disposed',
        triangleCount: 0,
        regions: [],
        selectionMode: snapshot.selectionMode,
        selections: [],
      };
    },
    fit() {
      assertActive();
      calls.push('fit');
    },
    getSnapshot() {
      return snapshot;
    },
    async load(model) {
      assertActive();
      calls.push(`load:${model.id}`);
      const loadableSnapshot = { ...snapshot };
      delete loadableSnapshot.failure;
      publish({ ...loadableSnapshot, status: 'loading' });
      try {
        if (model.parts.length === 0) {
          throw new ViewerDomainError(
            'EmptyModel',
            'The model contains no renderable parts.',
            true,
          );
        }
        if (lastModel) releasedModelIds.push(lastModel.id);
        lastModel = model;
        publish({
          status: 'ready',
          modelId: model.id,
          modelName: model.name,
          triangleCount: estimateTriangleCount(model),
          regions: model.parts.flatMap((part) =>
            part.region ? [part.region] : [],
          ),
          selectionMode: snapshot.selectionMode,
          selections: [],
        });
      } catch (error) {
        publish({
          ...snapshot,
          status: 'error',
          failure: toViewerFailure(error),
        });
        throw error;
      }
    },
    resize(viewport: ViewerViewport) {
      assertActive();
      calls.push(`resize:${viewport.width}x${viewport.height}`);
    },
    async retry() {
      assertActive();
      calls.push('retry');
      if (!lastModel) {
        throw new ViewerDomainError(
          'EmptyModel',
          'There is no model to load again.',
          true,
        );
      }
      await controller.load(lastModel);
    },
    select(selection: ViewerSelection | undefined) {
      assertActive();
      calls.push(selection ? `select:${selection.entityId}` : 'select:none');
      const selections = selection
        ? [
            ...snapshot.selections.filter(
              (candidate) => candidate.entityId !== selection.entityId,
            ),
            selection,
          ].slice(-2)
        : [];
      const measurement = measureSelections(selections);
      const unmeasuredSnapshot = { ...snapshot };
      delete unmeasuredSnapshot.measurement;
      publish({
        ...unmeasuredSnapshot,
        selections,
        ...(measurement ? { measurement } : {}),
      });
    },
    setSelectionMode(mode: ViewerSelectionMode) {
      assertActive();
      calls.push(`selection-mode:${mode}`);
      publish({ ...snapshot, selectionMode: mode });
    },
    setView(view: ViewerView) {
      assertActive();
      calls.push(`view:${view}`);
    },
    subscribe(listener: ViewerSnapshotListener) {
      assertActive();
      listeners.add(listener);
      listener(snapshot);
      return () => listeners.delete(listener);
    },
  };

  return controller;
}
