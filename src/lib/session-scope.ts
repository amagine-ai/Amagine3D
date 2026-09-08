/** Scope asynchronous results to one activation of a session, including A → B → A. */
export function createSessionScope(initialSessionId: string) {
  let current = { sessionId: initialSessionId };

  return {
    activate(sessionId: string): void {
      current = { sessionId };
    },
    capture(sessionId: string): () => boolean {
      const activation = current;
      return () => activation === current && activation.sessionId === sessionId;
    },
  };
}
