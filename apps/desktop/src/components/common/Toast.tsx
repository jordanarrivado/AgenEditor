import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';

export interface ToastMessage {
  id: string;
  type: 'success' | 'warning' | 'info';
  title: string;
  message?: string;
  action?: { label: string; onClick: () => void };
}

type ToastListener = (toast: ToastMessage) => void;
const toastListeners = new Set<ToastListener>();
let toastSeq = 0;

export function showToast(toast: Omit<ToastMessage, 'id'>) {
  const full: ToastMessage = { ...toast, id: `t${toastSeq++}` };
  for (const listener of toastListeners) listener(full);
}

export const ToastContainer: React.FC = () => {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  useEffect(() => {
    const onToast = (t: ToastMessage) => {
      setToasts((prev) => [...prev.slice(-3), t]);
      window.setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== t.id)), t.action ? 6000 : 3500);
    };
    toastListeners.add(onToast);
    return () => {
      toastListeners.delete(onToast);
    };
  }, []);

  return (
    <div
      role="region"
      aria-label="Notifications"
      aria-live="polite"
      className="fixed bottom-10 right-4 z-[80] flex flex-col gap-2 w-[min(360px,calc(100vw-32px))] pointer-events-none"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className="pointer-events-auto flex items-start gap-3 p-3 rounded-lg border border-line bg-elevated text-fg shadow-xl"
        >
          {t.type === 'success' && <CheckCircle2 aria-hidden className="w-4 h-4 text-success shrink-0 mt-0.5" />}
          {t.type === 'warning' && <AlertTriangle aria-hidden className="w-4 h-4 text-warning shrink-0 mt-0.5" />}
          {t.type === 'info' && <Info aria-hidden className="w-4 h-4 text-accent shrink-0 mt-0.5" />}
          <div className="flex-1 min-w-0">
            <p className="text-[13px] font-semibold">{t.title}</p>
            {t.message && <p className="text-xs text-fg-2 mt-0.5 leading-relaxed">{t.message}</p>}
            {t.action && (
              <button
                className="mt-1.5 text-xs font-semibold text-accent hover:underline"
                onClick={() => {
                  t.action!.onClick();
                  setToasts((prev) => prev.filter((x) => x.id !== t.id));
                }}
              >
                {t.action.label}
              </button>
            )}
          </div>
          <button
            onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
            className="text-fg-3 hover:text-fg p-0.5"
            aria-label="Dismiss notification"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
};
