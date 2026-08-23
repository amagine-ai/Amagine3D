export function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${String(bytes)} B`;
  if (bytes < 1_024 * 1_024) return `${String(Math.round(bytes / 1_024))} KB`;
  return `${(bytes / 1_024 / 1_024).toFixed(1)} MB`;
}
