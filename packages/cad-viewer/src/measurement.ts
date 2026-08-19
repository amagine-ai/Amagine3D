import type { ViewerMeasurement, ViewerSelection, ViewerVector } from './types';

function assertFiniteVector(vector: ViewerVector, role: string): void {
  if (vector.some((component) => !Number.isFinite(component))) {
    throw new RangeError(
      `${role} point must contain three finite coordinates.`,
    );
  }
}

function scaledLength(x: number, y: number, z: number): number {
  const scale = Math.max(Math.abs(x), Math.abs(y), Math.abs(z));
  if (scale === 0) return 0;
  const sx = x / scale;
  const sy = y / scale;
  const sz = z / scale;
  return scale * Math.sqrt(sx * sx + sy * sy + sz * sz);
}

function coordinateToken(value: number): string {
  return Object.is(value, -0) ? '0' : value.toString();
}

function selectionToken(selection: ViewerSelection): string {
  const entity = `${selection.entityId.length}:${selection.entityId}`;
  const point = selection.point.map(coordinateToken).join(',');
  return `${entity}@${point}`;
}

/** Calculates a stable Euclidean distance without overflowing intermediate squares. */
export function distanceBetween(from: ViewerVector, to: ViewerVector): number {
  assertFiniteVector(from, 'From');
  assertFiniteVector(to, 'To');
  return scaledLength(to[0] - from[0], to[1] - from[1], to[2] - from[2]);
}

/** Builds a directed measurement from the two most recent selections. */
export function measureSelections(
  selections: readonly ViewerSelection[],
): ViewerMeasurement | undefined {
  const from = selections.at(-2);
  const to = selections.at(-1);
  if (!from || !to) return undefined;

  assertFiniteVector(from.point, 'From');
  assertFiniteVector(to.point, 'To');
  return {
    id: `${selectionToken(from)}>${selectionToken(to)}`,
    from: [...from.point],
    fromEntityId: from.entityId,
    to: [...to.point],
    toEntityId: to.entityId,
    distanceMm: distanceBetween(from.point, to.point),
  };
}
