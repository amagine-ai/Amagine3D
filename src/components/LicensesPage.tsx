import { useEffect, useMemo, useState } from 'react';

import styles from '../../apps/web/src/app/licenses/licenses.module.css';
import {
  curatedLicenses,
  licensePageCopy,
  type Language,
} from '../lib/licenses';

interface PackageRecord {
  license: string;
  name: string;
  version: string;
}

interface PackageInventory {
  packages: PackageRecord[];
}

export function LicensesPage() {
  const [language, setLanguage] = useState<Language>('zh');
  const [packages, setPackages] = useState<PackageRecord[]>([]);
  const copy = licensePageCopy[language];

  useEffect(() => {
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
    document.title = `${copy.route} · Amagine3D`;
  }, [copy.route, language]);

  useEffect(() => {
    void fetch('/licenses/npm-production-licenses.json')
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json() as Promise<PackageInventory>;
      })
      .then((inventory) => setPackages(inventory.packages))
      .catch(() => setPackages([]));
  }, []);

  const groups = useMemo(() => {
    const grouped = new Map<string, PackageRecord[]>();
    for (const packageRecord of packages) {
      const current = grouped.get(packageRecord.license) ?? [];
      current.push(packageRecord);
      grouped.set(packageRecord.license, current);
    }
    return [...grouped.entries()].sort(
      (left, right) => right[1].length - left[1].length,
    );
  }, [packages]);

  return (
    <main className={styles.page}>
      <header className={styles.appBar}>
        <a className={styles.wordmark} href="/">
          Amagine3D
        </a>
        <span className={styles.routeTitle}>{copy.route}</span>
        <nav aria-label={copy.route}>
          <div className={styles.languageSwitch}>
            <button
              aria-pressed={language === 'zh'}
              onClick={() => setLanguage('zh')}
              type="button"
            >
              中文
            </button>
            <button
              aria-pressed={language === 'en'}
              onClick={() => setLanguage('en')}
              type="button"
            >
              EN
            </button>
          </div>
          <a className={styles.backLink} href="/">
            <svg aria-hidden="true" fill="none" viewBox="0 0 20 20">
              <path d="m11.75 5-5 5 5 5M7 10h7" />
            </svg>
            <span>{copy.back}</span>
          </a>
        </nav>
      </header>

      <div className={styles.content}>
        <section className={styles.hero}>
          <p className={styles.eyebrow}>{copy.eyebrow}</p>
          <h1>{copy.title}</h1>
          <p className={styles.intro}>{copy.intro}</p>
        </section>

        <section className={styles.projectSection}>
          <div className={styles.sectionHeading}>
            <p className={styles.sectionNumber}>01</p>
            <div>
              <h2>{copy.project}</h2>
              <p>{copy.projectBody}</p>
            </div>
          </div>
          <div className={styles.projectCard}>
            <div>
              <span>{copy.license}</span>
              <strong>Apache-2.0</strong>
            </div>
            <div>
              <span>{copy.owner}</span>
              <a href="https://github.com/amagine-ai">{copy.organization}</a>
            </div>
            <div className={styles.projectLinks}>
              <a href="/licenses/apache-2.0.txt">{copy.fullText}</a>
              <a href="/licenses/amagine3d-notice.txt">{copy.notice}</a>
            </div>
          </div>
        </section>

        <section>
          <div className={styles.sectionHeading}>
            <p className={styles.sectionNumber}>02</p>
            <div>
              <h2>{copy.runtime}</h2>
              <p>{copy.runtimeBody}</p>
            </div>
          </div>
          <div className={styles.componentTable} role="table">
            <div className={styles.tableHeader} role="row">
              <span role="columnheader">{copy.component}</span>
              <span role="columnheader">{copy.use}</span>
              <span role="columnheader">{copy.source}</span>
              <span role="columnheader">{copy.text}</span>
            </div>
            {curatedLicenses.map((component) => (
              <article className={styles.componentRow} key={component.name} role="row">
                <div role="cell">
                  <strong>{component.name}</strong>
                  <span>{component.version}</span>
                </div>
                <p role="cell">{component.use[language]}</p>
                <a href={component.source} rel="noreferrer" role="cell" target="_blank">
                  {copy.source}
                  <span aria-hidden="true">↗</span>
                </a>
                <div className={styles.licenseFiles} role="cell">
                  {component.files.length > 0 ? (
                    component.files.map((file) => (
                      <a href={file.href} key={file.href}>
                        {file.label}
                      </a>
                    ))
                  ) : (
                    <span>{component.license}</span>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>

        <section>
          <div className={styles.sectionHeading}>
            <p className={styles.sectionNumber}>03</p>
            <div>
              <h2>{copy.dependencies}</h2>
              <p>{copy.dependencyBody}</p>
            </div>
          </div>
          <div className={styles.inventoryMeta}>
            <strong>{packages.length}</strong>
            <span>{copy.packages}</span>
            <i aria-hidden="true" />
            <span>{groups.length} SPDX</span>
          </div>
          <div className={styles.licenseGroups}>
            {groups.map(([license, records], index) => (
              <details key={license} open={index < 2}>
                <summary>
                  <span>{license}</span>
                  <span>
                    {records.length} {copy.packages}
                  </span>
                </summary>
                <ul>
                  {records.map((record) => (
                    <li key={`${record.name}@${record.version}`}>
                      <span>{record.name}</span>
                      <code>{record.version}</code>
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </div>
          <div className={styles.downloads}>
            <a href="/licenses/npm-production-licenses.json">
              {copy.downloadInventory}
            </a>
            <p>{copy.generatedNote}</p>
          </div>
        </section>

        <footer>
          <span>© 2026 amagine-ai</span>
          <span>Amagine3D · Apache-2.0</span>
        </footer>
      </div>
    </main>
  );
}
