import { KeyRound, Pencil, Plus } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Badge, Button, Checkbox, Dialog, Notice, PageHeader, SelectInput, Spinner, TextInput } from "../components/ui";
import { api, ApiError, type Role, type User } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateTime, roleLabel } from "../lib/format";

type Mode = { kind: "add" } | { kind: "edit"; user: User } | { kind: "password"; user: User } | null;

export function UsersPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    api.get<User[]>("/users").then(setUsers).catch((e) => setLoadError(e.message));
  }, []);
  useEffect(load, [load]);

  function done(message: string) {
    setMode(null);
    setFlash(message);
    load();
  }

  return (
    <>
      <PageHeader
        title="Users"
        description="Everyone who can log in. Sales staff can record sales and payments; only owners can change prices, stock, settings and accounts."
        actions={
          <Button onClick={() => { setFlash(null); setMode({ kind: "add" }); }}>
            <Plus className="size-4" aria-hidden /> Add user
          </Button>
        }
      />
      {flash && <div className="mb-4"><Notice tone="success">{flash}</Notice></div>}
      {loadError && <Notice tone="error">{loadError}</Notice>}
      {!users && !loadError && <Spinner />}

      {users && (
        <div className="relative overflow-x-auto rounded-lg border border-line bg-white">
          <table className="w-full text-left text-[15px]">
            <thead className="border-b border-line text-sm text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Username</th>
                <th className="px-4 py-3 font-medium">Role</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Last login</th>
                <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className={`border-b border-line last:border-0 ${u.is_active ? "" : "text-muted"}`}>
                  <td className="px-4 py-3 font-medium">
                    {u.full_name}
                    {u.id === me?.id && <span className="ml-2 text-sm text-muted">(you)</span>}
                  </td>
                  <td className="px-4 py-3">{u.username}</td>
                  <td className="px-4 py-3">
                    <Badge tone={u.role === "owner" ? "pad" : "neutral"}>{roleLabel(u.role)}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={u.is_active ? "ok" : "off"}>{u.is_active ? "Active" : "Deactivated"}</Badge>
                  </td>
                  <td className="px-4 py-3 num whitespace-nowrap">{dateTime(u.last_login_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" className="h-8" onClick={() => setMode({ kind: "edit", user: u })}>
                        <Pencil className="size-4" aria-hidden /> Edit
                      </Button>
                      <Button variant="ghost" className="h-8" onClick={() => setMode({ kind: "password", user: u })}>
                        <KeyRound className="size-4" aria-hidden /> Reset password
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={mode?.kind === "add"} title="Add user" onClose={() => setMode(null)}>
        <AddUserForm onDone={(u) => done(`${u.full_name} can now log in as “${u.username}”.`)} />
      </Dialog>
      <Dialog open={mode?.kind === "edit"} title="Edit user" onClose={() => setMode(null)}>
        {mode?.kind === "edit" && (
          <EditUserForm user={mode.user} isSelf={mode.user.id === me?.id} onDone={(u) => done(`Saved changes to ${u.full_name}.`)} />
        )}
      </Dialog>
      <Dialog open={mode?.kind === "password"} title="Reset password" onClose={() => setMode(null)}>
        {mode?.kind === "password" && (
          <ResetPasswordForm user={mode.user} onDone={(u) => done(`Password reset for ${u.full_name}. Their other sessions were logged out.`)} />
        )}
      </Dialog>
    </>
  );
}

function AddUserForm({ onDone }: { onDone: (u: User) => void }) {
  const [form, setForm] = useState({ full_name: "", username: "", password: "", role: "staff" as Role });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    setErrors({});
    try {
      onDone(await api.post<User>("/users", form));
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fieldErrors);
        if (!Object.keys(err.fieldErrors).length) setError(err.message);
      } else setError("Could not save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error && <Notice tone="error">{error}</Notice>}
      <TextInput label="Full name" value={form.full_name} error={errors.full_name}
        onChange={(e) => setForm({ ...form, full_name: e.target.value })} autoFocus />
      <TextInput label="Username" hint="3–50 characters: letters, numbers, dot, dash or underscore."
        value={form.username} error={errors.username} autoCapitalize="none" spellCheck={false}
        onChange={(e) => setForm({ ...form, username: e.target.value })} />
      <TextInput label="Temporary password" type="text" hint="At least 8 characters. Ask them to change it after first login."
        value={form.password} error={errors.password} autoComplete="new-password"
        onChange={(e) => setForm({ ...form, password: e.target.value })} />
      <SelectInput label="Role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
        hint={form.role === "owner" ? "Owners have full access, including prices, stock and accounts." : "Can record sales and payments, and look up products and customers."}>
        <option value="staff">Sales staff</option>
        <option value="owner">Owner</option>
      </SelectInput>
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy}>Create user</Button>
      </div>
    </form>
  );
}

function EditUserForm({ user, isSelf, onDone }: { user: User; isSelf: boolean; onDone: (u: User) => void }) {
  const [fullName, setFullName] = useState(user.full_name);
  const [role, setRole] = useState<Role>(user.role);
  const [active, setActive] = useState(user.is_active);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const deactivating = user.is_active && !active;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (deactivating && !window.confirm(`Deactivate ${user.full_name}? They will be logged out and unable to log in. Their past records are kept.`)) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api.patch<User>(`/users/${user.id}`, { full_name: fullName, role, is_active: active }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error && <Notice tone="error">{error}</Notice>}
      <TextInput label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
      <SelectInput label="Role" value={role} disabled={isSelf}
        hint={isSelf ? "You cannot change your own role." : "Changing the role logs this person out so the new access applies at once."}
        onChange={(e) => setRole(e.target.value as Role)}>
        <option value="staff">Sales staff</option>
        <option value="owner">Owner</option>
      </SelectInput>
      <Checkbox label="Account is active" checked={active} disabled={isSelf}
        hint={isSelf ? "You cannot deactivate yourself." : "Deactivated users cannot log in. Their history stays in the records."}
        onChange={(e) => setActive(e.target.checked)} />
      <div className="flex justify-end pt-2">
        <Button type="submit" variant={deactivating ? "danger" : "primary"} loading={busy}>
          {deactivating ? "Deactivate and save" : "Save changes"}
        </Button>
      </div>
    </form>
  );
}

function ResetPasswordForm({ user, onDone }: { user: User; onDone: (u: User) => void }) {
  const [pw, setPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api.post<User>(`/users/${user.id}/reset-password`, { new_password: pw }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reset. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <p className="text-muted">Set a new temporary password for {user.full_name} and tell them in person.</p>
      {error && <Notice tone="error">{error}</Notice>}
      <TextInput label="New password" type="text" autoComplete="new-password" value={pw}
        hint="At least 8 characters." onChange={(e) => setPw(e.target.value)} autoFocus />
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy}>Reset password</Button>
      </div>
    </form>
  );
}
