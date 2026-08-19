import {
  cadWorkflowPreferenceSchema,
  type CadWorkflowPreference,
  type WorkflowSelection,
} from '@amagine3d/cad-protocol';

const MULTI_COLOR_PATTERNS = [
  /\bams\b/iu,
  /\bmulti[ -]?colou?r\b/iu,
  /\btwo[ -]?(?:tone|colou?r)\b/iu,
  /\bcolou?r[ -]?regions?\b/iu,
  /\bcolou?red\s+(?:3mf|logo|text|lettering)\b/iu,
  /\b3mf\b.*\bcolou?r/iu,
  /\bcolou?r\b.*\b3mf\b/iu,
  /双色|多色|颜色区域|彩色(?:文字|字样|logo|标志)|多耗材|彩色\s*3mf/iu,
] as const;

export type WorkflowSelectionInput = {
  preference: CadWorkflowPreference;
  userRequest: string;
};

export function selectCadWorkflow(
  input: WorkflowSelectionInput,
): WorkflowSelection {
  const preference = cadWorkflowPreferenceSchema.parse(input.preference);
  const userRequest = input.userRequest.trim();
  if (userRequest.length === 0) {
    throw new RangeError('A non-empty user request is required.');
  }
  if (preference !== 'auto') {
    return {
      kind: preference,
      mode: 'user-override',
      reason:
        preference === 'single-color'
          ? 'The user explicitly selected the single-color workflow before briefing.'
          : 'The user explicitly selected the multi-color workflow before briefing.',
    };
  }
  const matched = MULTI_COLOR_PATTERNS.find((pattern) =>
    pattern.test(userRequest),
  );
  return matched === undefined
    ? {
        kind: 'single-color',
        mode: 'automatic',
        reason:
          'No explicit multi-color, color-region, AMS, or colored 3MF requirement was found; the safe default is single-color.',
      }
    : {
        kind: 'multi-color',
        mode: 'automatic',
        reason:
          'The request explicitly asks for multi-color, color-region, AMS, or colored 3MF output.',
      };
}
