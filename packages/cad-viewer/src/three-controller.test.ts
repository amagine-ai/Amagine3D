import {
  Box3,
  BoxGeometry,
  BufferGeometry,
  Group,
  Mesh,
  MeshBasicMaterial,
  Texture,
  Vector3,
} from 'three';
import { describe, expect, it, vi } from 'vitest';

import {
  arrangePartsByExplodedOffsets,
  arrangeSeparatedParts,
  disposeObjectTree,
  separatedLayoutObjects,
  viewerPartsForLayout,
} from './three-controller';

describe('Three viewer resource lifecycle', () => {
  it('disposes geometry, material and texture resources', () => {
    const geometry = new BufferGeometry();
    const material = new MeshBasicMaterial();
    const texture = new Texture();
    material.map = texture;
    const geometryDispose = vi.spyOn(geometry, 'dispose');
    const materialDispose = vi.spyOn(material, 'dispose');
    const textureDispose = vi.spyOn(texture, 'dispose');
    const mesh = new Mesh(geometry, material);

    disposeObjectTree(mesh);

    expect(geometryDispose).toHaveBeenCalledOnce();
    expect(materialDispose).toHaveBeenCalledOnce();
    expect(textureDispose).toHaveBeenCalledOnce();
  });
});

describe('separated preview layout', () => {
  it('uses the original 3MF when assembled and manifest STLs when exploded', () => {
    const model = {
      id: 'pomodoro',
      name: 'Pomodoro',
      parts: [
        {
          id: 'model-3mf',
          name: 'model.3mf',
          format: '3mf' as const,
          bytes: new ArrayBuffer(1),
        },
      ],
      separatedParts: [
        {
          id: 'housing',
          name: 'housing.stl',
          format: 'stl' as const,
          bytes: new ArrayBuffer(1),
          explodedOffset: [0, 0, 20] as const,
        },
      ],
    };

    expect(viewerPartsForLayout(model)).toBe(model.parts);
    expect(viewerPartsForLayout({ ...model, layout: 'separated' })).toBe(
      model.separatedParts,
    );
  });

  it('applies manifest offsets to every STL in the same logical part', () => {
    const panel = new Mesh(new BoxGeometry(4, 4, 2), new MeshBasicMaterial());
    const whitePixels = new Mesh(
      new BoxGeometry(1, 1, 1),
      new MeshBasicMaterial(),
    );
    const button = new Mesh(new BoxGeometry(2, 2, 2), new MeshBasicMaterial());

    expect(
      arrangePartsByExplodedOffsets([
        { object: panel, offset: [0, -70, 0] },
        { object: whitePixels, offset: [0, -70, 0] },
        { object: button, offset: [0, 0, 50] },
      ]),
    ).toBe(true);
    expect(panel.position.toArray()).toEqual([0, -70, 0]);
    expect(whitePixels.position.toArray()).toEqual([0, -70, 0]);
    expect(button.position.toArray()).toEqual([0, 0, 50]);

    disposeObjectTree(panel);
    disposeObjectTree(whitePixels);
    disposeObjectTree(button);
  });

  it('uses 3MF assembly groups but keeps material meshes together', () => {
    const assemblyRoot = new Group();
    const buildItems = [new Group(), new Group(), new Group(), new Group()];
    for (const item of buildItems) {
      item.add(new Mesh(new BoxGeometry(2, 2, 2), new MeshBasicMaterial()));
      assemblyRoot.add(item);
    }

    expect(separatedLayoutObjects(assemblyRoot, '3mf')).toEqual(buildItems);
    expect(separatedLayoutObjects(assemblyRoot, 'stl')).toEqual([assemblyRoot]);

    const materialRoot = new Group();
    const oneBuildItem = new Group();
    oneBuildItem.add(
      new Mesh(new BoxGeometry(2, 2, 2), new MeshBasicMaterial()),
      new Mesh(new BoxGeometry(1, 1, 1), new MeshBasicMaterial()),
    );
    materialRoot.add(oneBuildItem);
    expect(separatedLayoutObjects(materialRoot, '3mf')).toEqual([materialRoot]);

    disposeObjectTree(assemblyRoot);
    disposeObjectTree(materialRoot);
  });

  it('centres a non-overlapping row and places every part on the grid', () => {
    const parts = [
      new Mesh(new BoxGeometry(20, 8, 4), new MeshBasicMaterial()),
      new Mesh(new BoxGeometry(6, 14, 10), new MeshBasicMaterial()),
      new Mesh(new BoxGeometry(12, 5, 3), new MeshBasicMaterial()),
    ];

    arrangeSeparatedParts(parts);

    const boxes = parts.map((part) => new Box3().setFromObject(part));
    for (let index = 1; index < boxes.length; index += 1) {
      expect(boxes[index - 1]?.max.x).toBeLessThan(boxes[index]?.min.x ?? 0);
    }
    const complete = boxes.reduce(
      (bounds, box) => bounds.union(box),
      new Box3(),
    );
    expect(complete.getCenter(new Vector3()).x).toBeCloseTo(0, 10);
    for (const box of boxes) {
      expect(box.getCenter(new Vector3()).y).toBeCloseTo(0, 10);
      expect(box.min.z).toBeCloseTo(0, 10);
    }

    for (const part of parts) disposeObjectTree(part);
  });
});
