import { useEffect, useRef, type RefObject } from 'react';

interface DismissibleLayerOptions {
  onDismiss: () => void;
  open: boolean;
}

export function useDismissibleLayer<T extends HTMLElement>({
  onDismiss,
  open,
}: DismissibleLayerOptions): RefObject<T | null> {
  const layerRef = useRef<T>(null);
  const dismissRef = useRef(onDismiss);
  dismissRef.current = onDismiss;

  useEffect(() => {
    if (!open) return;

    const dismissOnOutsidePointer = (event: PointerEvent) => {
      if (!layerRef.current?.contains(event.target as Node)) {
        dismissRef.current();
      }
    };
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') dismissRef.current();
    };

    document.addEventListener('pointerdown', dismissOnOutsidePointer);
    document.addEventListener('keydown', dismissOnEscape);
    return () => {
      document.removeEventListener('pointerdown', dismissOnOutsidePointer);
      document.removeEventListener('keydown', dismissOnEscape);
    };
  }, [open]);

  return layerRef;
}
