import { useEffect, useMemo, useState } from 'react';

import styles from './StorageDrawer.module.css';
import { EmptyState } from '../ui/EmptyState';
import type {
  ArtifactSummary,
  SessionSummary,
  StorageSessionGroup,
} from '../../types';
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

export interface StorageArtifactSelection {
  paths: string[];
  sessionId: string;
}

export interface StorageDeleteSelection {
  artifactGroups: StorageArtifactSelection[];
  sessionIds: string[];
}

interface StorageDrawerProps {
  groups: StorageSessionGroup[];
  language: Language;
  loading: boolean;
  onClose: () => void;
  onDelete: (selection: StorageDeleteSelection) => Promise<void>;
  onDownload: (
    sessionId: string,
    artifacts: ArtifactSummary[],
  ) => Promise<void>;
  onRefresh: () => void;
  onSelect: (session: SessionSummary, artifact: ArtifactSummary) => void;
  sessionTitle: (session: SessionSummary) => string;
}

export function StorageDrawer({
  groups,
  language,
  loading,
  onClose,
  onDelete,
  onDownload,
  onRefresh,
  onSelect,
  sessionTitle,
}: StorageDrawerProps) {
  const text = translator(language);
  const [collapsedSessionIds, setCollapsedSessionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [deleteError, setDeleteError] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [downloadError, setDownloadError] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [selectedPathsBySession, setSelectedPathsBySession] = useState<
    Map<string, Set<string>>
  >(() => new Map());
  const [selectedSessionIds, setSelectedSessionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const selectedArtifactGroups = useMemo(
    () =>
      groups
        .map((group) => {
          const selectedPaths = selectedPathsBySession.get(group.session.id);
          return {
            artifacts: selectedPaths
              ? group.artifacts.filter(({ path }) => selectedPaths.has(path))
              : [],
            session: group.session,
          };
        })
        .filter(({ artifacts }) => artifacts.length > 0),
    [groups, selectedPathsBySession],
  );
  const selectedArtifactGroupsForDelete = useMemo(
    () =>
      selectedArtifactGroups.filter(
        ({ session }) => !selectedSessionIds.has(session.id),
      ),
    [selectedArtifactGroups, selectedSessionIds],
  );
  const selectedArtifactCount = selectedArtifactGroupsForDelete.reduce(
    (total, group) => total + group.artifacts.length,
    0,
  );
  const selectedSessionCount = selectedSessionIds.size;
  const downloadableGroup =
    selectedSessionCount === 0 && selectedArtifactGroups.length === 1
      ? selectedArtifactGroups[0]
      : undefined;

  useEffect(() => {
    const groupsBySession = new Map(
      groups.map((group) => [group.session.id, group]),
    );
    setCollapsedSessionIds((current) => {
      const next = new Set(
        [...current].filter((sessionId) => groupsBySession.has(sessionId)),
      );
      return next.size === current.size ? current : next;
    });
    setSelectedSessionIds((current) => {
      const next = new Set(
        [...current].filter((sessionId) => {
          const group = groupsBySession.get(sessionId);
          return group && !group.session.readOnly;
        }),
      );
      return next.size === current.size ? current : next;
    });
    setSelectedPathsBySession((current) => {
      let changed = false;
      const next = new Map<string, Set<string>>();
      for (const [sessionId, selectedPaths] of current) {
        const group = groupsBySession.get(sessionId);
        if (!group || group.session.readOnly) {
          changed = true;
          continue;
        }
        const availablePaths = new Set(
          group.artifacts.map(({ path }) => path),
        );
        const filtered = new Set(
          [...selectedPaths].filter((path) => availablePaths.has(path)),
        );
        if (filtered.size !== selectedPaths.size) changed = true;
        if (filtered.size > 0) next.set(sessionId, filtered);
      }
      return changed || next.size !== current.size ? next : current;
    });
  }, [groups]);

  function clearErrors() {
    setDeleteError(false);
    setDownloadError(false);
  }

  function toggleSession(sessionId: string, readOnly: boolean) {
    if (readOnly || deleting || downloading) return;
    const selecting = !selectedSessionIds.has(sessionId);
    setSelectedSessionIds((current) => {
      const next = new Set(current);
      if (selecting) next.add(sessionId);
      else next.delete(sessionId);
      return next;
    });
    if (selecting) {
      setSelectedPathsBySession((current) => {
        const next = new Map(current);
        next.delete(sessionId);
        return next;
      });
    }
    clearErrors();
  }

  function toggleSelected(sessionId: string, path: string) {
    if (deleting || downloading) return;
    setSelectedSessionIds((current) => {
      if (!current.has(sessionId)) return current;
      const next = new Set(current);
      next.delete(sessionId);
      return next;
    });
    setSelectedPathsBySession((current) => {
      const next = new Map(current);
      const selectedPaths = new Set(next.get(sessionId));
      if (selectedPaths.has(path)) selectedPaths.delete(path);
      else selectedPaths.add(path);
      if (selectedPaths.size === 0) next.delete(sessionId);
      else next.set(sessionId, selectedPaths);
      return next;
    });
    clearErrors();
  }

  function toggleFolder(sessionId: string) {
    setCollapsedSessionIds((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }

  async function downloadSelection() {
    if (!downloadableGroup || deleting || downloading) return;
    setDownloading(true);
    setDownloadError(false);
    try {
      await onDownload(downloadableGroup.session.id, downloadableGroup.artifacts);
    } catch {
      setDownloadError(true);
    } finally {
      setDownloading(false);
    }
  }

  async function deleteSelection() {
    if (
      (selectedSessionCount === 0 && selectedArtifactCount === 0) ||
      deleting ||
      downloading
    ) {
      return;
    }
    const confirmation =
      selectedSessionCount > 0 && selectedArtifactCount > 0
        ? text(
            `The selected ${String(selectedSessionCount)} sessions and ${String(selectedArtifactCount)} files will be moved to Trash. Continue?`,
            `所选 ${String(selectedSessionCount)} 个会话和 ${String(selectedArtifactCount)} 个文件将会被移动到回收站。是否继续？`,
          )
        : selectedSessionCount > 0
          ? text(
              `The selected ${String(selectedSessionCount)} sessions will be moved to Trash. Continue?`,
              `所选 ${String(selectedSessionCount)} 个会话将会被移动到回收站。是否继续？`,
            )
          : text(
              `The selected ${String(selectedArtifactCount)} files will be moved to Trash. Continue?`,
              `所选 ${String(selectedArtifactCount)} 个文件将会被移动到回收站。是否继续？`,
            );
    if (!window.confirm(confirmation)) return;
    setDeleting(true);
    setDeleteError(false);
    setDownloadError(false);
    try {
      await onDelete({
        artifactGroups: selectedArtifactGroupsForDelete.map(
          ({ artifacts, session }) => ({
            paths: artifacts.map(({ path }) => path),
            sessionId: session.id,
          }),
        ),
        sessionIds: [...selectedSessionIds],
      });
      setSelectedPathsBySession(new Map());
      setSelectedSessionIds(new Set());
    } catch {
      setDeleteError(true);
    } finally {
      setDeleting(false);
    }
  }

  const statusText = deleteError
    ? text(
        'Unable to move the selected items to Trash.',
        '无法将所选项目移动到回收站。',
      )
    : downloadError
      ? text('Download failed. Please try again.', '下载失败，请重试。')
      : selectedSessionCount > 0
        ? selectedArtifactCount > 0
          ? text(
              `${String(selectedSessionCount)} sessions and ${String(selectedArtifactCount)} files selected`,
              `已选择 ${String(selectedSessionCount)} 个会话和 ${String(selectedArtifactCount)} 个文件`,
            )
          : text(
              `${String(selectedSessionCount)} sessions selected`,
              `已选择 ${String(selectedSessionCount)} 个会话`,
            )
        : selectedArtifactCount > 0
          ? text(
              `${String(selectedArtifactCount)} files selected`,
              `已选择 ${String(selectedArtifactCount)} 个文件`,
            )
          : loading
            ? text('Refreshing workspace storage', '正在刷新工作区存储')
            : text(
                'Select sessions or project files to manage',
                '选择要管理的会话或项目文件',
              );
  const deleteTooltip =
    selectedSessionCount > 0
      ? text('Move selected sessions to Trash', '将所选会话移动到回收站')
      : text('Move selected files to Trash', '将所选文件移动到回收站');
  const downloadTooltip =
    selectedSessionCount > 0
      ? text(
          'Select files in one session to download',
          '请选择单个会话中的文件下载',
        )
      : selectedArtifactGroups.length > 1
        ? text('Download one session at a time', '每次只能下载一个会话中的文件')
        : text('Download', '下载');

  return (
    <aside
      aria-label={text('Workspace storage', '工作区存储')}
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
                aria-label={deleteTooltip}
                className={styles.storageDeleteButton}
                data-tooltip={deleteTooltip}
                disabled={
                  (selectedSessionCount === 0 && selectedArtifactCount === 0) ||
                  deleting ||
                  downloading
                }
                onClick={() => void deleteSelection()}
                type="button"
              >
                {deleting ? <LoadingSpinner /> : <TrashIcon />}
              </button>
              <button
                aria-label={downloadTooltip}
                className={styles.storageDownloadButton}
                data-tooltip={downloadTooltip}
                disabled={!downloadableGroup || deleting || downloading}
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
          {groups.length === 0 ? (
            <EmptyState
              className={styles.emptyState}
              description={text(
                'Stored session files will appear here.',
                '已存储的会话文件会显示在这里。',
              )}
              title={
                loading
                  ? text('Loading workspace storage.', '正在载入工作区存储。')
                  : text('No stored sessions yet.', '暂无已存储会话。')
              }
            />
          ) : (
            <div className={styles.storageGroups}>
              {groups.map(({ artifactWorkspace, artifacts, session }) => {
                const expanded = !collapsedSessionIds.has(session.id);
                const selectedPaths =
                  selectedPathsBySession.get(session.id) ?? new Set<string>();
                const sessionSelected = selectedSessionIds.has(session.id);
                return (
                  <section className={styles.folderGroup} key={session.id}>
                    <div className={styles.storageGroupHeading}>
                      <input
                        aria-label={text(
                          `Select session ${sessionTitle(session)}`,
                          `选择会话 ${sessionTitle(session)}`,
                        )}
                        checked={sessionSelected}
                        className={styles.folderSelectAll}
                        disabled={session.readOnly || deleting || downloading}
                        onChange={() => toggleSession(session.id, session.readOnly)}
                        type="checkbox"
                      />
                      <button
                        aria-expanded={expanded}
                        className={styles.storageFolderToggle}
                        onClick={() => toggleFolder(session.id)}
                        type="button"
                      >
                        <span aria-hidden="true" className={styles.folderIcon}>
                          {expanded ? '⌄' : '›'}
                        </span>
                        <span className={styles.projectSummary}>
                          <span className={styles.projectIdentity}>
                            <span className={styles.projectName}>
                              {sessionTitle(session)}
                            </span>
                            <small>{artifactWorkspace.path}</small>
                          </span>
                          <span className={styles.projectFileCount}>
                            {String(artifacts.length)} {text('files', '个文件')}
                          </span>
                        </span>
                      </button>
                    </div>
                    {expanded ? (
                      artifacts.length > 0 ? (
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
                                disabled={
                                  session.readOnly ||
                                  sessionSelected ||
                                  deleting ||
                                  downloading
                                }
                                onChange={() =>
                                  toggleSelected(session.id, artifact.path)
                                }
                                type="checkbox"
                              />
                              <ArtifactIcon artifact={artifact} size="compact" />
                              <button
                                className={styles.storageFileIdentity}
                                onClick={() => onSelect(session, artifact)}
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
                      ) : (
                        <p className={styles.emptyFolder}>
                          {text('No files in this session.', '该会话暂无文件。')}
                        </p>
                      )
                    ) : null}
                  </section>
                );
              })}
            </div>
          )}
        </div>
      </section>
    </aside>
  );
}
