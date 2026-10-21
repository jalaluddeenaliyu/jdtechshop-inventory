import type { BusinessSettings, Sale } from "./api";
import { dateOnly, dateTime, naira } from "./format";

/**
 * A phone number as WhatsApp wants it: country code + number, digits only.
 * Nigerian numbers ("0803 123 4567", "803…", "+234…") become "2348031234567".
 * Returns null when it can't be a real phone number.
 */
export function whatsappNumber(value: string | null | undefined): string | null {
  if (!value) return null;
  const international = value.trim().startsWith("+");
  let digits = value.replace(/\D/g, "");
  if (digits.startsWith("00")) digits = digits.slice(2);
  else if (!international) {
    if (digits.length === 11 && digits.startsWith("0")) digits = `234${digits.slice(1)}`;
    else if (digits.length === 10 && /^[789]/.test(digits)) digits = `234${digits}`;
  }
  if (digits.startsWith("2340")) digits = `234${digits.slice(4)}`; // "+234 0803…"
  return digits.length >= 10 && digits.length <= 15 ? digits : null;
}

/** The receipt as a WhatsApp message (*bold* and _italic_ are WhatsApp formatting). */
export function receiptText(sale: Sale, shop: BusinessSettings): string {
  const out: string[] = [];
  const line = "--------------------------";
  out.push(`*${shop.business_name}*`);
  if (shop.slogan) out.push(`_${shop.slogan}_`);
  if (shop.address) out.push(shop.address);
  if (shop.phone) out.push(`Tel: ${shop.phone}`);
  out.push(line);
  out.push(`Receipt: *${sale.receipt_no}*`);
  out.push(`Date: ${dateTime(sale.sold_at)}`);
  if (!sale.is_walk_in) out.push(`Customer: ${sale.customer_name}`);
  out.push(`Served by: ${sale.sold_by_name}`);
  if (sale.status === "reversed") out.push("*THIS SALE WAS CANCELLED (REVERSED)*");
  out.push(line);

  for (const l of sale.lines ?? []) {
    out.push(`*${l.product_name}*`);
    out.push(`${l.quantity} × ${naira(l.unit_price)} = ${naira(l.line_total)}`);
    const ids = [l.imei1 && `IMEI ${l.imei1}`, l.imei2 && `IMEI ${l.imei2}`, l.serial_number && `S/N ${l.serial_number}`]
      .filter(Boolean).join(" · ");
    if (ids) out.push(ids);
    if (l.condition && l.condition !== "new") out.push(`Condition: ${l.condition}`);
    if (Number(l.discount) > 0) out.push(`Discount −${naira(l.discount)}`);
    if (l.returned_quantity > 0) out.push(`Returned: ${l.returned_quantity}`);
  }
  out.push(line);

  if (Number(sale.discount_total) > 0) {
    out.push(`Subtotal: ${naira(sale.subtotal)}`);
    out.push(`Discount: −${naira(sale.discount_total)}`);
  }
  out.push(`*TOTAL: ${naira(sale.total)}*`);
  for (const p of sale.payments ?? []) out.push(`Paid (${p.method_name}): ${naira(p.amount)}`);
  if (Number(sale.returned_total) > 0) out.push(`Items returned: −${naira(sale.returned_total)}`);
  if (Number(sale.reversal_refund) > 0) out.push(`Refunded: −${naira(sale.reversal_refund)}`);
  if (Number(sale.balance_due) > 0) {
    out.push(`*Balance owed: ${naira(sale.balance_due)}*`);
    if (sale.due_date) out.push(`Due by: ${dateOnly(sale.due_date)}`);
  } else if (sale.status === "completed") {
    out.push("Fully paid ✅");
  }
  if (shop.receipt_footer) {
    out.push(line);
    out.push(shop.receipt_footer);
  }
  return out.join("\n");
}

/** Link that opens WhatsApp with the text typed in. No number → WhatsApp asks which chat. */
export function whatsappLink(text: string, number: string | null): string {
  return `https://wa.me/${number ?? ""}?text=${encodeURIComponent(text)}`;
}
