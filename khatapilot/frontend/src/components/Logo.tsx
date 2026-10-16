/**
 * The KhataPilot mark: an open khata with a compass needle rising from its spine, the ledger kept and
 * steered. Drawn on a 32-unit grid in the current text colour; the favicon in index.html is the same
 * drawing on the dark tile.
 */
export function LogoMark({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden fill="none" stroke="currentColor" strokeWidth="2">
      <path
        d="M5 12.5c3.5-1.6 7.5-1.4 11 1 3.5-2.4 7.5-2.6 11-1V25c-3.5-1.6-7.5-1.4-11 1-3.5-2.4-7.5-2.6-11-1z"
        strokeLinejoin="round"
      />
      <path d="M16 14v11.5" strokeLinecap="round" />
      <path d="M16 2.8l2.5 5.4L16 12.2l-2.5-4z" fill="currentColor" stroke="none" />
    </svg>
  );
}
