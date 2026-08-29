import { useEffect, useMemo, useState } from 'react';

import styles from './FilesPanel.module.css';
import { fileSectionArtifacts } from '../../lib/artifact-selection';
import type { ArtifactSummary } from '../../types';
import { ArtifactIcon } from './ArtifactIcon';
import type { Language } from './types';
import { translator } from './types';
import { LoadingSpinner, RefreshIcon } from './WorkbenchPrimitives';

export interface FilesPanelProps {
  artifacts: ArtifactSummary[];
  language: Language;
  loading: boolean;
  onDownload: (artifacts: ArtifactSummary[]) => Promise<void>;
  onRefresh: () => void;
  onSelect: (artifact: ArtifactSummary) => void;
  selectionScope: string;
  selectedPath: string | undefined;
  workspaceName: string;
}

export function FilesPanel({
  artifacts,
  language,
  loading,
  onDownload,
  onRefresh,
  onSelect,
  selectionScope,
  selectedPath,
  workspaceName,
}: FilesPanelProps) {
  const text = translator(language);
  const [downloadError, setDownloadError] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const visibleArtifacts = useMemo(
    () => fileSectionArtifacts(artifacts),
    [artifacts],
  );
  const hasPrintableArtifacts = useMemo(
    () =>
      visibleArtifacts.some(
        ({ format }) => format === '3mf' || format === 'stl',
      ),
    [visibleArtifacts],
  );
  const selectedArtifacts = useMemo(
    () => visibleArtifacts.filter(({ path }) => selectedPaths.has(path)),
    [selectedPaths, visibleArtifacts],
  );

  useEffect(() => {
    const availablePaths = new Set(visibleArtifacts.map(({ path }) => path));
    setSelectedPaths((current) => {
      const next = new Set(
        [...current].filter((path) => availablePaths.has(path)),
      );
      return next.size === current.size ? current : next;
    });
  }, [visibleArtifacts]);

  useEffect(() => {
    setSelectedPaths(new Set());
    setDownloadError(false);
  }, [selectionScope]);

  function toggleSelected(path: string) {
    setSelectedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    setDownloadError(false);
  }

  async function downloadSelection() {
    if (selectedArtifacts.length === 0 || downloading) return;
    setDownloading(true);
    setDownloadError(false);
    try {
      await onDownload(selectedArtifacts);
    } catch {
      setDownloadError(true);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className={styles.fileWorkspace} role="tabpanel">
      <section className={styles.fileSection}>
        <div className={styles.sectionHeading}>
          <h2>{workspaceName}</h2>
          <div className={styles.fileHeadingActions}>
            <span>{visibleArtifacts.length}</span>
            <button
              aria-label={text('Refresh files', '刷新文件')}
              className={styles.fileRefreshButton}
              disabled={loading}
              onClick={onRefresh}
              type="button"
            >
              {loading ? <LoadingSpinner /> : <RefreshIcon />}
            </button>
          </div>
        </div>
        {hasPrintableArtifacts ? (
          <p className={styles.fileHint}>
            {text(
              '3MF and STL files can be downloaded for printing.',
              '3MF 和 STL 文件可下载用于打印。',
            )}
          </p>
        ) : null}
        {visibleArtifacts.length === 0 ? (
          <p className={styles.fileEmpty}>
            {text(
              'Models and PNG previews appear after the Agent saves them.',
              'Agent 保存模型或 PNG 预览后会显示在这里。',
            )}
          </p>
        ) : (
          <ul className={styles.fileTree}>
            {visibleArtifacts.map((artifact) => (
              <li key={artifact.path}>
                <input
                  aria-label={text(
                    `Select ${artifact.name}`,
                    `选择 ${artifact.name}`,
                  )}
                  checked={selectedPaths.has(artifact.path)}
                  className={styles.fileTreeCheckbox}
                  onChange={() => toggleSelected(artifact.path)}
                  type="checkbox"
                />
                <button
                  aria-current={selectedPath === artifact.path}
                  onClick={() => onSelect(artifact)}
                  title={artifact.path}
                  type="button"
                >
                  <ArtifactIcon
                    artifact={artifact}
                    selected={selectedPath === artifact.path}
                  />
                  <span>{artifact.name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <footer className={styles.fileActions}>
        <span aria-live="polite">
          {downloadError
            ? text('Download failed. Please try again.', '下载失败，请重试。')
            : text(
                `${String(selectedArtifacts.length)} files selected`,
                `已选择 ${String(selectedArtifacts.length)} 个文件`,
              )}
        </span>
        <button
          disabled={selectedArtifacts.length === 0 || downloading}
          onClick={() => void downloadSelection()}
          type="button"
        >
          {downloading
            ? text('Preparing…', '正在准备…')
            : selectedArtifacts.length > 1
              ? text('Download ZIP', '下载 ZIP')
              : text('Download', '下载')}
        </button>
      </footer>
    </div>
  );
}
