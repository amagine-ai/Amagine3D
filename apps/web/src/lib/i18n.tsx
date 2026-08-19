'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export type Language = 'en' | 'zh';

type MessageKey =
  | 'language'
  | 'chooseLanguage'
  | 'workbenchTitle'
  | 'viewer'
  | 'licenses'
  | 'storage'
  | 'openStoragePanel'
  | 'closeStoragePanel'
  | 'download'
  | 'downloadCurrent'
  | 'compatibilityNotice'
  | 'notRecorded'
  | 'seconds'
  | 'minutes'
  | 'elapsed';

const messages: Record<Language, Record<MessageKey, string>> = {
  en: {
    language: 'Language',
    chooseLanguage: 'Choose language',
    workbenchTitle: 'Smart hardware enclosure 3D design studio',
    viewer: 'Viewer',
    licenses: 'Licenses',
    storage: 'Storage',
    openStoragePanel: 'Open storage panel',
    closeStoragePanel: 'Close storage panel',
    download: 'Download',
    downloadCurrent: 'Download current selection',
    compatibilityNotice:
      'This version is developed and tested for desktop Google Chrome. Other Chromium browsers may work but are not guaranteed.',
    notRecorded: 'Not recorded',
    seconds: 's',
    minutes: 'min',
    elapsed: 'Elapsed',
  },
  zh: {
    language: '语言',
    chooseLanguage: '选择语言',
    workbenchTitle: '智能硬件外壳3D设计工坊',
    viewer: '查看器',
    licenses: '许可证',
    storage: '存储',
    openStoragePanel: '打开存储面板',
    closeStoragePanel: '关闭存储面板',
    download: '下载',
    downloadCurrent: '下载当前所选文件',
    compatibilityNotice:
      '当前版本针对桌面版 Google Chrome 开发和测试。其他 Chromium 浏览器可能可用，但暂不保证；Firefox、Safari 和移动浏览器暂不属于正式支持范围。',
    notRecorded: '未记录',
    seconds: '秒',
    minutes: '分',
    elapsed: '耗时',
  },
};

type I18nContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  t: (key: MessageKey) => string;
};

const I18nContext = createContext<I18nContextValue | undefined>(undefined);
const STORAGE_KEY = 'amagine3d-language';

function isLanguage(value: string | null): value is Language {
  return value === 'en' || value === 'zh';
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>('zh');

  useEffect(() => {
    const saved = globalThis.localStorage?.getItem(STORAGE_KEY);
    const next = isLanguage(saved) ? saved : 'zh';
    setLanguageState(next);
    document.documentElement.lang = next === 'zh' ? 'zh-CN' : 'en';
    if (!isLanguage(saved)) globalThis.localStorage?.setItem(STORAGE_KEY, next);
  }, []);

  const setLanguage = useCallback((next: Language) => {
    setLanguageState(next);
    document.documentElement.lang = next === 'zh' ? 'zh-CN' : 'en';
    globalThis.localStorage?.setItem(STORAGE_KEY, next);
  }, []);
  const value = useMemo<I18nContextValue>(
    () => ({
      language,
      setLanguage,
      t: (key) => messages[language][key],
    }),
    [language, setLanguage],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (context === undefined) {
    throw new Error('useI18n must be used within I18nProvider');
  }
  return context;
}
