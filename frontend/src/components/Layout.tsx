import { LogOut, Menu, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { Logo } from "./Logo";
import { roleLabel } from "../lib/format";
import { navFor } from "../lib/nav";
import { UpdateBanner } from "./UpdateBanner";

function Wordmark() {
  return (
    <div className="px-5 pt-6 pb-5">
      <Logo className="h-16" />
      <p className="mt-2 text-[13px] text-board-300">Delivering Happiness</p>
    </div>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <nav aria-label="Main" className="flex-1 overflow-y-auto px-3 pb-4">
      <ul className="space-y-0.5">
        {navFor(user.role).map(({ to, label, icon: Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={to === "/"}
              onClick={onNavigate}
              className={({ isActive }) =>
                `group relative flex items-center gap-3 rounded-md pl-4 pr-3 h-10 text-[15px] transition-colors ${
                  isActive ? "bg-board-700 text-white" : "text-board-300 hover:text-white hover:bg-board-700/60"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {/* the active item is marked by a gold pad on a thin trace */}
                  <span
                    aria-hidden
                    className={`absolute -left-3 top-1/2 h-px w-4 -translate-y-1/2 ${isActive ? "bg-pad" : "bg-transparent"}`}
                  />
                  <span
                    aria-hidden
                    className={`absolute left-0.5 top-1/2 size-2 -translate-y-1/2 rounded-full ${
                      isActive ? "bg-pad" : "bg-transparent"
                    }`}
                  />
                  <Icon className="size-[18px] shrink-0" aria-hidden />
                  <span>{label}</span>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function UserBlock() {
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <div className="border-t border-board-600 px-5 py-4">
      <p className="text-sm font-medium text-white truncate">{user.full_name}</p>
      <p className="text-[13px] text-board-300">{roleLabel(user.role)}</p>
      <button
        onClick={logout}
        className="mt-3 inline-flex items-center gap-2 text-sm text-board-300 hover:text-white"
      >
        <LogOut className="size-4" aria-hidden /> Log out
      </button>
    </div>
  );
}

export function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  return (
    <div className="min-h-dvh lg:pl-64 print:pl-0">
      {/* Desktop sidebar */}
      <aside className="no-print hidden lg:flex fixed inset-y-0 left-0 w-64 flex-col bg-board">
        <Wordmark />
        <Sidebar />
        <UserBlock />
      </aside>

      {/* Mobile top bar */}
      <div className="no-print lg:hidden sticky top-0 z-30 flex items-center justify-between bg-board px-4 h-14">
        <Logo className="h-9" />
        <button
          onClick={() => setMobileOpen(true)}
          className="rounded p-2 text-white"
          aria-label="Open menu"
          aria-expanded={mobileOpen}
        >
          <Menu className="size-5" />
        </button>
      </div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="lg:hidden fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-ink/50" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[85vw] flex flex-col bg-board">
            <div className="flex items-start justify-between">
              <Wordmark />
              <button
                onClick={() => setMobileOpen(false)}
                className="m-4 rounded p-1 text-board-300 hover:text-white"
                aria-label="Close menu"
              >
                <X className="size-5" />
              </button>
            </div>
            <Sidebar onNavigate={() => setMobileOpen(false)} />
            <UserBlock />
          </aside>
        </div>
      )}

      <main key={location.pathname} className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-10 lg:py-10">
        <UpdateBanner />
        <Outlet />
      </main>
    </div>
  );
}
