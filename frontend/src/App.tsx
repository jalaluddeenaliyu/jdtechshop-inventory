import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Layout } from "./components/Layout";
import { PageHeader, Spinner } from "./components/ui";
import { useAuth } from "./lib/auth";
import { NAV } from "./lib/nav";
import { CustomersPage } from "./pages/Customers";
import { ExpensesPage } from "./pages/Expenses";
import { PaymentReceiptPage } from "./pages/Debts";
import { DashboardPage } from "./pages/Dashboard";
import { InventoryPage } from "./pages/Inventory";
import { LoginPage } from "./pages/Login";
import { ModulePendingPage } from "./pages/ModulePending";
import { PosPage } from "./pages/Pos";
import { ProductsPage } from "./pages/Products";
import { ReceiptPage } from "./pages/Receipt";
import { ReportsPage } from "./pages/Reports";
import { PurchaseEditorPage } from "./pages/PurchaseEditor";
import { PurchasesPage } from "./pages/Purchases";
import { SuppliersPage } from "./pages/Suppliers";
import { SettingsPage } from "./pages/Settings";
import { UsersPage } from "./pages/Users";
import type { Role } from "./lib/api";
import type { ReactNode } from "react";

const BUILT: Record<string, () => ReactNode> = {
  "/": () => <DashboardPage />,
  "/sales": () => <PosPage />,
  "/products": () => <ProductsPage />,
  "/inventory": () => <InventoryPage />,
  "/purchases": () => <PurchasesPage />,
  "/suppliers": () => <SuppliersPage />,
  "/customers": () => <CustomersPage />,
  "/reports": () => <ReportsPage />,
  "/expenses": () => <ExpensesPage />,
  "/users": () => <UsersPage />,
  "/settings": () => <SettingsPage />,
};

function RequireLogin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="px-6"><Spinner label="Opening JD TechShop…" /></div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  return <>{children}</>;
}

function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { user } = useAuth();
  if (user && !roles.includes(user.role)) {
    return (
      <PageHeader title="Not available to your account" description="Ask the owner if you need access to this page." />
    );
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireLogin><Layout /></RequireLogin>}>
        {NAV.map((item) => (
          <Route
            key={item.to}
            path={item.to}
            element={
              <RequireRole roles={item.roles}>
                {BUILT[item.to]?.() ?? <ModulePendingPage item={item} />}
              </RequireRole>
            }
          />
        ))}
        <Route path="/sales/:id" element={<ReceiptPage />} />
        <Route path="/customer-payments/:id" element={<PaymentReceiptPage />} />
        <Route path="/purchases/:id" element={<RequireRole roles={["owner"]}><PurchaseEditorPage /></RequireRole>} />
        <Route path="*" element={<PageHeader title="Page not found" description="Use the menu to find what you need." />} />
      </Route>
    </Routes>
  );
}
