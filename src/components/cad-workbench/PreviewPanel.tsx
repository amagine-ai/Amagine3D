import {
  lazy,
  Suspense,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';

import styles from './PreviewPanel.module.css';
import activityStyles from './ActivityLog.module.css';
import type { ArtifactSummary } from '../../types';
import type { Language, RuntimeEntry } from './types';
import { translator } from './types';
import { LoadingSpinner } from './WorkbenchPrimitives';
import type { ViewerStatus } from '../CadViewer';

const CadViewer = lazy(() =>
  import('../CadViewer').then((module) => ({ default: module.CadViewer })),
);

interface PreviewPanelProps {
  activity: string;
  connectionStatus: string | undefined;
  language: Language;
  logCollapsed: boolean;
  onLogResize: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onToggleLog: () => void;
  onTogglePrintPreview: () => void;
  previewArtifact: ArtifactSummary | undefined;
  printPreview: boolean;
  printPreviewAvailable: boolean;
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
  onTogglePrintPreview,
  previewArtifact,
  printPreview,
  printPreviewAvailable,
  running,
  runtimeEntries,
  runtimeReady,
  selectedArtifact,
  selectedText,
}: PreviewPanelProps) {
  const text = translator(language);
  const [viewerStatus, setViewerStatus] = useState<ViewerStatus>({
    state: 'empty',
    text: 'Waiting for model data',
  });
  const headingArtifact =
    selectedArtifact?.kind === 'image' || selectedText !== undefined
      ? selectedArtifact
      : previewArtifact;
  const timeFormatter = new Intl.DateTimeFormat(
    language === 'zh' ? 'zh-CN' : 'en',
    { hour: '2-digit', minute: '2-digit', second: '2-digit' },
  );
  return (
    <section className={styles.centerPanel} aria-label={text('Model preview', '模型预览')}>
      <header className={styles.canvasToolbar}>
        <div className={styles.canvasHeading}>
          <div className={styles.canvasHeadingCopy}>
            <h2>{headingArtifact?.name ?? text('Model preview', '模型预览')}</h2>
            {headingArtifact?.path ?? connectionStatus ? (
              <span className={styles.canvasLabel}>
                {headingArtifact?.path ?? connectionStatus}
              </span>
            ) : null}
          </div>
        </div>
        <div className={styles.canvasMeta}>
          {selectedArtifact?.kind !== 'image' && selectedText === undefined ? (
            <span
              className={styles.viewerSummary}
              data-status={viewerStatus.state}
            >
              <span aria-hidden="true" className={styles.statusMark} />
              <span>{viewerStatus.text}</span>
            </span>
          ) : null}
          <span
            aria-live="polite"
            className={styles.phase}
            data-state={running ? 'running' : runtimeReady ? 'ready' : 'offline'}
          >
            {running ? 'RUNNING' : runtimeReady ? 'READY' : 'OFFLINE'}
          </span>
          <button
            aria-checked={printPreview}
            aria-label={text('Toggle print preview', '切换打印预览')}
            className={styles.previewSwitch}
            disabled={!printPreviewAvailable}
            onClick={onTogglePrintPreview}
            role="switch"
            title={text(
              'Show the 3MF or STL print package',
              '显示 3MF 或 STL 打印文件',
            )}
            type="button"
          >
            <span>{text('Print preview', '打印预览')}</span>
            <span aria-hidden="true" className={styles.switchTrack}>
              <span />
            </span>
          </button>
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
            <CadViewer
              artifact={previewArtifact}
              onStatusChange={setViewerStatus}
            />
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
            {activity || connectionStatus ? (
              <small>{activity || connectionStatus}</small>
            ) : null}
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
                    <p>
                      {entry.localizedMessage?.[language] ?? entry.message}
                    </p>
                  </li>
                ))}
              </ol>
            )}
            <div className={activityStyles.platformNotices}>
              <p>
                {text(
                  'A3D sessions and generated files are stored in repository folders.',
                  'A3D 会话与生成文件均保存在仓库目录中。',
                )}
              </p>
            </div>
          </div>
        )}
      </section>
    </section>
  );
}
