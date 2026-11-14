import React from 'react';

/** Compact app mark: a cut frame with a playhead (original artwork, CSS/SVG only). */
export const BrandMark: React.FC<{ size?: number }> = ({ size = 36 }) => (
  <svg width={size} height={size} viewBox="0 0 36 36" aria-hidden role="img">
    <defs>
      <linearGradient id="ae-g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#A7C2FF" />
        <stop offset="1" stopColor="#5E88D7" />
      </linearGradient>
    </defs>
    <rect x="1" y="1" width="34" height="34" rx="9" fill="url(#ae-g)" />
    {/* split frame */}
    <rect x="8" y="10" width="9" height="16" rx="2" fill="#0B0F15" opacity=".85" />
    <rect x="20" y="10" width="9" height="16" rx="2" fill="#0B0F15" opacity=".55" />
    {/* playhead */}
    <rect x="17.25" y="6" width="1.5" height="24" rx=".75" fill="#F3F6FC" />
    <path d="M14.5 6h7l-3.5 3.5z" fill="#F3F6FC" />
  </svg>
);

export const Wordmark: React.FC<{ className?: string }> = ({ className }) => (
  <span className={`inline-flex items-center gap-2.5 ${className ?? ''}`}>
    <BrandMark size={32} />
    <span className="text-lg font-semibold tracking-tight text-fg">AgenEditor</span>
  </span>
);
