import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';

import styles from '../../apps/web/src/components/cad-workbench.module.css';
import { LeftPanel } from './cad-workbench/LeftPanel';
import { ParametersPanel } from './cad-workbench/ParametersPanel';
import { PreviewPanel } from './cad-workbench/PreviewPanel';
import { StorageDrawer } from './cad-workbench/StorageDrawer';
import {
  type Language,
  type LeftView,
  type PendingImage,
  type RuntimeEntry,
  translator,
} from './cad-workbench/types';
import {
  createSessionId,
  draftSession,
  draftWorkspace,
  errorText,
  readImage,
} from './cad-workbench/utils';
import { useWorkbenchLayout } from './cad-workbench/useWorkbenchLayout';
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
  type AgentEvent,
  type ArtifactSummary,
  type ArtifactWorkspace,
  type ChatMessage,
  type HealthResponse,
  type SessionSummary,
} from '../types';

interface CadWorkbenchProps {
  language: Language;
  onDownloadTargetChange?: (target: ArtifactSummary | undefined) => void;
  onStorageOpenChange?: (open: boolean) => void;
  storageOpen: boolean;
}

export interface CadWorkbenchHandle {
  downloadCurrent: () => void;
}

const acceptedImageTypes = new Set<string>(ACCEPTED_IMAGE_TYPES);
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
    const text = translator(language);
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
    const [leftView, setLeftView] = useState<LeftView>('chat');
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
    const [prompt, setPrompt] = useState('');
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
    const {
      beginLogResize,
      beginSideResize,
      leftCollapsed,
      logCollapsed,
      rightCollapsed,
      setLeftCollapsed,
      setLogCollapsed,
      setRightCollapsed,
      workspaceStyle,
    } = useWorkbenchLayout();

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

    return (
      <div className={styles.workspace} style={workspaceStyle}>
        <LeftPanel
          chat={{
            activity,
            conversationRef,
            language,
            messages,
            onKeyDown: handleComposerKeyDown,
            onNewProject: beginFreshRun,
            onPromptChange: setPrompt,
            onRemoveImage: (id) =>
              setPendingImages((current) =>
                current.filter((image) => image.id !== id),
              ),
            onSelectImages: (event) => void selectImages(event),
            onStop: () => abortRef.current?.abort(),
            onSubmit: (event) => void submit(event),
            pendingImages,
            prompt,
            running,
            sessionLoading,
            textareaRef,
          }}
          collapsed={leftCollapsed}
          connectionStatus={connectionStatus}
          files={{
            artifacts,
            language,
            onRefresh: () => void refreshArtifacts(),
            onSelect: selectArtifact,
            selectedPath,
            workspaceName: artifactWorkspaceName,
          }}
          language={language}
          menuOpen={sessionMenuOpen}
          onOpenSession={(session) => void openSession(session)}
          onToggleCollapsed={() =>
            setLeftCollapsed((collapsed) => !collapsed)
          }
          onToggleMenu={() => setSessionMenuOpen((open) => !open)}
          onViewChange={setLeftView}
          running={running}
          sessionId={sessionId}
          sessionLoading={sessionLoading}
          sessionMenuRef={sessionMenuRef}
          sessionTitle={sessionTitle}
          sessions={sessions}
          view={leftView}
          workspaceName={artifactWorkspaceName}
        />
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

        <PreviewPanel
          activity={activity}
          connectionStatus={connectionStatus}
          language={language}
          logCollapsed={logCollapsed}
          onLogResize={beginLogResize}
          onToggleLog={() => setLogCollapsed((collapsed) => !collapsed)}
          previewArtifact={previewArtifact}
          running={running}
          runtimeEntries={runtimeEntries}
          runtimeReady={Boolean(health?.runtimeReady)}
          selectedArtifact={selectedArtifact}
          selectedText={selectedText}
        />
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

        <ParametersPanel
          artifactWorkspace={artifactWorkspace}
          artifacts={artifacts}
          collapsed={rightCollapsed}
          language={language}
          onDownload={downloadArtifact}
          onToggle={() => setRightCollapsed((collapsed) => !collapsed)}
        />
        {storageOpen ? (
          <StorageDrawer
            artifactWorkspace={artifactWorkspace}
            artifacts={artifacts}
            language={language}
            loading={storageLoading}
            onClose={() => onStorageOpenChange?.(false)}
            onRefresh={() => void refreshArtifacts()}
            onSelect={(artifact) => {
              selectArtifact(artifact);
              onStorageOpenChange?.(false);
            }}
            workspaceName={artifactWorkspaceName}
          />
        ) : null}
      </div>
    );
  },
);
