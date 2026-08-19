import { describe, expect, it } from 'vitest';

import { externalHttpUrl, safeDownloadFileName } from './browser-security';

describe('browser output security', () => {
  it('allows only absolute HTTP(S) links', () => {
    expect(externalHttpUrl('https://example.com/spec.pdf')).toBe(
      'https://example.com/spec.pdf',
    );
    expect(externalHttpUrl('javascript:alert(1)')).toBeUndefined();
    expect(externalHttpUrl('/relative')).toBeUndefined();
  });

  it('reduces download names to a safe leaf name', () => {
    expect(safeDownloadFileName('../../board:model.step')).toBe(
      'board-model.step',
    );
    expect(safeDownloadFileName('..')).toBe('amagine3d-download');
  });
});
