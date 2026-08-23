import styles from './StorageDrawer.module.css';
import { EmptyState } from '../ui/EmptyState';
import type { ArtifactSummary, ArtifactWorkspace } from '../../types';
import { formatBytes } from '../../lib/format';
import { ArtifactIcon } from './ArtifactIcon';
import type { Language } from './types';
import { translator } from './types';
import { LoadingSpinner } from './WorkbenchPrimitives';

interface StorageDrawerProps {
  artifactWorkspace: ArtifactWorkspace;
  artifacts: ArtifactSummary[];
  language: Language;
  loading: boolean;
  onClose: () => void;
  onRefresh: () => void;
  onSelect: (artifact: ArtifactSummary) => void;
  workspaceName: string;
}

export function StorageDrawer({
  artifactWorkspace,
  artifacts,
  language,
  loading,
  onClose,
  onRefresh,
  onSelect,
  workspaceName,
}: StorageDrawerProps) {
  const text = translator(language);
  return (
    <aside
      aria-label={text('Project folder storage', '项目目录存储')}
      className={styles.storageDrawer}
      data-open="true"
    >
      <section className={styles.storageSection}>
        <div className={styles.storageDrawerHeader}>
          <div className={styles.sectionHeading}>
            <div>
              <h2>{workspaceName}</h2>
              <small>
                {artifactWorkspace.readOnly
                  ? text('Built-in read-only project', '内置只读项目')
                  : text(
                      'Files stored in this repository folder',
                      '文件直接保存在当前仓库目录',
                    )}
              </small>
            </div>
            <button
              aria-label={text('Refresh storage', '刷新存储')}
              className={styles.storageRefreshButton}
              disabled={loading}
              onClick={onRefresh}
              type="button"
            >
              {loading ? (
                <LoadingSpinner />
              ) : (
                <svg fill="none" focusable="false" viewBox="0 0 20 20">
                  <path d="M15.4 6.4A6.25 6.25 0 1 0 16 12" />
                  <path d="M15.5 3.5v3.25h-3.25" />
                </svg>
              )}
            </button>
          </div>
          <button
            aria-label={text('Close storage', '关闭存储')}
            className={styles.storageDrawerClose}
            onClick={onClose}
            type="button"
          >
            <svg fill="none" focusable="false" viewBox="0 0 20 20">
              <path d="m5.25 5.25 9.5 9.5M14.75 5.25l-9.5 9.5" />
            </svg>
          </button>
        </div>
        <div className={styles.storageViewport}>
          {artifacts.length === 0 ? (
            <EmptyState
              className={styles.emptyState}
              description={text(
                `Generated files will appear under ${artifactWorkspace.path}.`,
                `生成文件会出现在 ${artifactWorkspace.path} 下。`,
              )}
              title={text('No project files yet.', '暂无项目文件。')}
            />
          ) : (
            <div className={styles.storageGroups}>
              <section className={styles.folderGroup}>
                <div className={styles.storageGroupHeading}>
                  <span className={styles.folderIcon}>⌄</span>
                  <div className={styles.projectSummary}>
                    <h3>{artifactWorkspace.path}</h3>
                    <span>
                      {String(artifacts.length)} {text('files', '个文件')}
                    </span>
                  </div>
                </div>
                <ul className={styles.folderFileList}>
                  {artifacts.map((artifact) => (
                    <li key={artifact.path}>
                      <ArtifactIcon artifact={artifact} size="compact" />
                      <button
                        className={styles.storageFileIdentity}
                        onClick={() => onSelect(artifact)}
                        type="button"
                      >
                        <strong>{artifact.name}</strong>
                        <small>{artifact.path}</small>
                      </button>
                      <span className={styles.storageFileSize}>
                        {formatBytes(artifact.size)}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>
          )}
        </div>
        <footer className={styles.storageActions}>
          <div className={styles.storageSelectionBar}>
            <span>{artifactWorkspace.path}</span>
            <button onClick={onRefresh} type="button">
              {text('Refresh', '刷新')}
            </button>
          </div>
        </footer>
      </section>
    </aside>
  );
}
