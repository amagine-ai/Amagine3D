import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useState,
} from 'react';

import { clamp } from './utils';

const LEFT_PANEL_MIN = 280;
const LEFT_PANEL_MAX = 520;
const RIGHT_PANEL_MIN = 288;
const RIGHT_PANEL_MAX = 480;

export function useWorkbenchLayout() {
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [leftWidth, setLeftWidth] = useState(340);
  const [rightCollapsed, setRightCollapsed] = useState(true);
  const [rightWidth, setRightWidth] = useState(320);

  const workspaceStyle = {
    '--workspace-left': leftCollapsed ? '3.25rem' : `${String(leftWidth)}px`,
    '--workspace-right': rightCollapsed ? '3.25rem' : `${String(rightWidth)}px`,
  } as CSSProperties;

  function beginSideResize(
    side: 'left' | 'right',
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    const startX = event.clientX;
    const startWidth = side === 'left' ? leftWidth : rightWidth;
    const move = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      if (side === 'left') {
        setLeftWidth(clamp(startWidth + delta, LEFT_PANEL_MIN, LEFT_PANEL_MAX));
      } else {
        setRightWidth(
          clamp(startWidth - delta, RIGHT_PANEL_MIN, RIGHT_PANEL_MAX),
        );
      }
    };
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop, { once: true });
  }

  return {
    beginSideResize,
    leftCollapsed,
    rightCollapsed,
    setLeftCollapsed,
    setRightCollapsed,
    workspaceStyle,
  };
}
