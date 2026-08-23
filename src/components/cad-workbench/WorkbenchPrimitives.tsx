import styles from './WorkbenchPrimitives.module.css';

type ToolbarIconName = 'new-run' | 'send' | 'stop';

export function ToolbarIcon({ name }: { name: ToolbarIconName }) {
  return (
    <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 24 24">
      {name === 'new-run' ? (
        <path d="M12 5v14M5 12h14" />
      ) : name === 'send' ? (
        <path d="M12 19V5M6 11l6-6 6 6" />
      ) : (
        <rect height="10" rx="1.5" width="10" x="7" y="7" />
      )}
    </svg>
  );
}

export function LoadingSpinner() {
  return <span aria-hidden="true" className={styles.loadingSpinner} />;
}
