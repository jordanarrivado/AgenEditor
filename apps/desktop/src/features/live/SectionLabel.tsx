import React from 'react';

/** Small uppercase heading used for the workspace's column sections (Media, Transitions…). */
export const SectionLabel: React.FC<{ id?: string; title: string; meta?: React.ReactNode }> = ({ id, title, meta }) => (
  <div className="flex items-baseline justify-between gap-3">
    <h2 id={id} className="text-xs font-semibold uppercase tracking-[0.08em] text-fg-2">{title}</h2>
    {meta && <span className="min-w-0 truncate text-xs text-fg-3">{meta}</span>}
  </div>
);
