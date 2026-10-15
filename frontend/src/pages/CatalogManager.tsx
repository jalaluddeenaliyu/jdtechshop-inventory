import { Check, Pencil, Plus, X } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Badge, Button, Notice, Spinner } from "../components/ui";
import { api, ApiError, type Brand, type Category } from "../lib/api";

/** Owner tool for categories (with one level of subcategories) and brands. Nothing is deleted. */
export function CatalogManager({ onChanged }: { onChanged: () => void }) {
  const [tab, setTab] = useState<"categories" | "brands">("categories");
  return (
    <div>
      <div role="tablist" aria-label="Catalogue lists" className="mb-4 flex gap-1 border-b border-line">
        {(["categories", "brands"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-3 py-2 text-[15px] capitalize ${
              tab === t ? "border-pad font-medium text-ink" : "border-transparent text-muted hover:text-ink"
            }`}>
            {t}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "categories" ? <Categories onChanged={onChanged} /> : <Brands onChanged={onChanged} />}
      </div>
    </div>
  );
}

const smallInput =
  "h-9 rounded-md border border-line bg-white px-3 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40";

function errorText(err: unknown) {
  return err instanceof ApiError ? err.message : "Could not save. Try again.";
}

function Categories({ onChanged }: { onChanged: () => void }) {
  const [items, setItems] = useState<Category[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ id: number; name: string; parent_id: string } | null>(null);

  const load = useCallback(() => {
    api.get<Category[]>("/categories?include_inactive=true").then(setItems).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function run(action: () => Promise<unknown>) {
    if (busy) return false;
    setBusy(true);
    setError(null);
    try {
      await action();
      load();
      onChanged();
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    const ok = await run(() => api.post("/categories", { name, parent_id: parentId ? Number(parentId) : null }));
    if (ok) setName("");
  }

  if (!items) return error ? <Notice tone="error">{error}</Notice> : <Spinner />;
  const topLevel = items.filter((c) => c.parent_id === null);
  const ordered = topLevel.flatMap((t) => [t, ...items.filter((c) => c.parent_id === t.id)]);

  return (
    <div className="space-y-4">
      {error && <Notice tone="error">{error}</Notice>}
      <form onSubmit={add} className="flex flex-wrap items-end gap-2">
        <label className="flex-1 min-w-48">
          <span className="mb-1 block text-sm font-medium">New category</span>
          <input className={`${smallInput} w-full`} value={name} onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Chargers" />
        </label>
        <label>
          <span className="mb-1 block text-sm font-medium">Inside</span>
          <select className={smallInput} value={parentId} onChange={(e) => setParentId(e.target.value)}>
            <option value="">Top level</option>
            {topLevel.filter((c) => c.is_active).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <Button type="submit" className="h-9" loading={busy} disabled={!name.trim()}>
          <Plus className="size-4" aria-hidden /> Add
        </Button>
      </form>

      <ul className="divide-y divide-line rounded-md border border-line">
        {ordered.map((c) => (
          <li key={c.id} className={`flex flex-wrap items-center gap-2 px-3 py-2 ${c.parent_id ? "pl-8" : ""}`}>
            {editing?.id === c.id ? (
              <form className="flex flex-1 flex-wrap items-center gap-2"
                onSubmit={async (e) => {
                  e.preventDefault();
                  const ok = await run(() => api.patch(`/categories/${c.id}`, {
                    name: editing.name,
                    parent_id: editing.parent_id ? Number(editing.parent_id) : null,
                  }));
                  if (ok) setEditing(null);
                }}>
                <label className="sr-only" htmlFor={`cat-${c.id}`}>Category name</label>
                <input id={`cat-${c.id}`} className={`${smallInput} flex-1 min-w-40`} value={editing.name} autoFocus
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                <label className="sr-only" htmlFor={`cat-parent-${c.id}`}>Inside</label>
                <select id={`cat-parent-${c.id}`} className={smallInput} value={editing.parent_id}
                  onChange={(e) => setEditing({ ...editing, parent_id: e.target.value })}>
                  <option value="">Top level</option>
                  {topLevel.filter((t) => t.id !== c.id).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                <Button type="submit" variant="ghost" className="h-8" loading={busy} aria-label="Save">
                  <Check className="size-4" aria-hidden /> Save
                </Button>
                <Button type="button" variant="ghost" className="h-8" onClick={() => setEditing(null)} aria-label="Cancel">
                  <X className="size-4" aria-hidden />
                </Button>
              </form>
            ) : (
              <>
                <span className={`flex-1 ${c.is_active ? "" : "text-muted"}`}>
                  {c.parent_id ? "— " : ""}{c.name}
                  {!c.is_active && <span className="ml-2"><Badge tone="off">Turned off</Badge></span>}
                </span>
                <Button variant="ghost" className="h-8"
                  onClick={() => setEditing({ id: c.id, name: c.name, parent_id: c.parent_id ? String(c.parent_id) : "" })}>
                  <Pencil className="size-4" aria-hidden /> Edit
                </Button>
                <Button variant="ghost" className="h-8" disabled={busy}
                  onClick={() => run(() => api.patch(`/categories/${c.id}`, { is_active: !c.is_active }))}>
                  {c.is_active ? "Turn off" : "Turn on"}
                </Button>
              </>
            )}
          </li>
        ))}
        {!ordered.length && <li className="px-3 py-6 text-center text-muted">No categories yet.</li>}
      </ul>
      <p className="text-sm text-muted">
        Turned-off categories cannot be chosen for products. Products already in them keep their category.
      </p>
    </div>
  );
}

function Brands({ onChanged }: { onChanged: () => void }) {
  const [items, setItems] = useState<Brand[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);

  const load = useCallback(() => {
    api.get<Brand[]>("/brands?include_inactive=true").then(setItems).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function run(action: () => Promise<unknown>) {
    if (busy) return false;
    setBusy(true);
    setError(null);
    try {
      await action();
      load();
      onChanged();
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!items) return error ? <Notice tone="error">{error}</Notice> : <Spinner />;

  return (
    <div className="space-y-4">
      {error && <Notice tone="error">{error}</Notice>}
      <form className="flex flex-wrap items-end gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api.post("/brands", { name }))) setName("");
        }}>
        <label className="flex-1 min-w-48">
          <span className="mb-1 block text-sm font-medium">New brand</span>
          <input className={`${smallInput} w-full`} value={name} onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Tecno" />
        </label>
        <Button type="submit" className="h-9" loading={busy} disabled={!name.trim()}>
          <Plus className="size-4" aria-hidden /> Add
        </Button>
      </form>
      <ul className="divide-y divide-line rounded-md border border-line">
        {items.map((b) => (
          <li key={b.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
            {editing?.id === b.id ? (
              <form className="flex flex-1 items-center gap-2"
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (await run(() => api.patch(`/brands/${b.id}`, { name: editing.name }))) setEditing(null);
                }}>
                <label className="sr-only" htmlFor={`brand-${b.id}`}>Brand name</label>
                <input id={`brand-${b.id}`} className={`${smallInput} flex-1`} value={editing.name} autoFocus
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                <Button type="submit" variant="ghost" className="h-8" loading={busy}>
                  <Check className="size-4" aria-hidden /> Save
                </Button>
                <Button type="button" variant="ghost" className="h-8" onClick={() => setEditing(null)} aria-label="Cancel">
                  <X className="size-4" aria-hidden />
                </Button>
              </form>
            ) : (
              <>
                <span className={`flex-1 ${b.is_active ? "" : "text-muted"}`}>
                  {b.name}
                  {!b.is_active && <span className="ml-2"><Badge tone="off">Turned off</Badge></span>}
                </span>
                <Button variant="ghost" className="h-8" onClick={() => setEditing({ id: b.id, name: b.name })}>
                  <Pencil className="size-4" aria-hidden /> Edit
                </Button>
                <Button variant="ghost" className="h-8" disabled={busy}
                  onClick={() => run(() => api.patch(`/brands/${b.id}`, { is_active: !b.is_active }))}>
                  {b.is_active ? "Turn off" : "Turn on"}
                </Button>
              </>
            )}
          </li>
        ))}
        {!items.length && <li className="px-3 py-6 text-center text-muted">No brands yet.</li>}
      </ul>
    </div>
  );
}
