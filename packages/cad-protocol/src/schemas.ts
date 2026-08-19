import { z } from 'zod';

import { containsPrintableClosureIntent } from './printable-closure';

export const SCHEMA_VERSION = 1 as const;

export type JsonValue =
  boolean | number | string | null | JsonValue[] | { [key: string]: JsonValue };

export const jsonValueSchema: z.ZodType<JsonValue> = z.lazy(() =>
  z.union([
    z.boolean(),
    z.number().finite(),
    z.string(),
    z.null(),
    z.array(jsonValueSchema),
    z.record(z.string(), jsonValueSchema),
  ]),
);

const idSchema = z.string().trim().min(1).max(160);
const timestampSchema = z.iso.datetime({ offset: true });
const sha256Schema = z.string().regex(/^[a-f0-9]{64}$/u);
const arrayBufferSchema = z.custom<ArrayBuffer>(
  (value) => value instanceof ArrayBuffer,
  'Expected a transferable ArrayBuffer.',
);

export const cadWorkflowKindSchema = z.enum(['single-color', 'multi-color']);
export type CadWorkflowKind = z.infer<typeof cadWorkflowKindSchema>;

export const cadWorkflowPreferenceSchema = z.enum([
  'auto',
  'single-color',
  'multi-color',
]);
export type CadWorkflowPreference = z.infer<typeof cadWorkflowPreferenceSchema>;

export const modelCapabilitiesSchema = z.object({
  textInput: z.boolean(),
  imageInput: z.boolean(),
  toolCalling: z.boolean(),
  reasoning: z.boolean(),
});
export type ModelCapabilities = z.infer<typeof modelCapabilitiesSchema>;

export const modelConnectionSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  id: idSchema,
  displayName: z.string().trim().min(1).max(160),
  provider: z.string().trim().min(1).max(120),
  baseURL: z.url({ protocol: /^https?$/u }),
  secretRef: idSchema,
  enabled: z.boolean(),
});
export type ModelConnection = z.infer<typeof modelConnectionSchema>;

export const modelValidationSchema = z.object({
  status: z.enum(['pending', 'valid', 'failed']),
  validatedAt: timestampSchema.nullable(),
  reason: z.string().trim().min(1).max(2_000).nullable(),
  sdkVersion: z.string().trim().min(1).max(120).nullable(),
});
export type ModelValidation = z.infer<typeof modelValidationSchema>;

export const modelProfileSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  id: idSchema,
  revision: z.number().int().positive(),
  displayName: z.string().trim().min(1).max(160),
  connectionId: idSchema,
  provider: z.string().trim().min(1).max(120),
  modelId: z.string().trim().min(1).max(240),
  defaultParameters: z.record(z.string(), jsonValueSchema),
  capabilities: modelCapabilitiesSchema,
  enabled: z.boolean(),
  validation: modelValidationSchema,
});
export type ModelProfile = z.infer<typeof modelProfileSchema>;

export const modelProfileSnapshotSchema = z.object({
  profileId: idSchema,
  profileRevision: z.number().int().positive(),
  provider: z.string().trim().min(1).max(120),
  modelId: z.string().trim().min(1).max(240),
  capabilities: modelCapabilitiesSchema,
});
export type ModelProfileSnapshot = z.infer<typeof modelProfileSnapshotSchema>;

export const modelProfileSettingsSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    defaultProfileId: idSchema.nullable(),
    profiles: z.array(modelProfileSchema),
    connections: z.array(modelConnectionSchema).optional(),
  })
  .superRefine((settings, context) => {
    if (settings.connections === undefined) return;
    const ids = new Set(
      settings.connections.map((connection) => connection.id),
    );
    settings.profiles.forEach((profile, index) => {
      if (!ids.has(profile.connectionId)) {
        context.addIssue({
          code: 'custom',
          path: ['profiles', index, 'connectionId'],
          message: 'Model profile must reference a saved provider connection.',
        });
      }
    });
  });
export type ModelProfileSettings = z.infer<typeof modelProfileSettingsSchema>;

export const attachmentSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  id: idSchema,
  fileName: z.string().trim().min(1).max(255),
  mediaType: z.enum(['image/png', 'image/jpeg', 'image/webp', 'image/gif']),
  byteLength: z.number().int().positive(),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  sha256: sha256Schema,
  createdAt: timestampSchema,
});
export type Attachment = z.infer<typeof attachmentSchema>;

export const runModeSchema = z.enum([
  'baseline',
  'modification',
  'parameter-rebuild',
]);
export type RunMode = z.infer<typeof runModeSchema>;

export const workflowSelectionSchema = z.object({
  kind: cadWorkflowKindSchema,
  mode: z.enum(['automatic', 'user-override']),
  reason: z.string().trim().min(1).max(1_000),
});
export type WorkflowSelection = z.infer<typeof workflowSelectionSchema>;

export const serializedCadErrorSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  code: z.enum([
    'Cancelled',
    'ArtifactTooLarge',
    'ExecutionRejected',
    'IntegrityMismatch',
    'InvalidExternalData',
    'InvalidWorkerMessage',
    'QaFailed',
    'ResearchUnavailable',
    'SourceHashConflict',
    'StorageUnavailable',
    'UnexpectedFailure',
    'WorkflowFrozen',
    'IllegalWorkflowTransition',
    'WorkerCrashed',
    'WorkerTimeout',
  ]),
  category: z.enum([
    'cancelled',
    'execution',
    'integrity',
    'protocol',
    'qa',
    'research',
    'storage',
    'workflow',
    'unknown',
  ]),
  message: z.string().min(1).max(4_000),
  operation: z.string().min(1).max(160).optional(),
  retryable: z.boolean(),
  details: z.record(z.string(), jsonValueSchema).optional(),
});
export type SerializedCadError = z.infer<typeof serializedCadErrorSchema>;
export type CadErrorCode = SerializedCadError['code'];
export type CadErrorCategory = SerializedCadError['category'];

export const cadProjectSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  id: idSchema,
  name: z.string().trim().min(1).max(200),
  createdAt: timestampSchema,
  updatedAt: timestampSchema,
  revision: z.number().int().nonnegative(),
  currentRunId: idSchema.nullable(),
});
export type CadProject = z.infer<typeof cadProjectSchema>;

export const artifactKindSchema = z.enum([
  'build-report',
  'color-plan',
  'design-brief',
  'model-3mf',
  'model-source',
  'preview-glb',
  'qa-report',
  'region-stl',
  'research-packet',
  'step',
  'stl',
]);

export const artifactSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  id: idSchema,
  runId: idSchema,
  kind: artifactKindSchema,
  fileName: z.string().min(1).max(255),
  mediaType: z.string().min(1).max(160),
  byteLength: z.number().int().nonnegative(),
  sha256: sha256Schema,
  createdAt: timestampSchema,
  regionName: z.string().trim().min(1).max(120).optional(),
});
export type Artifact = z.infer<typeof artifactSchema>;

export const colorRegionSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  id: idSchema,
  name: z.string().trim().min(1).max(120),
  colorName: z.string().trim().min(1).max(120),
  hex: z.string().regex(/^#[0-9a-fA-F]{6}$/u),
  filament: z.string().trim().min(1).max(160).optional(),
  expectedComponentCount: z.number().int().positive(),
  features: z.array(z.string().trim().min(1).max(200)).max(100),
});
export type ColorRegion = z.infer<typeof colorRegionSchema>;

export const colorRegionPlanSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  regions: z.array(colorRegionSchema).min(2),
});
export type ColorRegionPlan = z.infer<typeof colorRegionPlanSchema>;

const vector3Schema = z.tuple([
  z.number().finite(),
  z.number().finite(),
  z.number().finite(),
]);
const mechanismIdSchema = z
  .string()
  .trim()
  .regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$/u);
const mechanismBodyIdSchema = z
  .string()
  .trim()
  .regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/u);

const rotationMotionSchema = z
  .object({
    type: z.literal('rotation'),
    axisOrigin: vector3Schema.describe(
      'World-space point on the rotation axis in millimetres.',
    ),
    axisDirection: vector3Schema.describe(
      'Non-zero world-space rotation-axis direction.',
    ),
    angleDegrees: z.number().finite().min(-720).max(720),
    minimumSamples: z.number().int().min(3).max(72).optional(),
  })
  .strict();

const translationMotionSchema = z
  .object({
    type: z.literal('translation'),
    direction: vector3Schema.describe(
      'Non-zero world-space travel direction; the runtime normalizes it.',
    ),
    distanceMm: z.number().finite().min(-1_000).max(1_000),
    minimumSamples: z.number().int().min(3).max(72).optional(),
  })
  .strict();

const screwMotionSchema = z
  .object({
    type: z.literal('screw'),
    axisOrigin: vector3Schema.describe(
      'World-space point on the screw axis in millimetres.',
    ),
    axisDirection: vector3Schema.describe(
      'Non-zero world-space screw-axis and translation direction.',
    ),
    angleDegrees: z.number().finite().min(-2_160).max(2_160),
    distanceMm: z.number().finite().min(-1_000).max(1_000),
    minimumSamples: z.number().int().min(3).max(144).optional(),
  })
  .strict();

export const mechanismMotionSchema = z
  .discriminatedUnion('type', [
    rotationMotionSchema,
    translationMotionSchema,
    screwMotionSchema,
  ])
  .superRefine((motion, context) => {
    const direction =
      motion.type === 'translation' ? motion.direction : motion.axisDirection;
    const magnitudeSquared = direction.reduce(
      (sum, component) => sum + component * component,
      0,
    );
    if (magnitudeSquared <= 1e-12) {
      context.addIssue({
        code: 'custom',
        path: [motion.type === 'translation' ? 'direction' : 'axisDirection'],
        message: 'Mechanism motion direction must be non-zero.',
      });
    }
    const hasTravel =
      motion.type === 'rotation'
        ? Math.abs(motion.angleDegrees) >= 1
        : motion.type === 'translation'
          ? Math.abs(motion.distanceMm) >= 0.1
          : Math.abs(motion.angleDegrees) >= 1 ||
            Math.abs(motion.distanceMm) >= 0.1;
    if (!hasTravel) {
      context.addIssue({
        code: 'custom',
        message: 'Mechanism motion must contain measurable travel.',
      });
    }
  });
export type MechanismMotion = z.infer<typeof mechanismMotionSchema>;

export const mechanismClearanceCheckSchema = z
  .object({
    id: mechanismIdSchema,
    leftBodyId: mechanismBodyIdSchema,
    rightBodyId: mechanismBodyIdSchema,
    minimumMm: z.number().min(0.2).max(10),
    maximumMm: z.number().positive().max(10).optional(),
    poseScope: z.enum(['all', 'intermediate']),
  })
  .strict()
  .superRefine((clearance, context) => {
    if (clearance.leftBodyId === clearance.rightBodyId) {
      context.addIssue({
        code: 'custom',
        path: ['rightBodyId'],
        message: 'A clearance check must reference two different bodies.',
      });
    }
    if (
      clearance.maximumMm !== undefined &&
      clearance.maximumMm < clearance.minimumMm
    ) {
      context.addIssue({
        code: 'custom',
        path: ['maximumMm'],
        message: 'Maximum mechanism gap cannot be smaller than its minimum.',
      });
    }
  });
export type MechanismClearanceCheck = z.infer<
  typeof mechanismClearanceCheckSchema
>;

export const mechanismDefinitionSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    id: mechanismIdSchema,
    kind: z.enum(['revolute', 'linear', 'compound', 'screw']),
    movingBodyIds: z.array(mechanismBodyIdSchema).min(1).max(40),
    stationaryBodyIds: z.array(mechanismBodyIdSchema).min(1).max(40),
    motions: z.array(mechanismMotionSchema).min(1).max(8),
    clearanceChecks: z.array(mechanismClearanceCheckSchema).min(1).max(40),
    collisionToleranceMm3: z.number().nonnegative().max(0.01).optional(),
  })
  .strict()
  .superRefine((mechanism, context) => {
    const moving = new Set(mechanism.movingBodyIds);
    const stationary = new Set(mechanism.stationaryBodyIds);
    mechanism.movingBodyIds.forEach((bodyId, index) => {
      if (mechanism.movingBodyIds.indexOf(bodyId) !== index) {
        context.addIssue({
          code: 'custom',
          path: ['movingBodyIds', index],
          message: 'Moving body IDs must be unique.',
        });
      }
      if (stationary.has(bodyId)) {
        context.addIssue({
          code: 'custom',
          path: ['movingBodyIds', index],
          message: 'Moving and stationary body groups must be disjoint.',
        });
      }
    });
    mechanism.stationaryBodyIds.forEach((bodyId, index) => {
      if (mechanism.stationaryBodyIds.indexOf(bodyId) !== index) {
        context.addIssue({
          code: 'custom',
          path: ['stationaryBodyIds', index],
          message: 'Stationary body IDs must be unique.',
        });
      }
    });
    const knownBodies = new Set([...moving, ...stationary]);
    const clearanceIds = new Set<string>();
    mechanism.clearanceChecks.forEach((clearance, index) => {
      if (clearanceIds.has(clearance.id)) {
        context.addIssue({
          code: 'custom',
          path: ['clearanceChecks', index, 'id'],
          message: 'Mechanism clearance-check IDs must be unique.',
        });
      }
      clearanceIds.add(clearance.id);
      for (const key of ['leftBodyId', 'rightBodyId'] as const) {
        if (!knownBodies.has(clearance[key])) {
          context.addIssue({
            code: 'custom',
            path: ['clearanceChecks', index, key],
            message: 'Clearance checks must reference a mechanism body.',
          });
        }
      }
      const crossesMotionBoundary =
        (moving.has(clearance.leftBodyId) &&
          stationary.has(clearance.rightBodyId)) ||
        (moving.has(clearance.rightBodyId) &&
          stationary.has(clearance.leftBodyId));
      if (!crossesMotionBoundary) {
        context.addIssue({
          code: 'custom',
          path: ['clearanceChecks', index],
          message:
            'A mechanism clearance check must compare one moving body with one stationary body.',
        });
      }
    });
    const motionTypes = new Set(mechanism.motions.map((motion) => motion.type));
    if (mechanism.kind === 'revolute' && !motionTypes.has('rotation')) {
      context.addIssue({
        code: 'custom',
        path: ['motions'],
        message: 'A revolute mechanism requires a rotation motion.',
      });
    }
    if (
      mechanism.kind === 'revolute' &&
      !mechanism.clearanceChecks.some(
        (clearance) => clearance.maximumMm !== undefined,
      )
    ) {
      context.addIssue({
        code: 'custom',
        path: ['clearanceChecks'],
        message:
          'A revolute mechanism requires an upper-bounded clearance check that keeps the joint anchored to its declared axis.',
      });
    }
    if (mechanism.kind === 'linear' && !motionTypes.has('translation')) {
      context.addIssue({
        code: 'custom',
        path: ['motions'],
        message: 'A linear mechanism requires a translation motion.',
      });
    }
    if (mechanism.kind === 'screw' && !motionTypes.has('screw')) {
      context.addIssue({
        code: 'custom',
        path: ['motions'],
        message: 'A screw mechanism requires a coupled screw motion.',
      });
    }
    if (mechanism.kind === 'compound' && mechanism.motions.length < 2) {
      context.addIssue({
        code: 'custom',
        path: ['motions'],
        message: 'A compound mechanism requires at least two ordered motions.',
      });
    }
  });
export type MechanismDefinition = z.infer<typeof mechanismDefinitionSchema>;

export const featureVerificationTargetSchema = z
  .object({
    id: mechanismIdSchema,
    featureId: mechanismBodyIdSchema,
    metric: z.enum([
      'sizeX',
      'sizeY',
      'sizeZ',
      'centerX',
      'centerY',
      'centerZ',
      'minX',
      'minY',
      'minZ',
      'maxX',
      'maxY',
      'maxZ',
      'volumeMm3',
    ]),
    expected: z.number().finite(),
    tolerance: z.number().finite().nonnegative().max(1_000),
  })
  .strict();
export type FeatureVerificationTarget = z.infer<
  typeof featureVerificationTargetSchema
>;

const constraintValueSchema = z.union([
  z.boolean(),
  z.number().finite(),
  z.string().trim().min(1).max(2_000),
]);

export const designConstraintSchema = z.object({
  name: z.string().trim().min(1).max(160),
  value: constraintValueSchema,
  unit: z
    .string()
    .trim()
    .min(1)
    .max(40)
    .describe(
      'A short measurement unit only, such as mm, degrees, count, or posts. Put explanations in rationale.',
    )
    .optional(),
  source: z.enum(['user', 'agent', 'research']),
  rationale: z.string().trim().min(1).max(2_000).optional(),
});

export const designBriefSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    runId: idSchema,
    workflowKind: cadWorkflowKindSchema,
    userConstraints: z.array(designConstraintSchema),
    agentAssumptions: z.array(designConstraintSchema),
    researchHints: z.array(designConstraintSchema),
    features: z.array(z.string().trim().min(1).max(300)),
    verificationTargets: z.array(designConstraintSchema),
    derivationNotes: z.array(z.string().trim().min(1).max(2_000)),
    colorRegionPlan: colorRegionPlanSchema.optional(),
    mechanisms: z.array(mechanismDefinitionSchema).max(12).optional(),
    featureChecks: z.array(featureVerificationTargetSchema).max(100).optional(),
  })
  .superRefine((brief, context) => {
    if (brief.workflowKind === 'multi-color' && !brief.colorRegionPlan) {
      context.addIssue({
        code: 'custom',
        message: 'Multi-color briefs require a color-region plan.',
        path: ['colorRegionPlan'],
      });
    }
    if (brief.workflowKind === 'single-color' && brief.colorRegionPlan) {
      context.addIssue({
        code: 'custom',
        message: 'Single-color briefs cannot contain a color-region plan.',
        path: ['colorRegionPlan'],
      });
    }
    const mechanismIds = new Set<string>();
    const featureCheckIds = new Set<string>();
    brief.featureChecks?.forEach((target, index) => {
      if (featureCheckIds.has(target.id)) {
        context.addIssue({
          code: 'custom',
          path: ['featureChecks', index, 'id'],
          message: 'Feature-check IDs must be unique.',
        });
      }
      featureCheckIds.add(target.id);
    });
    const firstMechanismBodyIds =
      brief.mechanisms?.[0] === undefined
        ? undefined
        : new Set([
            ...brief.mechanisms[0].movingBodyIds,
            ...brief.mechanisms[0].stationaryBodyIds,
          ]);
    brief.mechanisms?.forEach((mechanism, index) => {
      if (mechanismIds.has(mechanism.id)) {
        context.addIssue({
          code: 'custom',
          path: ['mechanisms', index, 'id'],
          message: 'Mechanism IDs must be unique.',
        });
      }
      mechanismIds.add(mechanism.id);
      if (firstMechanismBodyIds !== undefined) {
        const bodyIds = new Set([
          ...mechanism.movingBodyIds,
          ...mechanism.stationaryBodyIds,
        ]);
        if (
          bodyIds.size !== firstMechanismBodyIds.size ||
          [...bodyIds].some((bodyId) => !firstMechanismBodyIds.has(bodyId))
        ) {
          context.addIssue({
            code: 'custom',
            path: ['mechanisms', index],
            message:
              'Every mechanism definition must partition the same complete published-body set.',
          });
        }
      }
    });
    const constraintSignals = [
      ...brief.userConstraints,
      ...brief.agentAssumptions,
      ...brief.verificationTargets,
    ].flatMap((constraint) => [constraint.name, String(constraint.value)]);
    if (
      containsPrintableClosureIntent([
        ...brief.features,
        ...constraintSignals,
      ]) &&
      (brief.mechanisms?.length ?? 0) === 0
    ) {
      context.addIssue({
        code: 'custom',
        path: ['mechanisms'],
        message:
          'Opening or closing features require at least one deterministic mechanism definition.',
      });
    }
  });
export type DesignBrief = z.infer<typeof designBriefSchema>;

export const cadAgentToolNameSchema = z.enum([
  'saveDesignBrief',
  'writeCadSource',
  'buildAndCheck',
  'requestVisualReview',
  'finishCadRun',
]);
export type CadAgentToolName = z.infer<typeof cadAgentToolNameSchema>;

export const writeCadSourceInputSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    runId: idSchema,
    workflowKind: cadWorkflowKindSchema,
    source: z.string().min(1).max(2_000_000),
  })
  .strict();
export type WriteCadSourceInput = z.infer<typeof writeCadSourceInputSchema>;

export const buildAndCheckInputSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    runId: idSchema,
    sourceHash: sha256Schema,
    qaTargets: z
      .object({
        sizeX: z
          .number()
          .positive()
          .optional()
          .describe(
            'Full assembled X envelope in millimetres, including every published body and external protrusion.',
          ),
        sizeY: z
          .number()
          .positive()
          .optional()
          .describe(
            'Full assembled Y envelope in millimetres, including every published body and external protrusion.',
          ),
        sizeZ: z
          .number()
          .positive()
          .optional()
          .describe(
            'Full assembled Z envelope in millimetres, including every published body and external protrusion.',
          ),
        dimensionTolerance: z.number().nonnegative().optional(),
        volume: z.number().positive().optional(),
        volumeTolerancePercent: z.number().nonnegative().optional(),
        componentCount: z
          .number()
          .int()
          .positive()
          .optional()
          .describe(
            'Intended number of separately published physical print bodies. Never derive this target from the generated shape solid count; each named body must contain one connected solid.',
          ),
      })
      .optional(),
  })
  .strict();
export type BuildAndCheckInput = z.infer<typeof buildAndCheckInputSchema>;

export const visualReviewInputSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    runId: idSchema,
    artifactId: idSchema,
    reviewFocus: z.string().trim().min(1).max(1_000),
  })
  .strict();
export type VisualReviewInput = z.infer<typeof visualReviewInputSchema>;

export const finishCadRunInputSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    runId: idSchema,
    artifactIds: z.array(idSchema).min(1).max(100),
  })
  .strict();
export type FinishCadRunInput = z.infer<typeof finishCadRunInputSchema>;

export const repairContextSchema = z
  .object({
    baselineSourceHash: sha256Schema,
    baselineRevisionId: idSchema.optional(),
    newlyFailedCheckIds: z.array(idSchema).max(100),
    resolvedCheckIds: z.array(idSchema).max(100),
    regression: z.boolean(),
    rollbackApplied: z.boolean().optional(),
    affectedConstraintIds: z.array(idSchema).max(100).optional(),
    featureOwnership: z
      .array(
        z
          .object({
            bodyId: idSchema,
            constraintIds: z.array(idSchema).max(100),
          })
          .strict(),
      )
      .max(100)
      .optional(),
    sourceDelta: z
      .object({
        addedLineCount: z.number().int().nonnegative(),
        removedLineCount: z.number().int().nonnegative(),
        changedLineRanges: z
          .array(
            z
              .object({
                startLine: z.number().int().positive(),
                endLine: z.number().int().positive(),
              })
              .strict(),
          )
          .max(100),
      })
      .strict()
      .optional(),
    geometryDelta: z
      .object({
        affectedBodyIds: z.array(idSchema).max(100),
        affectedArtifactIds: z.array(idSchema).max(100),
        artifactChanges: z
          .array(
            z
              .object({
                kind: artifactKindSchema,
                fileName: z.string().trim().min(1).max(255),
                baselineSha256: sha256Schema.optional(),
                candidateSha256: sha256Schema.optional(),
              })
              .strict(),
          )
          .max(200),
      })
      .strict()
      .optional(),
    allowedMutationScope: z
      .object({
        bodyIds: z.array(idSchema).max(100),
        parameterNames: z.array(z.string().trim().min(1).max(160)).max(100),
        sourceLineRanges: z
          .array(
            z
              .object({
                startLine: z.number().int().positive(),
                endLine: z.number().int().positive(),
              })
              .strict(),
          )
          .max(100),
      })
      .strict()
      .optional(),
    directive: z.string().trim().min(1).max(2_000),
  })
  .strict();
export type RepairContext = z.infer<typeof repairContextSchema>;

export const cadToolOutputSchema = z.discriminatedUnion('tool', [
  z.object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    tool: z.literal('saveDesignBrief'),
    accepted: z.literal(true),
    workflowKind: cadWorkflowKindSchema,
  }),
  z.object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    tool: z.literal('writeCadSource'),
    accepted: z.literal(true),
    sourceHash: sha256Schema,
  }),
  z.object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    tool: z.literal('buildAndCheck'),
    status: z.enum(['passed', 'failed']),
    failedCheckIds: z.array(idSchema).max(100),
    artifactIds: z.array(idSchema).max(100),
    summary: z.string().trim().min(1).max(4_000),
    repairContext: repairContextSchema.optional(),
  }),
  z.object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    tool: z.literal('requestVisualReview'),
    accepted: z.boolean(),
    passed: z.boolean(),
    summary: z.string().trim().min(1).max(2_000),
  }),
  z.object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    tool: z.literal('finishCadRun'),
    completed: z.literal(true),
    artifactIds: z.array(idSchema).min(1).max(100),
  }),
]);
export type CadToolOutput = z.infer<typeof cadToolOutputSchema>;

export const researchRequestSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    runId: idSchema,
    query: z.string().trim().min(1).max(8_000),
    enabled: z.boolean(),
  })
  .strict();
export type ResearchRequest = z.infer<typeof researchRequestSchema>;

export const researchSourceSchema = z
  .object({
    id: idSchema,
    title: z.string().trim().min(1).max(500),
    url: z.url({ protocol: /^https?$/u }),
    accessedAt: timestampSchema,
    sourceType: z.enum([
      'manufacturer',
      'datasheet',
      'distributor',
      'community',
      'other',
    ]),
    summary: z.string().trim().min(1).max(1_000).optional(),
  })
  .strict();
export type ResearchSource = z.infer<typeof researchSourceSchema>;

export const researchFindingSchema = z
  .object({
    topic: z.string().trim().min(1).max(300),
    summary: z.string().trim().min(1).max(2_000),
    value: z.union([z.number().finite(), z.string().max(500)]).optional(),
    unit: z.string().trim().min(1).max(40).optional(),
    originalExpression: z.string().trim().min(1).max(500).optional(),
    confidence: z.enum(['low', 'medium', 'high']),
    sourceIds: z.array(idSchema).min(1),
    caveat: z.string().trim().min(1).max(1_000).optional(),
  })
  .strict();
export type ResearchFinding = z.infer<typeof researchFindingSchema>;

export const researchPacketDraftSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    status: z.enum(['complete', 'partial', 'failed']),
    advisoryOnly: z.literal(true),
    queries: z.array(z.string().trim().min(1).max(1_000)).max(8),
    findings: z.array(researchFindingSchema).max(40),
    sources: z.array(researchSourceSchema).max(20),
    warnings: z.array(z.string().trim().min(1).max(1_000)).max(20),
  })
  .strict();
export type ResearchPacketDraft = z.infer<typeof researchPacketDraftSchema>;

export const researchPacketSchema = researchPacketDraftSchema.superRefine(
  (packet, context) => {
    const sourceIds = new Set<string>();
    const urls = new Set<string>();
    packet.sources.forEach((source, index) => {
      if (sourceIds.has(source.id)) {
        context.addIssue({
          code: 'custom',
          path: ['sources', index, 'id'],
          message: 'Research source IDs must be unique.',
        });
      }
      if (urls.has(source.url)) {
        context.addIssue({
          code: 'custom',
          path: ['sources', index, 'url'],
          message: 'Research source URLs must be unique.',
        });
      }
      sourceIds.add(source.id);
      urls.add(source.url);
    });
    packet.findings.forEach((finding, findingIndex) => {
      const referenced = new Set<string>();
      finding.sourceIds.forEach((sourceId, sourceIndex) => {
        if (!sourceIds.has(sourceId)) {
          context.addIssue({
            code: 'custom',
            path: ['findings', findingIndex, 'sourceIds', sourceIndex],
            message: 'Research findings must reference a saved source.',
          });
        }
        if (referenced.has(sourceId)) {
          context.addIssue({
            code: 'custom',
            path: ['findings', findingIndex, 'sourceIds', sourceIndex],
            message: 'Finding source references must be unique.',
          });
        }
        referenced.add(sourceId);
      });
    });
    if (
      packet.status === 'complete' &&
      (packet.sources.length === 0 || packet.findings.length === 0)
    ) {
      context.addIssue({
        code: 'custom',
        path: ['status'],
        message: 'Complete research requires at least one source and finding.',
      });
    }
    if (packet.status === 'failed' && packet.findings.length > 0) {
      context.addIssue({
        code: 'custom',
        path: ['status'],
        message: 'Research with usable findings must be marked partial.',
      });
    }
  },
);
export type ResearchPacket = z.infer<typeof researchPacketSchema>;

const researchStreamEventBaseSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  runId: idSchema,
});

export const researchStreamEventSchema = z.discriminatedUnion('type', [
  researchStreamEventBaseSchema.extend({
    type: z.literal('research-status'),
    status: z.enum(['researching', 'complete', 'partial', 'failed', 'skipped']),
    message: z.string().trim().min(1).max(500),
  }),
  researchStreamEventBaseSchema.extend({
    type: z.literal('research-reference'),
    source: researchSourceSchema,
  }),
  researchStreamEventBaseSchema.extend({
    type: z.literal('research-result'),
    packet: researchPacketSchema.optional(),
  }),
  researchStreamEventBaseSchema.extend({
    type: z.literal('workflow-ready'),
    next: z.literal('briefing'),
  }),
]);
export type ResearchStreamEvent = z.infer<typeof researchStreamEventSchema>;

export const qaCheckSchema = z.object({
  id: idSchema,
  status: z.enum(['passed', 'failed', 'warning', 'not-applicable']),
  message: z.string().trim().min(1).max(2_000),
  expected: jsonValueSchema.optional(),
  actual: jsonValueSchema.optional(),
  tolerance: z.number().nonnegative().optional(),
});
export type QaCheck = z.infer<typeof qaCheckSchema>;

export const regionQaSchema = z.object({
  regionId: idSchema,
  componentCount: z.number().int().nonnegative(),
  watertight: z.boolean(),
  checks: z.array(qaCheckSchema),
});

export const mechanismQaReportSchema = z
  .object({
    mechanismId: mechanismIdSchema,
    sampledPoseCount: z.number().int().nonnegative(),
    maxCollisionVolumeMm3: z.number().nonnegative().optional(),
    minimumClearanceMm: z.number().nonnegative().optional(),
    maximumClearanceMm: z.number().nonnegative().optional(),
    checks: z.array(qaCheckSchema).min(3),
  })
  .superRefine((report, context) => {
    const ids = new Set(report.checks.map((check) => check.id));
    const prefix = `mechanism-${report.mechanismId}`;
    for (const suffix of ['body-set', 'step-readback', 'motion-collision']) {
      if (!ids.has(`${prefix}-${suffix}`)) {
        context.addIssue({
          code: 'custom',
          path: ['checks'],
          message: `Mechanism QA report is missing ${suffix}.`,
        });
      }
    }
  });
export type MechanismQaReport = z.infer<typeof mechanismQaReportSchema>;

export const qaReportSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    runId: idSchema,
    workflowKind: cadWorkflowKindSchema,
    status: z.enum(['passed', 'failed']),
    checks: z.array(qaCheckSchema),
    regionReports: z.array(regionQaSchema).optional(),
    mechanismReports: z.array(mechanismQaReportSchema).optional(),
    overlapCheck: qaCheckSchema.optional(),
    threeMfReadbackCheck: qaCheckSchema.optional(),
  })
  .superRefine((report, context) => {
    const multiColorFields = [
      report.regionReports,
      report.overlapCheck,
      report.threeMfReadbackCheck,
    ];
    if (
      report.workflowKind === 'multi-color' &&
      multiColorFields.some((field) => field === undefined)
    ) {
      context.addIssue({
        code: 'custom',
        message:
          'Multi-color QA requires region, overlap, and 3MF readback checks.',
      });
    }
    if (
      report.workflowKind === 'single-color' &&
      multiColorFields.some((field) => field !== undefined)
    ) {
      context.addIssue({
        code: 'custom',
        message: 'Single-color QA cannot contain multi-color checks.',
      });
    }
    const allChecks = [
      ...report.checks,
      ...(report.regionReports?.flatMap((region) => region.checks) ?? []),
      ...(report.mechanismReports?.flatMap((mechanism) => mechanism.checks) ??
        []),
      ...(report.overlapCheck === undefined ? [] : [report.overlapCheck]),
      ...(report.threeMfReadbackCheck === undefined
        ? []
        : [report.threeMfReadbackCheck]),
    ];
    const hasFailure = allChecks.some((check) => check.status === 'failed');
    if (report.status === 'passed' && hasFailure) {
      context.addIssue({
        code: 'custom',
        path: ['status'],
        message: 'A passed QA report cannot contain failed checks.',
      });
    }
    if (report.status === 'failed' && !hasFailure) {
      context.addIssue({
        code: 'custom',
        path: ['status'],
        message: 'A failed QA report must identify at least one failed check.',
      });
    }
  });
export type QaReport = z.infer<typeof qaReportSchema>;

export const cadBuildQaTargetsSchema = z.object({
  sizeX: z.number().positive().optional(),
  sizeY: z.number().positive().optional(),
  sizeZ: z.number().positive().optional(),
  dimensionTolerance: z.number().nonnegative().optional(),
  volume: z.number().positive().optional(),
  volumeTolerancePercent: z.number().nonnegative().optional(),
  componentCount: z.number().int().positive().optional(),
});
export type CadBuildQaTargets = z.infer<typeof cadBuildQaTargetsSchema>;

export const parameterValueSchema = z.union([
  z.boolean(),
  z.number().finite(),
  z.string(),
]);
export type ParameterValue = z.infer<typeof parameterValueSchema>;

export const parameterDefinitionSchema = z.object({
  name: z.string().regex(/^[A-Z][A-Z0-9_]*$/u),
  label: z.string().trim().min(1).max(160),
  group: z.string().trim().min(1).max(120).optional(),
  description: z.string().trim().min(1).max(500).optional(),
  type: z.enum(['boolean', 'number', 'string']),
  defaultValue: parameterValueSchema,
  value: parameterValueSchema,
  unit: z.string().trim().min(1).max(40).optional(),
  minimum: z.number().finite().optional(),
  maximum: z.number().finite().optional(),
  step: z.number().positive().optional(),
});
export type ParameterDefinition = z.infer<typeof parameterDefinitionSchema>;

export const parameterChangeSchema = z.object({
  parameterName: z.string().min(1),
  previousValue: parameterValueSchema,
  nextValue: parameterValueSchema,
  changedAt: timestampSchema,
});

export const parameterCouplingSchema = z.object({
  id: z.string().min(1).max(200),
  members: z.array(z.string().regex(/^[A-Z][A-Z0-9_]*$/u)).min(2),
  source: z.string().trim().min(1).max(300),
});
export type ParameterCoupling = z.infer<typeof parameterCouplingSchema>;

export const parameterSetSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    sourceHash: sha256Schema,
    parameters: z.array(parameterDefinitionSchema),
    history: z.array(parameterChangeSchema),
    historyCursor: z.number().int().nonnegative(),
    couplings: z.array(parameterCouplingSchema).optional(),
  })
  .superRefine((parameterSet, context) => {
    if (parameterSet.historyCursor > parameterSet.history.length) {
      context.addIssue({
        code: 'custom',
        message: 'Parameter history cursor cannot exceed history length.',
        path: ['historyCursor'],
      });
    }
    const known = new Set(parameterSet.parameters.map((p) => p.name));
    for (const coupling of parameterSet.couplings ?? []) {
      for (const member of coupling.members) {
        if (!known.has(member)) {
          context.addIssue({
            code: 'custom',
            message: `Coupling ${coupling.id} references unknown parameter ${member}.`,
            path: ['couplings'],
          });
        }
      }
    }
  });
export type ParameterSet = z.infer<typeof parameterSetSchema>;

export const cadRunSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  id: idSchema,
  projectId: idSchema,
  createdAt: timestampSchema,
  completedAt: timestampSchema.nullable(),
  status: z.enum(['active', 'succeeded', 'failed', 'cancelled']),
  workflowKind: cadWorkflowKindSchema.nullable(),
  workflowSelectionReason: z.string().max(1_000).nullable(),
  sourceHash: sha256Schema.nullable(),
  workflowSnapshot: z
    .object({
      engine: z.literal('Amagine3D-CAD'),
      revision: z.string().regex(/^[A-Za-z0-9._-]+$/u),
      profile: z.enum([
        'hardware-enclosure-single',
        'hardware-enclosure-multi',
      ]),
    })
    .nullable()
    .optional(),
  runtimeVersions: z.record(z.string(), z.string().min(1)).optional(),
  artifactIds: z.array(idSchema),
  parentRunId: idSchema.nullable().optional(),
  baseRevisionId: idSchema.nullable().optional(),
  modelProfileId: idSchema.nullable().optional(),
  mode: runModeSchema.optional(),
  modelSnapshot: modelProfileSnapshotSchema.nullable().optional(),
  lastError: serializedCadErrorSchema.optional(),
  failureReason: z.string().trim().max(4_000).optional(),
});
export type CadRun = z.infer<typeof cadRunSchema>;

export const projectRevisionSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  id: idSchema,
  projectId: idSchema,
  revision: z.number().int().nonnegative(),
  createdAt: timestampSchema,
  sourceHash: sha256Schema,
  modelSource: z.string().min(1).max(2_000_000),
  parameters: parameterSetSchema.nullable(),
  reason: z
    .enum([
      'generated',
      'parameter-writeback',
      'manual-restore',
      'automatic-rollback',
    ])
    .optional(),
  parentRevisionId: idSchema.nullable().optional(),
  restoredFromRevisionId: idSchema.nullable().optional(),
  repairContext: repairContextSchema.optional(),
});
export type ProjectRevision = z.infer<typeof projectRevisionSchema>;

export const messageHistorySchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  messages: z.array(jsonValueSchema),
});
export type MessageHistory = z.infer<typeof messageHistorySchema>;

export const versionedJsonDocumentSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  data: jsonValueSchema,
});
export type VersionedJsonDocument = z.infer<typeof versionedJsonDocumentSchema>;

const workerMessageBaseSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  requestId: idSchema,
});

export const cadWorkerRequestSchema = z
  .discriminatedUnion('type', [
    workerMessageBaseSchema.extend({ type: z.literal('bootstrap') }),
    workerMessageBaseSchema.extend({
      type: z.literal('build'),
      runId: idSchema,
      projectId: idSchema.optional(),
      workflowKind: cadWorkflowKindSchema,
      source: z.string().min(1).max(2_000_000),
      sourceHash: sha256Schema,
      parameterOverrides: z.record(z.string(), parameterValueSchema),
      colorRegionPlan: colorRegionPlanSchema.optional(),
      qaTargets: cadBuildQaTargetsSchema.optional(),
      mechanisms: z.array(mechanismDefinitionSchema).max(12).optional(),
      featureChecks: z
        .array(featureVerificationTargetSchema)
        .max(100)
        .optional(),
    }),
    workerMessageBaseSchema.extend({
      type: z.literal('cancel'),
      targetRequestId: idSchema,
    }),
  ])
  .superRefine((request, context) => {
    if (request.type !== 'build') return;
    if (
      request.workflowKind === 'multi-color' &&
      request.colorRegionPlan === undefined
    ) {
      context.addIssue({
        code: 'custom',
        path: ['colorRegionPlan'],
        message: 'Multi-color builds require a frozen color-region plan.',
      });
    }
    if (
      request.workflowKind === 'single-color' &&
      request.colorRegionPlan !== undefined
    ) {
      context.addIssue({
        code: 'custom',
        path: ['colorRegionPlan'],
        message: 'Single-color builds cannot contain a color-region plan.',
      });
    }
  });
export type CadWorkerRequest = z.infer<typeof cadWorkerRequestSchema>;

export const cadWorkerArtifactPayloadSchema = z.object({
  artifact: artifactSchema,
  bytes: arrayBufferSchema,
});
export type CadWorkerArtifactPayload = z.infer<
  typeof cadWorkerArtifactPayloadSchema
>;

export const cadExecutionResultSchema = z
  .object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    runId: idSchema,
    qaReport: qaReportSchema,
    buildReport: jsonValueSchema,
    artifacts: z.array(cadWorkerArtifactPayloadSchema),
    runtimeVersions: z.record(z.string(), z.string().min(1)),
    durationMs: z.number().nonnegative().optional(),
    wasmHeapBytes: z.number().int().nonnegative().optional(),
  })
  .superRefine((result, context) => {
    if (result.qaReport.runId !== result.runId) {
      context.addIssue({
        code: 'custom',
        path: ['qaReport', 'runId'],
        message: 'QA report belongs to another run.',
      });
    }
    const artifactIds = new Set<string>();
    result.artifacts.forEach((payload, index) => {
      if (
        payload.artifact.runId !== result.runId ||
        payload.artifact.byteLength !== payload.bytes.byteLength ||
        artifactIds.has(payload.artifact.id)
      ) {
        context.addIssue({
          code: 'custom',
          path: ['artifacts', index],
          message:
            'Artifact run, byte length, and unique ID must match its execution result.',
        });
      }
      artifactIds.add(payload.artifact.id);
    });
  });
export type CadExecutionResult = z.infer<typeof cadExecutionResultSchema>;

export const cadWorkerResponseSchema = z.discriminatedUnion('type', [
  workerMessageBaseSchema.extend({
    type: z.literal('ready'),
    runtimeVersions: z.record(z.string(), z.string().min(1)),
  }),
  workerMessageBaseSchema.extend({
    type: z.literal('progress'),
    stage: z.enum(['bootstrap', 'build', 'export', 'qa']),
    progress: z.number().min(0).max(1),
    message: z.string().min(1).max(500),
  }),
  workerMessageBaseSchema.extend({
    type: z.literal('log'),
    level: z.enum(['debug', 'info', 'warning', 'error']),
    line: z.string().max(4_000),
    truncated: z.boolean(),
  }),
  workerMessageBaseSchema.extend({
    type: z.literal('result'),
    runId: idSchema,
    qaReport: qaReportSchema,
    artifacts: z.array(artifactSchema),
    artifactPayloads: z.array(cadWorkerArtifactPayloadSchema).optional(),
    buildReport: versionedJsonDocumentSchema.optional(),
    runtimeVersions: z.record(z.string(), z.string().min(1)).optional(),
    metrics: z
      .object({
        durationMs: z.number().nonnegative(),
        wasmHeapBytes: z.number().int().nonnegative().optional(),
      })
      .optional(),
  }),
  workerMessageBaseSchema.extend({
    type: z.literal('error'),
    error: serializedCadErrorSchema,
  }),
  workerMessageBaseSchema.extend({ type: z.literal('cancelled') }),
]);
export type CadWorkerResponse = z.infer<typeof cadWorkerResponseSchema>;

export const workflowEventRecordSchema = z.discriminatedUnion('type', [
  z.object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    id: idSchema,
    runId: idSchema,
    sequence: z.number().int().nonnegative(),
    occurredAt: timestampSchema,
    type: z.literal('workflow-transition'),
    payload: z.object({
      eventType: z.string().min(1).max(160),
      from: z.string().min(1).max(160),
      to: z.string().min(1).max(160),
    }),
  }),
  z.object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    id: idSchema,
    runId: idSchema,
    sequence: z.number().int().nonnegative(),
    occurredAt: timestampSchema,
    type: z.literal('warning'),
    payload: z.object({ code: z.string().min(1), message: z.string().min(1) }),
  }),
  z.object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    id: idSchema,
    runId: idSchema,
    sequence: z.number().int().nonnegative(),
    occurredAt: timestampSchema,
    type: z.literal('error'),
    payload: serializedCadErrorSchema,
  }),
  z.object({
    schemaVersion: z.literal(SCHEMA_VERSION),
    id: idSchema,
    runId: idSchema,
    sequence: z.number().int().nonnegative(),
    occurredAt: timestampSchema,
    type: z.literal('artifact'),
    payload: z.object({
      artifactId: idSchema,
      action: z.enum(['created', 'verified']),
    }),
  }),
]);
export type WorkflowEventRecord = z.infer<typeof workflowEventRecordSchema>;

export const workflowEventLogSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  runId: idSchema,
  events: z.array(workflowEventRecordSchema),
});
export type WorkflowEventLog = z.infer<typeof workflowEventLogSchema>;

export const artifactIndexSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  runId: idSchema,
  artifacts: z.array(artifactSchema),
});
export type ArtifactIndex = z.infer<typeof artifactIndexSchema>;

export const projectArchiveManifestSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  format: z.literal('amagine3d-project'),
  projectId: idSchema,
  projectName: z.string().trim().min(1).max(200),
  exportedAt: timestampSchema,
  entries: z.array(
    z.object({
      path: z.string().min(1).max(1_024),
      byteLength: z.number().int().nonnegative(),
      sha256: sha256Schema,
    }),
  ),
});
export type ProjectArchiveManifest = z.infer<
  typeof projectArchiveManifestSchema
>;
