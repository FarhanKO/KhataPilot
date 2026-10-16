import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/* A horizontal row that slides when it doesn't fit: swipe it by touch or by dragging
   with a mouse, roll the wheel, or use the chevrons on whichever edge hides buttons. */
export function TabScroller({
  label,
  activeKey,
  className = "",
  innerClassName = "",
  children,
}: {
  label: string;
  activeKey: string;
  className?: string;
  innerClassName?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    setEdges({ left: el.scrollLeft > 1, right: el.scrollLeft < max - 1 });
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    for (const child of Array.from(el.children)) ro.observe(child);
    // Vertical wheel → sideways scroll, but only while the row actually overflows.
    const onWheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
      e.preventDefault();
      el.scrollLeft += e.deltaY;
    };
    // Mouse/pen drag → swipe. Touch already swipes natively. A drag past a few pixels
    // swallows the click that follows, so swiping never switches tabs by accident.
    let drag: { id: number; x: number; left: number; moved: boolean } | null = null;
    let swallowClick = false;
    const onDown = (e: PointerEvent) => {
      if (e.pointerType === "touch" || e.button !== 0 || el.scrollWidth <= el.clientWidth) return;
      drag = { id: e.pointerId, x: e.clientX, left: el.scrollLeft, moved: false };
    };
    const onMove = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) < 5) return;
      if (!drag.moved) {
        drag.moved = true;
        el.setPointerCapture(e.pointerId);
        el.style.cursor = "grabbing";
      }
      el.scrollLeft = drag.left - dx;
    };
    const onUp = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      swallowClick = drag.moved;
      drag = null;
      el.style.cursor = "";
      if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
    };
    const onClick = (e: MouseEvent) => {
      if (!swallowClick) return;
      swallowClick = false;
      e.preventDefault();
      e.stopPropagation();
    };
    const noNativeDrag = (e: DragEvent) => e.preventDefault();
    el.addEventListener("scroll", measure, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
    el.addEventListener("click", onClick, true);
    el.addEventListener("dragstart", noNativeDrag);
    return () => {
      ro.disconnect();
      el.removeEventListener("scroll", measure);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onUp);
      el.removeEventListener("click", onClick, true);
      el.removeEventListener("dragstart", noNativeDrag);
    };
  }, [measure]);

  // Keep the selected tab in view when it changes.
  useEffect(() => {
    ref.current
      ?.querySelector<HTMLElement>('[aria-current="page"]')
      ?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }, [activeKey]);

  const nudge = (dir: -1 | 1) => {
    const el = ref.current;
    if (el) el.scrollBy({ left: dir * el.clientWidth * 0.7, behavior: "smooth" });
  };

  const arrow =
    "absolute inset-y-0 z-10 flex w-11 items-center text-bar-ink/80 transition hover:text-bar-ink from-bar from-55% to-transparent";

  return (
    <div className={`relative min-w-0 ${className}`}>
      <nav ref={ref} aria-label={label} className={`no-scrollbar flex touch-pan-x overflow-x-auto overscroll-x-contain select-none ${innerClassName}`}>
        {children}
      </nav>
      {edges.left && (
        <button
          type="button"
          aria-label="Scroll sections left"
          onClick={() => nudge(-1)}
          className={`${arrow} left-0 justify-start bg-gradient-to-r pl-1`}
        >
          <ChevronLeft className="size-4" />
        </button>
      )}
      {edges.right && (
        <button
          type="button"
          aria-label="Scroll sections right"
          onClick={() => nudge(1)}
          className={`${arrow} right-0 justify-end bg-gradient-to-l pr-1`}
        >
          <ChevronRight className="size-4" />
        </button>
      )}
    </div>
  );
}
