/** Designed empty / loading / error states (spec §3 "Onboarding and empty states"). */
import React from 'react';
import { AlertOctagon, Inbox } from 'lucide-react';
import { Button, cx } from './primitives';

export const EmptyState: React.FC<{
  icon?: React.ReactNode;
  title: string;
  message?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  compact?: boolean;
}> = ({ icon, title, message, action, className, compact }) => (
  <div
    className={cx(
      'flex flex-col items-center justify-center text-center rounded-xl border border-dashed border-line',
      compact ? 'p-5 gap-2' : 'p-10 gap-3',
      className
    )}
  >
    <div className="w-10 h-10 rounded-full bg-elevated flex items-center justify-center text-fg-3">
      {icon ?? <Inbox className="w-5 h-5" />}
    </div>
    <p className="text-sm font-semibold text-fg">{title}</p>
    {message && <p className="text-[13px] text-fg-2 max-w-sm leading-relaxed">{message}</p>}
    {action && <div className="mt-1 flex gap-2 flex-wrap justify-center">{action}</div>}
  </div>
);

export const ErrorState: React.FC<{ title?: string; message?: React.ReactNode; onRetry?: () => void; className?: string }> = ({
  title = 'Something went wrong',
  message = 'The demo data could not be loaded.',
  onRetry,
  className,
}) => (
  <div role="alert" className={cx('flex flex-col items-center justify-center text-center p-10 gap-3 rounded-xl border border-danger/30 bg-danger/5', className)}>
    <AlertOctagon className="w-8 h-8 text-danger" />
    <p className="text-sm font-semibold text-fg">{title}</p>
    <p className="text-[13px] text-fg-2 max-w-sm">{message}</p>
    {onRetry && (
      <Button variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    )}
  </div>
);

export const Skeleton: React.FC<{ className?: string }> = ({ className }) => (
  <div aria-hidden className={cx('animate-pulse rounded-md bg-elevated', className)} />
);

export const LoadingState: React.FC<{ label?: string; rows?: number; className?: string }> = ({
  label = 'Loading demo data…',
  rows = 3,
  className,
}) => (
  <div role="status" aria-live="polite" className={cx('flex flex-col gap-3', className)}>
    <span className="sr-only">{label}</span>
    {Array.from({ length: rows }).map((_, i) => (
      <Skeleton key={i} className="h-14 w-full" />
    ))}
  </div>
);
