import { Info, Repeat, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Notice, SelectInput, TextInput } from "../components/ui";
import { api, qs, type Condition, type Page, type PosProduct, type PosResult, type TradeInIn, type Unit } from "../lib/api";
import { naira } from "../lib/format";
import { ScanBox } from "./Pos";

/** A swap on the till before the sale is completed. */
export type SwapDraft = { payload: TradeInIn; productName: string; identifier: string; value: number };

const money = (s: string) => Number(s.replaceAll(",", "").trim()) || 0;
const digits = (s: string) => s.replace(/[\s-]/g, "");

/** The customer's old phone: which model, its IMEI/serial, condition and the value agreed. */
export function SwapForm({ initial, total, onSave }: { initial: SwapDraft | null; total: number; onSave: (d: SwapDraft) => void }) {
  const p = initial?.payload;
  const [isNew, setIsNew] = useState(!!p?.new_product_name);
  const [product, setProduct] = useState<PosProduct | null>(
    p?.product_id ? { id: p.product_id, name: initial!.productName } as PosProduct : null);
  const [newName, setNewName] = useState(p?.new_product_name ?? "");
  const [newPrice, setNewPrice] = useState(p?.new_product_price ?? "");
  const [imei1, setImei1] = useState(p?.imei1 ?? "");
  const [imei2, setImei2] = useState(p?.imei2 ?? "");
  const [serial, setSerial] = useState(p?.serial_number ?? "");
  const [condition, setCondition] = useState<Condition>(p?.condition ?? "used");
  const [value, setValue] = useState(p?.value ?? "");
  const [notes, setNotes] = useState(p?.notes ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [known, setKnown] = useState<Unit | null>(null); // this IMEI/serial is already in the system

  // A phone the shop sold before comes back as that same phone.
  const lookup = digits(imei1).length === 15 ? digits(imei1) : serial.trim().length >= 5 ? serial.trim().toUpperCase() : "";
  useEffect(() => {
    setKnown(null);
    if (!lookup) return;
    const t = setTimeout(() => {
      api.get<Page<Unit>>(`/units${qs({ q: lookup, page_size: 5 })}`)
        .then((r) => setKnown(r.items.find((u) => [u.imei1, u.imei2, u.serial_number].includes(lookup)) ?? null))
        .catch(() => {});
    }, 300);
    return () => clearTimeout(t);
  }, [lookup]);
  const comesBack = known?.status === "sold";
  const clash = known && !comesBack;

  function pick(r: PosResult) {
    if (r.product.tracking_type !== "serialized") {
      setErrors({ product: `${r.product.name} isn't a phone/laptop product (counted by IMEI or serial).` });
      return;
    }
    setErrors({});
    setProduct(r.product);
  }

  function save(e: React.FormEvent) {
    e.preventDefault();
    const found: Record<string, string> = {};
    if (!comesBack) {
      if (isNew) {
        if (newName.trim().length < 2) found.newName = "Type the phone's name, e.g. “iPhone 11 64GB”.";
        if (newPrice.trim() === "" || money(newPrice) < 0) found.newPrice = "What will you sell it for?";
      } else if (!product) found.product = "Search for the phone model, or add it as a new product.";
    }
    const i1 = digits(imei1), i2 = digits(imei2);
    if (i1 && !/^\d{15}$/.test(i1)) found.imei1 = "15 digits (dial *#06# on the phone).";
    if (i2 && !/^\d{15}$/.test(i2)) found.imei2 = "15 digits.";
    if (!i1 && !serial.trim()) found.imei1 = "Enter the IMEI (or a serial number for a laptop).";
    const v = money(value);
    if (!(v > 0)) found.value = "Enter the value you agreed for it.";
    else if (total > 0 && v > total) found.value = `More than this sale (${naira(total)}). A swap can only pay for part or all of it.`;
    if (clash) found.imei1 = "This phone is already in your records (see below).";
    setErrors(found);
    if (Object.keys(found).length) return;
    const payload: TradeInIn = {
      ...(comesBack ? { product_id: known!.product_id }
        : isNew ? { new_product_name: newName.trim(), new_product_price: money(newPrice).toFixed(2) }
        : { product_id: product!.id }),
      imei1: i1 || null, imei2: i2 || null, serial_number: serial.trim() || null,
      condition, value: v.toFixed(2), notes: notes.trim() || null,
    };
    onSave({
      payload, value: v, identifier: i1 ? `IMEI ${i1}` : `S/N ${serial.trim().toUpperCase()}`,
      productName: comesBack ? known!.product_name : isNew ? newName.trim() : product!.name,
    });
  }

  return (
    <form onSubmit={save} className="space-y-4" noValidate>
      <p className="flex items-start gap-2 text-sm text-muted">
        <Repeat className="mt-0.5 size-4 shrink-0" aria-hidden />
        The customer's old phone pays for part of this sale. It goes into your stock at the value you agree,
        so when you sell it later the app shows your real profit.
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <TextInput label="IMEI 1" inputMode="numeric" autoFocus value={imei1} onChange={(e) => setImei1(e.target.value)}
          error={errors.imei1} hint="Dial *#06# on the phone" />
        <TextInput label="IMEI 2 (optional)" inputMode="numeric" value={imei2} onChange={(e) => setImei2(e.target.value)} error={errors.imei2} />
      </div>

      {comesBack && (
        <div className="flex items-start gap-2 rounded-md border border-pad/60 bg-pad/15 px-3 py-2 text-sm">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>You sold this phone before: <span className="font-medium">{known!.product_name}</span>. It goes back into your stock as that phone.</span>
        </div>
      )}
      {clash && <Notice tone="error">This phone is already in your records as {known!.product_name} ({known!.status.replaceAll("_", " ")}). Check the IMEI.</Notice>}

      {!comesBack && (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-medium">Which phone is it?</p>
            <button type="button" className="text-sm font-medium text-board underline"
              onClick={() => { setIsNew(!isNew); setErrors({}); }}>
              {isNew ? "Choose from my products" : "Not in my products? Add it"}
            </button>
          </div>
          {isNew ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <TextInput label="Product name" value={newName} onChange={(e) => setNewName(e.target.value)}
                error={errors.newName} placeholder="e.g. iPhone 11 64GB" />
              <TextInput label="You'll sell it for (₦)" inputMode="decimal" value={newPrice}
                onChange={(e) => setNewPrice(e.target.value)} error={errors.newPrice} />
            </div>
          ) : product ? (
            <div className="flex items-center justify-between rounded-md border border-line bg-paper px-3 py-2">
              <span className="font-medium">{product.name}</span>
              <button type="button" aria-label="Choose another product" onClick={() => setProduct(null)}
                className="rounded p-1 text-muted hover:text-ink"><X className="size-4" aria-hidden /></button>
            </div>
          ) : (
            <ScanBox onPick={pick} placeholder="Search your phone models, e.g. iPhone 11" />
          )}
          {errors.product && <p className="text-sm text-alert">{errors.product}</p>}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        <TextInput label="Serial (optional)" value={serial} onChange={(e) => setSerial(e.target.value)} />
        <SelectInput label="Condition" value={condition} onChange={(e) => setCondition(e.target.value as Condition)}>
          <option value="used">Used</option>
          <option value="refurbished">Refurbished</option>
          <option value="new">New</option>
        </SelectInput>
        <TextInput label="Value agreed (₦)" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} error={errors.value} />
      </div>
      <TextInput label="Notes (optional)" value={notes} maxLength={255} onChange={(e) => setNotes(e.target.value)}
        placeholder="e.g. cracked back glass, battery 82%" />
      {money(value) > 0 && total > 0 && (
        <p className="text-sm">Customer pays the rest: <span className="num font-medium">{naira(Math.max(0, total - money(value)))}</span></p>
      )}
      <div className="flex justify-end">
        <Button type="submit"><Repeat className="size-4" aria-hidden /> {initial ? "Update swap" : "Add swap"}</Button>
      </div>
    </form>
  );
}
