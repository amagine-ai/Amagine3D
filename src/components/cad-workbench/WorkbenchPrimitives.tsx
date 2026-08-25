import styles from './WorkbenchPrimitives.module.css';

type ToolbarIconName = 'new-run' | 'search' | 'send' | 'stop';

export function ToolbarIcon({ name }: { name: ToolbarIconName }) {
  return (
    <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 24 24">
      {name === 'new-run' ? (
        <path d="M12 5v14M5 12h14" />
      ) : name === 'search' ? (
        <>
          <circle cx="11" cy="11" r="6" />
          <path d="m15.5 15.5 4 4M5 11h12M11 5c2 2.2 2 9.8 0 12M11 5c-2 2.2-2 9.8 0 12" />
        </>
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

export function RefreshIcon() {
  return (
    <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 20 20">
      <path d="M15.4 6.4A6.25 6.25 0 1 0 16 12" />
      <path d="M15.5 3.5v3.25h-3.25" />
    </svg>
  );
}

export function DownloadIcon() {
  return (
    <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 20 20">
      <path d="M10 3.5v8m-3-3 3 3 3-3" />
      <path d="M4.5 15.5h11" />
    </svg>
  );
}

export function TrashIcon() {
  return (
    <svg aria-hidden="true" fill="none" focusable="false" viewBox="0 0 20 20">
      <path d="M4.5 5.5h11M8 3.5h4M6.5 5.5l.6 10h5.8l.6-10" />
      <path d="M8.5 8v5M11.5 8v5" />
    </svg>
  );
}
