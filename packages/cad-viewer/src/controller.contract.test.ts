import { describe, expect, it } from 'vitest';

import { createFakeViewerController } from './fake-controller';
import type { ViewerModel, ViewerSelection } from './types';

const partBytes = new TextEncoder().encode('solid test\nendsolid test').buffer;

function model(id: string): ViewerModel {
  return {
    id,
    name: `Model ${id}`,
    parts: [
      { id: `${id}-part`, name: `${id}.stl`, format: 'stl', bytes: partBytes },
    ],
  };
}

const firstSelection: ViewerSelection = {
  entityId: 'part:face:1',
  kind: 'face',
  partId: 'part',
  point: [0, 0, 0],
};
const secondSelection: ViewerSelection = {
  entityId: 'part:vertex:2',
  kind: 'vertex',
  partId: 'part',
  point: [3, 4, 0],
};

describe('ViewerController contract', () => {
  it('publishes loading and ready state and replaces model resources', async () => {
    const controller = createFakeViewerController();
    const statuses: string[] = [];
    controller.subscribe((snapshot) => statuses.push(snapshot.status));

    await controller.load(model('first'));
    await controller.load(model('second'));

    expect(statuses).toEqual(['empty', 'loading', 'ready', 'loading', 'ready']);
    expect(controller.getSnapshot().modelId).toBe('second');
    expect(controller.releasedModelIds).toEqual(['first']);
  });

  it('maintains at most two selections and derives a millimetre measurement', async () => {
    const controller = createFakeViewerController();
    await controller.load(model('measure'));

    controller.select(firstSelection);
    controller.select(secondSelection);

    expect(controller.getSnapshot().selections).toEqual([
      firstSelection,
      secondSelection,
    ]);
    expect(controller.getSnapshot().measurement?.distanceMm).toBe(5);

    controller.select(undefined);
    expect(controller.getSnapshot().selections).toEqual([]);
    expect(controller.getSnapshot().measurement).toBeUndefined();
  });

  it('covers resize, fit, view, selection mode, clear and idempotent dispose', async () => {
    const controller = createFakeViewerController();
    await controller.load(model('commands'));
    controller.resize({ width: 900, height: 600, devicePixelRatio: 2 });
    controller.fit();
    controller.setView('top');
    controller.setSelectionMode('edge');
    controller.clear();
    controller.dispose();
    controller.dispose();

    expect(controller.calls).toEqual([
      'load:commands',
      'resize:900x600',
      'fit',
      'view:top',
      'selection-mode:edge',
      'clear',
      'dispose',
    ]);
    expect(controller.getSnapshot().status).toBe('disposed');
  });

  it('surfaces empty models as recoverable named failures', async () => {
    const controller = createFakeViewerController();
    const empty: ViewerModel = { id: 'empty', name: 'Empty', parts: [] };

    await expect(controller.load(empty)).rejects.toMatchObject({
      code: 'EmptyModel',
    });
    expect(controller.getSnapshot()).toMatchObject({
      status: 'error',
      failure: { code: 'EmptyModel', recoverable: true },
    });
  });
});
