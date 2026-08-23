import type { RefObject } from 'react';

import styles from './LeftPanel.module.css';
import type { SessionSummary } from '../../types';
import { ChatPanel, type ChatPanelProps } from './ChatPanel';
import { FilesPanel, type FilesPanelProps } from './FilesPanel';
import type { Language, LeftView } from './types';
import { translator } from './types';

interface LeftPanelProps {
  chat: ChatPanelProps;
  collapsed: boolean;
  connectionStatus: string;
  files: FilesPanelProps;
  language: Language;
  menuOpen: boolean;
  onOpenSession: (session: SessionSummary) => void;
  onToggleCollapsed: () => void;
  onToggleMenu: () => void;
  onViewChange: (view: LeftView) => void;
  running: boolean;
  sessionId: string;
  sessionLoading: boolean;
  sessionMenuRef: RefObject<HTMLDivElement | null>;
  sessionTitle: (session: SessionSummary) => string;
  sessions: SessionSummary[];
  view: LeftView;
  workspaceName: string;
}

export function LeftPanel({
  chat,
  collapsed,
  connectionStatus,
  files,
  language,
  menuOpen,
  onOpenSession,
  onToggleCollapsed,
  onToggleMenu,
  onViewChange,
  running,
  sessionId,
  sessionLoading,
  sessionMenuRef,
  sessionTitle,
  sessions,
  view,
  workspaceName,
}: LeftPanelProps) {
  const text = translator(language);
  const dateFormatter = new Intl.DateTimeFormat(
    language === 'zh' ? 'zh-CN' : 'en',
    { month: 'short', day: 'numeric' },
  );
  return (
    <aside
      aria-label={text('Conversation and generated files', '对话与生成文件')}
      className={`${styles.leftPanel} ${collapsed ? styles.collapsedPanel : ''}`}
      data-menu-open={menuOpen || undefined}
    >
      <header className={styles.panelHeader}>
        <div className={styles.panelTitle}>
          <span aria-hidden="true" className={styles.projectMark} />
          <div className={styles.panelTitleSelect} ref={sessionMenuRef}>
            <button
              aria-expanded={menuOpen}
              aria-haspopup="listbox"
              className={styles.panelTitleButton}
              disabled={running || sessionLoading}
              onClick={onToggleMenu}
              type="button"
            >
              <strong>{workspaceName}</strong>
              <span
                aria-hidden="true"
                className={styles.panelTitleChevron}
                data-open={menuOpen}
              >
                <svg fill="none" focusable="false" viewBox="0 0 16 16">
                  <path d="m5.25 6.25 2.75-2 2.75 2M5.25 9.75l2.75 2 2.75-2" />
                </svg>
              </span>
            </button>
            {menuOpen ? (
              <div
                aria-label={text('Sessions', '会话')}
                className={styles.executionMenu}
                role="listbox"
              >
                <div className={styles.executionMenuHeading}>
                  <strong>{text('Sessions', '会话')}</strong>
                  <span>{sessions.length}</span>
                </div>
                {sessions.map((session) => (
                  <button
                    aria-selected={session.id === sessionId}
                    className={styles.executionMenuItem}
                    key={session.id}
                    onClick={() => onOpenSession(session)}
                    role="option"
                    type="button"
                  >
                    <span>
                      {sessionTitle(session)}
                      {session.kind === 'builtin' ? (
                        <small className={styles.bundledProjectBadge}>
                          {text('Built-in', '内置')}
                        </small>
                      ) : null}
                    </span>
                    <time dateTime={session.updatedAt}>
                      {dateFormatter.format(Date.parse(session.updatedAt))}
                    </time>
                  </button>
                ))}
              </div>
            ) : null}
            <small>
              {sessionLoading
                ? text('Loading session…', '正在载入会话…')
                : connectionStatus}
            </small>
          </div>
        </div>
        <div className={styles.panelControls}>
          <button
            aria-expanded={!collapsed}
            aria-label={text('Toggle conversation panel', '切换对话面板')}
            className={styles.panelCollapseButton}
            onClick={onToggleCollapsed}
            type="button"
          >
            {collapsed ? '›' : '‹'}
          </button>
        </div>
      </header>

      {collapsed ? null : (
        <>
          <div
            aria-label={text('Left panel view', '左侧面板视图')}
            className={styles.leftTabs}
            role="tablist"
          >
            <button
              aria-selected={view === 'chat'}
              onClick={() => onViewChange('chat')}
              role="tab"
              type="button"
            >
              {text('Chat', '对话')}
            </button>
            <button
              aria-selected={view === 'files'}
              onClick={() => onViewChange('files')}
              role="tab"
              type="button"
            >
              {text('Files', '文件')}
            </button>
          </div>
          {view === 'chat' ? <ChatPanel {...chat} /> : <FilesPanel {...files} />}
        </>
      )}
    </aside>
  );
}
