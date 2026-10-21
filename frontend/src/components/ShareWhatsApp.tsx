import { Check, Copy, MessageCircle } from "lucide-react";
import { useState } from "react";
import type { BusinessSettings, Sale } from "../lib/api";
import { phone } from "../lib/format";
import { receiptText, whatsappLink, whatsappNumber } from "../lib/whatsapp";
import { Button, Dialog, TextInput } from "./ui";

/** "Send on WhatsApp" button → small box: check the number, then WhatsApp opens with the receipt typed in. */
export function ShareWhatsApp({ sale, shop, compact = false }: { sale: Sale; shop: BusinessSettings; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [number, setNumber] = useState("");
  const [copied, setCopied] = useState(false);

  function start() {
    setNumber(sale.customer_phone ? phone(sale.customer_phone) : "");
    setCopied(false);
    setOpen(true);
  }

  const text = receiptText(sale, shop);
  const digits = whatsappNumber(number);
  const badNumber = number.trim() !== "" && !digits;

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <>
      {compact ? (
        <button type="button" onClick={start} className="inline-flex items-center gap-1 font-medium underline">
          <MessageCircle className="size-4" aria-hidden /> Send on WhatsApp
        </button>
      ) : (
        <Button variant="secondary" onClick={start} className="text-ok">
          <MessageCircle className="size-4" aria-hidden /> Send on WhatsApp
        </Button>
      )}

      <Dialog open={open} title={`Send receipt ${sale.receipt_no} on WhatsApp`} onClose={() => setOpen(false)}>
        {open && (
          <div className="space-y-4">
            <TextInput label="Customer's WhatsApp number" inputMode="tel" autoComplete="off"
              placeholder="0803 123 4567" value={number} onChange={(e) => setNumber(e.target.value)}
              error={badNumber ? "That doesn't look like a phone number. Example: 0803 123 4567" : undefined}
              hint={sale.customer_phone ? "From the customer's record. You can change it." : "Type the number, or leave it empty to pick a chat in WhatsApp."} />

            <div>
              <p className="mb-1.5 text-sm font-medium">The message</p>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-paper p-3 text-[13px] leading-snug font-sans">{text}</pre>
            </div>

            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={copy}>
                {copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}
                {copied ? "Copied" : "Copy text"}
              </Button>
              <a href={badNumber ? undefined : whatsappLink(text, digits)} target="_blank" rel="noopener noreferrer"
                aria-disabled={badNumber || undefined}
                onClick={(e) => { if (badNumber) e.preventDefault(); else setOpen(false); }}
                className={`inline-flex items-center gap-2 rounded-md px-3.5 h-10 text-sm font-medium text-white ${badNumber ? "bg-muted cursor-not-allowed" : "bg-[#1f8f4e] hover:bg-[#187a42]"}`}>
                <MessageCircle className="size-4" aria-hidden /> {digits ? "Open WhatsApp" : "Open WhatsApp and pick a chat"}
              </a>
            </div>
          </div>
        )}
      </Dialog>
    </>
  );
}
