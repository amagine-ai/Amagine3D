import {
  type CSSProperties,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  forwardRef,
  lazy,
  Suspense,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import styles from '../../apps/web/src/components/cad-workbench.module.css';
import {
  fetchArtifacts,
  fetchHealth,
  fetchSessionCatalog,
  fetchSessionDetail,
  streamAgent,
} from '../lib/agent-api';
import { preferredPreviewArtifact } from '../lib/artifact-selection';
import { finishChatStages, startChatStage } from '../lib/chat-stages';
import {
  ACCEPTED_IMAGE_TYPES,
  BUNDLED_POMODORO_SESSION_ID,
  MAX_IMAGE_BYTES,
  MAX_IMAGE_COUNT,
  MAX_TOTAL_IMAGE_BYTES,
  type AcceptedImageType,
  type AgentEvent,
  type ArtifactSummary,
  type ArtifactWorkspace,
  type ChatMessage,
  type HealthResponse,
  type ImageAttachment,
  type SessionSummary,
} from '../types';

const CadViewer = lazy(() =>
  import('./CadViewer').then((module) => ({ default: module.CadViewer })),
);

type Language = 'en' | 'zh';
type LeftView = 'chat' | 'files';

interface PendingImage extends ImageAttachment {
  id: string;
  size: number;
  url: string;
}

interface RuntimeEntry {
  id: string;
  level: 'error' | 'info';
  message: string;
  occurredAt: number;
  stage: string;
}

interface CadWorkbenchProps {
  language: Language;
  onDownloadTargetChange?: (target: ArtifactSummary | undefined) => void;
  onStorageOpenChange?: (open: boolean) => void;
  storageOpen: boolean;
}

export interface CadWorkbenchHandle {
  downloadCurrent: () => void;
}

type ToolbarIconName = 'new-run' | 'send' | 'stop';

const acceptedImageTypes = new Set<string>(ACCEPTED_IMAGE_TYPES);
const LEFT_PANEL_MIN = 280;
const LEFT_PANEL_MAX = 520;
const RIGHT_PANEL_MIN = 288;
const RIGHT_PANEL_MAX = 480;
const LOG_PANEL_MIN = 128;
const LOG_PANEL_MAX = 420;

function draftSession(sessionId: string): SessionSummary {
  const timestamp = new Date().toISOString();
  return {
    createdAt: timestamp,
    id: sessionId,
    kind: 'user',
    persisted: false,
    readOnly: false,
    title: 'New printable object',
    updatedAt: timestamp,
  };
}

function draftWorkspace(sessionId: string): ArtifactWorkspace {
  return {
    id: sessionId,
    name: 'Workspace',
    path: `workspace/sessions/${sessionId}/`,
    readOnly: false,
    sessionId,
  };
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function createSessionId(): string {
  return crypto.randomUUID();
}

function errorText(error: unknown, language: Language): string {
  if (error instanceof DOMException && error.name === 'AbortError') {
    return language === 'zh' ? '本轮执行已停止。' : 'This run was stopped.';
  }
  return error instanceof Error
    ? error.message
    : language === 'zh'
      ? '智能体执行失败，请检查服务端日志。'
      : 'The agent run failed. Check the server log.';
}

function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${String(bytes)} B`;
  if (bytes < 1_024 * 1_024) return `${String(Math.round(bytes / 1_024))} KB`;
  return `${(bytes / 1_024 / 1_024).toFixed(1)} MB`;
}

function fileGlyph(artifact: ArtifactSummary): string {
  if (artifact.kind === 'model') return '3D';
  if (artifact.kind === 'source') return 'PY';
  if (artifact.kind === 'image') return 'IMG';
  return '{}';
}

function readImage(file: File): Promise<PendingImage> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Unable to read ${file.name}.`));
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        reject(new Error(`Unable to read ${file.name}.`));
        return;
      }
      const separator = reader.result.indexOf(',');
      if (separator < 0) {
        reject(new Error(`Invalid image data: ${file.name}.`));
        return;
      }
      resolve({
        data: reader.result.slice(separator + 1),
        id: crypto.randomUUID(),
        mimeType: file.type as AcceptedImageType,
        name: file.name.slice(0, 255),
        size: file.size,
        url: reader.result,
      });
    };
    reader.readAsDataURL(file);
  });
}

function ToolbarIcon({ name }: { name: ToolbarIconName }) {
  return (
    <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 24 24">
      {name === 'new-run' ? (
        <path d="M12 5v14M5 12h14" />
      ) : name === 'send' ? (
        <path d="M12 19V5M6 11l6-6 6 6" />
      ) : (
        <rect height="10" rx="1.5" width="10" x="7" y="7" />
      )}
    </svg>
  );
}

function LoadingSpinner() {
  return <span aria-hidden="true" className={styles.loadingSpinner} />;
}

export const CadWorkbench = forwardRef<CadWorkbenchHandle, CadWorkbenchProps>(
  function CadWorkbench(
    {
      language,
      onDownloadTargetChange,
      onStorageOpenChange,
      storageOpen,
    },
    ref,
  ) {
    const text = (english: string, chinese: string) =>
      language === 'zh' ? chinese : english;
    const [activity, setActivity] = useState('');
    const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
    const [artifactWorkspace, setArtifactWorkspace] = useState<ArtifactWorkspace>({
      id: 'amagine3d-pomodoro',
      name: 'Amagine3D Pomodoro Timer',
      path: 'bundled-projects/amagine3d-pomodoro/',
      readOnly: true,
      sessionId: BUNDLED_POMODORO_SESSION_ID,
    });
    const [health, setHealth] = useState<HealthResponse>();
    const [healthError, setHealthError] = useState(false);
    const [leftCollapsed, setLeftCollapsed] = useState(false);
    const [leftView, setLeftView] = useState<LeftView>('chat');
    const [leftWidth, setLeftWidth] = useState(340);
    const [logCollapsed, setLogCollapsed] = useState(true);
    const [logHeight, setLogHeight] = useState(208);
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
    const [prompt, setPrompt] = useState('');
    const [rightCollapsed, setRightCollapsed] = useState(true);
    const [rightWidth, setRightWidth] = useState(320);
    const [running, setRunning] = useState(false);
    const [runtimeEntries, setRuntimeEntries] = useState<RuntimeEntry[]>([]);
    const [selectedPath, setSelectedPath] = useState<string>();
    const [selectedText, setSelectedText] = useState<string>();
    const [sessionId, setSessionId] = useState(BUNDLED_POMODORO_SESSION_ID);
    const [sessionLoading, setSessionLoading] = useState(true);
    const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
    const [sessions, setSessions] = useState<SessionSummary[]>([]);
    const [storageLoading, setStorageLoading] = useState(false);
    const abortRef = useRef<AbortController | undefined>(undefined);
    const artifactSnapshotRef = useRef<ArtifactSummary[]>([]);
    const conversationRef = useRef<HTMLElement>(null);
    const sessionMenuRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const selectedArtifact = useMemo(
      () => artifacts.find((artifact) => artifact.path === selectedPath),
      [artifacts, selectedPath],
    );
    const activeSession = useMemo(
      () => sessions.find((session) => session.id === sessionId),
      [sessionId, sessions],
    );
    const sessionTitle = (session: SessionSummary | undefined) =>
      session?.kind === 'builtin'
        ? text('Amagine3D Pomodoro Timer', 'Amagine3D 番茄钟')
        : session?.persisted
          ? session.title
          : text('New printable object', '新建可打印物体');
    const previewArtifact =
      selectedArtifact?.kind === 'model'
        ? selectedArtifact
        : preferredPreviewArtifact(artifacts);
    const artifactWorkspaceName =
      sessionId === BUNDLED_POMODORO_SESSION_ID
        ? text('Amagine3D Pomodoro Timer', 'Amagine3D 番茄钟')
        : sessionTitle(activeSession);

    const workspaceStyle = {
      '--workspace-left': leftCollapsed ? '3.25rem' : `${String(leftWidth)}px`,
      '--workspace-log-height': logCollapsed ? '52px' : `${String(logHeight)}px`,
      '--workspace-log-resizer': logCollapsed ? '0px' : '0.5rem',
      '--workspace-right': rightCollapsed ? '3.25rem' : `${String(rightWidth)}px`,
    } as CSSProperties;

    function addRuntimeEntry(
      message: string,
      stage: string,
      level: RuntimeEntry['level'] = 'info',
    ) {
      setRuntimeEntries((current) => [
        ...current.slice(-99),
        {
          id: crypto.randomUUID(),
          level,
          message,
          occurredAt: Date.now(),
          stage,
        },
      ]);
    }

    function startDraftStage(draftId: string, label: string, stage: string) {
      const nextStage = {
        id: crypto.randomUUID(),
        label,
        occurredAt: Date.now(),
        stage,
        status: 'running' as const,
      };
      setMessages((current) =>
        current.map((message) =>
          message.id === draftId
            ? {
                ...message,
                stages: startChatStage(message.stages ?? [], nextStage),
              }
            : message,
        ),
      );
    }

    function finishDraftStages(
      draftId: string,
      status: 'cancelled' | 'completed' | 'failed',
    ) {
      setMessages((current) =>
        current.map((message) =>
          message.id === draftId
            ? {
                ...message,
                stages: finishChatStages(message.stages ?? [], status),
              }
            : message,
        ),
      );
    }

    function selectArtifact(artifact: ArtifactSummary) {
      setSelectedPath(artifact.path);
      if (artifact.kind === 'model' || artifact.kind === 'image') {
        setLeftView('files');
      }
    }

    function downloadArtifact(artifact: ArtifactSummary | undefined) {
      if (!artifact) return;
      const anchor = document.createElement('a');
      anchor.download = artifact.name;
      anchor.href = artifact.url;
      anchor.click();
    }

    useImperativeHandle(
      ref,
      () => ({ downloadCurrent: () => downloadArtifact(selectedArtifact) }),
      [selectedArtifact],
    );

    useEffect(() => {
      onDownloadTargetChange?.(selectedArtifact);
    }, [onDownloadTargetChange, selectedArtifact]);

    function selectInitialArtifact(nextArtifacts: ArtifactSummary[]) {
      setSelectedPath(
        preferredPreviewArtifact(nextArtifacts)?.path ?? nextArtifacts[0]?.path,
      );
    }

    async function openSession(target: SessionSummary) {
      if (running || sessionLoading || target.id === sessionId) {
        setSessionMenuOpen(false);
        return;
      }
      setSessionMenuOpen(false);
      setSessionLoading(true);
      setPrompt('');
      setPendingImages([]);
      setRuntimeEntries([]);
      setLeftView('chat');
      try {
        if (!target.persisted) {
          setSessionId(target.id);
          setMessages([]);
          setArtifacts([]);
          setArtifactWorkspace(draftWorkspace(target.id));
          setSelectedPath(undefined);
          setSelectedText(undefined);
          return;
        }
        const detail = await fetchSessionDetail(target.id);
        setSessionId(detail.session.id);
        setMessages(detail.messages);
        setArtifacts(detail.artifacts);
        setArtifactWorkspace(detail.artifactWorkspace);
        selectInitialArtifact(detail.artifacts);
      } catch (error) {
        addRuntimeEntry(errorText(error, language), 'session', 'error');
      } finally {
        setSessionLoading(false);
      }
    }

    async function refreshArtifacts() {
      setStorageLoading(true);
      try {
        const next = await fetchArtifacts(sessionId);
        setArtifacts(next.artifacts);
        setArtifactWorkspace(next.artifactWorkspace);
        setSelectedPath((current) => {
          if (
            current &&
            next.artifacts.some(({ path }) => path === current)
          ) {
            return current;
          }
          return (
            preferredPreviewArtifact(next.artifacts)?.path ??
            next.artifacts[0]?.path
          );
        });
      } finally {
        setStorageLoading(false);
      }
    }

    useEffect(() => {
      let live = true;
      let timer: number | undefined;
      async function refresh() {
        try {
          const next = await fetchHealth();
          if (!live) return;
          setHealth(next);
          setHealthError(false);
        } catch {
          if (live) setHealthError(true);
        } finally {
          if (live) timer = window.setTimeout(refresh, 5_000);
        }
      }
      void refresh();
      return () => {
        live = false;
        if (timer !== undefined) window.clearTimeout(timer);
        abortRef.current?.abort();
      };
    }, []);

    useEffect(() => {
      let live = true;
      void fetchSessionCatalog()
        .then(async (catalog) => {
          if (!live) return;
          setSessions(catalog.sessions);
          const detail = await fetchSessionDetail(catalog.initialSessionId);
          if (!live) return;
          setSessionId(detail.session.id);
          setMessages(detail.messages);
          setArtifacts(detail.artifacts);
          setArtifactWorkspace(detail.artifactWorkspace);
          selectInitialArtifact(detail.artifacts);
        })
        .catch((error: unknown) => {
          if (live) addRuntimeEntry(errorText(error, language), 'session', 'error');
        })
        .finally(() => {
          if (live) setSessionLoading(false);
        });
      return () => {
        live = false;
      };
    }, []);

    useEffect(() => {
      if (!sessionMenuOpen) return;
      const closeOnOutsidePointer = (event: PointerEvent) => {
        if (!sessionMenuRef.current?.contains(event.target as Node)) {
          setSessionMenuOpen(false);
        }
      };
      const closeOnEscape = (event: globalThis.KeyboardEvent) => {
        if (event.key === 'Escape') setSessionMenuOpen(false);
      };
      document.addEventListener('pointerdown', closeOnOutsidePointer);
      document.addEventListener('keydown', closeOnEscape);
      return () => {
        document.removeEventListener('pointerdown', closeOnOutsidePointer);
        document.removeEventListener('keydown', closeOnEscape);
      };
    }, [sessionMenuOpen]);

    useEffect(() => {
      if (!selectedArtifact || !['report', 'source'].includes(selectedArtifact.kind)) {
        setSelectedText(undefined);
        return;
      }
      const controller = new AbortController();
      setSelectedText(undefined);
      void fetch(selectedArtifact.url, { signal: controller.signal })
        .then((response) => {
          if (!response.ok) throw new Error(String(response.status));
          return response.text();
        })
        .then(setSelectedText)
        .catch((error: unknown) => {
          if (!(error instanceof DOMException && error.name === 'AbortError')) {
            setSelectedText(text('Unable to read this file.', '无法读取该文件。'));
          }
        });
      return () => controller.abort();
    }, [selectedArtifact, language]);

    useEffect(() => {
      const frame = requestAnimationFrame(() => {
        const conversation = conversationRef.current;
        if (conversation) conversation.scrollTop = conversation.scrollHeight;
      });
      return () => cancelAnimationFrame(frame);
    }, [messages, activity]);

    const connectionStatus = useMemo(() => {
      if (healthError) return text('Service unavailable', '服务未连接');
      if (!health) return text('Checking runtime…', '正在检查运行环境…');
      if (!health.runtimeReady) return text('PI runtime unavailable', 'PI 运行时未就绪');
      if (!health.python.ready) return text('Python unavailable', 'Python 未就绪');
      if (!health.configured) return text('API key required', '等待配置密钥');
      return text('Ready for a new CAD request.', '可以开始新的 CAD 请求。');
    }, [health, healthError, language]);

    function updateDraft(
      event: AgentEvent,
      draftId: string,
      runSessionId: string,
    ) {
      if (event.type === 'start') {
        startDraftStage(
          draftId,
          text(`PI started ${event.model}`, `PI 已启动 ${event.model}`),
          'agent',
        );
        addRuntimeEntry(
          text(`PI started ${event.model}`, `PI 已启动 ${event.model}`),
          'agent',
        );
        return;
      }
      if (event.type === 'activity') {
        setActivity(event.label);
        startDraftStage(draftId, event.label, event.tool ?? 'agent');
        addRuntimeEntry(event.label, event.tool ?? 'agent');
        return;
      }
      if (event.type === 'token') {
        setActivity(text('Composing response', '正在组织回复'));
        setMessages((current) =>
          current.map((message) =>
            message.id === draftId
              ? { ...message, text: message.text + event.content }
              : message,
          ),
        );
        return;
      }
      if (event.type === 'assistant') {
        setMessages((current) =>
          current.map((message) =>
            message.id === draftId
              ? { ...message, state: 'complete', text: event.content }
              : message,
          ),
        );
        return;
      }
      if (event.type === 'artifacts') {
        if (event.sessionId !== runSessionId) return;
        setArtifacts(event.artifacts);
        if (event.artifactWorkspace) {
          setArtifactWorkspace(event.artifactWorkspace);
        }
        const previous = new Map(
          artifactSnapshotRef.current.map((artifact) => [
            artifact.path,
            `${artifact.modifiedAt}:${String(artifact.size)}`,
          ]),
        );
        const changedArtifacts = event.artifacts.filter(
          (artifact) =>
            previous.get(artifact.path) !==
            `${artifact.modifiedAt}:${String(artifact.size)}`,
        );
        const currentPreview =
          preferredPreviewArtifact(changedArtifacts) ??
          preferredPreviewArtifact(event.artifacts);
        if (currentPreview) setSelectedPath(currentPreview.path);
        addRuntimeEntry(
          text(
            `${String(event.artifacts.length)} workspace files discovered`,
            `已发现 ${String(event.artifacts.length)} 个工作区文件`,
          ),
          'files',
        );
        return;
      }
      if (event.type === 'done') {
        finishDraftStages(draftId, 'completed');
        addRuntimeEntry(text('Run completed', '执行完成'), 'done');
        void fetchSessionCatalog()
          .then((catalog) => setSessions(catalog.sessions))
          .catch(() => undefined);
        return;
      }
      if (event.type === 'error') throw new Error(event.message);
    }

    async function submit(event?: FormEvent) {
      event?.preventDefault();
      const messageText = prompt.trim();
      if (
        (!messageText && pendingImages.length === 0) ||
        running ||
        sessionLoading
      ) {
        return;
      }

      const images = pendingImages.map(({ data, mimeType, name }) => ({
        data,
        mimeType,
        name,
      }));
      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        images: pendingImages.map(({ name, url }) => ({ name, url })),
        role: 'user',
        state: 'complete',
        text: messageText,
      };
      const draftId = crypto.randomUUID();
      const controller = new AbortController();
      const requestSessionId =
        sessionId === BUNDLED_POMODORO_SESSION_ID
          ? beginUserDraft(true)
          : sessionId;
      abortRef.current = controller;
      artifactSnapshotRef.current =
        requestSessionId === sessionId ? artifacts : [];
      setMessages((current) => [
        ...current,
        userMessage,
        {
          id: draftId,
          role: 'assistant',
          stages: [
            {
              id: crypto.randomUUID(),
              label: text('Starting PI Agent', '正在启动 PI Agent'),
              occurredAt: Date.now(),
              stage: 'start',
              status: 'running',
            },
          ],
          state: 'streaming',
          text: '',
        },
      ]);
      setPendingImages([]);
      setPrompt('');
      setRunning(true);
      setActivity(text('Starting PI Agent', '正在启动 PI Agent'));
      addRuntimeEntry(text('Starting PI Agent', '正在启动 PI Agent'), 'start');

      try {
        await streamAgent({
          images,
          message: messageText,
          onEvent: (agentEvent) =>
            updateDraft(agentEvent, draftId, requestSessionId),
          sessionId: requestSessionId,
          signal: controller.signal,
        });
        setMessages((current) =>
          current.map((message) =>
            message.id === draftId
              ? {
                  ...message,
                  state: 'complete',
                  text:
                    message.text || text('Run completed.', '执行已完成。'),
                }
              : message,
          ),
        );
      } catch (error) {
        const message = errorText(error, language);
        finishDraftStages(
          draftId,
          error instanceof DOMException && error.name === 'AbortError'
            ? 'cancelled'
            : 'failed',
        );
        addRuntimeEntry(message, 'error', 'error');
        setMessages((current) =>
          current.map((item) =>
            item.id === draftId
              ? { ...item, state: 'complete', text: message }
              : item,
          ),
        );
      } finally {
        abortRef.current = undefined;
        setActivity('');
        setRunning(false);
        requestAnimationFrame(() => textareaRef.current?.focus());
      }
    }

    async function selectImages(event: ChangeEvent<HTMLInputElement>) {
      const files = Array.from(event.currentTarget.files ?? []);
      event.currentTarget.value = '';
      if (files.length === 0) return;
      if (pendingImages.length + files.length > MAX_IMAGE_COUNT) {
        addRuntimeEntry(
          text(
            `Attach at most ${String(MAX_IMAGE_COUNT)} images.`,
            `每次最多上传 ${String(MAX_IMAGE_COUNT)} 张图片。`,
          ),
          'image',
          'error',
        );
        return;
      }
      if (files.some((file) => !acceptedImageTypes.has(file.type))) {
        addRuntimeEntry(text('Unsupported image format.', '存在不支持的图片格式。'), 'image', 'error');
        return;
      }
      if (files.some((file) => file.size > MAX_IMAGE_BYTES)) {
        addRuntimeEntry(text('An image is too large.', '单张图片大小超出限制。'), 'image', 'error');
        return;
      }
      const totalSize =
        pendingImages.reduce((sum, image) => sum + image.size, 0) +
        files.reduce((sum, file) => sum + file.size, 0);
      if (totalSize > MAX_TOTAL_IMAGE_BYTES) {
        addRuntimeEntry(text('Images are too large.', '图片总大小超出限制。'), 'image', 'error');
        return;
      }
      try {
        const next = await Promise.all(files.map(readImage));
        setPendingImages((current) => [...current, ...next]);
      } catch (error) {
        addRuntimeEntry(errorText(error, language), 'image', 'error');
      }
    }

    function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        event.currentTarget.form?.requestSubmit();
      }
    }

    function beginUserDraft(preserveComposer = false): string {
      const nextSessionId = createSessionId();
      const nextSession = draftSession(nextSessionId);
      setSessions((current) => [
        nextSession,
        ...current.filter((session) => session.persisted),
      ]);
      setSessionId(nextSessionId);
      setMessages([]);
      setArtifacts([]);
      setArtifactWorkspace(draftWorkspace(nextSessionId));
      setSelectedPath(undefined);
      setSelectedText(undefined);
      if (!preserveComposer) {
        setPrompt('');
        setPendingImages([]);
      }
      setRuntimeEntries([]);
      setLeftView('chat');
      requestAnimationFrame(() => textareaRef.current?.focus());
      return nextSessionId;
    }

    function beginFreshRun() {
      if (running || sessionLoading) return;
      beginUserDraft();
    }

    function beginSideResize(
      side: 'left' | 'right',
      event: ReactPointerEvent<HTMLDivElement>,
    ) {
      const startX = event.clientX;
      const startWidth = side === 'left' ? leftWidth : rightWidth;
      const move = (moveEvent: PointerEvent) => {
        const delta = moveEvent.clientX - startX;
        if (side === 'left') {
          setLeftWidth(clamp(startWidth + delta, LEFT_PANEL_MIN, LEFT_PANEL_MAX));
        } else {
          setRightWidth(
            clamp(startWidth - delta, RIGHT_PANEL_MIN, RIGHT_PANEL_MAX),
          );
        }
      };
      const stop = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', stop);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', stop, { once: true });
    }

    function beginLogResize(event: ReactPointerEvent<HTMLDivElement>) {
      const startY = event.clientY;
      const startHeight = logHeight;
      const move = (moveEvent: PointerEvent) => {
        setLogHeight(
          clamp(startHeight + startY - moveEvent.clientY, LOG_PANEL_MIN, LOG_PANEL_MAX),
        );
      };
      const stop = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', stop);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', stop, { once: true });
    }

    return (
      <div className={styles.workspace} style={workspaceStyle}>
        <aside
          aria-label={text('Conversation and generated files', '对话与生成文件')}
          className={`${styles.leftPanel} ${leftCollapsed ? styles.collapsedPanel : ''}`}
          data-menu-open={sessionMenuOpen || undefined}
        >
          <header className={styles.panelHeader}>
            <div className={styles.panelTitle}>
              <span aria-hidden="true" className={styles.projectMark} />
              <div className={styles.panelTitleSelect} ref={sessionMenuRef}>
                <button
                  aria-expanded={sessionMenuOpen}
                  aria-haspopup="listbox"
                  className={styles.panelTitleButton}
                  disabled={running || sessionLoading}
                  onClick={() => setSessionMenuOpen((open) => !open)}
                  type="button"
                >
                  <strong>{artifactWorkspaceName}</strong>
                  <span
                    aria-hidden="true"
                    className={styles.panelTitleChevron}
                    data-open={sessionMenuOpen}
                  >
                    <svg fill="none" focusable="false" viewBox="0 0 16 16">
                      <path d="m5.25 6.25 2.75-2 2.75 2M5.25 9.75l2.75 2 2.75-2" />
                    </svg>
                  </span>
                </button>
                {sessionMenuOpen ? (
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
                        onClick={() => void openSession(session)}
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
                          {new Intl.DateTimeFormat(
                            language === 'zh' ? 'zh-CN' : 'en',
                            { month: 'short', day: 'numeric' },
                          ).format(Date.parse(session.updatedAt))}
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
                aria-expanded={!leftCollapsed}
                aria-label={text('Toggle conversation panel', '切换对话面板')}
                className={styles.panelCollapseButton}
                onClick={() => setLeftCollapsed((collapsed) => !collapsed)}
                type="button"
              >
                {leftCollapsed ? '›' : '‹'}
              </button>
            </div>
          </header>

          {leftCollapsed ? null : (
            <>
              <div
                aria-label={text('Left panel view', '左侧面板视图')}
                className={styles.leftTabs}
                role="tablist"
              >
                <button
                  aria-selected={leftView === 'chat'}
                  onClick={() => setLeftView('chat')}
                  role="tab"
                  type="button"
                >
                  {text('Chat', '对话')}
                </button>
                <button
                  aria-selected={leftView === 'files'}
                  onClick={() => setLeftView('files')}
                  role="tab"
                  type="button"
                >
                  {text('Files', '文件')}
                </button>
              </div>

              {leftView === 'chat' ? (
                <div className={styles.chatPanel} role="tabpanel">
                  <section className={styles.conversation} ref={conversationRef}>
                    {messages.length === 0 ? (
                      <div className={styles.emptyState}>
                        <strong>
                          {text(
                            'Describe the printable object you want.',
                            '描述你想要的可打印物体。',
                          )}
                        </strong>
                      </div>
                    ) : (
                      <ol className={styles.messageList}>
                        {messages.map((message) => (
                          <li data-role={message.role} key={message.id}>
                            <span className={styles.messageRole}>
                              {message.role === 'user'
                                ? text('You', '你')
                                : 'Amagine'}
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
                                        <span
                                          aria-hidden="true"
                                          className={styles.chatStageMarker}
                                        >
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

                  <section className={styles.composer}>
                    <form className={styles.composerForm} onSubmit={submit}>
                      <div className={styles.composerShell}>
                        <textarea
                          aria-busy={running}
                          aria-label={text('CAD request', 'CAD 请求')}
                          disabled={running || sessionLoading}
                          maxLength={8_000}
                          onChange={(event) => setPrompt(event.target.value)}
                          onKeyDown={handleComposerKeyDown}
                          placeholder={text(
                            'Describe a printable object…',
                            '描述一个可打印物体…',
                          )}
                          ref={textareaRef}
                          rows={1}
                          value={prompt}
                        />

                        {pendingImages.length > 0 ? (
                          <div className={styles.attachmentStrip}>
                            {pendingImages.map((image) => (
                              <button
                                className={styles.attachmentChip}
                                key={image.id}
                                onClick={() =>
                                  setPendingImages((current) =>
                                    current.filter(({ id }) => id !== image.id),
                                  )
                                }
                                title={text('Remove image', '移除图片')}
                                type="button"
                              >
                                <img alt="" src={image.url} />
                                {image.name}
                              </button>
                            ))}
                          </div>
                        ) : null}

                        <div className={styles.composerFooter}>
                          <div className={styles.composerTools}>
                            <button
                              aria-label={text('New project', '新项目')}
                              className={styles.composerTool}
                              data-tooltip={text('New project', '新项目')}
                              disabled={running || sessionLoading}
                              onClick={beginFreshRun}
                              type="button"
                            >
                              <ToolbarIcon name="new-run" />
                            </button>
                            <label
                              className={styles.composerTool}
                              data-tooltip={text(
                                'Attach reference images',
                                '附加参考图',
                              )}
                            >
                              <span className={styles.srOnly}>
                                {text('Attach reference images', '附加参考图')}
                              </span>
                              <input
                                accept={ACCEPTED_IMAGE_TYPES.join(',')}
                                className={styles.srOnly}
                                disabled={running || sessionLoading}
                                multiple
                                onChange={(event) => void selectImages(event)}
                                type="file"
                              />
                              <span aria-hidden="true">▧</span>
                            </label>
                          </div>
                          <button
                            aria-label={
                              running
                                ? text('Stop current run', '停止当前执行')
                                : text('Send message', '发送消息')
                            }
                            className={styles.sendButton}
                            data-state={running ? 'stop' : 'send'}
                            disabled={
                              !running &&
                              (sessionLoading ||
                                (!prompt.trim() && pendingImages.length === 0))
                            }
                            onClick={running ? () => abortRef.current?.abort() : undefined}
                            type={running ? 'button' : 'submit'}
                          >
                            <ToolbarIcon name={running ? 'stop' : 'send'} />
                          </button>
                        </div>
                      </div>
                    </form>
                  </section>
                </div>
              ) : (
                <div className={styles.fileWorkspace} role="tabpanel">
                  <section className={styles.fileSection}>
                    <div className={styles.sectionHeading}>
                      <h2>{artifactWorkspaceName}</h2>
                      <div className={styles.fileHeadingActions}>
                        <span>{artifacts.length}</span>
                        <button onClick={() => void refreshArtifacts()} type="button">
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
                                onChange={() => selectArtifact(artifact)}
                                type="checkbox"
                              />
                            </label>
                            <button
                              aria-current={selectedPath === artifact.path}
                              onClick={() => selectArtifact(artifact)}
                              title={artifact.path}
                              type="button"
                            >
                              <span className={styles.fileIcon}>
                                {fileGlyph(artifact)}
                              </span>
                              <span>{artifact.name}</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                </div>
              )}
            </>
          )}
        </aside>

        <div
          aria-disabled={leftCollapsed}
          aria-label={text('Resize conversation panel', '调整对话面板宽度')}
          className={styles.panelResizer}
          data-side="left"
          onPointerDown={(event) => beginSideResize('left', event)}
          role="separator"
        >
          <span aria-hidden="true" />
        </div>

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
                {running ? 'RUNNING' : health?.runtimeReady ? 'READY' : 'OFFLINE'}
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
            className={styles.logResizer}
            onPointerDown={beginLogResize}
            role="separator"
          >
            <span aria-hidden="true" />
          </div>

          <section
            className={`${styles.activityLog} ${logCollapsed ? styles.activityLogCollapsed : ''}`}
          >
            <header className={styles.activityLogHeader}>
              <div>
                <strong>{text('Activity', '执行')}</strong>
                <small>{activity || connectionStatus}</small>
              </div>
              <button
                aria-expanded={!logCollapsed}
                onClick={() => setLogCollapsed((collapsed) => !collapsed)}
                type="button"
              >
                {logCollapsed ? '⌃' : '⌄'}
              </button>
            </header>
            {logCollapsed ? null : (
              <div className={styles.activityLogBody}>
                {runtimeEntries.length === 0 ? (
                  <p className={styles.runtimeEventsEmpty}>
                    {text(
                      'Runtime events will appear here.',
                      '运行时事件会显示在这里。',
                    )}
                  </p>
                ) : (
                  <ol className={styles.runtimeEvents}>
                    {runtimeEntries.map((entry) => (
                      <li data-level={entry.level} key={entry.id}>
                        <time>
                          {new Intl.DateTimeFormat(language === 'zh' ? 'zh-CN' : 'en', {
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit',
                          }).format(entry.occurredAt)}
                        </time>
                        <span>{entry.stage}</span>
                        <p>{entry.message}</p>
                      </li>
                    ))}
                  </ol>
                )}
                <div className={styles.platformNotices}>
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

        <div
          aria-disabled={rightCollapsed}
          aria-label={text('Resize parameter panel', '调整参数面板宽度')}
          className={styles.panelResizer}
          data-side="right"
          onPointerDown={(event) => beginSideResize('right', event)}
          role="separator"
        >
          <span aria-hidden="true" />
        </div>

        <aside
          aria-label={text('Model parameters and exports', '模型参数与导出')}
          className={`${styles.rightPanel} ${rightCollapsed ? styles.collapsedPanel : ''}`}
        >
          <header className={styles.panelHeader}>
            <div className={styles.panelTitle}>
              <button
                aria-expanded={!rightCollapsed}
                aria-label={text('Toggle parameter panel', '切换参数面板')}
                className={styles.panelCollapseButton}
                onClick={() => setRightCollapsed((collapsed) => !collapsed)}
                type="button"
              >
                {rightCollapsed ? '‹' : '›'}
              </button>
              <div>
                <strong>{text('Parameters', '参数')}</strong>
                <small>{text('Agent-produced project', 'Agent 生成项目')}</small>
              </div>
            </div>
          </header>
          {rightCollapsed ? null : (
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
                          <button onClick={() => downloadArtifact(artifact)} type="button">
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

        {storageOpen ? (
          <aside
            aria-label={text('Project folder storage', '项目目录存储')}
            className={styles.storageDrawer}
            data-open="true"
          >
            <section className={styles.storageSection}>
              <div className={styles.storageDrawerHeader}>
                <div className={styles.sectionHeading}>
                  <div>
                    <h2>{artifactWorkspaceName}</h2>
                    <small>
                      {artifactWorkspace.readOnly
                        ? text('Built-in read-only project', '内置只读项目')
                        : text(
                            'Files stored in this repository folder',
                            '文件直接保存在当前仓库目录',
                          )}
                    </small>
                  </div>
                  <button
                    aria-label={text('Refresh storage', '刷新存储')}
                    className={styles.storageRefreshButton}
                    disabled={storageLoading}
                    onClick={() => void refreshArtifacts()}
                    type="button"
                  >
                    {storageLoading ? (
                      <LoadingSpinner />
                    ) : (
                      <svg fill="none" focusable="false" viewBox="0 0 20 20">
                        <path d="M15.4 6.4A6.25 6.25 0 1 0 16 12" />
                        <path d="M15.5 3.5v3.25h-3.25" />
                      </svg>
                    )}
                  </button>
                </div>
                <button
                  aria-label={text('Close storage', '关闭存储')}
                  className={styles.storageDrawerClose}
                  onClick={() => onStorageOpenChange?.(false)}
                  type="button"
                >
                  <svg fill="none" focusable="false" viewBox="0 0 20 20">
                    <path d="m5.25 5.25 9.5 9.5M14.75 5.25l-9.5 9.5" />
                  </svg>
                </button>
              </div>
              <div className={styles.storageViewport}>
                {artifacts.length === 0 ? (
                  <div className={styles.emptyState}>
                    <strong>{text('No project files yet.', '暂无项目文件。')}</strong>
                    <span>
                      {text(
                        `Generated files will appear under ${artifactWorkspace.path}.`,
                        `生成文件会出现在 ${artifactWorkspace.path} 下。`,
                      )}
                    </span>
                  </div>
                ) : (
                  <div className={styles.storageGroups}>
                    <section className={styles.folderGroup}>
                      <div className={styles.storageGroupHeading}>
                        <span className={styles.fileIcon}>⌄</span>
                        <div className={styles.projectSummary}>
                          <h3>{artifactWorkspace.path}</h3>
                          <span>
                            {String(artifacts.length)} {text('files', '个文件')}
                          </span>
                        </div>
                      </div>
                      <ul className={styles.folderFileList}>
                        {artifacts.map((artifact) => (
                          <li key={artifact.path}>
                            <span className={styles.fileIcon}>{fileGlyph(artifact)}</span>
                            <button
                              className={styles.storageFileIdentity}
                              onClick={() => {
                                selectArtifact(artifact);
                                onStorageOpenChange?.(false);
                              }}
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
                    </section>
                  </div>
                )}
              </div>
              <footer className={styles.storageActions}>
                <div className={styles.storageSelectionBar}>
                  <span>{artifactWorkspace.path}</span>
                  <button onClick={() => void refreshArtifacts()} type="button">
                    {text('Refresh', '刷新')}
                  </button>
                </div>
              </footer>
            </section>
          </aside>
        ) : null}
      </div>
    );
  },
);
