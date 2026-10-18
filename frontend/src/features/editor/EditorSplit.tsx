import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

export function readLayoutPreference(key: string, fallback: number): number {
  try {
    const value = localStorage.getItem(key);
    return value !== null && Number.isFinite(Number(value))
      ? Number(value)
      : fallback;
  } catch {
    return fallback;
  }
}
export function saveLayoutPreference(key: string, value: number) {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    /* Layout still works without storage. */
  }
}

export function EditorSplit({
  children,
  assistant,
  collapsed,
  mobileAI,
}: {
  children: ReactNode;
  assistant: ReactNode;
  collapsed: boolean;
  mobileAI: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(1000);
  const [preferred, setPreferred] = useState(() =>
    readLayoutPreference("paperplane.ai.width", 360),
  );
  const [dragging, setDragging] = useState(false);
  const maximum = Math.max(300, Math.min(640, available - 310));
  const width = Math.round(Math.max(300, Math.min(maximum, preferred)));
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) =>
      setAvailable(entry.contentRect.width),
    );
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  function change(value: number) {
    const next = Math.max(300, Math.min(maximum, value));
    setPreferred(next);
    saveLayoutPreference("paperplane.ai.width", next);
  }
  return (
    <div
      ref={root}
      className={
        "editing-layout" +
        (mobileAI ? " mobile-ai" : "") +
        (collapsed ? " ai-collapsed" : "") +
        (dragging ? " resizing" : "")
      }
      style={{ "--assistant-width": `${width}px` } as CSSProperties}
    >
      {children}
      <div
        className="assistant-divider"
        role="separator"
        aria-label="调整 AI 侧栏宽度"
        aria-orientation="vertical"
        aria-valuemin={300}
        aria-valuemax={maximum}
        aria-valuenow={width}
        tabIndex={collapsed ? -1 : 0}
        title="拖动调整宽度；方向键微调，双击恢复默认"
        onDoubleClick={() => change(360)}
        onKeyDown={(event) => {
          const values: Record<string, number> = {
            ArrowLeft: width + 24,
            ArrowRight: width - 24,
            Home: 300,
            End: maximum,
          };
          if (event.key in values) {
            event.preventDefault();
            change(values[event.key]);
          }
        }}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          setDragging(true);
        }}
        onPointerMove={(event) => {
          if (
            event.currentTarget.hasPointerCapture(event.pointerId) &&
            root.current
          )
            change(
              root.current.getBoundingClientRect().right - event.clientX - 5,
            );
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
          setDragging(false);
        }}
        onLostPointerCapture={() => setDragging(false)}
      />
      {assistant}
    </div>
  );
}
