import type { ImageAttachment } from '../../types';

export type Language = 'en' | 'zh';
export type LeftView = 'chat' | 'files';

export interface PendingImage extends ImageAttachment {
  id: string;
  size: number;
  url: string;
}

export type Translate = (english: string, chinese: string) => string;

export function translator(language: Language): Translate {
  return (english, chinese) => (language === 'zh' ? chinese : english);
}
