import { useEffect, useMemo, useRef, useState } from 'react';

import styles from './StorageDrawer.module.css';
import { EmptyState } from '../ui/EmptyState';
import type { ArtifactSummary, ArtifactWorkspace } from '../../types';
import { formatBytes } from '../../lib/format';
import { ArtifactIcon } from './ArtifactIcon';
import type { Language } from './types';
import { translator } from './types';
import {
  DownloadIcon,
  LoadingSpinner,
  RefreshIcon,
  TrashIcon,
} from './WorkbenchPrimitives';

interface StorageDrawerProps {
  artifactWorkspace: ArtifactWorkspace;
  artifacts: ArtifactSummary[];
  language: Language;
  loading: boolean;
  onClose: () => void;
  onDelete: (artifacts: ArtifactSummary[]) => Promise<void>;
  onDownload: (artifacts: ArtifactSummary[]) => Promise<void>;
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
  onDelete,
  onDownload,
  onRefresh,
  onSelect,
  workspaceName,
}: StorageDrawerProps) {
  const text = translator(language);
  const [deleteError, setDeleteError] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [downloadError, setDownloadError] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [folderExpanded, setFolderExpanded] = useState(true);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const selectAllRef = useRef<HTMLInputElement>(null);
  const selectedArtifacts = useMemo(
    () => artifacts.filter(({ path }) => selectedPaths.has(path)),
    [artifacts, selectedPaths],
  );

  useEffect(() => {
    const availablePaths = new Set(artifacts.map(({ path }) => path));
    setSelectedPaths((current) => {
      const next = new Set(
        [...current].filter((path) => availablePaths.has(path)),
      );
      return next.size === current.size ? current : next;
    });
  }, [artifacts]);

  useEffect(() => {
    setSelectedPaths(new Set());
    setDeleteError(false);
    setDownloadError(false);
  }, [artifactWorkspace.sessionId]);

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate =
        selectedArtifacts.length > 0 &&
        selectedArtifacts.length < artifacts.length;
    }
  }, [artifacts.length, selectedArtifacts.length]);

  function toggleSelected(path: string) {
    setSelectedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    setDeleteError(false);
    setDownloadError(false);
  }

  function toggleAll() {
    setSelectedPaths((current) =>
      selectedArtifacts.length === artifacts.length
        ? new Set()
        : new Set(artifacts.map(({ path }) => path)),
    );
    setDeleteError(false);
    setDownloadError(false);
  }

  async function downloadSelection() {
    if (selectedArtifacts.length === 0 || deleting || downloading) return;
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

  async function deleteSelection() {
    if (
      artifactWorkspace.readOnly ||
      selectedArtifacts.length === 0 ||
      deleting ||
      downloading
    ) {
      return;
    }
    const confirmed = window.confirm(
      text(
        `The selected ${String(selectedArtifacts.length)} files will be moved to Trash. Continue?`,
        `所选 ${String(selectedArtifacts.length)} 个文件将会被移动到回收站。是否继续？`,
      ),
    );
    if (!confirmed) return;
    setDeleting(true);
    setDeleteError(false);
    setDownloadError(false);
    try {
      await onDelete(selectedArtifacts);
      setSelectedPaths(new Set());
    } catch {
      setDeleteError(true);
    } finally {
      setDeleting(false);
    }
  }

  const statusText = deleteError
    ? text(
        'Unable to move the selected files to Trash.',
        '无法将所选文件移动到回收站。',
      )
    : downloadError
      ? text('Download failed. Please try again.', '下载失败，请重试。')
      : selectedArtifacts.length > 0
        ? text(
            `${String(selectedArtifacts.length)} files selected`,
            `已选择 ${String(selectedArtifacts.length)} 个文件`,
          )
        : text(
            'Select project files to manage',
            '选择要管理的项目文件',
          );

  return (
    <aside
      aria-label={text('Project folder storage', '项目目录存储')}
      className={styles.storageDrawer}
      data-open="true"
    >
      <section className={styles.storageSection}>
        <div className={styles.storageDrawerHeader}>
          <div className={styles.sectionHeading}>
            <div className={styles.sectionHeadingText}>
              <h2>{text('Storage', '存储')}</h2>
              <small aria-live="polite">{statusText}</small>
            </div>
            <div className={styles.storageHeaderActions}>
              <button
                aria-label={text('Move selected files to Trash', '删除所选文件')}
                className={styles.storageDeleteButton}
                data-tooltip={
                  artifactWorkspace.readOnly
                    ? text(
                        'Built-in project files cannot be deleted',
                        '内置项目文件不可删除',
                      )
                    : text(
                        'Move selected files to Trash',
                        '将所选文件移动到回收站',
                      )
                }
                disabled={
                  artifactWorkspace.readOnly ||
                  selectedArtifacts.length === 0 ||
                  deleting ||
                  downloading
                }
                onClick={() => void deleteSelection()}
                type="button"
              >
                {deleting ? <LoadingSpinner /> : <TrashIcon />}
              </button>
              <button
                aria-label={
                  selectedArtifacts.length > 1
                    ? text('Download selected files as ZIP', '将所选文件下载为 ZIP')
                    : text('Download selected file', '下载所选文件')
                }
                className={styles.storageDownloadButton}
                data-tooltip={
                  selectedArtifacts.length > 1
                    ? text('Download ZIP', '下载 ZIP')
                    : text('Download', '下载')
                }
                disabled={
                  selectedArtifacts.length === 0 || deleting || downloading
                }
                onClick={() => void downloadSelection()}
                type="button"
              >
                {downloading ? <LoadingSpinner /> : <DownloadIcon />}
              </button>
              <button
                aria-label={text('Refresh storage', '刷新存储')}
                className={styles.storageRefreshButton}
                data-tooltip={text('Refresh', '刷新')}
                disabled={loading || deleting || downloading}
                onClick={onRefresh}
                type="button"
              >
                {loading ? <LoadingSpinner /> : <RefreshIcon />}
              </button>
            </div>
          </div>
          <button
            aria-label={text('Close storage', '关闭存储')}
            className={styles.storageDrawerClose}
            data-tooltip={text('Close', '关闭')}
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
                  <input
                    aria-label={text(
                      `Select all files in ${workspaceName}`,
                      `全选 ${workspaceName} 中的文件`,
                    )}
                    checked={
                      artifacts.length > 0 &&
                      selectedArtifacts.length === artifacts.length
                    }
                    className={styles.folderSelectAll}
                    onChange={toggleAll}
                    ref={selectAllRef}
                    type="checkbox"
                  />
                  <button
                    aria-expanded={folderExpanded}
                    className={styles.storageFolderToggle}
                    onClick={() => setFolderExpanded((expanded) => !expanded)}
                    type="button"
                  >
                    <span aria-hidden="true" className={styles.folderIcon}>
                      {folderExpanded ? '⌄' : '›'}
                    </span>
                    <span className={styles.projectSummary}>
                      <span className={styles.projectIdentity}>
                        <span className={styles.projectName}>
                          {workspaceName}
                        </span>
                        <small>{artifactWorkspace.path}</small>
                      </span>
                      <span className={styles.projectFileCount}>
                        {String(artifacts.length)} {text('files', '个文件')}
                      </span>
                    </span>
                  </button>
                </div>
                {folderExpanded ? (
                  <ul className={styles.folderFileList}>
                    {artifacts.map((artifact) => (
                      <li key={artifact.path}>
                        <input
                          aria-label={text(
                            `Select ${artifact.name}`,
                            `选择 ${artifact.name}`,
                          )}
                          checked={selectedPaths.has(artifact.path)}
                          className={styles.storageFileCheckbox}
                          onChange={() => toggleSelected(artifact.path)}
                          type="checkbox"
                        />
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
                ) : null}
              </section>
            </div>
          )}
        </div>
      </section>
    </aside>
  );
}
