import {
  ChartColumn,
  Factory,
  LayoutDashboard,
  Package,
  Receipt,
  Settings,
  ShoppingCart,
  Truck,
  UserCog,
  Users,
  Warehouse,
  type LucideIcon,
} from "lucide-react";
import type { Role } from "./api";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  roles: Role[];
  /** Short description shown on pages that are still being built. */
  purpose: string;
  phase?: string;
}

// The sidebar only shows what each role may use. The server enforces the same rules.
export const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, roles: ["owner", "staff"],
    purpose: "Today's sales, profit, stock alerts and debts at a glance." },
  { to: "/sales", label: "Sales / POS", icon: ShoppingCart, roles: ["owner", "staff"],
    purpose: "Ring up sales, pick phone units by IMEI, take payments and print receipts." },
  { to: "/products", label: "Products", icon: Package, roles: ["owner", "staff"],
    purpose: "Your catalogue of phones, laptops and accessories with prices and stock levels." },
  { to: "/inventory", label: "Inventory", icon: Warehouse, roles: ["owner", "staff"],
    purpose: "Stock movement history, IMEI/serial units, and stock adjustments." },
  { to: "/purchases", label: "Purchases", icon: Truck, roles: ["owner"],
    purpose: "Receive stock from suppliers and track what you owe them." },
  { to: "/customers", label: "Customers", icon: Users, roles: ["owner", "staff"],
    purpose: "Customer details, purchase history, debts and repayments." },
  { to: "/suppliers", label: "Suppliers", icon: Factory, roles: ["owner"],
    purpose: "Supplier contacts, purchase history and balances." },
  { to: "/reports", label: "Reports", icon: ChartColumn, roles: ["owner"],
    purpose: "Sales, profit, stock valuation, debts and staff performance, with CSV export." },
  { to: "/expenses", label: "Expenses", icon: Receipt, roles: ["owner"],
    purpose: "Running costs, for an honest net profit." },
  { to: "/users", label: "Users", icon: UserCog, roles: ["owner"],
    purpose: "Staff accounts and access." },
  { to: "/settings", label: "Settings", icon: Settings, roles: ["owner", "staff"],
    purpose: "Business details, payment methods and your password." },
];

export function navFor(role: Role) {
  return NAV.filter((n) => n.roles.includes(role));
}
