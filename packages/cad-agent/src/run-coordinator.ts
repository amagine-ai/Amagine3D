import {
  CadDomainError,
  buildAndCheckInputSchema,
  cadExecutionResultSchema,
  containsPrintableClosureIntent,
  designBriefSchema,
  finishCadRunInputSchema,
  SCHEMA_VERSION,
  transitionCadWorkflow,
  visualReviewInputSchema,
  writeCadSourceInputSchema,
  type Artifact,
  type BuildAndCheckInput,
  type CadExecutionResult,
  type CadToolOutput,
  type CadWorkflowPreference,
  type CadWorkflowState,
  type CadWorkflowTransitionEvent,
  type DesignBrief,
  type FinishCadRunInput,
  type QaReport,
  type ResearchPacket,
  type VisualReviewInput,
  type WorkflowEventRecord,
  type WorkflowSelection,
  type WriteCadSourceInput,
} from '@amagine3d/cad-protocol';

import { missingCompletionRequirements } from './workflow-profile';
import { selectCadWorkflow } from './workflow-selector';

export type VisualReviewConsent = 'approved' | 'declined';

export type CadRunCoordinatorOptions = {
  runId: string;
  userRequest: string;
  preference: CadWorkflowPreference;
  research?: ResearchPacket;
  researchEnabled: boolean;
  visualReviewConsent: VisualReviewConsent;
  maxSteps?: number;
  maxFailureOccurrences?: number;
  /** @deprecated Use maxFailureOccurrences. */
  maxConsecutiveFailures?: number;
  maxVisualReviews?: number;
  now?: () => Date;
  createId?: () => string;
};

function failedQaIds(result: CadExecutionResult): string[] {
  const report = result.qaReport;
  return [
    ...report.checks,
    ...(report.regionReports?.flatMap((region) => region.checks) ?? []),
    ...(report.mechanismReports?.flatMap((mechanism) => mechanism.checks) ??
      []),
    ...(report.overlapCheck === undefined ? [] : [report.overlapCheck]),
    ...(report.threeMfReadbackCheck === undefined
      ? []
      : [report.threeMfReadbackCheck]),
  ]
    .filter((check) => check.status === 'failed')
    .map((check) => check.id);
}

function repairPriority(id: string): number {
  const normalized = id.toLowerCase();
  if (
    /build:|syntax|nameerror|typeerror|importerror|missed-cut|boolean|shape-valid|component-count|part-count|watertight|positive-volume/u.test(
      normalized,
    )
  ) {
    return 0;
  }
  if (/overlap|collision|clearance|insert|assembly-path/u.test(normalized)) {
    return 1;
  }
  if (/dimension|size-|volume/u.test(normalized)) return 2;
  if (/round|bevel|fillet|chamfer|finish/u.test(normalized)) return 4;
  return 3;
}

function orderFailureIds(ids: Iterable<string>): string[] {
  return [...new Set(ids)].sort(
    (left, right) =>
      repairPriority(left) - repairPriority(right) || left.localeCompare(right),
  );
}

function failedQaDetails(result: CadExecutionResult): string[] {
  const describe = (
    scope: string,
    check: QaReport['checks'][number],
  ): string => {
    const values = [
      check.expected === undefined
        ? undefined
        : `expected=${JSON.stringify(check.expected)}`,
      check.actual === undefined
        ? undefined
        : `actual=${JSON.stringify(check.actual)}`,
    ].filter((value): value is string => value !== undefined);
    return `${scope}${check.id}: ${check.message}${
      values.length === 0 ? '' : ` (${values.join(', ')})`
    }`;
  };
  return [
    ...result.qaReport.checks
      .filter((check) => check.status === 'failed')
      .map((check) => ({ id: check.id, text: describe('', check) })),
    ...(result.qaReport.regionReports?.flatMap((region) =>
      region.checks
        .filter((check) => check.status === 'failed')
        .map((check) => ({
          id: check.id,
          text: describe(`region ${region.regionId} / `, check),
        })),
    ) ?? []),
    ...(result.qaReport.mechanismReports?.flatMap((mechanism) =>
      mechanism.checks
        .filter((check) => check.status === 'failed')
        .map((check) => ({
          id: check.id,
          text: describe(`mechanism ${mechanism.mechanismId} / `, check),
        })),
    ) ?? []),
    ...(result.qaReport.overlapCheck?.status === 'failed'
      ? [
          {
            id: result.qaReport.overlapCheck.id,
            text: describe('', result.qaReport.overlapCheck),
          },
        ]
      : []),
    ...(result.qaReport.threeMfReadbackCheck?.status === 'failed'
      ? [
          {
            id: result.qaReport.threeMfReadbackCheck.id,
            text: describe('', result.qaReport.threeMfReadbackCheck),
          },
        ]
      : []),
  ]
    .sort((left, right) => repairPriority(left.id) - repairPriority(right.id))
    .map(({ text }) => text);
}

export class CadRunCoordinator {
  readonly selection: WorkflowSelection;
  readonly events: WorkflowEventRecord[] = [];
  readonly #now: () => Date;
  readonly #createId: () => string;
  readonly #maxSteps: number;
  readonly #maxFailureOccurrences: number;
  readonly #maxVisualReviews: number;
  readonly #visualReviewConsent: VisualReviewConsent;
  readonly #closureQaRequired: boolean;
  #state: CadWorkflowState;
  #steps = 0;
  #visualReviews = 0;
  #failureOccurrences = new Map<string, number>();
  #previousFailedCheckIds: Set<string> | undefined;
  #bestFailureBaseline:
    { sourceHash: string; failedCheckIds: Set<string> } | undefined;
  #sourceHash: string | undefined;
  #artifacts: Artifact[] = [];
  #requiredMechanismIds = new Set<string>();

  constructor(options: CadRunCoordinatorOptions) {
    this.#now = options.now ?? (() => new Date());
    this.#createId = options.createId ?? (() => crypto.randomUUID());
    this.#maxSteps = options.maxSteps ?? 20;
    this.#maxFailureOccurrences =
      options.maxFailureOccurrences ?? options.maxConsecutiveFailures ?? 5;
    this.#maxVisualReviews = options.maxVisualReviews ?? 3;
    this.#visualReviewConsent = options.visualReviewConsent;
    this.#closureQaRequired = containsPrintableClosureIntent([
      options.userRequest,
    ]);
    this.selection = selectCadWorkflow({
      preference: options.preference,
      userRequest: options.userRequest,
    });
    this.#state = {
      schemaVersion: SCHEMA_VERSION,
      runId: options.runId,
      status: 'received',
      workflowFrozen: false,
    };

    if (options.researchEnabled) {
      this.transition({ type: 'begin_research' });
      if (
        options.research?.status === 'failed' ||
        options.research === undefined
      ) {
        this.warning(
          'ResearchUnavailable',
          options.research?.warnings[0] ??
            'Web Research failed softly; CAD continues without research hints.',
        );
        this.transition({ type: 'research_failed' });
      } else {
        this.transition({ type: 'research_succeeded' });
      }
    } else {
      this.transition({ type: 'skip_research' });
    }
    this.transition({ type: 'begin_workflow_selection' });
    this.transition({ type: 'select_workflow', selection: this.selection });
    this.transition({ type: 'start_briefing' });
  }

  get state(): CadWorkflowState {
    return structuredClone(this.#state);
  }

  get workflowKind(): WorkflowSelection['kind'] {
    return this.selection.kind;
  }

  get sourceHash(): string | undefined {
    return this.#sourceHash;
  }

  get artifacts(): readonly Artifact[] {
    return this.#artifacts;
  }

  private transition(event: CadWorkflowTransitionEvent): void {
    const from = this.#state.status;
    const next = transitionCadWorkflow(this.#state, event);
    this.events.push({
      schemaVersion: SCHEMA_VERSION,
      id: this.#createId(),
      runId: this.#state.runId,
      sequence: this.events.length,
      occurredAt: this.#now().toISOString(),
      type: 'workflow-transition',
      payload: { eventType: event.type, from, to: next.status },
    });
    this.#state = next;
  }

  private warning(code: string, message: string): void {
    this.events.push({
      schemaVersion: SCHEMA_VERSION,
      id: this.#createId(),
      runId: this.#state.runId,
      sequence: this.events.length,
      occurredAt: this.#now().toISOString(),
      type: 'warning',
      payload: { code, message },
    });
  }

  private useStep(toolName: string): void {
    this.#steps += 1;
    if (this.#steps <= this.#maxSteps) return;
    this.transition({
      type: 'fail',
      reason: `CAD agent exceeded the ${String(this.#maxSteps)} step limit at ${toolName}.`,
      ...(this.#artifacts[0] === undefined
        ? {}
        : { lastArtifactId: this.#artifacts[0].id }),
    });
    throw new CadDomainError(
      'UnexpectedFailure',
      `CAD agent exceeded the ${String(this.#maxSteps)} step limit.`,
      { category: 'workflow', retryable: true, operation: toolName },
    );
  }

  private recordFailure(signature: string): void {
    const occurrences = (this.#failureOccurrences.get(signature) ?? 0) + 1;
    this.#failureOccurrences.set(signature, occurrences);
    if (occurrences < this.#maxFailureOccurrences) return;
    this.transition({
      type: 'fail',
      reason: `The same deterministic failure recurred ${String(occurrences)} times, including nonconsecutive repair attempts: ${signature}.`,
      ...(this.#artifacts[0] === undefined
        ? {}
        : { lastArtifactId: this.#artifacts[0].id }),
    });
  }

  private repairContext(failedCheckIds: readonly string[]) {
    if (this.#sourceHash === undefined) {
      throw new CadDomainError(
        'SourceHashConflict',
        'Cannot create repair guidance without an accepted source revision.',
        { category: 'integrity', retryable: true, operation: 'buildAndCheck' },
      );
    }
    const current = new Set(failedCheckIds);
    const previous = this.#previousFailedCheckIds;
    const newlyFailed =
      previous === undefined
        ? []
        : orderFailureIds([...current].filter((id) => !previous.has(id)));
    const resolved =
      previous === undefined
        ? []
        : orderFailureIds([...previous].filter((id) => !current.has(id)));
    const regression = previous !== undefined && newlyFailed.length > 0;
    const best = this.#bestFailureBaseline;
    if (
      best === undefined ||
      (current.size < best.failedCheckIds.size &&
        [...current].every((id) => best.failedCheckIds.has(id)))
    ) {
      this.#bestFailureBaseline = {
        sourceHash: this.#sourceHash,
        failedCheckIds: new Set(current),
      };
    }
    const baseline = this.#bestFailureBaseline;
    if (baseline === undefined) {
      throw new CadDomainError(
        'UnexpectedFailure',
        'Failed to establish a CAD repair baseline.',
        { category: 'workflow', retryable: true, operation: 'buildAndCheck' },
      );
    }
    const directive =
      previous === undefined
        ? 'Use this accepted source as the repair baseline. Change only the smallest section responsible for the highest-priority failure, and preserve every unaffected body and feature.'
        : regression
          ? `This revision introduced new deterministic failures (${newlyFailed.join(', ')}). Restore unaffected geometry from baseline ${baseline.sourceHash}; do not keep a tradeoff that fixes one check by breaking another.`
          : resolved.length > 0
            ? `This revision resolved ${resolved.join(', ')} without introducing a new failure. Preserve those fixes and change only the smallest section responsible for the next failure.`
            : 'No deterministic check improved. Keep the baseline geometry intact and make one narrower, root-cause repair; do not redesign unrelated bodies or finishing.';
    this.#previousFailedCheckIds = current;
    return {
      baselineSourceHash: baseline.sourceHash,
      newlyFailedCheckIds: newlyFailed,
      resolvedCheckIds: resolved,
      regression,
      directive,
    };
  }

  saveDesignBrief(input: DesignBrief): CadToolOutput {
    this.useStep('saveDesignBrief');
    const brief = designBriefSchema.parse(input);
    if (
      brief.runId !== this.#state.runId ||
      brief.workflowKind !== this.workflowKind
    ) {
      throw new CadDomainError(
        'InvalidExternalData',
        'Design brief does not match the frozen run workflow.',
        {
          category: 'protocol',
          retryable: false,
          operation: 'saveDesignBrief',
        },
      );
    }
    if (this.#closureQaRequired && (brief.mechanisms?.length ?? 0) === 0) {
      throw new CadDomainError(
        'InvalidExternalData',
        'Opening or closing requests require a frozen deterministic mechanism definition.',
        { category: 'protocol', retryable: true, operation: 'saveDesignBrief' },
      );
    }
    this.#requiredMechanismIds = new Set(
      brief.mechanisms?.map((mechanism) => mechanism.id) ?? [],
    );
    this.transition({ type: 'brief_saved' });
    return {
      schemaVersion: SCHEMA_VERSION,
      tool: 'saveDesignBrief',
      accepted: true,
      workflowKind: this.workflowKind,
    };
  }

  writeCadSource(
    input: WriteCadSourceInput,
    sourceHash: string,
  ): CadToolOutput {
    this.useStep('writeCadSource');
    const source = writeCadSourceInputSchema.parse(input);
    if (
      source.runId !== this.#state.runId ||
      source.workflowKind !== this.workflowKind
    ) {
      throw new CadDomainError(
        'InvalidExternalData',
        'CAD source does not match the frozen run workflow.',
        { category: 'protocol', retryable: false, operation: 'writeCadSource' },
      );
    }
    if (!/^[a-f0-9]{64}$/u.test(sourceHash)) {
      throw new CadDomainError('InvalidExternalData', 'Invalid source hash.', {
        category: 'protocol',
        retryable: false,
        operation: 'writeCadSource',
      });
    }
    this.#sourceHash = sourceHash;
    this.transition({ type: 'source_written' });
    return {
      schemaVersion: SCHEMA_VERSION,
      tool: 'writeCadSource',
      accepted: true,
      sourceHash,
    };
  }

  validateBuildInput(input: BuildAndCheckInput): BuildAndCheckInput {
    this.useStep('buildAndCheck');
    const build = buildAndCheckInputSchema.parse(input);
    if (
      build.runId !== this.#state.runId ||
      build.sourceHash !== this.#sourceHash
    ) {
      throw new CadDomainError(
        'SourceHashConflict',
        'Build input does not match the current generated source.',
        { category: 'integrity', retryable: true, operation: 'buildAndCheck' },
      );
    }
    return build;
  }

  restoreRepairBaseline(sourceHash: string): void {
    const baseline = this.#bestFailureBaseline;
    if (
      this.#state.status !== 'coding' ||
      baseline === undefined ||
      baseline.sourceHash !== sourceHash
    ) {
      throw new CadDomainError(
        'SourceHashConflict',
        'The requested automatic rollback does not match the active repair baseline.',
        {
          category: 'integrity',
          retryable: false,
          operation: 'restore-repair-baseline',
        },
      );
    }
    this.#sourceHash = baseline.sourceHash;
    this.#previousFailedCheckIds = new Set(baseline.failedCheckIds);
  }

  recordBuildFailure(signature: string): CadToolOutput {
    this.transition({ type: 'build_failed' });
    const failureId = `build:${signature}`.slice(0, 160);
    this.recordFailure(failureId);
    const repairContext = this.repairContext([failureId]);
    return {
      schemaVersion: SCHEMA_VERSION,
      tool: 'buildAndCheck',
      status: 'failed',
      failedCheckIds: [failureId],
      artifactIds: [],
      summary:
        `Repair baseline: ${repairContext.baselineSourceHash}. ${repairContext.directive} Build failed: ${signature}`.slice(
          0,
          4_000,
        ),
      repairContext,
    };
  }

  recordBuildResult(input: CadExecutionResult): CadToolOutput {
    const result = cadExecutionResultSchema.parse(input);
    if (
      result.runId !== this.#state.runId ||
      result.qaReport.workflowKind !== this.workflowKind
    ) {
      throw new CadDomainError(
        'InvalidExternalData',
        'CAD result does not match the frozen run workflow.',
        { category: 'protocol', retryable: false, operation: 'buildAndCheck' },
      );
    }
    this.transition({ type: 'build_succeeded' });
    const artifacts = result.artifacts.map(({ artifact }) => artifact);
    const concreteFailures = failedQaIds(result);
    const completionFailures = missingCompletionRequirements(
      this.workflowKind,
      result.qaReport,
      artifacts,
    ).filter((id) => id !== 'qa:passed' || concreteFailures.length === 0);
    const reportedMechanismIds = new Set(
      result.qaReport.mechanismReports?.map(
        (mechanism) => mechanism.mechanismId,
      ) ?? [],
    );
    for (const mechanismId of this.#requiredMechanismIds) {
      if (!reportedMechanismIds.has(mechanismId)) {
        completionFailures.push(`qa:mechanism:${mechanismId}`);
      }
    }
    const failed = orderFailureIds([
      ...concreteFailures,
      ...completionFailures,
    ]);
    if (failed.length > 0) {
      this.transition({ type: 'qa_failed' });
      const signature = [...failed].sort().join(',');
      this.recordFailure(signature);
      const repairContext = this.repairContext(failed);
      const details = failedQaDetails(result);
      const report = result.buildReport as
        { issues?: unknown; measurements?: unknown } | undefined;
      const diagnostics: string[] = [];
      if (Array.isArray(report?.issues)) {
        const issueTexts = report.issues.filter(
          (issue): issue is string => typeof issue === 'string',
        );
        const deduped = issueTexts.filter(
          (issue) => !details.some((detail) => detail.includes(issue)),
        );
        if (deduped.length > 0)
          diagnostics.push(`Build issues: ${deduped.join(' | ')}`);
      }
      if (
        report?.measurements !== undefined &&
        report.measurements !== null &&
        typeof report.measurements === 'object' &&
        !Array.isArray(report.measurements)
      ) {
        const compact: string[] = [];
        for (const [name, value] of Object.entries(report.measurements)) {
          if (
            value !== null &&
            typeof value === 'object' &&
            'size_x' in value
          ) {
            const size = value as {
              size_x?: unknown;
              size_y?: unknown;
              size_z?: unknown;
            };
            compact.push(
              `${name}=${String(size.size_x)}x${String(size.size_y)}x${String(
                size.size_z,
              )}mm`,
            );
          } else {
            compact.push(`${name}=${JSON.stringify(value)}`);
          }
        }
        if (compact.length > 0)
          diagnostics.push(`Measurements: ${compact.join(', ')}`);
      }
      const summary = `Repair baseline: ${repairContext.baselineSourceHash}. ${repairContext.directive} Deterministic QA failed: ${signature}.${
        details.length === 0 ? '' : ` Details: ${details.join(' | ')}`
      }${diagnostics.length === 0 ? '' : ` ${diagnostics.join(' ')}`}`;
      return {
        schemaVersion: SCHEMA_VERSION,
        tool: 'buildAndCheck',
        status: 'failed',
        failedCheckIds: failed.slice(0, 100),
        artifactIds: artifacts.map((artifact) => artifact.id),
        summary: summary.slice(0, 4_000),
        repairContext,
      };
    }
    this.#failureOccurrences.clear();
    this.#previousFailedCheckIds = undefined;
    this.#bestFailureBaseline = undefined;
    this.#artifacts = artifacts;
    const primary =
      artifacts.find((artifact) =>
        this.workflowKind === 'multi-color'
          ? artifact.kind === 'model-3mf'
          : artifact.kind === 'step',
      ) ?? artifacts[0];
    if (primary === undefined) {
      throw new CadDomainError(
        'InvalidExternalData',
        'Successful deterministic QA did not return a primary artifact.',
        { category: 'protocol', retryable: false, operation: 'buildAndCheck' },
      );
    }
    this.transition({
      type: 'qa_passed',
      artifactId: primary.id,
      visualReviewRequired: this.#visualReviewConsent === 'approved',
    });
    return {
      schemaVersion: SCHEMA_VERSION,
      tool: 'buildAndCheck',
      status: 'passed',
      failedCheckIds: [],
      artifactIds: artifacts.map((artifact) => artifact.id),
      summary: `Deterministic ${this.workflowKind} QA passed.`,
    };
  }

  requestVisualReview(input: VisualReviewInput): VisualReviewInput {
    this.useStep('requestVisualReview');
    if (this.#visualReviewConsent !== 'approved') {
      throw new CadDomainError(
        'ExecutionRejected',
        'Visual review is unavailable because the user did not approve it.',
        {
          category: 'execution',
          retryable: false,
          operation: 'requestVisualReview',
        },
      );
    }
    const review = visualReviewInputSchema.parse(input);
    if (review.runId !== this.#state.runId) {
      throw new CadDomainError(
        'InvalidExternalData',
        'Visual review belongs to another run.',
        {
          category: 'protocol',
          retryable: false,
          operation: 'requestVisualReview',
        },
      );
    }
    this.#visualReviews += 1;
    if (this.#visualReviews > this.#maxVisualReviews) {
      this.transition({
        type: 'fail',
        reason: `Visual review exceeded the ${String(this.#maxVisualReviews)}-round limit.`,
      });
      throw new CadDomainError(
        'UnexpectedFailure',
        'Visual review iteration limit exceeded.',
        {
          category: 'workflow',
          retryable: true,
          operation: 'requestVisualReview',
        },
      );
    }
    this.transition({ type: 'start_visual_review' });
    return review;
  }

  recordVisualReview(passed: boolean, summary: string): CadToolOutput {
    if (passed) {
      this.transition({ type: 'visual_review_passed' });
    } else {
      this.transition({ type: 'visual_review_rejected' });
      this.recordFailure('visual-review');
    }
    return {
      schemaVersion: SCHEMA_VERSION,
      tool: 'requestVisualReview',
      accepted: true,
      passed,
      summary: summary.trim().slice(0, 2_000) || 'Visual review completed.',
    };
  }

  finish(input: FinishCadRunInput): CadToolOutput {
    this.useStep('finishCadRun');
    const finish = finishCadRunInputSchema.parse(input);
    const actual = new Set(this.#artifacts.map((artifact) => artifact.id));
    if (
      finish.runId !== this.#state.runId ||
      finish.artifactIds.length !== actual.size ||
      finish.artifactIds.some((id) => !actual.has(id))
    ) {
      throw new CadDomainError(
        'InvalidExternalData',
        'finishCadRun must contain the exact verified artifact set.',
        { category: 'protocol', retryable: false, operation: 'finishCadRun' },
      );
    }
    const primary =
      this.#artifacts.find((artifact) =>
        this.workflowKind === 'multi-color'
          ? artifact.kind === 'model-3mf'
          : artifact.kind === 'step',
      ) ?? this.#artifacts[0];
    if (primary === undefined) {
      throw new CadDomainError(
        'InvalidExternalData',
        'No verified artifact is available to finish.',
        { category: 'protocol', retryable: false, operation: 'finishCadRun' },
      );
    }
    this.transition({ type: 'finish', artifactId: primary.id });
    for (const artifact of this.#artifacts) {
      this.events.push({
        schemaVersion: SCHEMA_VERSION,
        id: this.#createId(),
        runId: this.#state.runId,
        sequence: this.events.length,
        occurredAt: this.#now().toISOString(),
        type: 'artifact',
        payload: { artifactId: artifact.id, action: 'verified' },
      });
    }
    return {
      schemaVersion: SCHEMA_VERSION,
      tool: 'finishCadRun',
      completed: true,
      artifactIds: [...finish.artifactIds],
    };
  }

  fail(reason: string): void {
    if (
      this.#state.status === 'completed' ||
      this.#state.status === 'failed' ||
      this.#state.status === 'cancelled'
    ) {
      return;
    }
    this.transition({
      type: 'fail',
      reason: reason.trim().slice(0, 2_000) || 'The CAD Agent run failed.',
      ...(this.#artifacts[0] === undefined
        ? {}
        : { lastArtifactId: this.#artifacts[0].id }),
    });
  }

  cancel(reason = 'The user cancelled the CAD run.'): void {
    this.transition({
      type: 'cancel',
      reason,
      ...(this.#artifacts[0] === undefined
        ? {}
        : { lastArtifactId: this.#artifacts[0].id }),
    });
  }
}
