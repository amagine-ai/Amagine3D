import styles from './ParametersPanel.module.css';
import type { ArtifactSummary, ArtifactWorkspace } from '../../types';
import { formatBytes } from '../../lib/format';
import type { Language } from './types';
import { translator } from './types';

interface ParametersPanelProps {
  artifactWorkspace: ArtifactWorkspace;
  artifacts: ArtifactSummary[];
  collapsed: boolean;
  language: Language;
  onDownload: (artifact: ArtifactSummary) => void;
  onToggle: () => void;
}

export function ParametersPanel({
  artifactWorkspace,
  artifacts,
  collapsed,
  language,
  onDownload,
  onToggle,
}: ParametersPanelProps) {
  const text = translator(language);
  return (
    <aside
      aria-label={text('Model parameters and exports', '模型参数与导出')}
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
        <>
          <div className={styles.parameterScroll}>
            <div className={styles.emptyState}>
              <strong>{text('No adjustable literals yet.', '暂时没有可调字面量。')}</strong>
              <span>
                {text(
                  'Generated files remain available below for download.',
                  '生成文件仍可在下方下载。',
                )}
              </span>
            </div>
            {artifacts.length > 0 ? (
              <section className={styles.exports}>
                <h2>{text('Downloads', '下载')}</h2>
                <ul>
                  {artifacts.map((artifact) => (
                    <li key={artifact.path}>
                      <div>
                        <strong>{artifact.name}</strong>
                        <small>
                          {artifact.kind} · {formatBytes(artifact.size)}
                        </small>
                      </div>
                      <button onClick={() => onDownload(artifact)} type="button">
                        {text('Download', '下载')}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
          <footer className={styles.parameterActions}>
            <p>
              {text(
                artifactWorkspace.readOnly
                  ? 'This bundled example is read-only and kept outside the Agent workspace.'
                  : 'Project files are stored under workspace/ in this repository.',
                artifactWorkspace.readOnly
                  ? '该内置示例为只读项目，不会写入 Agent 工作区。'
                  : '项目文件保存在仓库的 workspace/ 目录中。',
              )}
            </p>
          </footer>
        </>
      )}
    </aside>
  );
}
