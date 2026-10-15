/** The JD TechShop logo. "light" has white lettering for the green sidebar and login panel;
 *  "dark" has black lettering for white pages and printed receipts. The orange smile stays orange. */
export function Logo({ on = "light", className = "", alt = "JD TechShop" }: { on?: "light" | "dark"; className?: string; alt?: string }) {
  return <img src={on === "light" ? "/brand/logo-white.png" : "/brand/logo.png"} alt={alt} className={`w-auto select-none ${className}`} draggable={false} />;
}
