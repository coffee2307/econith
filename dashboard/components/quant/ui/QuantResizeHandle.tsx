"use client";

import { useRef } from "react";

/** Drag handle between main body and bottom event-log dock (row resize). */
export function QuantResizeHandle({
  onResize,
  label = "Resize event log",
}: {
  onResize: (dy: number) => void;
  label?: string;
}) {
  const lastY = useRef(0);

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      className="world-resize-handle-h group relative z-10 h-[6px] shrink-0"
      onPointerDown={(e) => {
        lastY.current = e.clientY;
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
        const dy = e.clientY - lastY.current;
        if (dy !== 0) {
          lastY.current = e.clientY;
          onResize(dy);
        }
      }}
      onPointerUp={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) {
          e.currentTarget.releasePointerCapture(e.pointerId);
        }
      }}
    >
      <div className="absolute inset-x-0 -top-2 -bottom-2" />
      <div className="pointer-events-none absolute inset-x-0 top-1/2 mx-auto h-px w-16 -translate-y-1/2 rounded-full bg-line opacity-60 transition-opacity group-hover:opacity-100 group-active:bg-zone-exec" />
    </div>
  );
}
