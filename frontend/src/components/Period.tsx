import { useState } from "react";
import { PRESET_LABELS, presetRange, todayISO, type PeriodPreset } from "../lib/format";

export type Period = { preset: PeriodPreset; from: string; to: string };

export function initialPeriod(preset: PeriodPreset = "today"): Period {
  const [from, to] = presetRange(preset);
  return { preset, from, to };
}

/** Today / Yesterday / Last 7 days / This month / Last month / Custom — shop days (Lagos). */
export function PeriodPicker({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  const [custom, setCustom] = useState(value.preset === "custom");
  const cls = "h-10 rounded-md border border-line bg-white px-3 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40";
  return (
    <div className="flex flex-wrap items-center gap-2 no-print">
      <label className="sr-only" htmlFor="period-preset">Period</label>
      <select id="period-preset" className={cls} value={value.preset}
        onChange={(e) => {
          const preset = e.target.value as PeriodPreset;
          setCustom(preset === "custom");
          if (preset === "custom") onChange({ ...value, preset });
          else {
            const [from, to] = presetRange(preset);
            onChange({ preset, from, to });
          }
        }}>
        {(Object.keys(PRESET_LABELS) as PeriodPreset[]).map((p) => <option key={p} value={p}>{PRESET_LABELS[p]}</option>)}
      </select>
      {custom && (
        <>
          <label className="sr-only" htmlFor="period-from">From</label>
          <input id="period-from" type="date" className={cls} value={value.from} max={value.to}
            onChange={(e) => e.target.value && onChange({ ...value, from: e.target.value })} />
          <span className="text-muted">to</span>
          <label className="sr-only" htmlFor="period-to">To</label>
          <input id="period-to" type="date" className={cls} value={value.to} min={value.from} max={todayISO()}
            onChange={(e) => e.target.value && onChange({ ...value, to: e.target.value })} />
        </>
      )}
    </div>
  );
}
