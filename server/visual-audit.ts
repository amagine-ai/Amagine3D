const CAD_REQUEST =
  /\b(?:cad|stl|step|3mf|3d\s*(?:model|print)|build123d|bambu|ams)\b|建模|三维模型|多色模型|彩色模型|3D\s*打印|打印件|零件|支架|外壳|手办|雕塑|浮雕/i;
const VISUAL_REQUEST =
  /参考图|参考图片|按图|复刻|还原|相似|像不像|外观|造型|轮廓|比例|视觉|四视图|多视图|预览|手办|角色|雕塑|浮雕|像素画|配色|多色|彩色|3mf|AMS/i;
const SKIP_VISUAL =
  /(?:跳过|不要|不用|无需|关闭).{0,8}(?:视觉|读图|预览|渲染|四视图|多视图|验收)|(?:视觉|读图|预览|渲染|四视图|多视图|验收).{0,8}(?:跳过|不要|不用|无需|关闭)/i;
const PREVIEW_NAME =
  /(?:^|[/\\._-])(?:views?|preview|snapshot|reference[_-]?view)(?:[/\\._-]|$)/i;
const IMAGE_PATH = /\.(?:png|jpe?g|webp|gif|bmp)$/i;
const NON_MUTATING_CAD_SCRIPTS = new Set([
  'assembly_check.py',
  'compare_silhouette.py',
  'freshness_check.py',
  'intent_contract.py',
  'palette_plan.py',
  'qa_check.py',
  'reference_analyze.py',
  'render_preview.py',
]);

interface ToolCallBlock {
  arguments?: unknown;
  id?: unknown;
  name?: unknown;
  type?: unknown;
}

export interface VisualAuditResult {
  pass: boolean;
  previewRead: boolean;
  referenceAnalyzed: boolean;
  renderCalled: boolean;
}

export interface VisualAuditOptions {
  requireReferenceAnalysis?: boolean;
}

export interface VisualRepairOptions extends VisualAuditOptions {
  attempt: number;
  maxAttempts: number;
}

export function requiresCadVisualValidation(
  message: string,
  imageCount: number,
): boolean {
  if (SKIP_VISUAL.test(message)) return false;
  const isCadRequest =
    CAD_REQUEST.test(message) ||
    (imageCount > 0 && /三维|模型文件|(?:做|生成|转|变成).{0,8}模型/i.test(message));
  if (!isCadRequest) return false;
  return imageCount > 0 || VISUAL_REQUEST.test(message);
}

function recordArguments(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : {};
}

function isRenderCall(call: ToolCallBlock): boolean {
  if (call.name !== 'bash') return false;
  const command = recordArguments(call.arguments).command;
  return (
    typeof command === 'string' &&
    /(?:render_preview\.py|(?:^|\s)(?:snapshot|render)[-_a-z0-9]*(?:\s|$))/i.test(
      command,
    )
  );
}

function isReferenceAnalysisCall(call: ToolCallBlock): boolean {
  if (call.name !== 'bash') return false;
  const command = recordArguments(call.arguments).command;
  return typeof command === 'string' && /reference_analyze\.py/i.test(command);
}

function isCadMutation(call: ToolCallBlock): boolean {
  const args = recordArguments(call.arguments);
  if (call.name === 'edit' || call.name === 'write') {
    const path = args.path;
    return (
      typeof path === 'string' &&
      /\.(?:py|stl|step|stp|3mf)$/i.test(path)
    );
  }
  if (call.name !== 'bash') return false;
  const command = args.command;
  if (typeof command !== 'string') return false;
  const script = /(?:^|[;&|]\s*)python(?:3(?:\.\d+)?)?\s+["']?([^\s"']+\.py)(?:["']|\s|$)/i.exec(
    command,
  )?.[1];
  if (!script) return false;
  const filename = script.split(/[/\\]/u).at(-1)?.toLowerCase();
  return Boolean(filename && !NON_MUTATING_CAD_SCRIPTS.has(filename));
}

function isPreviewRead(call: ToolCallBlock): boolean {
  if (call.name !== 'read') return false;
  const path = recordArguments(call.arguments).path;
  return (
    typeof path === 'string' &&
    IMAGE_PATH.test(path) &&
    PREVIEW_NAME.test(path)
  );
}

export function auditCadVisualValidation(
  messages: readonly unknown[],
  options: VisualAuditOptions = {},
): VisualAuditResult {
  let renderCalled = false;
  let previewRead = false;
  let referenceAnalyzed = false;
  const pendingRenders = new Set<string>();
  const pendingReferenceAnalyses = new Set<string>();
  const pendingPreviewReads = new Set<string>();
  for (const rawMessage of messages) {
    if (!rawMessage || typeof rawMessage !== 'object') continue;
    const message = rawMessage as {
      content?: unknown;
      isError?: unknown;
      role?: unknown;
      toolCallId?: unknown;
      toolName?: unknown;
    };
    if (message.role === 'assistant' && Array.isArray(message.content)) {
      for (const rawBlock of message.content) {
        if (!rawBlock || typeof rawBlock !== 'object') continue;
        const call = rawBlock as ToolCallBlock;
        if (call.type !== 'toolCall') continue;
        if (isCadMutation(call)) {
          renderCalled = false;
          previewRead = false;
          pendingPreviewReads.clear();
        }
        if (
          isReferenceAnalysisCall(call) &&
          typeof call.id === 'string'
        ) {
          pendingReferenceAnalyses.add(call.id);
        }
        if (isRenderCall(call)) {
          renderCalled = false;
          previewRead = false;
          pendingPreviewReads.clear();
          if (typeof call.id === 'string') pendingRenders.add(call.id);
          continue;
        }
        if (
          renderCalled &&
          isPreviewRead(call) &&
          typeof call.id === 'string'
        ) {
          pendingPreviewReads.add(call.id);
        }
      }
      continue;
    }
    if (
      message.role === 'toolResult' &&
      message.toolName === 'bash' &&
      message.isError !== true &&
      typeof message.toolCallId === 'string' &&
      pendingReferenceAnalyses.has(message.toolCallId)
    ) {
      referenceAnalyzed = true;
      pendingReferenceAnalyses.delete(message.toolCallId);
    }
    if (
      message.role === 'toolResult' &&
      message.toolName === 'bash' &&
      message.isError !== true &&
      typeof message.toolCallId === 'string' &&
      pendingRenders.has(message.toolCallId)
    ) {
      renderCalled = true;
      pendingRenders.delete(message.toolCallId);
      continue;
    }
    if (
      message.role === 'toolResult' &&
      message.toolName === 'read' &&
      message.isError !== true &&
      typeof message.toolCallId === 'string' &&
      pendingPreviewReads.has(message.toolCallId) &&
      Array.isArray(message.content) &&
      message.content.some(
        (block) =>
          block &&
          typeof block === 'object' &&
          (block as { type?: unknown }).type === 'image',
      )
    ) {
      previewRead = true;
    }
  }
  return {
    pass:
      renderCalled &&
      previewRead &&
      (!options.requireReferenceAnalysis || referenceAnalyzed),
    previewRead,
    referenceAnalyzed,
    renderCalled,
  };
}

export function visualValidationInstruction(
  required: boolean,
  requireReferenceAnalysis = false,
): string {
  if (!required) return '';
  return [
    '<visual_validation_required>',
    'This CAD turn has a mandatory visual gate. Follow the selected skill: render the generated artifact with render_preview.py, then use the read tool on the generated preview PNG and compare it against the independent reference/design contract before answering. Mesh QA and freshness alone do not pass this gate. If the comparison fails, revise and regenerate; never claim that the result matches without preview-read evidence.',
    ...(requireReferenceAnalysis
      ? [
          'Uploaded reference images are present. Run the selected skill\'s reference_analyze.py against the saved local image path before modeling; hand-copied pixel coordinates or unsupported visual assertions do not satisfy the reference contract.',
        ]
      : []),
    '</visual_validation_required>',
  ].join('\n');
}

export function visualValidationRepairInstruction(
  audit: VisualAuditResult,
  options: VisualRepairOptions,
): string {
  const missing: string[] = [];
  if (options.requireReferenceAnalysis && !audit.referenceAnalyzed) {
    missing.push(
      'run the selected skill\'s reference_analyze.py against the saved uploaded-image path',
    );
  }
  if (!audit.renderCalled) {
    missing.push(
      'render a fresh preview of the latest generated CAD artifact with render_preview.py',
    );
  }
  if (!audit.previewRead) {
    missing.push(
      'use the read tool on that fresh preview PNG and visually compare it with the independent contract/reference',
    );
  }

  return [
    '<visual_validation_repair>',
    `Automatic visual-validation repair attempt ${options.attempt}/${options.maxAttempts}.`,
    `The previous attempt ended without sufficient evidence. Missing: ${missing.join('; ')}.`,
    'Continue in this same session and complete the missing tool work now. Do not ask the user to retry and do not merely describe commands.',
    'Validation must apply to the latest artifact: if you edit or regenerate CAD after reading a preview, render and read a new preview again.',
    'Follow the active skill through QA, freshness, visual comparison, and any required repair. Only provide a final success response when the evidence passes; otherwise report the concrete remaining failure.',
    '</visual_validation_repair>',
  ].join('\n');
}
