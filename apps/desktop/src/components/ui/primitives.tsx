/**
 * Shared, token-styled primitives. Screens compose these instead of
 * re-declaring button/field styles.
 */
import React from 'react';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';

const cx = (...parts: Array<string | false | null | undefined>) => parts.filter(Boolean).join(' ');
export { cx };

// ---------------------------------------------------------------- Button

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success';
type ButtonSize = 'sm' | 'md' | 'lg';

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-on-accent hover:bg-accent-hover font-semibold',
  secondary: 'border border-line bg-surface text-fg-2 hover:bg-elevated hover:text-fg',
  ghost: 'text-fg-2 hover:bg-elevated hover:text-fg',
  danger: 'bg-danger/15 text-danger border border-danger/30 hover:bg-danger/25',
  success: 'bg-success text-on-accent hover:opacity-90 font-semibold',
};
const SIZE: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1.5 rounded-md',
  md: 'h-8 px-3 text-[13px] gap-1.5 rounded-md',
  lg: 'h-10 px-4 text-sm gap-2 rounded-lg',
};

/** Classes for styling a <Link>/<a> as a button (avoids nesting a button inside a link). */
export function buttonClass(variant: ButtonVariant = 'secondary', size: ButtonSize = 'md', extra?: string): string {
  return cx('inline-flex items-center justify-center whitespace-nowrap transition-colors', VARIANT[variant], SIZE[size], extra);
}

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: React.ReactNode;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon, className, children, type = 'button', ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx(
        'inline-flex items-center justify-center whitespace-nowrap transition-colors disabled:opacity-45 disabled:cursor-not-allowed',
        VARIANT[variant],
        SIZE[size],
        className
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  );
});

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  label: string; // required accessible name
  active?: boolean;
  size?: 'sm' | 'md';
}

export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, active, size = 'md', className, children, type = 'button', ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={rest.title ?? label}
      aria-pressed={active}
      className={cx(
        'inline-flex items-center justify-center rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed',
        size === 'sm' ? 'w-7 h-7' : 'w-8 h-8',
        active ? 'bg-accent/15 text-accent' : 'text-fg-3 hover:text-fg hover:bg-elevated',
        className
      )}
      {...rest}
    >
      {children}
    </button>
  );
});

// ---------------------------------------------------------------- Badge

type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';
const TONE: Record<Tone, string> = {
  neutral: 'bg-elevated text-fg-2 border-line',
  accent: 'bg-accent/12 text-accent border-accent/30',
  success: 'bg-success/12 text-success border-success/30',
  warning: 'bg-warning/12 text-warning border-warning/30',
  danger: 'bg-danger/12 text-danger border-danger/30',
};

export const Badge: React.FC<{ tone?: Tone; className?: string; children: React.ReactNode; icon?: React.ReactNode }> = ({
  tone = 'neutral',
  className,
  icon,
  children,
}) => (
  <span
    className={cx(
      'inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[11px] font-medium leading-none whitespace-nowrap',
      TONE[tone],
      className
    )}
  >
    {icon}
    {children}
  </span>
);

// ---------------------------------------------------------------- Form fields

const fieldBase =
  'w-full rounded-md bg-app border border-line text-[13px] text-fg placeholder:text-fg-3 focus:outline-none focus:border-accent aria-[invalid=true]:border-danger';

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={cx(fieldBase, 'h-8 px-2.5', className)} {...rest} />;
  }
);

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={cx(fieldBase, 'p-2.5 leading-relaxed resize-y', className)} {...rest} />;
  }
);

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...rest }, ref) {
    return (
      <select ref={ref} className={cx(fieldBase, 'h-8 px-2', className)} {...rest}>
        {children}
      </select>
    );
  }
);

export const Field: React.FC<{
  label: string;
  htmlFor: string;
  hint?: React.ReactNode;
  error?: string;
  children: React.ReactNode;
  className?: string;
}> = ({ label, htmlFor, hint, error, children, className }) => (
  <div className={cx('flex flex-col gap-1', className)}>
    <label htmlFor={htmlFor} className="text-xs font-medium text-fg-2">
      {label}
    </label>
    {children}
    {error ? (
      <p id={`${htmlFor}-error`} role="alert" className="text-xs text-danger">
        {error}
      </p>
    ) : hint ? (
      <p className="text-xs text-fg-3">{hint}</p>
    ) : null}
  </div>
);

export const Switch: React.FC<{
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
  id?: string;
}> = ({ checked, onChange, label, description, disabled, id }) => (
  <label className={cx('flex items-start justify-between gap-3 py-1', disabled && 'opacity-50')}>
    <span className="flex flex-col">
      <span className="text-[13px] text-fg">{label}</span>
      {description && <span className="text-xs text-fg-3">{description}</span>}
    </span>
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        'relative w-8 h-[18px] rounded-full shrink-0 transition-colors mt-0.5',
        checked ? 'bg-accent' : 'bg-line'
      )}
    >
      <span
        className={cx(
          'absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white transition-transform',
          checked ? 'translate-x-[16px]' : 'translate-x-[2px]'
        )}
      />
    </button>
  </label>
);

// ---------------------------------------------------------------- Segmented control

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  size = 'md',
}: {
  value: T;
  options: { value: T; label: React.ReactNode; title?: string }[];
  onChange: (v: T) => void;
  label: string;
  size?: 'sm' | 'md';
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex p-0.5 rounded-md bg-app border border-line">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cx(
            'rounded px-2.5 font-medium transition-colors inline-flex items-center gap-1',
            size === 'sm' ? 'h-6 text-xs' : 'h-7 text-[13px]',
            value === o.value ? 'bg-elevated text-fg shadow-sm' : 'text-fg-3 hover:text-fg'
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- Tooltip

export const TooltipProvider = TooltipPrimitive.Provider;

export const Tooltip: React.FC<{ content: React.ReactNode; children: React.ReactElement; side?: 'top' | 'bottom' | 'left' | 'right' }> = ({
  content,
  children,
  side = 'top',
}) => (
  <TooltipPrimitive.Root delayDuration={300}>
    <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        side={side}
        sideOffset={6}
        collisionPadding={8}
        className="z-[70] max-w-xs rounded-md bg-elevated border border-line px-2 py-1 text-xs text-fg shadow-lg"
      >
        {content}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  </TooltipPrimitive.Root>
);

/** A visibly disabled control with the reason exposed via tooltip + aria. */
export const Unavailable: React.FC<{ reason?: string; children: React.ReactElement<{ disabled?: boolean }> }> = ({
  reason = 'Requires media engine integration',
  children,
}) => (
  <Tooltip content={reason}>
    <span tabIndex={0} aria-label={reason} className="inline-flex">
      {React.cloneElement(children, { disabled: true })}
    </span>
  </Tooltip>
);

// ---------------------------------------------------------------- Card / Panel

export const Card: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({ className, ...rest }) => (
  <div className={cx('rounded-xl border border-line bg-surface', className)} {...rest} />
);

export const SectionHeading: React.FC<{ title: string; action?: React.ReactNode; id?: string }> = ({ title, action, id }) => (
  <div className="flex items-center justify-between gap-3 mb-3">
    <h2 id={id} className="text-[15px] font-semibold text-fg">
      {title}
    </h2>
    {action}
  </div>
);

export const Kbd: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <kbd className="inline-flex items-center px-1.5 h-5 rounded border border-line bg-app font-mono text-[11px] text-fg-2">
    {children}
  </kbd>
);
