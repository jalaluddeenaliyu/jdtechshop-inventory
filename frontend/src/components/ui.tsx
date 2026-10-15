// Small, accessible building blocks used across the app.
import { CircleAlert, CircleCheck, LoaderCircle, X } from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const variants: Record<Variant, string> = {
  primary: "bg-board text-white hover:bg-board-700 disabled:bg-board-300",
  secondary: "bg-white text-ink border border-line hover:border-board-300 disabled:text-muted",
  danger: "bg-alert text-white hover:brightness-95 disabled:opacity-60",
  ghost: "text-board hover:bg-board/5 disabled:text-muted",
};

export function Button({
  variant = "primary",
  loading = false,
  className = "",
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3.5 h-10 text-sm font-medium
        transition-colors disabled:cursor-not-allowed ${variants[variant]} ${className}`}
    >
      {loading && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
  id,
}: {
  label: string;
  hint?: string;
  error?: string;
  id: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-ink">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="text-sm text-alert">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-sm text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

const inputBase =
  "block w-full h-10 rounded-md border bg-white px-3 text-[15px] text-ink placeholder:text-muted/70 " +
  "focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40 disabled:bg-paper";

export function TextInput({
  label,
  hint,
  error,
  className = "",
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; error?: string }) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} id={id}>
      <input
        id={id}
        aria-invalid={!!error || undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={`${inputBase} ${error ? "border-alert" : "border-line"} ${className}`}
        {...rest}
      />
    </Field>
  );
}

export function TextArea({
  label,
  hint,
  error,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; hint?: string; error?: string }) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} id={id}>
      <textarea
        id={id}
        rows={3}
        aria-invalid={!!error || undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={`${inputBase} h-auto py-2 ${error ? "border-alert" : "border-line"}`}
        {...rest}
      />
    </Field>
  );
}

export function SelectInput({
  label,
  hint,
  error,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string; hint?: string; error?: string }) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} id={id}>
      <select
        id={id}
        aria-invalid={!!error || undefined}
        className={`${inputBase} ${error ? "border-alert" : "border-line"}`}
        {...rest}
      >
        {children}
      </select>
    </Field>
  );
}

export function Checkbox({
  label,
  hint,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  const id = useId();
  return (
    <div className="flex items-start gap-3">
      <input id={id} type="checkbox" className="mt-1 size-4 accent-board" {...rest} />
      <div>
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        {hint && <p className="text-sm text-muted">{hint}</p>}
      </div>
    </div>
  );
}

export function Notice({ tone, children }: { tone: "error" | "success"; children: ReactNode }) {
  const Icon = tone === "error" ? CircleAlert : CircleCheck;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`flex gap-2.5 rounded-md px-3.5 py-2.5 text-sm ${
        tone === "error" ? "bg-alert-bg text-alert" : "bg-ok-bg text-ok"
      }`}
    >
      <Icon className="size-4 mt-0.5 shrink-0" aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 text-muted" role="status">
      <LoaderCircle className="size-5 animate-spin" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "off" | "pad" | "alert"; children: ReactNode }) {
  const tones = {
    alert: "bg-alert-bg text-alert border-transparent",
    neutral: "bg-paper text-muted border-line",
    ok: "bg-ok-bg text-ok border-transparent",
    off: "bg-white text-muted border-line",
    pad: "bg-pad/15 text-[#7a5a0c] border-transparent",
  };
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium border ${tones[tone]}`}>
      {children}
    </span>
  );
}

/** Modal dialog built on the native <dialog> element (keyboard and focus handled by the browser). */
export function Dialog({
  open,
  title,
  onClose,
  children,
  wide = false,
  extraWide = false,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  extraWide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      d.showModal();
      // The browser focuses the first focusable element (the close button). Move focus to the
      // field the form asks for (data-autofocus), else its first field, so typing goes there.
      const field = d.querySelector<HTMLElement>("[data-autofocus]")
        ?? d.querySelector<HTMLElement>("input:not([type=hidden]):not([disabled]), select, textarea");
      field?.focus();
    }
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      // React passes these events up to parent dialogs too: only react to our own.
      onClose={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onCancel={(e) => {
        if (e.target !== e.currentTarget) return;
        e.preventDefault();
        onClose();
      }}
      className={`m-auto ${extraWide ? "w-[min(62rem,calc(100vw-2rem))]" : wide ? "w-[min(46rem,calc(100vw-2rem))]" : "w-[min(32rem,calc(100vw-2rem))]"}
        rounded-lg p-0 shadow-xl backdrop:bg-ink/40`}
    >
      {open && (
        <div className="p-5">
          <div className="flex items-start justify-between gap-4 mb-4">
            <h2 className="text-lg font-semibold">{title}</h2>
            <button onClick={onClose} className="rounded p-1 text-muted hover:text-ink" aria-label="Close">
              <X className="size-5" />
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-muted max-w-2xl">{description}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function Panel({ title, description, children }: { title?: string; description?: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-white">
      {title && (
        <header className="border-b border-line px-5 py-4">
          <h2 className="font-semibold">{title}</h2>
          {description && <p className="text-sm text-muted mt-0.5">{description}</p>}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

/** "12 products · page 1 of 3" with Previous / Next. */
export function Pager({
  page,
  pageSize,
  total,
  noun,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  noun: [string, string];
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm text-muted">
      <span className="num">
        {total} {total === 1 ? noun[0] : noun[1]} · page {page} of {pages}
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" className="h-8" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
        <Button variant="secondary" className="h-8" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
      </div>
    </div>
  );
}
