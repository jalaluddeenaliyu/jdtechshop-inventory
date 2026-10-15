import { PageHeader, Panel } from "../components/ui";
import type { NavItem } from "../lib/nav";

/** Honest placeholder for modules not yet built. Shows no fake data. */
export function ModulePendingPage({ item }: { item: NavItem }) {
  return (
    <>
      <PageHeader title={item.label} description={item.purpose} />
      <Panel>
        <p className="font-medium">Not available yet</p>
        <p className="mt-1 text-muted">
          This screen is scheduled for {item.phase ?? "a later phase"} of the build.
        </p>
      </Panel>
    </>
  );
}
