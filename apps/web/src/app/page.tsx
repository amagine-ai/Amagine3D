'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  CadWorkbench,
  type CadDownloadTarget,
  type CadWorkbenchHandle,
} from '../components/cad-workbench';
import { useI18n } from '../lib/i18n';
import styles from './agent-page.module.css';

export default function AgentPage() {
  const [storageOpen, setStorageOpen] = useState(false);
  const [downloadTarget, setDownloadTarget] =
    useState<CadDownloadTarget>(undefined);
  const [languageMenuOpen, setLanguageMenuOpen] = useState(false);
  const languageMenuRef = useRef<HTMLDivElement>(null);
  const workbenchRef = useRef<CadWorkbenchHandle>(null);
  const { language, setLanguage, t } = useI18n();

  const handleDownloadTargetChange = useCallback(
    (target: CadDownloadTarget) => setDownloadTarget(target),
    [],
  );

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
    <main className={styles.page}>
      <header className={styles.appBar}>
        <Link className={styles.wordmark} href="/">
          Amagine3D
        </Link>
        <div className={styles.routeTitle}>
          <span>{t('workbenchTitle')}</span>
        </div>
        <nav aria-label={t('workbenchTitle')}>
          <Link className={styles.licenseLink} href="/licenses">
            <svg aria-hidden="true" fill="none" viewBox="0 0 24 24">
              <path d="M7 3.75h7l3 3V20.25H7z" />
              <path d="M14 3.75v3h3M10 11h4M10 15h4" />
            </svg>
            <span>{t('licenses')}</span>
          </Link>
          <div className={styles.languageMenu} ref={languageMenuRef}>
            <button
              aria-expanded={languageMenuOpen}
              aria-haspopup="menu"
              aria-label={t('chooseLanguage')}
              className={styles.languageButton}
              onClick={() => setLanguageMenuOpen((open) => !open)}
              type="button"
            >
              <span>{language === 'zh' ? '中文' : 'English'}</span>
              <span aria-hidden="true" className={styles.languageChevron}>
                <svg fill="none" focusable="false" viewBox="0 0 16 16">
                  <path d="m5.25 6.25 2.75-2 2.75 2M5.25 9.75l2.75 2 2.75-2" />
                </svg>
              </span>
            </button>
            {languageMenuOpen ? (
              <div
                aria-label={t('language')}
                className={styles.languageDropdown}
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
            aria-label={t('downloadCurrent')}
            className={styles.downloadButton}
            disabled={downloadTarget === undefined}
            onClick={() => workbenchRef.current?.downloadCurrent()}
            title={
              downloadTarget === undefined
                ? t('downloadCurrent')
                : `${t('download')}: ${downloadTarget.fileName}`
            }
            type="button"
          >
            <svg aria-hidden="true" fill="none" viewBox="0 0 24 24">
              <path d="M12 4v11m-4-4 4 4 4-4" />
              <path d="M4 19h16" />
            </svg>
            <span>{t('download')}</span>
          </button>
          <button
            aria-expanded={storageOpen}
            aria-label={
              storageOpen ? t('closeStoragePanel') : t('openStoragePanel')
            }
            className={styles.storageButton}
            onClick={() => setStorageOpen((open) => !open)}
            type="button"
          >
            <svg aria-hidden="true" fill="none" viewBox="0 0 24 24">
              <path d="M4 7.5h6l1.5 2H20v9H4z" />
              <path d="M4 7.5V5.8A1.8 1.8 0 0 1 5.8 4h4l1.5 2H18a2 2 0 0 1 2 2v1.5" />
            </svg>
            <span>{t('storage')}</span>
          </button>
        </nav>
      </header>
      <CadWorkbench
        onDownloadTargetChange={handleDownloadTargetChange}
        onStorageOpenChange={setStorageOpen}
        ref={workbenchRef}
        storageOpen={storageOpen}
      />
    </main>
  );
}
