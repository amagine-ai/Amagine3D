import type { ReactNode } from 'react';

import styles from './LicenseSection.module.css';

interface LicenseSectionProps {
  children: ReactNode;
  description: ReactNode;
  number: string;
  title: ReactNode;
}

export function LicenseSection({
  children,
  description,
  number,
  title,
}: LicenseSectionProps) {
  return (
    <section className={styles.section}>
      <div className={styles.heading}>
        <p className={styles.number}>{number}</p>
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      {children}
    </section>
  );
}
