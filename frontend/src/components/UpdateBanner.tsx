import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

/** The script file this page is running (its name changes with every new build). */
function runningBuild(): string | null {
  const s = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]');
  return s ? new URL(s.src).pathname : null;
}

async function latestBuild(): Promise<string | null> {
  try {
    const html = await (await fetch("/", { cache: "no-store" })).text();
    return html.match(/src="(\/assets\/[^"]+\.js)"/)?.[1] ?? null;
  } catch {
    return null; // offline: try again later
  }
}

/**
 * The app stays open all day, so it wouldn't otherwise notice an update. This checks every
 * few minutes (and when the window comes back into view) and offers a one-click reload.
 */
export function UpdateBanner() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const current = runningBuild();
    if (!current) return; // development server: nothing to compare
    let stopped = false;
    const check = async () => {
      const latest = await latestBuild();
      if (!stopped && latest && latest !== current) setReady(true);
    };
    const timer = window.setInterval(check, 5 * 60 * 1000);
    const onVisible = () => { if (document.visibilityState === "visible") check(); };
    document.addEventListener("visibilitychange", onVisible);
    check();
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  if (!ready) return null;
  return (
    <div role="status" className="no-print mb-5 flex flex-wrap items-center justify-between gap-3 rounded-md border border-pad/40 bg-pad/10 px-4 py-3 text-sm">
      <span>A newer version of JD TechShop is available.</span>
      <button
        onClick={() => window.location.reload()}
        className="inline-flex items-center gap-2 rounded-md bg-board px-3 h-8 font-medium text-white hover:bg-board-700"
      >
        <RefreshCw className="size-4" aria-hidden /> Reload now
      </button>
    </div>
  );
}
