const activeSessionIds = new Set<string>();

export function acquireSessionActivity(
  sessionId: string,
): (() => void) | undefined {
  if (activeSessionIds.has(sessionId)) return undefined;
  activeSessionIds.add(sessionId);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    activeSessionIds.delete(sessionId);
  };
}
