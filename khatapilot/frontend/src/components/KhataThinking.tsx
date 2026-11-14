/* The chat's "thinking" mark: a small khata that opens, a pen writes three entries, and it closes.
   All motion is CSS (see .khata in index.css); with reduced motion it rests open, entries written. */
export function KhataThinking({ className = "" }: { className?: string }) {
  return (
    <svg className={`khata ${className}`} viewBox="0 0 34 24" width="34" height="24" aria-hidden>
      {/* Right page, always there; the cover closes over it. */}
      <rect x="17" y="3" width="14" height="18" rx="1.2" className="khata-page" />
      <line x1="17" y1="4" x2="17" y2="20" className="khata-rule" />
      <path d="M20 9h9" className="khata-entry khata-entry-1" pathLength={1} />
      <path d="M20 13h9" className="khata-entry khata-entry-2" pathLength={1} />
      <path d="M20 17h6" className="khata-entry khata-entry-3" pathLength={1} />

      {/* Left page: swings out from the spine once the cover has lifted. */}
      <g className="khata-left">
        <rect x="3" y="3" width="14" height="18" rx="1.2" className="khata-page" />
        <path d="M6 9h8M6 13h8M6 17h5" className="khata-rule" />
      </g>

      {/* Cover: lies over the right page when closed, folds toward the spine to open. */}
      <g className="khata-cover">
        <rect x="17" y="2.5" width="14.5" height="19" rx="1.4" className="khata-cloth" />
        <rect x="21" y="9.5" width="6.5" height="4" rx="0.6" className="khata-label" />
      </g>

      {/* Pen, its nib at (0,0); moved along the entries. */}
      <g className="khata-pen">
        <path d="M0 0 L1 -2.4 L6.2 -7.6 L7.6 -6.2 L2.4 -1 Z" />
      </g>
    </svg>
  );
}
