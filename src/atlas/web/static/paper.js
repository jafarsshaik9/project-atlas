"use strict";
const node = (tag, text) => {const e = document.createElement(tag); e.textContent = text; return e;};
function table(target, headers, records) {
  const host = document.getElementById(target);
  if (!records.length) {host.append(node("p", "None recorded.")); return;}
  const t = document.createElement("table"), head = document.createElement("tr");
  headers.forEach(h => head.append(node("th", h))); t.append(head);
  records.forEach(record => {const row = document.createElement("tr"); record.forEach(v => row.append(node("td", v))); t.append(row);});
  host.append(t);
}
async function loadPaper() {
  const status = document.getElementById("paper-status");
  try {
    const response = await fetch("/api/paper", {credentials: "same-origin", cache: "no-store"});
    if (response.status === 401) {location.replace("/login"); return;}
    if (!response.ok) throw new Error("Unavailable");
    const p = await response.json();
    if (!p.configured) {status.textContent = "No paper account configured."; return;}
    status.textContent = p.status + " · " + p.as_of + " · Kill switch " + (p.kill_switch ? "ON" : "off");
    const summary = document.getElementById("paper-summary");
    [["Cash GBP", p.cash_gbp], ["Equity GBP", p.equity_gbp], ["Realised P/L GBP", p.realised_pnl_gbp],
     ["Fees GBP", p.fees_gbp], ["Reconciled", String(p.reconciled)]].forEach(([label, value]) => summary.append(node("dt", label), node("dd", value)));
    table("paper-positions", ["Instrument", "Quantity", "Saved value GBP"], p.positions.map(x => [x.instrument.instrument_id, x.quantity, x.mark_value_gbp]));
    table("paper-trades", ["Time UTC", "Side", "Quantity", "USD price", "Fees GBP", "Net realised GBP"], p.recent_simulated_trades.map(x => [x.timestamp, x.side, x.quantity, x.price_usd, x.fees_gbp, x.realised_pnl_gbp]));
  } catch (_) {status.textContent = "Paper account unavailable or failed reconciliation.";}
}
loadPaper();
