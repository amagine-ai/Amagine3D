import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { BoxGeometry, Group, Mesh, MeshBasicMaterial } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

import { referenceVisibility } from '../src/lib/reference-visibility.ts';

function mesh(role?: string): Mesh {
  const result = new Mesh(new BoxGeometry(10, 20, 30), new MeshBasicMaterial());
  if (role) result.userData.amagine3d = { role, physicalFeatureRef: 'component-1' };
  return result;
}

test('hides only explicitly classified reference geometry without changing the assembly', () => {
  const root = new Group();
  const manufactured = mesh('manufactured');
  const reference = mesh('display-only');
  const unknown = mesh();
  reference.position.set(10, 20, 30);
  reference.rotation.set(0.2, 0.4, 0.6);
  root.add(manufactured, reference, unknown);
  root.updateMatrixWorld(true);
  const originalTransform = reference.matrixWorld.clone();
  const originalGeometry = reference.geometry;
  const originalMaterial = reference.material;
  const controller = referenceVisibility(root);
  assert.deepEqual(controller.info, {
    referenceMeshes: 1, manufacturedMeshes: 1, unclassifiedMeshes: 1,
  });

  controller.setHidden(true);
  assert.equal(reference.visible, false);
  assert.equal(manufactured.visible, true);
  assert.equal(unknown.visible, true);
  assert.deepEqual(reference.matrixWorld, originalTransform);
  assert.equal(reference.geometry, originalGeometry);
  assert.equal(reference.material, originalMaterial);
  assert.equal(reference.parent, root);
  controller.setHidden(false);
  assert.equal(reference.visible, true);
});

test('inherits group extras but honors an explicit manufactured child', () => {
  const root = new Group();
  root.userData.amagine3d = { role: 'display-only' };
  const inherited = mesh();
  const manufactured = mesh('manufactured');
  root.add(inherited, manufactured);
  const controller = referenceVisibility(root);
  controller.setHidden(true);
  assert.equal(root.visible, true);
  assert.equal(inherited.visible, false);
  assert.equal(manufactured.visible, true);
  assert.deepEqual(controller.info, {
    referenceMeshes: 1, manufacturedMeshes: 1, unclassifiedMeshes: 0,
  });
});

test('legacy names, colors, and malformed extras never classify geometry', () => {
  const root = new Group();
  const legacy = mesh();
  legacy.name = 'display-only-lcd-reference';
  const unsupported = mesh('screen');
  const malformed = mesh();
  malformed.userData.amagine3d = 'display-only';
  root.add(legacy, unsupported, malformed);
  const controller = referenceVisibility(root);
  controller.setHidden(true);
  assert.deepEqual(controller.info, {
    referenceMeshes: 0, manufacturedMeshes: 0, unclassifiedMeshes: 3,
  });
  assert.ok(root.children.every((child) => child.visible));
});

test('showing references restores their original visibility and repeated toggles are stable', () => {
  const root = new Group();
  const visible = mesh('display-only');
  const hidden = mesh('display-only');
  hidden.visible = false;
  root.add(visible, hidden);
  const controller = referenceVisibility(root);
  for (let index = 0; index < 3; index += 1) {
    controller.setHidden(true);
    assert.equal(visible.visible, false);
    assert.equal(hidden.visible, false);
    controller.setHidden(false);
    assert.equal(visible.visible, true);
    assert.equal(hidden.visible, false);
  }
});

test('reference visibility controllers stay scoped to their own loaded model', () => {
  const oldModel = new Group();
  const oldReference = mesh('display-only');
  oldModel.add(oldReference);
  referenceVisibility(oldModel).setHidden(true);
  const nextModel = new Group();
  const nextReference = mesh('display-only');
  nextModel.add(nextReference);
  const nextController = referenceVisibility(nextModel);
  assert.equal(nextReference.visible, true);
  nextController.setHidden(false);
  assert.equal(oldReference.visible, false);
});

test('reads role and feature metadata from mesh extras in an actual binary GLB', async () => {
  const positions = new Float32Array([0, 0, 0, 10, 0, 0, 0, 10, 0]);
  const metadata = { role: 'display-only', physicalFeatureRef: 'lcd-1' };
  const json = Buffer.from(JSON.stringify({
    asset: { version: '2.0' },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ extras: { amagine3d: metadata }, primitives: [{ attributes: { POSITION: 0 } }] }],
    buffers: [{ byteLength: positions.byteLength }],
    bufferViews: [{ buffer: 0, byteLength: positions.byteLength }],
    accessors: [{
      bufferView: 0, componentType: 5126, count: 3, type: 'VEC3',
      min: [0, 0, 0], max: [10, 10, 0],
    }],
  }));
  const jsonLength = Math.ceil(json.byteLength / 4) * 4;
  const glb = Buffer.alloc(12 + 8 + jsonLength + 8 + positions.byteLength);
  glb.writeUInt32LE(0x46546c67, 0);
  glb.writeUInt32LE(2, 4);
  glb.writeUInt32LE(glb.byteLength, 8);
  glb.writeUInt32LE(jsonLength, 12);
  glb.writeUInt32LE(0x4e4f534a, 16);
  glb.fill(0x20, 20, 20 + jsonLength);
  json.copy(glb, 20);
  glb.writeUInt32LE(positions.byteLength, 20 + jsonLength);
  glb.writeUInt32LE(0x004e4942, 24 + jsonLength);
  Buffer.from(positions.buffer).copy(glb, 28 + jsonLength);

  const buffer = new Uint8Array(glb).buffer;
  const loaded = await new GLTFLoader().parseAsync(buffer, '');
  const controller = referenceVisibility(loaded.scene);
  assert.equal(controller.info.referenceMeshes, 1);
  const loadedMesh = loaded.scene.children[0] as Mesh;
  assert.deepEqual(loadedMesh.userData.amagine3d, metadata);
  controller.setHidden(true);
  assert.equal(loadedMesh.visible, false);
});
