import { Mesh, type Object3D } from 'three';

type PreviewRole = 'display-only' | 'manufactured';

export interface ReferenceComponentInfo {
  referenceMeshes: number;
  manufacturedMeshes: number;
  unclassifiedMeshes: number;
}

function previewRole(object: Object3D): PreviewRole | undefined {
  const metadata: unknown = object.userData.amagine3d;
  if (!metadata || typeof metadata !== 'object' || !('role' in metadata)) return;
  return metadata.role === 'display-only' || metadata.role === 'manufactured'
    ? metadata.role
    : undefined;
}

/** GLTFLoader preserves node/mesh extras as userData. Never infer roles from names or colors. */
export function referenceVisibility(root: Object3D): {
  info: ReferenceComponentInfo;
  setHidden: (hidden: boolean) => void;
} {
  const info: ReferenceComponentInfo = {
    referenceMeshes: 0,
    manufacturedMeshes: 0,
    unclassifiedMeshes: 0,
  };
  const references: Array<{ mesh: Mesh; initiallyVisible: boolean }> = [];

  function visit(object: Object3D, inheritedRole?: PreviewRole): void {
    const role = previewRole(object) ?? inheritedRole;
    if (object instanceof Mesh) {
      if (role === 'display-only') {
        info.referenceMeshes += 1;
        references.push({ mesh: object, initiallyVisible: object.visible });
      } else if (role === 'manufactured') {
        info.manufacturedMeshes += 1;
      } else {
        info.unclassifiedMeshes += 1;
      }
    }
    object.children.forEach((child) => visit(child, role));
  }
  visit(root);

  return {
    info,
    setHidden(hidden) {
      for (const { mesh, initiallyVisible } of references) {
        mesh.visible = hidden ? false : initiallyVisible;
      }
    },
  };
}
