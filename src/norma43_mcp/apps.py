"""MCP Apps widgets: an interactive card rendered inside the chat by hosts that support MCP Apps.

Hosts with MCP Apps (Claude, ChatGPT, VS Code, Goose...) fetch `ui://<slug>/card.html` and show the tool result as a
card: credit balance with a refill bar, or a result summary with stats, issues, table previews and credits spent.
Other hosts ignore the metadata and keep using the JSON result, so nothing changes for them.

The card is self-contained (no network, no external assets) and talks to the host over the MCP Apps postMessage
protocol (`ui/initialize`, `ui/notifications/tool-result`, `tools/call` to refresh the balance, `ui/open-link`).
"""

from __future__ import annotations

import json
from typing import Any, Callable

MIME = "text/html;profile=mcp-app"

ICONS = {
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/><path d="M3.6 9h16.8M3.6 15h16.8"/>',
    "landmark": '<path d="M3.5 9 12 4l8.5 5"/><path d="M4.5 9.5h15"/><path d="M6.5 12v5M10.2 12v5M13.8 12v5M17.5 12v5"/><path d="M4.5 17.5h15M3.5 20.5h17"/>',
    "ledger": '<path d="M5 18.5V5.5A2 2 0 0 1 7 3.5h12v14H7a2 2 0 0 0-2 2 2 2 0 0 0 2 2h12v-4"/><path d="M9 7.5h6M9 11h4"/>',
    "table": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M9.5 9.5v10M9.5 14.5h11"/>',
    "shield": '<path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.3 7.5 9.5 4.3-1.2 7.5-4.9 7.5-9.5V6z"/><path d="m9 12 2.2 2.2L15.2 10"/>',
    "coins": '<circle cx="9" cy="9" r="5.5"/><path d="M14.6 8.2a5.5 5.5 0 1 1-6.4 6.4"/><path d="M9 7v4M7.5 8.5h3"/>',
    "refresh": '<path d="M19.5 12a7.5 7.5 0 0 1-13.1 5M4.5 12a7.5 7.5 0 0 1 13.1-5"/><path d="M17.8 3.5v3.7h-3.7M6.2 20.5v-3.7h3.7"/>',
}


def uri(slug: str) -> str:
    return f"ui://{slug}/card.html"


def ui_meta(slug: str) -> dict[str, Any]:
    """`_meta` for a tool whose result the card renders (current key plus the pre-1.0 flat key)."""
    return {"ui": {"resourceUri": uri(slug)}, "ui/resourceUri": uri(slug)}


def html(*, slug: str, name: str, icon: str, version: str, lang: str = "en", buy_url: str = "",
         labels: dict[str, str] | None = None) -> str:
    cfg = {"slug": slug, "name": name, "version": version, "lang": lang, "buy": buy_url or "",
           "labels": labels or {}, "icon": ICONS.get(icon, ICONS["coins"]), "coins": ICONS["coins"],
           "refresh": ICONS["refresh"]}
    # </script> can never appear inside the JSON: "<" is escaped.
    return _TEMPLATE.replace("__CFG__", json.dumps(cfg, ensure_ascii=False).replace("<", "\\u003c"))


def register(mcp: Any, *, slug: str, name: str, icon: str, version: str, lang: str = "en",
             buy_url: Callable[[], str | None] = lambda: None, labels: dict[str, str] | None = None) -> None:
    """Serve the card at ui://<slug>/card.html."""

    @mcp.resource(uri(slug), name=f"{slug}-card", title=f"{name} card", mime_type=MIME,
                  description="Interactive result card (MCP Apps).",
                  meta={"ui": {"prefersBorder": True}})
    def card() -> str:
        return html(slug=slug, name=name, icon=icon, version=version, lang=lang, buy_url=buy_url() or "",
                    labels=labels)


_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:var(--color-background-primary,#fff);--bg2:var(--color-background-secondary,#f6f6f3);
--fg:var(--color-text-primary,#16181d);--mut:var(--color-text-secondary,#6b6f76);--line:var(--color-border-primary,#e6e6e1);
--ok:#1f9d55;--bad:#d93f3f;--warn:#b7791f;--acc:#ccff00;--ink:#0b0d17;--bar:#4f7cff;
--r:var(--border-radius-lg,12px);--font:var(--font-sans,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif);
--mono:var(--font-mono,ui-monospace,"SF Mono",Menlo,Consolas,monospace);color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:var(--color-background-primary,#17181c);
--bg2:var(--color-background-secondary,#202227);--fg:var(--color-text-primary,#ececec);--mut:var(--color-text-secondary,#9a9ea6);
--line:var(--color-border-primary,#2e3036);--bar:#7c9cff;color-scheme:dark}}
:root[data-theme=dark]{--bg:var(--color-background-primary,#17181c);--bg2:var(--color-background-secondary,#202227);
--fg:var(--color-text-primary,#ececec);--mut:var(--color-text-secondary,#9a9ea6);--line:var(--color-border-primary,#2e3036);
--bar:#7c9cff;color-scheme:dark}
*{box-sizing:border-box}html,body{margin:0;background:transparent}
body{font:14px/1.45 var(--font);color:var(--fg);padding:2px}
.card{background:var(--bg);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;overflow:hidden}
.top{display:flex;align-items:center;gap:12px}
.tile{flex:none;width:44px;height:44px;border-radius:10px;background:var(--ink);border:1px solid var(--line);display:grid;place-items:center}
.tile svg{width:24px;height:24px;stroke:var(--acc);fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.ttl{min-width:0;flex:1}.big{font-size:26px;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.big small{font-size:14px;font-weight:500;color:var(--mut);margin-left:6px;letter-spacing:0}
.h{font-size:15px;font-weight:650}.sub{color:var(--mut);font-size:12.5px}
.badge{flex:none;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;border:1px solid}
.badge.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,transparent);background:color-mix(in srgb,var(--ok) 10%,transparent)}
.badge.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,transparent);background:color-mix(in srgb,var(--bad) 10%,transparent)}
hr{border:0;border-top:1px solid var(--line);margin:14px 0}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}
.stat{background:var(--bg2);border-radius:9px;padding:8px 10px;min-width:0}
.stat b{display:block;font-size:16px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat span{font-size:11.5px;color:var(--mut);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sec{margin-top:14px}.sec h4{margin:0 0 6px;font-size:12px;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
.issues{margin:0;padding:0;list-style:none}.issues li{padding:5px 0 5px 14px;position:relative;font-size:13px;border-bottom:1px dashed var(--line)}
.issues li:last-child{border:0}.issues li:before{content:"";position:absolute;left:0;top:12px;width:6px;height:6px;border-radius:50%;background:var(--warn)}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:9px}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{text-align:left;padding:6px 9px;white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis}
th{background:var(--bg2);font-weight:600;color:var(--mut)}tr+tr td{border-top:1px solid var(--line)}
.more{font-size:12px;color:var(--mut);margin-top:5px}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{background:var(--bg2);border-radius:999px;padding:2px 9px;font-size:12px}
pre{margin:0;background:var(--bg2);border-radius:9px;padding:10px;font:12px/1.5 var(--mono);white-space:pre-wrap;word-break:break-word;max-height:180px;overflow:hidden;
-webkit-mask-image:linear-gradient(#000 75%,transparent);mask-image:linear-gradient(#000 75%,transparent)}
.bars{display:grid;grid-template-columns:1fr;gap:6px}.row{display:flex;justify-content:space-between;font-size:12.5px}
.row b{font-weight:600}.row span{color:var(--mut)}
.track{height:6px;border-radius:99px;background:var(--bg2);overflow:hidden}.fill{height:100%;border-radius:99px;background:var(--bar);transition:width .5s}
.foot{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:14px;font-size:12.5px;color:var(--mut);flex-wrap:wrap}
.btns{display:flex;gap:8px}
button{font:inherit;font-size:12.5px;font-weight:600;border-radius:8px;padding:6px 12px;cursor:pointer;border:1px solid var(--line);background:var(--bg);color:var(--fg);display:inline-flex;gap:6px;align-items:center}
button.pri{background:var(--ink);color:var(--acc);border-color:var(--ink)}
button svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
button:disabled{opacity:.55;cursor:default}.spin svg{animation:sp 1s linear infinite}@keyframes sp{to{transform:rotate(360deg)}}
.err{color:var(--bad)}.skel{height:44px;border-radius:9px;background:var(--bg2);animation:pl 1.2s ease-in-out infinite}@keyframes pl{50%{opacity:.5}}
</style></head><body><div class="card" id="app"><div class="top"><div class="tile" id="tile"></div><div class="ttl"><div class="skel"></div></div></div></div>
<script>
const CFG=__CFG__;
const S={tool:null,data:null,error:null,ctx:{},busy:false};
const ES={credits:"créditos disponibles",used:"Usados",of:"de",reset:"Desde",buy:"Comprar créditos",refresh:"Actualizar",
charged:"créditos cobrados",left:"quedan",free:"Gratis",valid:"Válido",invalid:"Con errores",rows:"filas",more:"más",
balance:"Saldo",nocredit:"Sin créditos o licencia no válida.",loading:"Ejecutando…"};
const EN={credits:"credits available",used:"Used",of:"of",reset:"Since",buy:"Buy credits",refresh:"Refresh",
charged:"credits charged",left:"left",free:"Free",valid:"Valid",invalid:"Has errors",rows:"rows",more:"more",
balance:"Balance",nocredit:"Out of credits or invalid license.",loading:"Running…"};
const T=()=>(((S.ctx.locale||CFG.lang||"en")+"").toLowerCase().startsWith("es")?ES:EN);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const svg=p=>'<svg viewBox="0 0 24 24" aria-hidden="true">'+p+'</svg>';
const nf=n=>{try{return new Intl.NumberFormat(S.ctx.locale||undefined).format(n)}catch(e){return String(n)}};
const human=k=>{k=String(k).replace(/_/g," ");return k.charAt(0).toUpperCase()+k.slice(1)};
const label=t=>CFG.labels[t]||human(t||CFG.name);
let seq=0;const pend={};
const post=m=>window.parent.postMessage(Object.assign({jsonrpc:"2.0"},m),"*");
function req(method,params){const id=++seq;post({id,method,params});return new Promise((res,rej)=>{pend[id]={res,rej};
setTimeout(()=>{if(pend[id]){delete pend[id];rej(new Error("timeout"))}},20000)})}
const note=(method,params)=>post({method,params});
window.addEventListener("message",ev=>{const m=ev.data;if(!m||m.jsonrpc!=="2.0")return;
if(m.id!=null&&!m.method){const p=pend[m.id];if(p){delete pend[m.id];m.error?p.rej(m.error):p.res(m.result)}return}
switch(m.method){
case"ui/notifications/tool-input":case"ui/notifications/tool-input-partial":if(!S.data&&!S.error){S.pending=true;render()}break;
case"ui/notifications/tool-result":onResult(m.params||{});break;
case"ui/notifications/host-context-changed":ctx(m.params||{});render();break;
default:if(m.id!=null)post({id:m.id,result:{}})}});
function ctx(c){S.ctx=Object.assign({},S.ctx,c);const v=c.styles&&c.styles.variables;
if(v)for(const k in v)if(v[k])document.documentElement.style.setProperty(k,v[k]);
if(c.theme)document.documentElement.dataset.theme=c.theme;const tn=c.toolInfo&&c.toolInfo.tool&&c.toolInfo.tool.name;if(tn)S.tool=tn}
function unwrap(r){let d=r.structuredContent;
if(d&&typeof d==="object"&&!Array.isArray(d)&&Object.keys(d).length===1&&"result"in d)d=d.result;
if(d==null&&Array.isArray(r.content)){const t=r.content.find(c=>c&&c.type==="text");if(t){try{d=JSON.parse(t.text)}catch(e){d={message:t.text}}}}
return d}
function onResult(r){S.pending=false;const d=unwrap(r);
if(r.isError){const t=Array.isArray(r.content)&&r.content.find(c=>c&&c.type==="text");S.error=(t&&t.text)||(d&&d.message)||"Error";S.data=null}
else{S.data=d;S.error=null}render()}
const isBal=d=>d&&typeof d==="object"&&"balance"in d&&("total_allowance"in d||"consumed"in d||Object.keys(d).length<=3);
function head(title,sub,right){return'<div class="top"><div class="tile">'+svg(CFG.icon)+'</div><div class="ttl">'+title+
'<div class="sub">'+esc(sub)+'</div></div>'+(right||"")+'</div>'}
function balance(d){const t=T(),bal=+d.balance||0,tot=+d.total_allowance||0,used=+d.consumed||0;
const pct=tot?Math.max(0,Math.min(100,bal/tot*100)):0;
let h=head('<div class="big">'+nf(bal)+'<small>'+esc(t.credits)+'</small></div>',"KeyFlint · "+CFG.name);
if(tot){h+='<hr><div class="bars"><div class="row"><b>'+esc(t.balance)+'</b><span>'+nf(bal)+" / "+nf(tot)+'</span></div><div class="track"><div class="fill" style="width:'+pct.toFixed(1)+'%"></div></div>'+
'<div class="row"><span>'+esc(t.used)+" "+nf(used)+" "+esc(t.of)+" "+nf(tot)+'</span>'+(d.last_reset_at?'<span>'+esc(t.reset)+" "+esc(fmtDate(d.last_reset_at))+'</span>':"")+'</div></div>'}
h+='<div class="foot"><span></span><div class="btns">'+refreshBtn()+(CFG.buy?'<button class="pri" data-buy>'+esc(t.buy)+'</button>':"")+'</div></div>';return h}
const refreshBtn=()=>'<button data-refresh'+(S.busy?' disabled class="spin"':"")+'>'+svg(CFG.refresh)+esc(T().refresh)+'</button>';
function fmtDate(s){const d=new Date(s);if(isNaN(d))return s;try{return d.toLocaleDateString(S.ctx.locale||undefined,{day:"numeric",month:"short"})}catch(e){return s}}
const scalar=v=>v==null||["string","number","boolean"].includes(typeof v);
const ISSUE=/error|problem|warning|issue|invalid|flag|unmatched|missing|mismatch|fail/i;
const STATUS=["valid","ok","balanced","passed","is_valid","chain_valid"];
function fmt(v){if(typeof v==="boolean")return v?"✓":"✗";if(typeof v==="number")return nf(v);if(v==null||v==="")return"—";return String(v)}
function table(rows){const cols=[];for(const r of rows.slice(0,20))for(const k in r)if(scalar(r[k])&&!cols.includes(k)&&cols.length<6)cols.push(k);
if(!cols.length)return"";return'<div class="tw"><table><thead><tr>'+cols.map(c=>'<th>'+esc(human(c))+'</th>').join("")+'</tr></thead><tbody>'+
rows.slice(0,6).map(r=>'<tr>'+cols.map(c=>'<td title="'+esc(fmt(r[c]))+'">'+esc(fmt(r[c]))+'</td>').join("")+'</tr>').join("")+'</tbody></table></div>'+
(rows.length>6?'<div class="more">+'+nf(rows.length-6)+" "+esc(T().rows)+'</div>':"")}
function issueText(x){if(scalar(x))return fmt(x);const msg=x.message||x.reason||x.error||x.problem||x.detail;
const where=["row","line","index","column","field","record"].filter(k=>x[k]!=null).map(k=>human(k)+" "+fmt(x[k])).join(" · ");
return(msg?String(msg):Object.entries(x).filter(([k,v])=>scalar(v)).slice(0,4).map(([k,v])=>human(k)+": "+fmt(v)).join(" · "))+(where&&msg?"  ("+where+")":"")}
function result(d){const t=T();if(!d||typeof d!=="object")d={result:d};const bill=d.billing;
let status=null;for(const k of STATUS)if(typeof d[k]==="boolean"){status=d[k];break}
const stats=[],secs=[];
const walk=(o,pre,depth)=>{for(const[k,v]of Object.entries(o)){if(k==="billing"||STATUS.includes(k)&&!pre)continue;const name=human(k),full=pre?pre+" · "+human(k):name;
if(typeof v==="string"&&(v.length>60||v.includes("\n"))){if(!pre)secs.push('<div class="sec"><h4>'+esc(name)+' · '+nf(v.length)+' chars</h4><pre>'+esc(v.slice(0,1200))+'</pre></div>');}
else if(scalar(v)){if(v!==""&&v!=null)stats.push([name,v,full])}
else if(Array.isArray(v)){if(!v.length){if(ISSUE.test(k))stats.push([name,0]);continue}
if(ISSUE.test(k)){secs.push('<div class="sec"><h4>'+esc(name)+" ("+nf(v.length)+')</h4><ul class="issues">'+v.slice(0,6).map(x=>'<li>'+esc(issueText(x))+'</li>').join("")+'</ul>'+(v.length>6?'<div class="more">+'+nf(v.length-6)+" "+esc(t.more)+'</div>':"")+'</div>')}
else if(v.every(scalar))secs.push('<div class="sec"><h4>'+esc(name)+" ("+nf(v.length)+')</h4><div class="chips">'+v.slice(0,14).map(x=>'<span class="chip">'+esc(fmt(x))+'</span>').join("")+(v.length>14?'<span class="chip">+'+nf(v.length-14)+'</span>':"")+'</div></div>');
else if(v.every(x=>x&&typeof x==="object"&&!Array.isArray(x)))secs.push('<div class="sec"><h4>'+esc(name)+" ("+nf(v.length)+')</h4>'+table(v)+'</div>')}
else if(typeof v==="object"&&depth<1)walk(v,name,depth+1)}};
walk(d,"",0);
const badge=status==null?"":'<span class="badge '+(status?"ok":"bad")+'">'+esc(status?t.valid:t.invalid)+'</span>';
let h=head('<div class="h">'+esc(label(S.tool))+'</div>',CFG.name,badge);
if(stats.length)h+='<hr><div class="stats">'+stats.slice(0,8).map(([k,v,f])=>'<div class="stat"><b title="'+esc(fmt(v))+'">'+esc(fmt(v))+'</b><span title="'+esc(f||k)+'">'+esc(k)+'</span></div>').join("")+'</div>';
h+=secs.slice(0,5).join("");
if(bill&&typeof bill==="object"){const c=+bill.credits_charged||0,rem=bill.credits_remaining;
h+='<div class="foot"><span>'+svg(CFG.coins).replace("<svg",'<svg style="width:14px;height:14px;vertical-align:-2px;stroke:currentColor;fill:none;stroke-width:1.8"')+" "+
(c?"−"+nf(c)+" "+esc(t.charged):esc(t.free))+(rem!=null?" · "+nf(rem)+" "+esc(t.left):"")+'</span><div class="btns">'+(CFG.buy?'<button data-buy>'+esc(t.buy)+'</button>':"")+'</div></div>'}
return h}
function errorView(msg){const t=T(),pay=/credit|quota|licen|cr[eé]dito|402|paywall|exhaust/i.test(msg);
return head('<div class="h">'+esc(label(S.tool))+'</div>',CFG.name,'<span class="badge bad">Error</span>')+'<hr><div class="err">'+esc(msg.slice(0,600))+'</div>'+
(pay&&CFG.buy?'<div class="foot"><span>'+esc(t.nocredit)+'</span><div class="btns"><button class="pri" data-buy>'+esc(t.buy)+'</button></div></div>':"")}
function render(){const el=document.getElementById("app");
if(S.error)el.innerHTML=errorView(S.error);
else if(S.data!=null)el.innerHTML=(S.tool==="credits_balance"||(!S.tool&&isBal(S.data)))?balance(S.data):result(S.data);
else el.innerHTML=head('<div class="h">'+esc(label(S.tool))+'</div>',S.pending?T().loading:CFG.name);size()}
document.addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;
if(b.hasAttribute("data-buy")&&CFG.buy)req("ui/open-link",{url:CFG.buy}).catch(()=>{try{window.open(CFG.buy,"_blank","noopener")}catch(x){}});
if(b.hasAttribute("data-refresh")&&!S.busy){S.busy=true;render();req("tools/call",{name:"credits_balance",arguments:{}})
.then(r=>{S.busy=false;S.tool="credits_balance";onResult(r||{})}).catch(()=>{S.busy=false;render()})}});
let lastH=0;function size(){const h=Math.ceil(document.documentElement.getBoundingClientRect().height);if(h&&h!==lastH){lastH=h;note("ui/notifications/size-changed",{height:h})}}
try{new ResizeObserver(size).observe(document.body)}catch(e){}
document.getElementById("tile").innerHTML=svg(CFG.icon);
req("ui/initialize",{protocolVersion:"2026-01-26",appInfo:{name:CFG.slug+"-card",version:CFG.version},appCapabilities:{}})
.then(r=>{r=r||{};ctx(r.hostContext||{});note("ui/notifications/initialized",{});render()})
.catch(()=>{note("ui/notifications/initialized",{});render()});
</script></body></html>
"""
