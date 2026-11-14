/**
 * Accessible dialog built on Radix Dialog: traps focus, closes on Escape,
 * restores focus to the trigger.
 */
import React from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { AlertTriangle, X } from 'lucide-react';
import { Button, cx } from './primitives';

interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  icon?: React.ReactNode;
  badge?: React.ReactNode;
  footer?: React.ReactNode;
  size?: 'sm' | 'md' | 'lg';
  children?: React.ReactNode;
}

export const Modal: React.FC<ModalProps> = ({
  open,
  onOpenChange,
  title,
  description,
  icon,
  badge,
  footer,
  size = 'md',
  children,
}) => {
  const returnFocus = useReturnFocus(open);
  return (
  <Dialog.Root open={open} onOpenChange={onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
      <Dialog.Content
        onCloseAutoFocus={returnFocus}
        className={cx(
          'fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[calc(100vw-32px)] max-h-[calc(100vh-48px)]',
          'flex flex-col rounded-xl border border-line bg-surface text-fg shadow-2xl focus:outline-none',
          size === 'sm' ? 'max-w-md' : size === 'lg' ? 'max-w-3xl' : 'max-w-xl'
        )}
        aria-describedby={description ? undefined : undefined}
      >
        <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-3 border-b border-line">
          <div className="flex items-start gap-2.5 min-w-0">
            {icon && <span className="mt-0.5 text-accent shrink-0">{icon}</span>}
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <Dialog.Title className="text-base font-semibold text-fg">{title}</Dialog.Title>
                {badge}
              </div>
              {description ? (
                <Dialog.Description className="text-[13px] text-fg-2 mt-1 leading-relaxed">{description}</Dialog.Description>
              ) : (
                <Dialog.Description className="sr-only">{typeof title === 'string' ? title : 'Dialog'}</Dialog.Description>
              )}
            </div>
          </div>
          <Dialog.Close asChild>
            <button aria-label="Close dialog" className="p-1 rounded-md text-fg-3 hover:text-fg hover:bg-elevated">
              <X className="w-4 h-4" />
            </button>
          </Dialog.Close>
        </div>
        {children && <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>}
        {footer && <div className="px-5 py-3 border-t border-line flex items-center justify-end gap-2">{footer}</div>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
  );
};

/**
 * Radix only restores focus to a <Dialog.Trigger>. Our dialogs are controlled,
 * so remember whatever had focus when the dialog opened and return to it.
 */
export function useReturnFocus(open: boolean): (e: Event) => void {
  const last = React.useRef<HTMLElement | null>(null);
  const wasOpen = React.useRef(false);
  if (open && !wasOpen.current && typeof document !== 'undefined') {
    last.current = document.activeElement as HTMLElement | null;
  }
  wasOpen.current = open;
  return React.useCallback((e: Event) => {
    e.preventDefault();
    const el = last.current;
    if (el && el.isConnected) el.focus();
  }, []);
}

interface ConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
}

export const ConfirmDialog: React.FC<ConfirmProps> = ({
  open,
  onOpenChange,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive,
  onConfirm,
}) => (
  <Modal
    open={open}
    onOpenChange={onOpenChange}
    size="sm"
    title={title}
    icon={<AlertTriangle className={cx('w-5 h-5', destructive ? 'text-danger' : 'text-warning')} />}
    description={message}
    footer={
      <>
        <Button onClick={() => onOpenChange(false)}>{cancelLabel}</Button>
        <Button
          variant={destructive ? 'danger' : 'primary'}
          onClick={() => {
            onConfirm();
            onOpenChange(false);
          }}
        >
          {confirmLabel}
        </Button>
      </>
    }
  />
);
