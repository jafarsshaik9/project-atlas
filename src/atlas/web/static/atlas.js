"use strict";
const $ = (id) => document.getElementById(id);
const state = {run:null, period:"all", tradeOffset:0, activityOffset:0, sort:"timestamp", direction:"desc", loading:false, charts:{}, runs:[]};
const money = (value) => value === null || value === undefined ? "N/A" : new Intl.NumberFormat("en-GB",{style:"currency",currency:"GBP",maximumFractionDigits:2}).format(Number(value));
const number = (value) => value === null || value === undefined ? "N/A" : new Intl.NumberFormat("en-GB",{maximumFractionDigits:6}).format(Number(value));
const utc = (value) => value ? new Intl.DateTimeFormat("en-GB",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit",second:"2-digit",timeZone:"UTC",hour12:false}).format(new Date(value))+" UTC" : "N/A";
const sign = (value) => value === null || value === undefined ? "" : Number(value)>0 ? "positive" : Number(value)<0 ? "negative" : "";
const el = (tag,text,className) => { const item=document.createElement(tag); if(text!==undefined) item.textContent=text; if(className) item.className=className; return item; };
async function api(path,query={}) {
  const params = new URLSearchParams(query);
  if(state.run && !params.has("run_id") && path!=="/api/runs") params.set("run_id",state.run);
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),8000);
  try {
    const response=await fetch(path+(params.size?"?"+params:""),{credentials:"same-origin",signal:controller.signal});
    if(response.status===401){location.replace("/login");throw new Error("Session expired.");}
    if(!response.ok) throw new Error("Research data is unavailable.");
    return await response.json();
  } finally {clearTimeout(timer);}
}
function connection(ok) {
  $("connection").replaceChildren(el("span","",ok?"dot":"dot amber"),document.createTextNode(ok?"History connected":"Connection unavailable"));
}
function metric(label,value,note="",valueClass="") {
  const card=el("article",undefined,"metric-card");
  card.append(el("h3",label),el("div",value,"metric-value "+valueClass),el("p",note));
  return card;
}
function renderSummary(envelope) {
  const s=envelope.data;
  $("as-of").textContent=envelope.updated_at?"AS OF "+utc(envelope.updated_at):"No persisted portfolio valuation";
  const cards=[
    ["Current equity",s.equity_gbp,"Selected synthetic run",null],
    ["Starting capital",s.starting_capital_gbp,"Independent virtual capital",null],
    ["Available cash",s.available_cash_gbp,"After active reservations",null],
    ["Invested market value",s.invested_value_gbp,"At persisted marks",null],
    ["Realised net P/L",s.realized_pnl_gbp,"Confirmed simulated exits","signed"],
    ["Unrealised P/L",s.unrealized_pnl_gbp,"Entry fees included; exit fees excluded","signed"],
    ["Total net P/L",s.total_pnl_gbp,"Equity minus starting capital","signed"],
    ["Total return",s.total_return_pct,"Research result, not an expectation","percent"],
    ["Today's P/L",s.today_pnl_gbp,s.today_reason||"Persisted UTC-day equity change","signed"],
    ["Reserved cash",s.reserved_cash_gbp,"Not available for a new intent",null],
    ["Open positions",s.open_positions,"Confirmed simulated exposure","count"],
    ["Completed trades",s.completed_trades,"Positions closed, not signals","count"]
  ];
  $("kpi-grid").replaceChildren(...cards.map(([label,value,note,kind])=>{
    const card=el("article",undefined,"kpi");
    card.append(el("div",label,"kpi-label"),el("div",kind==="count"?number(value):kind==="percent"?(value===null?"N/A":number(value)+"%"):money(value),"kpi-value "+(kind==="signed"||kind==="percent"?sign(value):"")),el("p",note,"kpi-note"));
    return card;
  }));
}
function plotPoints(points,key,negative=false) {
  const latest=new Map(); points.forEach(p=>latest.set(new Date(p.at).getTime(),p));
  const data=[];let previous=null;
  for(const [time,p] of latest){
    if(previous!==null && time-previous>600000) data.push({x:time,y:null});
    data.push({x:time,y:p[key]===null?null:Number(p[key])*(negative?-1:1)});
    previous=time;
  }
  return data;
}
function line(label,data,color,extra={}) { return {label,data,borderColor:color,backgroundColor:color+"14",borderWidth:2,pointRadius:data.length>60?0:2,pointHoverRadius:5,tension:0,spanGaps:false,fill:false,...extra}; }
function chart(id,datasets) {
  const options={responsive:true,maintainAspectRatio:false,animation:false,parsing:false,normalized:false,
    interaction:{mode:"nearest",intersect:false},plugins:{legend:{display:id!=="equity-chart",labels:{color:"#97a9c1",boxWidth:10,boxHeight:2,font:{size:9}}},
      tooltip:{backgroundColor:"#15283c",borderColor:"#36516a",borderWidth:1,padding:12,titleColor:"#edf4fc",bodyColor:"#bdccdd",
        callbacks:{title:items=>items.length?utc(items[0].parsed.x):"",label:item=>item.dataset.label+": "+money(item.parsed.y)}}},
    scales:{x:{type:"linear",grid:{display:false},ticks:{maxTicksLimit:5,color:"#879db7",font:{size:9},callback:value=>new Intl.DateTimeFormat("en-GB",{hour:"2-digit",minute:"2-digit",timeZone:"UTC",hour12:false}).format(new Date(value))},border:{display:false}},
      y:{grid:{color:"#26384b55"},ticks:{maxTicksLimit:5,color:"#879db7",font:{size:9},callback:value=>money(value)},border:{display:false}}}};
  if(state.charts[id]) {state.charts[id].data.datasets=datasets;state.charts[id].update("none");}
  else if(window.Chart) state.charts[id]=new Chart($(id),{type:"line",data:{datasets},options});
}
function renderCharts(envelope) {
  const data=envelope.data, points=data.points;
  $("equity-empty").hidden=points.length>0;
  document.querySelectorAll("[data-period]").forEach(button=>{
    button.disabled=!data.available_ranges.includes(button.dataset.period);
    button.classList.toggle("selected",button.dataset.period===state.period);
    button.title=button.disabled?"Insufficient persisted history for this period.":"";
  });
  $("chart-note").textContent=(data.truncated?"Latest "+points.length+" of "+data.total_points:points.length+" actual snapshots")+" · gaps are not interpolated · UTC";
  const baseline=plotPoints(points,"equity_gbp").map(point=>({x:point.x,y:point.y===null?null:Number(data.starting_capital_gbp)}));
  chart("equity-chart",[line("Portfolio equity",plotPoints(points,"equity_gbp"),"#57dfcc",{fill:true}),line("Starting capital",baseline,"#7890ad",{borderDash:[5,5],borderWidth:1,pointRadius:0})]);
  chart("pnl-chart",[line("Realised",plotPoints(points,"realized_pnl_gbp"),"#57dfcc"),line("Unrealised",plotPoints(points,"unrealized_pnl_gbp"),"#66c5f7"),line("Total",plotPoints(points,"total_pnl_gbp"),"#bdacff")]);
  chart("drawdown-chart",[line("Observed drawdown",plotPoints(points,"drawdown_gbp",true),"#ff8e9c",{fill:true})]);
}
function td(row,text,className) { const cell=el("td",text,className);row.append(cell);return cell; }
function renderPositions(envelope) {
  const positions=envelope.data;
  $("positions-empty").hidden=positions.length>0;
  $("positions-body").replaceChildren(...positions.map(p=>{
    const row=el("tr"); const instrument=td(row,p.instrument_id);instrument.append(el("small",p.venue+" · "+p.currency));
    td(row,p.direction,"positive");td(row,number(p.quantity));
    const average=td(row,number(p.average_entry_native)+" "+p.quote_unit), mark=td(row,number(p.mark_native)+" "+p.quote_unit);
    if(p.unavailable_reason){average.append(el("small",p.unavailable_reason));mark.append(el("small",p.unavailable_reason));}
    td(row,money(p.market_value_gbp));td(row,money(p.unrealized_pnl_gbp),sign(p.unrealized_pnl_gbp));td(row,money(p.planned_risk_gbp));
    const entry=td(row,utc(p.opened_at));entry.append(el("small",p.strategy));return row;
  }));
}
function appendActivity(container,items) {
  if(!items.length){container.replaceChildren(el("div","No matching activity in the selected run.","empty-state"));return;}
  container.replaceChildren(...items.map(event=>{
    const kind=event.event_type,p=event.payload;
    const severity=kind.includes("error")||kind.includes("failed")||kind.includes("breached")?"negative":kind.includes("rejected")?"warning":"";
    const row=el("article",undefined,"activity-item "+severity), marker=el("div",kind.includes("fill")?"↗":kind.includes("risk")?"◇":kind.includes("notification")?"✉":"·","event-marker");
    const content=el("div",undefined,"event-content"), title=el("div",undefined,"event-title");
    const timestamp=el("time",utc(event.event_at));timestamp.dateTime=event.event_at;
    title.append(el("span",kind.replaceAll("."," / ").replaceAll("_"," ")),timestamp);
    const description=p.side?(p.side.toUpperCase()+" · "+p.quantity+" @ "+p.price+" "+p.quote_unit+" · "+money(p.gross_gbp)):(p.reasons?.join(", ")||p.code||p.dataset||event.instrument_id||"Persisted authoritative research observation");
    content.append(title,el("p",description),el("div",event.event_id.slice(0,20)+" · SYNTHETIC","event-id"));row.append(marker,content);return row;
  }));
}
function renderRisk(envelope) {
  const r=envelope.data;
  $("risk-cards").replaceChildren(
    metric("Starting virtual capital",money(r.starting_capital_gbp),"Separate from the Practice account"),
    metric("Planned risk per trade",money(r.per_trade_budget_gbp),number(r.per_trade_pct)+"% · provisional research setting"),
    metric("Daily loss threshold",money(r.daily_threshold_gbp),number(r.daily_pct)+"% · not a guaranteed loss limit"),
    metric("Concurrent planned risk limit",money(r.concurrent_limit_gbp),number(r.concurrent_pct)+"% · independent risk engine"),
    metric("Planned open-position risk",money(r.planned_open_risk_gbp),"As of the selected persisted valuation"),
    metric("Research-day realised loss",money(r.daily_realized_loss_gbp),"UTC research day · gains do not erase a loss policy",Number(r.daily_realized_loss_gbp)>0?"negative":""),
    metric("Portfolio exposure",money(r.exposure_gbp),"Marked invested value; no borrowing"),
    metric("Kill-switch state",r.kill_switch===null?"N/A":r.kill_switch?"ACTIVATED":"Not activated","As of the selected run",r.kill_switch?"negative":""),
    metric("Remaining risk budget","N/A",r.remaining_budget_reason),
    metric("Margin used",money(r.margin_gbp),r.margin_reason),
    metric("Leverage",r.leverage,"Disabled; no short selling"),
    metric("Execution authority","DISABLED","Dashboard actions cannot authorize or send orders")
  );
  appendActivity($("risk-alerts"),r.alerts);
}
function statusRows(items){ return items.map(([key,value])=>{const row=el("div",undefined,"status-row");row.append(el("span",key),el("b",value));return row;}); }
function renderHealth(envelope) {
  const h=envelope.data;
  $("engine-state").textContent=h.engine;$("engine-state").className="engine-state "+(h.engine==="ERROR"?"negative":h.engine==="RUNNING"?"positive":"");
  $("bot-status").replaceChildren(...statusRows([["Environment","SYNTHETIC"],["Strategy",h.strategy||"N/A"],["Last run activity",utc(h.last_observed_activity)],["Last market scan","Not implemented"],["Data","Historical fixture"]]));
  const counts=h.notification_delivery.counts;
  $("health-cards").replaceChildren(
    metric("Dashboard",h.dashboard,"This HTTP process responded; it is not a trading worker","positive"),
    metric("Research engine",h.engine,h.active_heartbeat?"Active lease heartbeat: "+utc(h.active_heartbeat):"No active research heartbeat or continuous scan"),
    metric("Event database",h.database,"SQLite WAL · schema version "+h.database_schema+" · persisted history","positive"),
    metric("Last observed activity",utc(h.last_observed_activity),"Processing time, not the historical market timestamp"),
    metric("Last successful run",h.last_successful_run?h.last_successful_run.slice(0,14):"N/A","Each run is an independent virtual portfolio"),
    metric("Latest signal",h.latest_signal?h.latest_signal.instrument_id:"N/A","A signal is not a confirmed simulated execution"),
    metric("Notification configuration",h.notification_configuration,"Credentials are never exposed in this interface"),
    metric("Notification deliveries",(counts.SENT||0)+" sent",JSON.stringify(counts)+" · delivery not guaranteed"),
    metric("Latest system error",h.latest_error?h.latest_error.payload.code:"None recorded","Absence of errors does not prove a healthy worker"),
    metric("Last event",utc(h.last_event_at),"Event timestamp (UTC)"),
    metric("Data freshness","Historical",h.freshness),
    metric("Scanner","Not implemented",h.scanner_reason)
  );
}
async function loadTrades() {
  const limit=Number($("trade-limit").value);
  const query={limit,offset:state.tradeOffset,sort:state.sort,direction:state.direction,search:$("trade-search").value};
  if($("trade-side").value) query.side=$("trade-side").value;
  const result=(await api("/api/trades",query)).data;
  $("trades-empty").hidden=result.items.length>0;
  $("trades-body").replaceChildren(...result.items.map(event=>{
    const p=event.payload,row=el("tr");td(row,utc(event.event_at));
    const instrument=td(row,event.instrument_id);instrument.append(el("small",p.venue+" · "+event.strategy+" · "+event.run_id.slice(0,8)));
    const side=td(row,"");side.append(el("span",p.side.toUpperCase(),"side-tag "+p.side));side.append(el("small",p.partial?"Partial simulated fill":"Simulated confirmed"));
    td(row,number(p.quantity));td(row,number(p.price)+" "+p.quote_unit);td(row,money(p.gross_gbp));td(row,money(p.fees_gbp));td(row,money(p.realized_pnl_gbp),sign(p.realized_pnl_gbp));
    const action=el("button","View ↗");action.dataset.fill=event.event_id;td(row,"").append(action);return row;
  }));
  $("trade-count").textContent=result.total?Math.min(state.tradeOffset+1,result.total)+"–"+Math.min(state.tradeOffset+limit,result.total)+" of "+result.total+" confirmed fills":"0 confirmed fills";
  $("trade-prev").disabled=state.tradeOffset===0;$("trade-next").disabled=state.tradeOffset+limit>=result.total;
}
async function loadActivity() {
  const query={limit:30,offset:state.activityOffset};if($("activity-kind").value)query.kind=$("activity-kind").value;
  const result=await api("/api/activity",query);appendActivity($("activity-list"),result.items);
  $("activity-count").textContent=result.total+" persisted events";
  $("activity-prev").disabled=state.activityOffset===0;$("activity-next").disabled=state.activityOffset+30>=result.total;
}
async function refresh() {
  if(state.loading)return;state.loading=true;$("refresh").disabled=true;
  try{
    const runs=await api("/api/runs");state.runs=runs.items;
    if(!state.run && runs.items.length)state.run=runs.items[0].run_id;
    $("run-select").replaceChildren(...(runs.items.length?runs.items.map(run=>{const option=el("option",run.dataset+" · "+run.venue+" · "+run.run_id.slice(0,8));option.value=run.run_id;return option;}):[el("option","No research runs yet")]));
    $("run-select").value=state.run||"";$("run-select").disabled=!runs.items.length;$("empty-banner").hidden=runs.items.length>0;
    const [portfolio,history,positions,risk,health]=await Promise.all([api("/api/portfolio"),api("/api/equity",{period:state.period}),api("/api/positions"),api("/api/risk"),api("/api/health")]);
    renderSummary(portfolio);renderCharts(history);renderPositions(positions);renderRisk(risk);renderHealth(health);
    await Promise.all([loadTrades(),loadActivity()]);
    connection(true);$("error-banner").hidden=true;$("poll-status").textContent="Read-only views · refreshed "+new Intl.DateTimeFormat("en-GB",{timeStyle:"medium"}).format(new Date());
  }catch(error){connection(false);$("error-banner").textContent="Dashboard connection unavailable. Cached observations may be stale. Check the local server; no trading action has been taken.";$("error-banner").hidden=false;}
  finally{state.loading=false;$("refresh").disabled=false;}
}
function safely(action){Promise.resolve().then(action).catch(()=>{$("error-banner").textContent="This research view is unavailable. Refresh or check the local server.";$("error-banner").hidden=false;});}
function closeMenu(){$("sidebar").classList.remove("open");$("scrim").hidden=true;$("menu").setAttribute("aria-expanded","false");}
document.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>{
  const names={overview:"Research overview",positions:"Open positions",trades:"Trade history",risk:"Risk monitor",activity:"Bot activity",system:"System health"};
  document.querySelectorAll(".view").forEach(section=>section.hidden=section.id!=="view-"+button.dataset.view);
  document.querySelectorAll("[data-view]").forEach(item=>item.classList.toggle("active",item===button));
  $("page-title").textContent=names[button.dataset.view];$("breadcrumb-page").textContent=names[button.dataset.view].toUpperCase();closeMenu();
  Object.values(state.charts).forEach(item=>item.resize());
}));
$("menu").addEventListener("click",()=>{const open=!$("sidebar").classList.contains("open");$("sidebar").classList.toggle("open",open);$("scrim").hidden=!open;$("menu").setAttribute("aria-expanded",String(open));});
$("scrim").addEventListener("click",closeMenu);
$("refresh").addEventListener("click",refresh);
$("run-select").addEventListener("change",()=>{state.run=$("run-select").value;state.period="all";state.tradeOffset=state.activityOffset=0;refresh();});
document.querySelectorAll("[data-period]").forEach(button=>button.addEventListener("click",()=>{state.period=button.dataset.period;refresh();}));
let searchTimer;
$("trade-search").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.tradeOffset=0;safely(loadTrades);},250);});
["trade-side","trade-limit"].forEach(id=>$(id).addEventListener("change",()=>{state.tradeOffset=0;safely(loadTrades);}));
document.querySelectorAll("[data-sort]").forEach(button=>button.addEventListener("click",()=>{state.direction=state.sort===button.dataset.sort&&state.direction==="desc"?"asc":"desc";state.sort=button.dataset.sort;state.tradeOffset=0;safely(loadTrades);}));
$("trade-prev").addEventListener("click",()=>{state.tradeOffset=Math.max(0,state.tradeOffset-Number($("trade-limit").value));safely(loadTrades);});
$("trade-next").addEventListener("click",()=>{state.tradeOffset+=Number($("trade-limit").value);safely(loadTrades);});
$("activity-kind").addEventListener("change",()=>{state.activityOffset=0;safely(loadActivity);});
$("activity-prev").addEventListener("click",()=>{state.activityOffset=Math.max(0,state.activityOffset-30);safely(loadActivity);});
$("activity-next").addEventListener("click",()=>{state.activityOffset+=30;safely(loadActivity);});
$("trades-body").addEventListener("click",event=>{
  const id=event.target.closest("[data-fill]")?.dataset.fill;if(!id)return;
  safely(async()=>{const fill=await api("/api/trades/"+id);const p=fill.payload;
    const rows=[["Environment","SYNTHETIC"],["Status",p.execution_status],["Event ID",fill.event_id],["Research run",fill.run_id],["Strategy",fill.strategy],["Instrument",fill.instrument_id+" / "+p.venue],["Timestamp",utc(fill.event_at)],["Side",p.side.toUpperCase()],["Filled quantity",number(p.quantity)],["Fill price",number(p.price)+" "+p.quote_unit],["Native currency",p.currency],["Gross reporting value",money(p.gross_gbp)],["Actual simulated fees",money(p.fees_gbp)],["Fee components",p.components.map(([key,value])=>key+": "+money(value)).join("; ")],["Realised net P/L",money(p.realized_pnl_gbp)],["FX GBP per major unit",number(p.fx_gbp_per_unit)],["Partial fill",p.partial?"Yes":"No"]];
    $("detail-body").replaceChildren(...rows.flatMap(([label,value])=>[el("dt",label),el("dd",value??"N/A")]));
    $("trade-detail").showModal();
  });
});
$("close-detail").addEventListener("click",()=>$("trade-detail").close());
$("logout").addEventListener("click",async()=>{await fetch("/auth/logout",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}",credentials:"same-origin"});location.replace("/login");});
refresh();setInterval(()=>{if(!document.hidden)refresh();},5000);

