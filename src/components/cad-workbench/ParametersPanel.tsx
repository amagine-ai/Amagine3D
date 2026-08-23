import styles from './ParametersPanel.module.css';
import { EmptyState } from '../ui/EmptyState';
import type { Language } from './types';
import { translator } from './types';

interface ParametersPanelProps {
  collapsed: boolean;
  language: Language;
  onToggle: () => void;
}

export function ParametersPanel({
  collapsed,
  language,
  onToggle,
}: ParametersPanelProps) {
  const text = translator(language);
  return (
    <aside
      aria-label={text('Model parameters', '模型参数')}
      className={`${styles.rightPanel} ${collapsed ? styles.collapsedPanel : ''}`}
    >
      <header className={styles.panelHeader}>
        <div className={styles.panelTitle}>
          <button
            aria-expanded={!collapsed}
            aria-label={text('Toggle parameter panel', '切换参数面板')}
            className={styles.panelCollapseButton}
            onClick={onToggle}
            type="button"
          >
            {collapsed ? '‹' : '›'}
          </button>
          <div>
            <strong>{text('Parameters', '参数')}</strong>
            <small>{text('Agent-produced project', 'Agent 生成项目')}</small>
          </div>
        </div>
      </header>
      {collapsed ? null : (
        <div className={styles.parameterScroll}>
          <EmptyState
            className={styles.emptyState}
            description={text(
              'Adjustable model parameters will appear here when the project provides them.',
              '项目提供可调模型参数后，它们会显示在这里。',
            )}
            title={text('No adjustable parameters yet.', '暂时没有可调参数。')}
          />
        </div>
      )}
    </aside>
  );
}
