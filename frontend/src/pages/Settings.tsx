import { Plus } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, Checkbox, Notice, PageHeader, Panel, Spinner, TextInput } from "../components/ui";
import {
  api,
  ApiError,
  type AuditEntry,
  type BusinessSettings,
  type Page,
  type PaymentMethod,
  type User,
} from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

const TABS = [
  { id: "business", label: "Business details", ownerOnly: false },
  { id: "payments", label: "Payment methods", ownerOnly: true },
  { id: "password", label: "My password", ownerOnly: false },
  { id: "activity", label: "Activity log", ownerOnly: true },
] as const;

export function SettingsPage() {
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  const [params, setParams] = useSearchParams();
  const tabs = TABS.filter((t) => isOwner || !t.ownerOnly);
  const active = tabs.find((t) => t.id === params.get("tab"))?.id ?? tabs[0].id;

  return (
    <>
      <PageHeader title="Settings" />
      <div role="tablist" aria-label="Settings sections" className="mb-6 flex gap-1 overflow-x-auto border-b border-line">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={active === t.id}
            onClick={() => setParams({ tab: t.id })}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-[15px] ${
              active === t.id ? "border-pad text-ink font-medium" : "border-transparent text-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {active === "business" && <BusinessTab canEdit={isOwner} />}
        {active === "payments" && isOwner && <PaymentsTab />}
        {active === "password" && <PasswordTab />}
        {active === "activity" && isOwner && <ActivityTab />}
      </div>
    </>
  );
}

// ---------------- Business details ----------------

function BusinessTab({ canEdit }: { canEdit: boolean }) {
  const [data, setData] = useState<BusinessSettings | null>(null);
  const [form, setForm] = useState<Record<string, any>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<BusinessSettings>("/settings").then((s) => {
      setData(s);
      setForm({ ...s });
    }).catch((e) => setMsg({ tone: "error", text: e.message }));
  }, []);

  if (!data) return msg ? <Notice tone="error">{msg.text}</Notice> : <Spinner />;

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setMsg(null);
    setErrors({});
    try {
      const saved = await api.put<BusinessSettings>("/settings", {
        business_name: form.business_name,
        slogan: form.slogan ?? "",
        address: form.address ?? "",
        phone: form.phone ?? "",
        email: form.email ?? "",
        receipt_footer: form.receipt_footer ?? "",
        default_reorder_level: Number(form.default_reorder_level),
        staff_max_discount_percent: String(form.staff_max_discount_percent),
        allow_staff_credit_sales: !!form.allow_staff_credit_sales,
      });
      setData(saved);
      setForm({ ...saved });
      setMsg({ tone: "success", text: "Business details saved." });
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fieldErrors);
        setMsg({ tone: "error", text: err.message });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-6">
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {!canEdit && <p className="text-muted">Only the owner can change these details.</p>}
      <fieldset disabled={!canEdit} className="space-y-6">
        <Panel title="Shop" description="Shown on receipts and reports.">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextInput label="Business name" value={form.business_name ?? ""} error={errors.business_name} onChange={set("business_name")} />
            <TextInput label="Slogan" value={form.slogan ?? ""} error={errors.slogan} onChange={set("slogan")} />
            <TextInput label="Address" value={form.address ?? ""} error={errors.address} onChange={set("address")} />
            <TextInput label="Phone" type="tel" value={form.phone ?? ""} error={errors.phone} onChange={set("phone")} />
            <TextInput label="Email" type="email" value={form.email ?? ""} error={errors.email} onChange={set("email")} />
            <TextInput label="Receipt footer" value={form.receipt_footer ?? ""} error={errors.receipt_footer} onChange={set("receipt_footer")} />
          </div>
        </Panel>
        <Panel title="Rules for sales staff" description="Used by the sales screen once it is built. The server enforces them.">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextInput label="Maximum discount staff can give (%)" type="number" inputMode="decimal" min={0} max={100} step="0.5"
              value={form.staff_max_discount_percent ?? ""} error={errors.staff_max_discount_percent}
              hint="Larger discounts will need owner approval." onChange={set("staff_max_discount_percent")} />
            <TextInput label="Default low-stock level" type="number" inputMode="numeric" min={0}
              value={form.default_reorder_level ?? ""} error={errors.default_reorder_level}
              hint="Used for new products unless you set their own level." onChange={set("default_reorder_level")} />
          </div>
          <div className="mt-4">
            <Checkbox label="Allow staff to record credit sales" checked={!!form.allow_staff_credit_sales}
              hint="Credit sales are only allowed for registered customers, never walk-ins."
              onChange={(e) => setForm({ ...form, allow_staff_credit_sales: e.target.checked })} />
          </div>
        </Panel>
      </fieldset>
      {canEdit && (
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-muted">Currency: Nigerian naira (₦). Last saved {dateTime(data.updated_at)}.</p>
          <Button type="submit" loading={busy}>Save changes</Button>
        </div>
      )}
    </form>
  );
}

// ---------------- Payment methods ----------------

function PaymentsTab() {
  const [methods, setMethods] = useState<PaymentMethod[] | null>(null);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<PaymentMethod[]>("/payment-methods?include_inactive=true").then(setMethods)
      .catch((e) => setMsg({ tone: "error", text: e.message }));
  }, []);
  useEffect(load, [load]);

  async function add(e: FormEvent) {
    e.preventDefault();
    if (busy || !name.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const pm = await api.post<PaymentMethod>("/payment-methods", {
        name, sort_order: (methods?.length ?? 0) + 1,
      });
      setName("");
      setMsg({ tone: "success", text: `Added “${pm.name}”.` });
      load();
    } catch (err) {
      setMsg({ tone: "error", text: err instanceof ApiError ? err.message : "Could not add." });
    } finally {
      setBusy(false);
    }
  }

  async function toggle(pm: PaymentMethod) {
    setMsg(null);
    try {
      await api.patch(`/payment-methods/${pm.id}`, { is_active: !pm.is_active });
      setMsg({ tone: "success", text: `“${pm.name}” ${pm.is_active ? "turned off" : "turned on"}.` });
      load();
    } catch (err) {
      setMsg({ tone: "error", text: err instanceof ApiError ? err.message : "Could not update." });
    }
  }

  return (
    <div className="space-y-4 max-w-2xl">
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <Panel title="Payment methods" description="Offered on the sales screen. Methods are turned off rather than deleted, so past payments keep their method.">
        {!methods ? <Spinner /> : (
          <ul className="divide-y divide-line">
            {methods.map((pm) => (
              <li key={pm.id} className="flex items-center justify-between py-2.5">
                <span className={pm.is_active ? "" : "text-muted"}>{pm.name}</span>
                <div className="flex items-center gap-3">
                  <Badge tone={pm.is_active ? "ok" : "off"}>{pm.is_active ? "On" : "Off"}</Badge>
                  <Button variant="ghost" className="h-8" onClick={() => toggle(pm)}>
                    {pm.is_active ? "Turn off" : "Turn on"}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={add} className="mt-4 flex items-end gap-2 border-t border-line pt-4" noValidate>
          <div className="flex-1">
            <TextInput label="New payment method" placeholder="e.g. Opay transfer" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <Button type="submit" variant="secondary" loading={busy} disabled={!name.trim()}>
            <Plus className="size-4" aria-hidden /> Add
          </Button>
        </form>
      </Panel>
    </div>
  );
}

// ---------------- My password ----------------

function PasswordTab() {
  const { setUser } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setMsg(null);
    if (next !== confirm) {
      setMsg({ tone: "error", text: "The new passwords do not match." });
      return;
    }
    setBusy(true);
    try {
      const u = await api.post<User>("/auth/change-password", { current_password: current, new_password: next });
      setUser(u);
      setCurrent(""); setNext(""); setConfirm("");
      setMsg({ tone: "success", text: "Password changed. You were logged out on your other devices." });
    } catch (err) {
      setMsg({ tone: "error", text: err instanceof ApiError ? err.message : "Could not change password." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="max-w-md space-y-4" noValidate>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <TextInput label="Current password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      <TextInput label="New password" type="password" autoComplete="new-password" hint="At least 8 characters." value={next} onChange={(e) => setNext(e.target.value)} />
      <TextInput label="Repeat new password" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      <Button type="submit" loading={busy}>Change password</Button>
    </form>
  );
}

// ---------------- Activity log ----------------

const ACTION_LABELS: Record<string, string> = {
  "auth.login": "Logged in",
  "auth.login_failed": "Failed login",
  "user.created": "Created user",
  "user.updated": "Changed user",
  "user.password_reset": "Reset a password",
  "user.password_changed": "Changed own password",
  "settings.updated": "Changed business settings",
  "payment_method.created": "Added payment method",
  "payment_method.updated": "Changed payment method",
};

function describe(e: AuditEntry): string {
  const d = e.details ?? {};
  if (e.action === "auth.login_failed") {
    const reason = String(d.reason ?? "");
    const who = d.username ? ` for “${d.username}”` : "";
    return reason === "locked" ? `Account locked after repeated failures${who}` : `Wrong password or unknown user${who}`;
  }
  const changed = Object.entries(d)
    .filter(([, v]) => v && typeof v === "object" && "to" in (v as object))
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${String((v as any).from ?? "—")} → ${String((v as any).to ?? "—")}`);
  if (changed.length) return changed.join("; ");
  if (d.username) return `${d.username}${d.role ? ` (${d.role})` : ""}`;
  if (d.name) return String(d.name);
  return "";
}

function ActivityTab() {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<AuditEntry> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    api.get<Page<AuditEntry>>(`/audit-logs?page=${page}&page_size=25`).then(setData).catch((e) => setError(e.message));
  }, [page]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Spinner />;
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));

  return (
    <div>
      <p className="mb-3 text-muted">A permanent record of logins and changes. Entries cannot be edited or deleted.</p>
      <div className="relative overflow-x-auto rounded-lg border border-line bg-white">
        <table className="w-full text-left text-[15px]">
          <thead className="border-b border-line text-sm text-muted">
            <tr>
              <th className="px-4 py-3 font-medium">When</th>
              <th className="px-4 py-3 font-medium">Who</th>
              <th className="px-4 py-3 font-medium">What</th>
              <th className="px-4 py-3 font-medium">Details</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((e) => (
              <tr key={e.id} className="border-b border-line last:border-0 align-top">
                <td className="px-4 py-2.5 num whitespace-nowrap">{dateTime(e.created_at)}</td>
                <td className="px-4 py-2.5 whitespace-nowrap">{e.user_name ?? "—"}</td>
                <td className="px-4 py-2.5 whitespace-nowrap">
                  {e.action === "auth.login_failed"
                    ? <span className="text-alert">{ACTION_LABELS[e.action]}</span>
                    : ACTION_LABELS[e.action] ?? e.action}
                </td>
                <td className="px-4 py-2.5 text-muted">{describe(e)}</td>
              </tr>
            ))}
            {!data.items.length && (
              <tr><td colSpan={4} className="px-4 py-8 text-center text-muted">No activity recorded yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center justify-between text-sm text-muted">
        <span className="num">{data.total} entries · page {page} of {pages}</span>
        <div className="flex gap-2">
          <Button variant="secondary" className="h-8" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
          <Button variant="secondary" className="h-8" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
        </div>
      </div>
    </div>
  );
}
