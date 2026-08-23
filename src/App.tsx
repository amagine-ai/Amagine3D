import { useCallback, useEffect, useRef, useState } from 'react';

import appStyles from '../apps/web/src/app/agent-page.module.css';
import { CadWorkbench, type CadWorkbenchHandle } from './components/CadWorkbench';
import type { ArtifactSummary } from './types';

type Language = 'en' | 'zh';

const copy = {
  en: {
    chooseLanguage: 'Choose language',
    closeStorage: 'Close storage panel',
    download: 'Download',
    downloadCurrent: 'Download current selection',
    language: 'Language',
    licenses: 'Licenses',
    openStorage: 'Open storage panel',
    storage: 'Storage',
    title: 'AI CAD workspace',
  },
  zh: {
    chooseLanguage: '选择语言',
    closeStorage: '关闭存储面板',
    download: '下载',
    downloadCurrent: '下载当前所选文件',
    language: '语言',
    licenses: '许可证',
    openStorage: '打开存储面板',
    storage: '存储',
    title: '智能硬件外壳3D设计工坊',
  },
} as const;

export function App() {
  const [downloadTarget, setDownloadTarget] = useState<ArtifactSummary>();
  const [language, setLanguage] = useState<Language>('zh');
  const [languageMenuOpen, setLanguageMenuOpen] = useState(false);
  const [storageOpen, setStorageOpen] = useState(false);
  const languageMenuRef = useRef<HTMLDivElement>(null);
  const workbenchRef = useRef<CadWorkbenchHandle>(null);
  const t = copy[language];

  const handleDownloadTargetChange = useCallback(
    (target: ArtifactSummary | undefined) => setDownloadTarget(target),
    [],
  );

  useEffect(() => {
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  }, [language]);

  useEffect(() => {
    if (!languageMenuOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!languageMenuRef.current?.contains(event.target as Node)) {
        setLanguageMenuOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setLanguageMenuOpen(false);
    };
    document.addEventListener('pointerdown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [languageMenuOpen]);

  return (
    <main className={appStyles.page}>
      <header className={appStyles.appBar}>
        <a className={appStyles.wordmark} href="/">
          Amagine3D
        </a>
        <div className={appStyles.routeTitle}>
          <span>{t.title}</span>
        </div>
        <nav aria-label={t.title}>
          <a className={appStyles.licenseLink} href="/licenses">
            <svg aria-hidden="true" fill="none" viewBox="0 0 24 24">
              <path d="M7 3.75h7l3 3V20.25H7z" />
              <path d="M14 3.75v3h3M10 11h4M10 15h4" />
            </svg>
            <span>{t.licenses}</span>
          </a>
          <div className={appStyles.languageMenu} ref={languageMenuRef}>
            <button
              aria-expanded={languageMenuOpen}
              aria-haspopup="menu"
              aria-label={t.chooseLanguage}
              className={appStyles.languageButton}
              onClick={() => setLanguageMenuOpen((open) => !open)}
              type="button"
            >
              <span>{language === 'zh' ? '中文' : 'English'}</span>
              <span aria-hidden="true" className={appStyles.languageChevron}>
                <svg fill="none" focusable="false" viewBox="0 0 16 16">
                  <path d="m5.25 6.25 2.75-2 2.75 2M5.25 9.75l2.75 2 2.75-2" />
                </svg>
              </span>
            </button>
            {languageMenuOpen ? (
              <div
                aria-label={t.language}
                className={appStyles.languageDropdown}
                role="menu"
              >
                <button
                  aria-checked={language === 'zh'}
                  onClick={() => {
                    setLanguage('zh');
                    setLanguageMenuOpen(false);
                  }}
                  role="menuitemradio"
                  type="button"
                >
                  <span>中文</span>
                  <span aria-hidden="true">{language === 'zh' ? '✓' : ''}</span>
                </button>
                <button
                  aria-checked={language === 'en'}
                  onClick={() => {
                    setLanguage('en');
                    setLanguageMenuOpen(false);
                  }}
                  role="menuitemradio"
                  type="button"
                >
                  <span>English</span>
                  <span aria-hidden="true">{language === 'en' ? '✓' : ''}</span>
                </button>
              </div>
            ) : null}
          </div>
          <button
            aria-disabled={downloadTarget === undefined}
            aria-label={t.downloadCurrent}
            className={appStyles.downloadButton}
            disabled={downloadTarget === undefined}
            onClick={() => workbenchRef.current?.downloadCurrent()}
            title={
              downloadTarget === undefined
                ? t.downloadCurrent
                : `${t.download}: ${downloadTarget.name}`
            }
            type="button"
          >
            <svg aria-hidden="true" fill="none" viewBox="0 0 24 24">
              <path d="M12 4v11m-4-4 4 4 4-4" />
              <path d="M4 19h16" />
            </svg>
            <span>{t.download}</span>
          </button>
          <button
            aria-expanded={storageOpen}
            aria-label={storageOpen ? t.closeStorage : t.openStorage}
            className={appStyles.storageButton}
            onClick={() => setStorageOpen((open) => !open)}
            type="button"
          >
            <svg aria-hidden="true" fill="none" viewBox="0 0 24 24">
              <path d="M4 7.5h6l1.5 2H20v9H4z" />
              <path d="M4 7.5V5.8A1.8 1.8 0 0 1 5.8 4h4l1.5 2H18a2 2 0 0 1 2 2v1.5" />
            </svg>
            <span>{t.storage}</span>
          </button>
        </nav>
      </header>
      <CadWorkbench
        language={language}
        onDownloadTargetChange={handleDownloadTargetChange}
        onStorageOpenChange={setStorageOpen}
        ref={workbenchRef}
        storageOpen={storageOpen}
      />
    </main>
  );
}
