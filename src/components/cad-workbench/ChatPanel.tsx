import {
  useEffect,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import styles from './ChatPanel.module.css';
import composerStyles from './Composer.module.css';
import { EmptyState } from '../ui/EmptyState';
import { chatStepLabel } from '../../lib/chat-step-label';
import {
  ACCEPTED_IMAGE_TYPES,
  type AssistantChatMessage,
  type ChatMessage,
  type ChatStepStatus,
  type ChatTurnTerminalStatus,
} from '../../types';
import type { Language, PendingImage } from './types';
import { translator } from './types';
import { LoadingSpinner, ToolbarIcon } from './WorkbenchPrimitives';

function formatElapsed(totalSeconds: number, language: Language) {
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;

  if (language === 'zh') {
    return `${hours ? `${hours}小时` : ''}${minutes ? `${minutes}分` : ''}${seconds || (!hours && !minutes) ? `${seconds}秒` : ''}`;
  }

  return [
    hours ? `${hours}h` : '',
    minutes ? `${minutes}m` : '',
    seconds || (!hours && !minutes) ? `${seconds}s` : '',
  ]
    .filter(Boolean)
    .join(' ');
}

function getStatusLabel(status: ChatStepStatus, language: Language) {
  const text = translator(language);

  switch (status) {
    case 'cancelled':
      return text('Cancelled', '已取消');
    case 'completed':
      return text('Completed', '已完成');
    case 'failed':
      return text('Failed', '失败');
    case 'running':
      return text('Running', '进行中');
  }
}

function getTerminalStatus(
  message: AssistantChatMessage,
): ChatTurnTerminalStatus {
  const status = message.steps.at(-1)?.status;
  return status === 'failed' || status === 'cancelled' ? status : 'completed';
}

function getStatusMarker(status: ChatTurnTerminalStatus) {
  return status === 'completed' ? '✓' : status === 'cancelled' ? '−' : '×';
}

function ChatStepMarker({ status }: { status: ChatStepStatus }) {
  if (status === 'running') {
    return <LoadingSpinner />;
  }

  return (
    <span aria-hidden="true" className={styles.chatStepMarker}>
      {getStatusMarker(status)}
    </span>
  );
}

function AssistantTurn({
  language,
  message,
}: {
  language: Language;
  message: AssistantChatMessage;
}) {
  const text = translator(language);
  const finishedAt = message.finishedAt;
  const finished = finishedAt !== undefined;
  const [traceOpen, setTraceOpen] = useState(!finished);
  const [now, setNow] = useState(() => Date.now());
  const terminalStatus = getTerminalStatus(message);
  const statusLabel = getStatusLabel(terminalStatus, language);
  const startedAt = message.startedAt ?? message.steps[0]?.occurredAt ?? now;
  const elapsedSeconds = Math.max(
    0,
    Math.floor(((finishedAt ?? now) - startedAt) / 1_000),
  );

  useEffect(() => {
    if (finishedAt !== undefined) {
      setTraceOpen(false);
      return;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [finishedAt]);

  return (
    <>
      <details
        aria-busy={!finished}
        className={styles.chatTrace}
        data-status={finished ? terminalStatus : 'running'}
        onToggle={(event) => setTraceOpen(event.currentTarget.open)}
        open={traceOpen}
      >
        <summary className={styles.chatTraceSummary}>
          {finished ? (
            <span aria-hidden="true" className={styles.chatTraceMarker}>
              {getStatusMarker(terminalStatus)}
            </span>
          ) : (
            <LoadingSpinner />
          )}
          <span>{finished ? statusLabel : text('In progress', '进行中')}</span>
          <span aria-hidden="true" className={styles.chatTraceSeparator}>
            ·
          </span>
          <time dateTime={`PT${elapsedSeconds}S`}>
            {finished
              ? text('Elapsed ', '用时 ')
              : text('Elapsed ', '已用时 ')}
            {formatElapsed(elapsedSeconds, language)}
          </time>
        </summary>

        {message.steps.length > 0 ? (
          <ol className={styles.chatSteps}>
            {message.steps.map((step) => (
              <li data-status={step.status} key={step.id}>
                <div className={styles.chatStepRow}>
                  <ChatStepMarker status={step.status} />
                  <span>
                    <span className={styles.srOnly}>
                      {getStatusLabel(step.status, language)}
                      {text(': ', '：')}
                    </span>
                    {chatStepLabel(step, language)}
                  </span>
                </div>
                {step.progressText ? (
                  <div className={`${styles.markdownText} ${styles.progressText}`}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {step.progressText}
                    </ReactMarkdown>
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
        ) : null}
      </details>

      {message.replyText ? (
        <div className={styles.markdownText}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.replyText}
          </ReactMarkdown>
        </div>
      ) : null}
    </>
  );
}

export interface ChatPanelProps {
  busy: boolean;
  conversationRef: RefObject<HTMLElement | null>;
  language: Language;
  messages: ChatMessage[];
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onNewProject: () => void;
  onPromptChange: (prompt: string) => void;
  onRemoveImage: (id: string) => void;
  onSelectImages: (event: ChangeEvent<HTMLInputElement>) => void;
  onStop: () => void;
  onSubmit: (event: FormEvent) => void;
  onWebSearchEnabledChange: (enabled: boolean) => void;
  pendingImages: PendingImage[];
  prompt: string;
  running: boolean;
  sessionLoading: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  webSearchConfigured: boolean;
  webSearchEnabled: boolean;
}

export function ChatPanel({
  busy,
  conversationRef,
  language,
  messages,
  onKeyDown,
  onNewProject,
  onPromptChange,
  onRemoveImage,
  onSelectImages,
  onStop,
  onSubmit,
  onWebSearchEnabledChange,
  pendingImages,
  prompt,
  running,
  sessionLoading,
  textareaRef,
  webSearchConfigured,
  webSearchEnabled,
}: ChatPanelProps) {
  const text = translator(language);
  return (
    <div className={styles.chatPanel} role="tabpanel">
      <section className={styles.conversation} ref={conversationRef}>
        {messages.length === 0 ? (
          <EmptyState
            className={styles.emptyState}
            title={text(
              'Describe the printable object you want.',
              '描述你想要的可打印物体。',
            )}
          />
        ) : (
          <ol className={styles.messageList}>
            {messages.map((message) => (
              <li data-role={message.role} key={message.id}>
                <span className={styles.messageRole}>
                  {message.role === 'user' ? text('You', '你') : 'A3D'}
                </span>
                <div className={styles.messageBubble}>
                  {message.role === 'user' ? (
                    <>
                      {message.images?.map((image) => (
                        <figure
                          className={styles.messageAttachment}
                          key={image.url}
                        >
                          <img alt={image.name} src={image.url} />
                          <figcaption>{image.name}</figcaption>
                        </figure>
                      ))}
                      {message.text ? (
                        <div className={styles.markdownText}>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {message.text}
                          </ReactMarkdown>
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <AssistantTurn
                      language={language}
                      message={message}
                    />
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className={composerStyles.composer}>
        <form className={composerStyles.composerForm} onSubmit={onSubmit}>
          <div className={composerStyles.composerShell}>
            <textarea
              aria-busy={running || busy}
              aria-label={text('CAD request', 'CAD 请求')}
              disabled={running || busy || sessionLoading}
              maxLength={8_000}
              onChange={(event) => onPromptChange(event.target.value)}
              onKeyDown={onKeyDown}
              placeholder={text(
                'Describe a printable object…',
                '描述一个可打印物体…',
              )}
              ref={textareaRef}
              rows={1}
              value={prompt}
            />

            {pendingImages.length > 0 ? (
              <div className={composerStyles.attachmentStrip}>
                {pendingImages.map((image) => (
                  <button
                    className={composerStyles.attachmentChip}
                    key={image.id}
                    onClick={() => onRemoveImage(image.id)}
                    title={text('Remove image', '移除图片')}
                    type="button"
                  >
                    <img alt="" src={image.url} />
                    {image.name}
                  </button>
                ))}
              </div>
            ) : null}

            <div className={composerStyles.composerFooter}>
              <div className={composerStyles.composerTools}>
                <button
                  aria-label={text('New project', '新项目')}
                  className={composerStyles.composerTool}
                  data-tooltip={text('New project', '新项目')}
                  disabled={running || busy || sessionLoading}
                  onClick={onNewProject}
                  type="button"
                >
                  <ToolbarIcon name="new-run" />
                </button>
                <label
                  className={composerStyles.composerTool}
                  data-tooltip={text(
                    'Attach reference images',
                    '附加参考图',
                  )}
                >
                  <span className={composerStyles.srOnly}>
                    {text('Attach reference images', '附加参考图')}
                  </span>
                  <input
                    accept={ACCEPTED_IMAGE_TYPES.join(',')}
                    className={composerStyles.srOnly}
                    disabled={running || busy || sessionLoading}
                    multiple
                    onChange={onSelectImages}
                    type="file"
                  />
                  <span aria-hidden="true">▧</span>
                </label>
                <button
                  aria-disabled="true"
                  aria-label={text(
                    'Runtime: Codex. This is currently the only available option.',
                    '运行时：Codex。当前仅支持此选项。',
                  )}
                  className={`${composerStyles.composerTool} ${composerStyles.runtimeSelector}`}
                  data-tooltip={text(
                    'Runtime · Codex (only option)',
                    '运行时 · Codex（当前唯一选项）',
                  )}
                  type="button"
                >
                  <ToolbarIcon name="runtime" />
                  <span>Codex</span>
                  <span
                    aria-hidden="true"
                    className={composerStyles.runtimeSelectorChevron}
                  >
                    ⌄
                  </span>
                </button>
                <button
                  aria-label={
                    webSearchConfigured
                      ? text('Toggle web references', '切换联网参考')
                      : text(
                          'A3D web search is unavailable',
                          'A3D 联网搜索不可用',
                        )
                  }
                  aria-pressed={webSearchEnabled}
                  className={`${composerStyles.composerTool} ${composerStyles.webSearchToggle}`}
                  data-active={webSearchEnabled}
                  data-tooltip={
                    webSearchConfigured
                      ? text(
                          'Allow A3D web search for this turn',
                          '本轮允许 A3D 联网搜索',
                        )
                      : text(
                          'A3D web search is unavailable',
                          'A3D 联网搜索不可用',
                        )
                  }
                  disabled={
                    running || busy || sessionLoading || !webSearchConfigured
                  }
                  onClick={() =>
                    onWebSearchEnabledChange(!webSearchEnabled)
                  }
                  type="button"
                >
                  <ToolbarIcon name="search" />
                  <span>{text('Web refs', '联网参考')}</span>
                </button>
              </div>
              <button
                aria-label={
                  running
                    ? text('Stop current run', '停止当前执行')
                    : text('Send message', '发送消息')
                }
                className={composerStyles.sendButton}
                data-state={running ? 'stop' : 'send'}
                disabled={
                  !running &&
                  (busy ||
                    sessionLoading ||
                    (!prompt.trim() && pendingImages.length === 0))
                }
                onClick={running ? onStop : undefined}
                type={running ? 'button' : 'submit'}
              >
                <ToolbarIcon name={running ? 'stop' : 'send'} />
              </button>
            </div>
          </div>
        </form>
      </section>
    </div>
  );
}
