import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import styles from './CadWorkbench.module.css';
import { LeftPanel } from './cad-workbench/LeftPanel';
import { ParametersPanel } from './cad-workbench/ParametersPanel';
import { PreviewPanel } from './cad-workbench/PreviewPanel';
import {
  StorageDrawer,
  type StorageDeleteSelection,
} from './cad-workbench/StorageDrawer';
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
  fetchArtifactArchive,
  fetchArtifacts,
  fetchHealth,
  fetchModelParameters,
  fetchSessionCatalog,
  fetchSessionDetail,
  fetchWorkspaceStorage,
  rebuildModelParameters,
  streamAgent,
  trashArtifacts,
  trashStorageSessions,
} from '../lib/agent-api';
import {
  fileSectionArtifacts,
  preferredPreviewArtifact,
} from '../lib/artifact-selection';
import {
  appendChatStepText,
  completeChatTurn,
  startChatStep,
} from '../lib/chat-turn';
import { useDismissibleLayer } from '../hooks/useDismissibleLayer';
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
  type ChatTurn,
  type HealthResponse,
  type ParameterModel,
  type SessionSummary,
  type StorageSessionGroup,
} from '../types';

interface CadWorkbenchProps {
  language: Language;
  onStorageOpenChange?: (open: boolean) => void;
  storageOpen: boolean;
}

const acceptedImageTypes = new Set<string>(ACCEPTED_IMAGE_TYPES);
export function CadWorkbench({
  language,
  onStorageOpenChange,
  storageOpen,
}: CadWorkbenchProps) {
    const text = translator(language);
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
    const [parameterBuilding, setParameterBuilding] = useState(false);
    const [parameterIssue, setParameterIssue] = useState<string>();
    const [parameterModels, setParameterModels] = useState<ParameterModel[]>([]);
    const [parameterValues, setParameterValues] = useState<
      Record<string, number>
    >({});
    const [prompt, setPrompt] = useState('');
    const [running, setRunning] = useState(false);
    const [runtimeEntries, setRuntimeEntries] = useState<RuntimeEntry[]>([]);
    const [selectedPath, setSelectedPath] = useState<string>();
    const [selectedText, setSelectedText] = useState<string>();
    const [sessionId, setSessionId] = useState(BUNDLED_POMODORO_SESSION_ID);
    const [sessionLoading, setSessionLoading] = useState(true);
    const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
    const [sessions, setSessions] = useState<SessionSummary[]>([]);
    const [storageGroups, setStorageGroups] = useState<StorageSessionGroup[]>([]);
    const [storageLoading, setStorageLoading] = useState(false);
    const [webSearchEnabled, setWebSearchEnabled] = useState(false);
    const abortRef = useRef<AbortController | undefined>(undefined);
    const artifactSnapshotRef = useRef<ArtifactSummary[]>([]);
    const conversationRef = useRef<HTMLElement>(null);
    const parameterBuildingRef = useRef(false);
    const sessionMenuRef = useDismissibleLayer<HTMLDivElement>({
      onDismiss: () => setSessionMenuOpen(false),
      open: sessionMenuOpen,
    });
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
    const activity = useMemo(() => {
      const activeTurn = messages.findLast(
        (message) =>
          message.role === 'assistant' && message.finishedAt === undefined,
      );
      if (!activeTurn || activeTurn.role !== 'assistant') return '';
      return (
        activeTurn.steps.at(-1)?.label ??
        text('Starting Amagine3D Agent', '正在启动 Amagine3D Agent')
      );
    }, [language, messages]);
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
    const activeParameterModel = useMemo(
      () =>
        selectedArtifact?.kind === 'model'
          ? parameterModels.find(
              (model) =>
                model.primaryPreviewPath === selectedArtifact.path ||
                model.displayPreviewPath === selectedArtifact.path,
            )
          : undefined,
      [parameterModels, selectedArtifact],
    );

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

    function updateDraftTurn(
      draftId: string,
      update: (turn: ChatTurn) => ChatTurn,
    ) {
      setMessages((current) =>
        current.map((message) =>
          message.id === draftId && message.role === 'assistant'
            ? {
                ...message,
                ...update(message),
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

    function triggerDownload(url: string, name: string) {
      const anchor = document.createElement('a');
      anchor.download = name;
      anchor.href = url;
      anchor.click();
    }

    async function downloadArtifactsForSession(
      targetSessionId: string,
      archiveName: string,
      selectedArtifacts: ArtifactSummary[],
    ) {
      if (selectedArtifacts.length === 0) return;
      if (selectedArtifacts.length === 1) {
        const artifact = selectedArtifacts[0];
        if (artifact) triggerDownload(artifact.url, artifact.name);
        return;
      }
      const archive = await fetchArtifactArchive(
        targetSessionId,
        selectedArtifacts.map(({ path }) => path),
      );
      const url = URL.createObjectURL(archive);
      triggerDownload(url, `${archiveName}-files.zip`);
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    }

    async function downloadArtifacts(selectedArtifacts: ArtifactSummary[]) {
      await downloadArtifactsForSession(
        sessionId,
        artifactWorkspace.id,
        selectedArtifacts,
      );
    }

    async function deleteArtifacts(selectedArtifacts: ArtifactSummary[]) {
      if (artifactWorkspace.readOnly || selectedArtifacts.length === 0) return;
      await trashArtifacts(
        sessionId,
        selectedArtifacts.map(({ path }) => path),
      );
      try {
        await refreshArtifacts();
      } catch (error) {
        addRuntimeEntry(errorText(error, language), 'files', 'error');
      }
    }

    async function refreshWorkspaceStorage() {
      setStorageLoading(true);
      try {
        const storage = await fetchWorkspaceStorage();
        setStorageGroups(storage.groups);
      } catch (error) {
        addRuntimeEntry(errorText(error, language), 'storage', 'error');
      } finally {
        setStorageLoading(false);
      }
    }

    async function deleteStorageSelection(selection: StorageDeleteSelection) {
      if (running || parameterBuilding) return;
      const selectedSessionIds = new Set(selection.sessionIds);
      const artifactGroups = selection.artifactGroups.filter(
        ({ paths, sessionId: targetSessionId }) =>
          paths.length > 0 && !selectedSessionIds.has(targetSessionId),
      );
      if (selectedSessionIds.size > 0) {
        await trashStorageSessions([...selectedSessionIds]);
      }
      await Promise.all(
        artifactGroups.map(({ paths, sessionId: targetSessionId }) =>
          trashArtifacts(targetSessionId, paths),
        ),
      );

      const [catalog, storage] = await Promise.all([
        fetchSessionCatalog(),
        fetchWorkspaceStorage(),
      ]);
      setSessions(catalog.sessions);
      setStorageGroups(storage.groups);

      const activeSessionDeleted =
        selectedSessionIds.has(sessionId) ||
        !catalog.sessions.some((session) => session.id === sessionId);
      if (activeSessionDeleted) {
        const fallbackSession =
          catalog.sessions.find(({ id }) => id === catalog.initialSessionId) ??
          catalog.sessions[0];
        if (fallbackSession) await openSession(fallbackSession);
        return;
      }
      if (artifactGroups.some((group) => group.sessionId === sessionId)) {
        await refreshArtifacts();
      }
    }

    function selectInitialArtifact(nextArtifacts: ArtifactSummary[]) {
      setSelectedPath(
        preferredPreviewArtifact(nextArtifacts)?.path ??
          fileSectionArtifacts(nextArtifacts)[0]?.path ??
          nextArtifacts[0]?.path,
      );
    }

    async function openSession(
      target: SessionSummary,
      preferredArtifactPath?: string,
    ) {
      if (running || parameterBuilding || sessionLoading || target.id === sessionId) {
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
          setParameterModels([]);
          setParameterIssue(undefined);
          setSelectedPath(undefined);
          setSelectedText(undefined);
          return;
        }
        const [detail, parameterCollection] = await Promise.all([
          fetchSessionDetail(target.id),
          fetchModelParameters(target.id),
        ]);
        setSessionId(detail.session.id);
        setMessages(detail.messages);
        setArtifacts(detail.artifacts);
        setArtifactWorkspace(detail.artifactWorkspace);
        setParameterModels(parameterCollection.models);
        setParameterIssue(undefined);
        setSelectedPath(
          preferredArtifactPath &&
            detail.artifacts.some(({ path }) => path === preferredArtifactPath)
            ? preferredArtifactPath
            : preferredPreviewArtifact(detail.artifacts)?.path ??
                fileSectionArtifacts(detail.artifacts)[0]?.path ??
                detail.artifacts[0]?.path,
        );
      } catch (error) {
        addRuntimeEntry(errorText(error, language), 'session', 'error');
      } finally {
        setSessionLoading(false);
      }
    }

    async function refreshArtifacts() {
      setStorageLoading(true);
      try {
        const [next, parameterCollection] = await Promise.all([
          fetchArtifacts(sessionId),
          fetchModelParameters(sessionId),
        ]);
        setArtifacts(next.artifacts);
        setArtifactWorkspace(next.artifactWorkspace);
        setParameterModels(parameterCollection.models);
        setSelectedPath((current) => {
          if (
            current &&
            next.artifacts.some(({ path }) => path === current)
          ) {
            return current;
          }
          return (
            preferredPreviewArtifact(next.artifacts)?.path ??
            fileSectionArtifacts(next.artifacts)[0]?.path ??
            next.artifacts[0]?.path
          );
        });
      } finally {
        setStorageLoading(false);
      }
    }

    async function selectStorageArtifact(
      target: SessionSummary,
      artifact: ArtifactSummary,
    ) {
      onStorageOpenChange?.(false);
      if (target.id === sessionId) {
        selectArtifact(artifact);
        return;
      }
      await openSession(target, artifact.path);
      if (artifact.kind === 'model' || artifact.kind === 'image') {
        setLeftView('files');
      }
    }

    useEffect(() => {
      if (storageOpen) void refreshWorkspaceStorage();
    }, [storageOpen]);

    useEffect(() => {
      setParameterIssue(undefined);
      setParameterValues(
        activeParameterModel
          ? Object.fromEntries(
              activeParameterModel.parameters.map((parameter) => [
                parameter.id,
                parameter.value,
              ]),
            )
          : {},
      );
    }, [activeParameterModel]);

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
          const [detail, parameterCollection] = await Promise.all([
            fetchSessionDetail(catalog.initialSessionId),
            fetchModelParameters(catalog.initialSessionId),
          ]);
          if (!live) return;
          setSessionId(detail.session.id);
          setMessages(detail.messages);
          setArtifacts(detail.artifacts);
          setArtifactWorkspace(detail.artifactWorkspace);
          setParameterModels(parameterCollection.models);
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
    }, [messages]);

    const connectionStatus = useMemo(() => {
      if (healthError) return text('Service unavailable', '服务未连接');
      if (!health) return text('Checking runtime…', '正在检查运行环境…');
      if (!health.runtimeReady) {
        return text(
          'Amagine3D Agent unavailable',
          'Amagine3D Agent 未就绪',
        );
      }
      if (!health.python.ready) return text('Python unavailable', 'Python 未就绪');
      if (!health.configured) return text('API key required', '等待配置密钥');
      return text('Ready for a new CAD request.', '可以开始新的 CAD 请求。');
    }, [health, healthError, language]);

    function updateDraft(
      event: AgentEvent,
      draftId: string,
      runSessionId: string,
    ) {
      if (event.type === 'step') {
        updateDraftTurn(draftId, (turn) => startChatStep(turn, event.step));
        addRuntimeEntry(event.step.label, event.step.stage);
        return;
      }
      if (event.type === 'step_delta') {
        updateDraftTurn(draftId, (turn) =>
          appendChatStepText(turn, event.stepId, event.content),
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
        void fetchModelParameters(event.sessionId)
          .then((collection) => setParameterModels(collection.models))
          .catch((error: unknown) => {
            setParameterIssue(errorText(error, language));
          });
        addRuntimeEntry(
          text(
            `${String(event.artifacts.length)} workspace files discovered`,
            `已发现 ${String(event.artifacts.length)} 个工作区文件`,
          ),
          'files',
        );
        return;
      }
      if (event.type === 'complete') {
        updateDraftTurn(draftId, (turn) =>
          completeChatTurn(turn, {
            finishedAt: event.finishedAt,
            replyText: event.content,
            sourceStepId: event.sourceStepId,
            status: 'completed',
          }),
        );
        addRuntimeEntry(text('Run completed', '执行完成'), 'done');
        void fetchSessionCatalog()
          .then((catalog) => setSessions(catalog.sessions))
          .catch(() => undefined);
        if (storageOpen) void refreshWorkspaceStorage();
        return;
      }
      updateDraftTurn(draftId, (turn) =>
        completeChatTurn(turn, {
          finishedAt: event.finishedAt,
          replyText: event.message,
          status: 'failed',
        }),
      );
      addRuntimeEntry(event.message, 'error', 'error');
    }

    async function commitParameter(parameterId: string) {
      const model = activeParameterModel;
      const parameter = model?.parameters.find(({ id }) => id === parameterId);
      const value = parameterValues[parameterId];
      if (
        !model ||
        !parameter ||
        value === undefined ||
        value === parameter.value ||
        parameterBuildingRef.current ||
        running ||
        artifactWorkspace.readOnly
      ) {
        return;
      }
      parameterBuildingRef.current = true;
      setParameterBuilding(true);
      setParameterIssue(undefined);
      const parameterLabel =
        language === 'zh' && parameter.labelZh?.trim()
          ? parameter.labelZh.trim()
          : parameter.label;
      addRuntimeEntry(
        text(
          `Rebuilding complete model with ${parameterLabel}=${String(value)}`,
          `正在以 ${parameterLabel}=${String(value)} 重建完整模型`,
        ),
        'parameters',
      );
      try {
        const next = await rebuildModelParameters(sessionId, model, {
          [parameterId]: value,
        });
        setArtifacts(next.artifacts);
        setArtifactWorkspace(next.artifactWorkspace);
        setParameterModels(next.models);
        setSelectedPath(model.displayPreviewPath);
        addRuntimeEntry(
          text('Complete model rebuilt', '完整模型已重建'),
          'parameters',
        );
      } catch (error) {
        setParameterIssue(errorText(error, language));
        setParameterValues(
          Object.fromEntries(
            model.parameters.map((item) => [item.id, item.value]),
          ),
        );
        addRuntimeEntry(errorText(error, language), 'parameters', 'error');
      } finally {
        parameterBuildingRef.current = false;
        setParameterBuilding(false);
      }
    }

    async function submit(event?: FormEvent) {
      event?.preventDefault();
      const messageText = prompt.trim();
      if (
        (!messageText && pendingImages.length === 0) ||
        running ||
        parameterBuilding ||
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
          replyText: '',
          role: 'assistant',
          steps: [],
        },
      ]);
      setPendingImages([]);
      setPrompt('');
      setRunning(true);
      addRuntimeEntry(
        text('Starting Amagine3D Agent', '正在启动 Amagine3D Agent'),
        'start',
      );

      try {
        await streamAgent({
          images,
          message: messageText,
          onEvent: (agentEvent) =>
            updateDraft(agentEvent, draftId, requestSessionId),
          sessionId: requestSessionId,
          signal: controller.signal,
          webSearchEnabled,
        });
      } catch (error) {
        const message = errorText(error, language);
        const status =
          error instanceof DOMException && error.name === 'AbortError'
            ? 'cancelled'
            : 'failed';
        updateDraftTurn(draftId, (turn) =>
          completeChatTurn(turn, {
            finishedAt: Date.now(),
            replyText: message,
            status,
          }),
        );
        addRuntimeEntry(message, 'error', 'error');
      } finally {
        abortRef.current = undefined;
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
      setParameterModels([]);
      setParameterIssue(undefined);
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
      if (running || parameterBuilding || sessionLoading) return;
      beginUserDraft();
    }

    return (
      <div className={styles.workspace} style={workspaceStyle}>
        <LeftPanel
          chat={{
            busy: parameterBuilding,
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
            onWebSearchEnabledChange: setWebSearchEnabled,
            pendingImages,
            prompt,
            running,
            sessionLoading,
            textareaRef,
            webSearchConfigured: Boolean(health?.webSearchConfigured),
            webSearchEnabled,
          }}
          collapsed={leftCollapsed}
          connectionStatus={connectionStatus}
          files={{
            artifacts,
            language,
            loading: storageLoading,
            onDownload: downloadArtifacts,
            onRefresh: () => void refreshArtifacts(),
            onSelect: selectArtifact,
            selectionScope: sessionId,
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
          running={running || parameterBuilding}
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
          running={running || parameterBuilding}
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
          busy={parameterBuilding || running}
          collapsed={rightCollapsed}
          hasParameterModels={parameterModels.length > 0}
          issue={parameterIssue}
          language={language}
          model={activeParameterModel}
          onCommit={(parameterId) => void commitParameter(parameterId)}
          onToggle={() => setRightCollapsed((collapsed) => !collapsed)}
          onValueChange={(parameterId, value) =>
            setParameterValues((current) => ({
              ...current,
              [parameterId]: value,
            }))
          }
          rebuilding={parameterBuilding}
          values={parameterValues}
        />
        {storageOpen ? (
          <StorageDrawer
            groups={storageGroups}
            language={language}
            loading={storageLoading}
            onClose={() => onStorageOpenChange?.(false)}
            onDelete={deleteStorageSelection}
            onDownload={(targetSessionId, selectedArtifacts) =>
              downloadArtifactsForSession(
                targetSessionId,
                targetSessionId,
                selectedArtifacts,
              )
            }
            onRefresh={() => void refreshWorkspaceStorage()}
            onSelect={(targetSession, artifact) =>
              void selectStorageArtifact(targetSession, artifact)
            }
            sessionTitle={sessionTitle}
          />
        ) : null}
      </div>
    );
}
