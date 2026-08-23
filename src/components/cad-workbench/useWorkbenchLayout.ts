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
const LOG_PANEL_MIN = 128;
const LOG_PANEL_MAX = 420;

export function useWorkbenchLayout() {
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [leftWidth, setLeftWidth] = useState(340);
  const [logCollapsed, setLogCollapsed] = useState(true);
  const [logHeight, setLogHeight] = useState(208);
  const [rightCollapsed, setRightCollapsed] = useState(true);
  const [rightWidth, setRightWidth] = useState(320);

  const workspaceStyle = {
    '--workspace-left': leftCollapsed ? '3.25rem' : `${String(leftWidth)}px`,
    '--workspace-log-height': logCollapsed ? '52px' : `${String(logHeight)}px`,
    '--workspace-log-resizer': logCollapsed ? '0px' : '0.5rem',
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

  function beginLogResize(event: ReactPointerEvent<HTMLDivElement>) {
    const startY = event.clientY;
    const startHeight = logHeight;
    const move = (moveEvent: PointerEvent) => {
      setLogHeight(
        clamp(startHeight + startY - moveEvent.clientY, LOG_PANEL_MIN, LOG_PANEL_MAX),
      );
    };
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop, { once: true });
  }

  return {
    beginLogResize,
    beginSideResize,
    leftCollapsed,
    logCollapsed,
    rightCollapsed,
    setLeftCollapsed,
    setLogCollapsed,
    setRightCollapsed,
    workspaceStyle,
  };
}
