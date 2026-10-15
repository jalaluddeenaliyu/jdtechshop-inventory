import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { Logo } from "../components/Logo";
import { Button, Notice, TextInput } from "../components/ui";
import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";

export function LoginPage() {
  const { user, login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    if (!username.trim() || !password) {
      setError("Enter your username and password.");
      return;
    }
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed. Try again.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-dvh grid lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div className="relative hidden lg:flex flex-col justify-between bg-board p-12 overflow-hidden">
        {/* faint circuit traces */}
        <svg className="absolute inset-0 h-full w-full opacity-[0.12]" aria-hidden>
          <defs>
            <pattern id="traces" width="120" height="120" patternUnits="userSpaceOnUse">
              <path d="M0 30h50l20 20h50M30 0v40l20 20v60M90 120V80l-20-20" stroke="#D9A520" fill="none" strokeWidth="1.2" />
              <circle cx="70" cy="50" r="3.5" fill="#D9A520" />
              <circle cx="50" cy="60" r="3.5" fill="#D9A520" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#traces)" />
        </svg>
        <div className="relative">
          <Logo className="h-24" />
        </div>
        <div className="relative">
          <p className="text-[44px] leading-[1.05] font-semibold tracking-tight text-white max-w-md">
            Delivering Happiness
          </p>
          <p className="mt-4 text-board-300 max-w-sm">
            Stock, sales and debts for the shop in Sokoto, recorded once and correct every time.
          </p>
        </div>
      </div>

      <div className="flex items-center justify-center px-6 py-12">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5" noValidate>
          <div>
            <Logo on="dark" className="lg:hidden h-16 mb-6" />
            <h1 className="text-2xl font-semibold tracking-tight">Log in</h1>
            <p className="mt-1 text-muted">Use the account the owner created for you.</p>
          </div>
          {error && <Notice tone="error">{error}</Notice>}
          <TextInput
            label="Username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
          />
          <TextInput
            label="Password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Button type="submit" loading={busy} className="w-full">
            Log in
          </Button>
          <p className="text-sm text-muted">Forgot your password? Ask the owner to reset it.</p>
        </form>
      </div>
    </div>
  );
}
