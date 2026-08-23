import styles from './FilesPanel.module.css';
import type { ArtifactSummary } from '../../types';
import { ArtifactIcon } from './ArtifactIcon';
import type { Language } from './types';
import { translator } from './types';

export interface FilesPanelProps {
  artifacts: ArtifactSummary[];
  language: Language;
  onRefresh: () => void;
  onSelect: (artifact: ArtifactSummary) => void;
  selectedPath: string | undefined;
  workspaceName: string;
}

export function FilesPanel({
  artifacts,
  language,
  onRefresh,
  onSelect,
  selectedPath,
  workspaceName,
}: FilesPanelProps) {
  const text = translator(language);
  return (
    <div className={styles.fileWorkspace} role="tabpanel">
      <section className={styles.fileSection}>
        <div className={styles.sectionHeading}>
          <h2>{workspaceName}</h2>
          <div className={styles.fileHeadingActions}>
            <span>{artifacts.length}</span>
            <button onClick={onRefresh} type="button">
              {text('Refresh', '刷新')}
            </button>
          </div>
        </div>
        {artifacts.length === 0 ? (
          <p className={styles.fileEmpty}>
            {text(
              'Files appear after the Agent saves them.',
              'Agent 保存文件后会显示在这里。',
            )}
          </p>
        ) : (
          <ul className={styles.fileTree}>
            {artifacts.map((artifact) => (
              <li key={artifact.path}>
                <label className={styles.fileTreeCheckbox}>
                  <input
                    aria-label={text('Select file', '选择文件')}
                    checked={selectedPath === artifact.path}
                    onChange={() => onSelect(artifact)}
                    type="checkbox"
                  />
                </label>
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
    </div>
  );
}
