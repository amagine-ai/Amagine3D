import styles from './ParametersPanel.module.css';
import { EmptyState } from '../ui/EmptyState';
import type { ParameterModel } from '../../types';
import type { Language } from './types';
import { translator } from './types';

interface ParameterGroup {
  id: string;
  label: string;
  parameters: ParameterModel['parameters'];
}

function localizedText(
  language: Language,
  english: string,
  chinese?: string,
): string {
  return language === 'zh' && chinese?.trim() ? chinese.trim() : english;
}

function groupedParameters(
  model: ParameterModel,
  language: Language,
): ParameterGroup[] {
  const groups = new Map<string, ParameterGroup>();
  for (const parameter of model.parameters) {
    const english = parameter.group?.trim();
    const chinese = parameter.groupZh?.trim();
    const id = english || chinese || '__general__';
    const label =
      language === 'zh'
        ? chinese || english || '常规'
        : english || chinese || 'General';
    const group = groups.get(id);
    if (group) group.parameters.push(parameter);
    else groups.set(id, { id, label, parameters: [parameter] });
  }
  return [...groups.values()];
}

interface ParametersPanelProps {
  busy: boolean;
  collapsed: boolean;
  hasParameterModels: boolean;
  issue?: string;
  language: Language;
  model?: ParameterModel;
  onCommit: (parameterId: string) => void;
  onToggle: () => void;
  onValueChange: (parameterId: string, value: number) => void;
  rebuilding: boolean;
  values: Record<string, number>;
}

export function ParametersPanel({
  busy,
  collapsed,
  hasParameterModels,
  issue,
  language,
  model,
  onCommit,
  onToggle,
  onValueChange,
  rebuilding,
  values,
}: ParametersPanelProps) {
  const text = translator(language);
  const parameterGroups = model ? groupedParameters(model, language) : [];
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
            <small>
              {rebuilding
                ? text('Rebuilding complete model…', '正在重建完整模型…')
                : model
                  ? model.modelId
                  : text('Complete model controls', '完整模型控制')}
            </small>
          </div>
        </div>
      </header>
      {collapsed ? null : (
        <div className={styles.parameterScroll}>
          {issue ? <div className={styles.parameterIssue}>{issue}</div> : null}
          {!model ? (
            <EmptyState
              className={styles.emptyState}
              description={
                hasParameterModels
                  ? text(
                      'Select the display GLB or its STL/3MF print root to adjust the complete model.',
                      '请选择展示 GLB 或对应的 STL/3MF 打印根文件，才能调整完整模型。',
                    )
                  : text(
                      'Adjustable model parameters will appear here when the project declares them.',
                      '项目声明可调模型参数后，它们会显示在这里。',
                    )
              }
              title={
                hasParameterModels
                  ? text('Open the complete model.', '请打开完整模型。')
                  : text('No adjustable parameters yet.', '暂时没有可调参数。')
              }
            />
          ) : model.parameterError ? (
            <div className={styles.parameterIssue}>{model.parameterError}</div>
          ) : model.parameters.length === 0 ? (
            <EmptyState
              className={styles.emptyState}
              description={text(
                'This complete model does not declare any parameter() controls.',
                '这个完整模型尚未声明任何 parameter() 控件。',
              )}
              title={text('No declared parameters.', '没有已声明的参数。')}
            />
          ) : (
            <div className={styles.parameterGroups}>
              {parameterGroups.map((group) => (
                <fieldset
                  className={styles.parameterGroup}
                  disabled={busy}
                  key={group.id}
                >
                  <legend>{group.label}</legend>
                  {group.parameters.map((parameter) => {
                    const label = localizedText(
                      language,
                      parameter.label,
                      parameter.labelZh,
                    );
                    const value = values[parameter.id] ?? parameter.value;
                    const unit = parameter.unit ? ` ${parameter.unit}` : '';
                    return (
                      <div className={styles.parameterField} key={parameter.id}>
                        <label htmlFor={`parameter-${parameter.id}`}>
                          <span>{label}</span>
                          <code>{parameter.name}</code>
                        </label>
                        <div className={styles.parameterValueRow}>
                          <input
                            id={`parameter-${parameter.id}`}
                            max={parameter.maximum}
                            min={parameter.minimum}
                            onBlur={() => onCommit(parameter.id)}
                            onChange={(event) => {
                              const next = event.currentTarget.valueAsNumber;
                              if (Number.isFinite(next)) {
                                onValueChange(parameter.id, next);
                              }
                            }}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') {
                                event.currentTarget.blur();
                              }
                            }}
                            step={parameter.step}
                            type="number"
                            value={value}
                          />
                          {parameter.unit ? <span>{parameter.unit}</span> : null}
                        </div>
                        <div className={styles.parameterRange}>
                          <input
                            aria-label={label}
                            max={parameter.maximum}
                            min={parameter.minimum}
                            onChange={(event) =>
                              onValueChange(
                                parameter.id,
                                event.currentTarget.valueAsNumber,
                              )
                            }
                            onKeyUp={() => onCommit(parameter.id)}
                            onPointerUp={() => onCommit(parameter.id)}
                            step={parameter.step}
                            type="range"
                            value={value}
                          />
                          <small className={styles.parameterLimits}>
                            <span>
                              <b>MIN</b> {parameter.minimum}
                              {unit}
                            </span>
                            <span>
                              <b>MAX</b> {parameter.maximum}
                              {unit}
                            </span>
                          </small>
                        </div>
                        {parameter.affects.length > 0 ? (
                          <small className={styles.parameterAffects}>
                            {text('Affects', '影响')}:{' '}
                            {parameter.affects.join(', ')}
                          </small>
                        ) : null}
                      </div>
                    );
                  })}
                </fieldset>
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
