import {
  lazy,
  Suspense,
  type PointerEvent as ReactPointerEvent,
} from 'react';

import styles from './PreviewPanel.module.css';
import activityStyles from './ActivityLog.module.css';
import type { ArtifactSummary } from '../../types';
import type { Language, RuntimeEntry } from './types';
import { translator } from './types';
import { LoadingSpinner } from './WorkbenchPrimitives';

const CadViewer = lazy(() =>
  import('../CadViewer').then((module) => ({ default: module.CadViewer })),
);

interface PreviewPanelProps {
  activity: string;
  connectionStatus: string;
  language: Language;
  logCollapsed: boolean;
  onLogResize: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onToggleLog: () => void;
  previewArtifact: ArtifactSummary | undefined;
  running: boolean;
  runtimeEntries: RuntimeEntry[];
  runtimeReady: boolean;
  selectedArtifact: ArtifactSummary | undefined;
  selectedText: string | undefined;
}

export function PreviewPanel({
  activity,
  connectionStatus,
  language,
  logCollapsed,
  onLogResize,
  onToggleLog,
  previewArtifact,
  running,
  runtimeEntries,
  runtimeReady,
  selectedArtifact,
  selectedText,
}: PreviewPanelProps) {
  const text = translator(language);
  const timeFormatter = new Intl.DateTimeFormat(
    language === 'zh' ? 'zh-CN' : 'en',
    { hour: '2-digit', minute: '2-digit', second: '2-digit' },
  );
  return (
    <section className={styles.centerPanel} aria-label={text('Model preview', '模型预览')}>
      <header className={styles.canvasToolbar}>
        <div className={styles.canvasHeading}>
          <div className={styles.canvasHeadingCopy}>
            <h2>{selectedArtifact?.name ?? text('Model preview', '模型预览')}</h2>
            <span className={styles.canvasLabel}>
              {selectedArtifact?.path ?? connectionStatus}
            </span>
          </div>
        </div>
        <div className={styles.canvasMeta}>
          {running ? (
            <span className={styles.buildingState}>
              <LoadingSpinner />
              {text('Building…', '构建中…')}
            </span>
          ) : null}
          <span className={styles.phase}>
            {running ? 'RUNNING' : runtimeReady ? 'READY' : 'OFFLINE'}
          </span>
        </div>
      </header>

      <div className={styles.canvasBody}>
        {selectedArtifact?.kind === 'image' ? (
          <div className={styles.emptyCanvas}>
            <img
              alt={selectedArtifact.name}
              src={selectedArtifact.url}
              style={{ maxHeight: '100%', maxWidth: '100%', objectFit: 'contain' }}
            />
          </div>
        ) : selectedText !== undefined ? (
          <pre className={styles.codePreview} tabIndex={0}>
            <code>{selectedText}</code>
          </pre>
        ) : (
          <Suspense
            fallback={
              <div className={styles.emptyCanvas}>
                <LoadingSpinner />
                <span>{text('Loading viewer…', '正在载入查看器…')}</span>
              </div>
            }
          >
            <CadViewer artifact={previewArtifact} />
          </Suspense>
        )}
      </div>

      <div
        aria-disabled={logCollapsed}
        aria-label={text('Resize activity log', '调整活动日志高度')}
        className={activityStyles.logResizer}
        onPointerDown={onLogResize}
        role="separator"
      >
        <span aria-hidden="true" />
      </div>

      <section
        className={`${activityStyles.activityLog} ${logCollapsed ? activityStyles.activityLogCollapsed : ''}`}
      >
        <header className={activityStyles.activityLogHeader}>
          <div>
            <strong>{text('Activity', '执行')}</strong>
            <small>{activity || connectionStatus}</small>
          </div>
          <button
            aria-expanded={!logCollapsed}
            onClick={onToggleLog}
            type="button"
          >
            {logCollapsed ? '⌃' : '⌄'}
          </button>
        </header>
        {logCollapsed ? null : (
          <div className={activityStyles.activityLogBody}>
            {runtimeEntries.length === 0 ? (
              <p className={activityStyles.runtimeEventsEmpty}>
                {text(
                  'Runtime events will appear here.',
                  '运行时事件会显示在这里。',
                )}
              </p>
            ) : (
              <ol className={activityStyles.runtimeEvents}>
                {runtimeEntries.map((entry) => (
                  <li data-level={entry.level} key={entry.id}>
                    <time>{timeFormatter.format(entry.occurredAt)}</time>
                    <span>{entry.stage}</span>
                    <p>{entry.message}</p>
                  </li>
                ))}
              </ol>
            )}
            <div className={activityStyles.platformNotices}>
              <p>
                {text(
                  'Agent sessions and generated files are stored in repository folders.',
                  'Agent 会话与生成文件均保存在仓库目录中。',
                )}
              </p>
            </div>
          </div>
        )}
      </section>
    </section>
  );
}
