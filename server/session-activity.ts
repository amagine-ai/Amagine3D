const activeSessionIds = new Set<string>();
const sessionQuarantineCounts = new Map<string, number>();

export function acquireSessionActivity(
  sessionId: string,
): (() => void) | undefined {
  if (
    activeSessionIds.has(sessionId) ||
    (sessionQuarantineCounts.get(sessionId) ?? 0) > 0
  ) {
    return undefined;
  }
  activeSessionIds.add(sessionId);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    activeSessionIds.delete(sessionId);
  };
}

/**
 * Keep a session unavailable after its request has ended while an interrupted
 * Agent operation is still settling. The reference count makes overlapping
 * creation/shutdown guards safe and keeps each release idempotent.
 */
export function quarantineSessionActivity(sessionId: string): () => void {
  sessionQuarantineCounts.set(
    sessionId,
    (sessionQuarantineCounts.get(sessionId) ?? 0) + 1,
  );
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const remaining = (sessionQuarantineCounts.get(sessionId) ?? 1) - 1;
    if (remaining > 0) sessionQuarantineCounts.set(sessionId, remaining);
    else sessionQuarantineCounts.delete(sessionId);
  };
}
