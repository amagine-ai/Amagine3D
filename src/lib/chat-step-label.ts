import type { ChatStep } from '../types';

export function chatStepLabel(
  step: ChatStep,
  language: 'en' | 'zh',
): string {
  return step.localizedLabel?.[language] ?? step.label;
}
