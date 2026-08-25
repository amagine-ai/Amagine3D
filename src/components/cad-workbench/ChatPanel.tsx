import type {
  ChangeEvent,
  FormEvent,
  KeyboardEvent,
  RefObject,
} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import styles from './ChatPanel.module.css';
import composerStyles from './Composer.module.css';
import { EmptyState } from '../ui/EmptyState';
import { ACCEPTED_IMAGE_TYPES, type ChatMessage } from '../../types';
import type { Language, PendingImage } from './types';
import { translator } from './types';
import { LoadingSpinner, ToolbarIcon } from './WorkbenchPrimitives';

export interface ChatPanelProps {
  activity: string;
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
  activity,
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
                  {message.role === 'user' ? text('You', '你') : 'Amagine'}
                </span>
                <div className={styles.messageBubble}>
                  {message.images?.map((image) => (
                    <figure className={styles.messageAttachment} key={image.url}>
                      <img alt={image.name} src={image.url} />
                      <figcaption>{image.name}</figcaption>
                    </figure>
                  ))}
                  {message.stages && message.stages.length > 0 ? (
                    <ol className={styles.chatStages}>
                      {message.stages.map((stage) => (
                        <li data-status={stage.status} key={stage.id}>
                          {stage.status === 'running' ? (
                            <LoadingSpinner />
                          ) : (
                            <span aria-hidden="true" className={styles.chatStageMarker}>
                              {stage.status === 'completed' ? '✓' : '×'}
                            </span>
                          )}
                          <span>{stage.label}</span>
                        </li>
                      ))}
                    </ol>
                  ) : null}
                  {message.text ? (
                    <div className={styles.reasoningText}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {message.text}
                      </ReactMarkdown>
                    </div>
                  ) : !message.stages?.length ? (
                    <div className={styles.conversationActivity}>
                      <LoadingSpinner />
                      <span>{activity || text('Thinking…', '正在思考…')}</span>
                    </div>
                  ) : null}
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
              aria-busy={running}
              aria-label={text('CAD request', 'CAD 请求')}
              disabled={running || sessionLoading}
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
                  disabled={running || sessionLoading}
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
                    disabled={running || sessionLoading}
                    multiple
                    onChange={onSelectImages}
                    type="file"
                  />
                  <span aria-hidden="true">▧</span>
                </label>
                <button
                  aria-label={
                    webSearchConfigured
                      ? text('Toggle web references', '切换联网参考')
                      : text(
                          'Configure TAVILY_API_KEY to use web references',
                          '配置 TAVILY_API_KEY 后可使用联网参考',
                        )
                  }
                  aria-pressed={webSearchEnabled}
                  className={`${composerStyles.composerTool} ${composerStyles.webSearchToggle}`}
                  data-active={webSearchEnabled}
                  data-tooltip={
                    webSearchConfigured
                      ? text(
                          'Require web references while enabled',
                          '开启时每轮强制使用联网参考',
                        )
                      : text(
                          'Tavily API key is not configured',
                          '尚未配置 Tavily API 密钥',
                        )
                  }
                  disabled={
                    running || sessionLoading || !webSearchConfigured
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
                  (sessionLoading ||
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
