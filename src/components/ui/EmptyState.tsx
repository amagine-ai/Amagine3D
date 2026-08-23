import type { ReactNode } from 'react';

import styles from './EmptyState.module.css';

interface EmptyStateProps {
  className?: string;
  description?: ReactNode;
  title: ReactNode;
}

export function EmptyState({
  className,
  description,
  title,
}: EmptyStateProps) {
  return (
    <div className={[styles.root, className].filter(Boolean).join(' ')}>
      <strong>{title}</strong>
      {description === undefined ? null : <span>{description}</span>}
    </div>
  );
}
