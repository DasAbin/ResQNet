"use strict";
const $=q=>document.querySelector(q),NS="http://www.w3.org/2000/svg";
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let cur,messages,changes,runs={},tick=0,timer=null,selDecision=null;
const PC={2:"sos",1:"med",0:"ok"},PN={2:"SOS",1:"MEDICAL",0:"CHECK-IN"};
function el(n,a){const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);return e}
function nodePos(id){const n=cur.nodes.find(n=>n.id===id);return n?[n.x,n.y]:[0,0]}
function lkey(a,b){return[a,b].sort().join("|")}
function fillPreset(p){cur=p;messages=p.messages.map(m=>({...m}));changes=p.changes.map(c=>({...c}));
 $("#scen-name").textContent=p.name.toUpperCase();$("#scen-story").textContent=p.story;
 $("#scrub").max=p.ticks;msgTable();chgTable();runs={};tick=0;draw(0);
 $("#feed").innerHTML='<div class="row"><span class="t">T+0</span><p>Awaiting run.</p></div>';
 $("#metrics").innerHTML="";$("#verdict").textContent="";$("#cmp-table").innerHTML="";
 $("#why").innerHTML='<p class="note">Run the scenario. Each time a device chooses where to pass a message, this panel explains the choice.</p>';}
function opts(ids,v){return ids.map(i=>`<option ${i===v?"selected":""}>${i}</option>`).join("")}
function msgTable(){const ids=cur.nodes.map(n=>n.id);
 $("#msg-table").innerHTML=messages.map((m,i)=>`<tr><td>${esc(m.label||m.id)}</td><td><select data-m="${i}" data-k="source">${opts(ids,m.source)}</select></td><td>&rarr;</td><td><select data-m="${i}" data-k="destination">${opts(ids,m.destination)}</select></td><td><select data-m="${i}" data-k="priority">${[0,1,2].map(p=>`<option value="${p}" ${p===m.priority?"selected":""}>${PN[p]}</option>`).join("")}</select></td><td>T+<input data-m="${i}" data-k="created" type="number" min="0" max="${cur.ticks}" value="${m.created}"></td><td><button class="ghost small" data-del-m="${i}">x</button></td></tr>`).join("")}
function chgTable(){const ks=cur.links.map(l=>lkey(l.a,l.b));
 $("#chg-table").innerHTML=changes.map((c,i)=>`<tr><td><select data-c="${i}" data-k="link">${cur.links.map(l=>`<option value="${lkey(l.a,l.b)}" ${lkey(l.a,l.b)===lkey(c.a,c.b)?"selected":""}>${l.a} - ${l.b}</option>`).join("")}</select></td><td>T+<input data-c="${i}" data-k="tick" type="number" min="1" max="${cur.ticks}" value="${c.tick}"></td><td><select data-c="${i}" data-k="active"><option value="1" ${c.active?"selected":""}>comes UP</option><option value="0" ${c.active?"":"selected"}>goes DOWN</option></select></td><td><button class="ghost small" data-del-c="${i}">x</button></td></tr>`).join("")}
function readTables(){document.querySelectorAll("[data-m]").forEach(i=>{const m=messages[+i.dataset.m],k=i.dataset.k;
 if(k==="priority"||k==="created")m[k]=+i.value;else m[k]=i.value});
 document.querySelectorAll("[data-c]").forEach(i=>{const c=changes[+i.dataset.c],k=i.dataset.k;
 if(k==="link"){const[a,b]=i.value.split("|");c.a=a;c.b=b}else if(k==="tick")c.tick=+i.value;else c.active=i.value==="1"})}
async function post(body){const r=await fetch("/v1/simulations",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 if(!r.ok)throw Error("API "+r.status+": "+(await r.text()).slice(0,160));return r.json()}
async function run(){stop();readTables();$("#run").disabled=true;
 try{runs.adaptive=await post(scenarioFrom(cur,messages,changes,"adaptive"));
  if($("#compare").checked){
   runs.compare={};
   for(const p of["direct_only","snapshot_shortest","epidemic","spray_wait","prophet","adaptive_static"])
    runs.compare[p]=await post(scenarioFrom(cur,messages,changes,p));
  }else delete runs.compare;
  selDecision=null;tick=0;$("#feed").innerHTML="";draw(0);renderModel();play();
 }catch(e){$("#verdict").textContent="Could not run: "+e.message}
 $("#run").disabled=false}
function stop(){clearInterval(timer);timer=null;$("#play").innerHTML="&#9654; PLAY"}
function play(){if(timer){stop();return}if(!runs.adaptive)return;
 if(tick>=cur.ticks){tick=0;draw(0)}
 timer=setInterval(()=>{if(tick>=cur.ticks){stop();verdict();return}go(tick+1)},800);
 $("#play").innerHTML="&#10073;&#10073; PAUSE"}
function go(t){const prev=tick;tick=t;$("#scrub").value=t;draw(t,prev);log(t)}
function log(t){const f=$("#feed");
 for(const e of runs.adaptive.events.filter(e=>e.tick===t)){
  const d=document.createElement("div");d.className="row "+e.type;
  if(e.type==="decision"){const tot=e.components.total;
   d.innerHTML=`<span class="t">T+${e.tick}</span><p>${esc(e.source)} chose ${esc(e.chosen)} &mdash; score ${typeof tot==="number"?tot.toFixed(2):"?"} (tap to inspect)</p>`;
   d.onclick=()=>{selDecision=e;renderWhy(e)}}
  else d.innerHTML=`<span class="t">T+${e.tick}</span><p>${describe(e)}</p>`;
  f.append(d)}f.scrollTop=f.scrollHeight}
function describe(e){const lbl=id=>{const m=messages.find(m=>m.id===id);return m?esc(m.label||id):esc(id)};
 switch(e.type){case"created":return`${lbl(e.id)} &mdash; raised at ${e.node}`;
  case"forward":return`${esc(e.id)} passed ${e.source} &rarr; ${e.destination}`;
  case"lost":return`transfer ${e.source} &rarr; ${e.destination} FAILED &mdash; message stays with ${e.source}`;
  case"link_changed":return`link ${e.a} &ndash; ${e.b} ${e.active?"is UP":"is DOWN"}`;
  case"delivered":return`${lbl(e.id)} &mdash; ARRIVED at ${e.node}`;
  case"expired":return`${esc(e.id)} expired undelivered`;case"cleared":return`${esc(e.id)} copy cleared at ${e.node}`;
  default:return esc(e.type)}}
function draw(t,prev){const svg=$("#map");svg.innerHTML="";$("#tickbox").textContent=`T+${t} / ${cur.ticks}`;
 if(!runs.adaptive){drawStatic(svg);return}
 const st=stateAt(runs.adaptive.events,t,scenarioFrom(cur,messages,changes,"adaptive").links);
 for(const l of cur.links){const[x1,y1]=nodePos(l.a),[x2,y2]=nodePos(l.b),on=st.up[lkey(l.a,l.b)];
  svg.append(el("line",{x1,y1,x2,y2,class:"lk "+(on?"on":"off")}));
  if(l.why){const tx=el("text",{x:(x1+x2)/2,y:(y1+y2)/2-8,class:"why-lbl","text-anchor":"middle"});tx.textContent=l.why+(on?"":" (down)");svg.append(tx)}}
 const counts={};for(const id in st.at)if(!st.delivered[id]){const n=st.at[id];counts[n]=(counts[n]||0)+1}
 for(const n of cur.nodes){const g=el("g",{class:"nd"});
  g.append(el("rect",{x:n.x-48,y:n.y-24,width:96,height:48,rx:4}));
  const t1=el("text",{x:n.x,y:n.y-4,"text-anchor":"middle",class:"nid"});t1.textContent=n.id;g.append(t1);
  const t2=el("text",{x:n.x,y:n.y+12,"text-anchor":"middle",class:"nrole"});t2.textContent=n.role;g.append(t2);
  if(counts[n.id]){g.append(el("rect",{x:n.x+36,y:n.y-38,width:26,height:16,rx:3,class:"badge"}));
   const tb=el("text",{x:n.x+49,y:n.y-26,"text-anchor":"middle",class:"badge-t"});tb.textContent=counts[n.id];g.append(tb)}
  svg.append(g)}
 for(const id in st.at){const m=messages.find(m=>m.id===id);if(!m)continue;const n=st.at[id],[x,y]=nodePos(n);
  const g=el("g",{class:"pk "+PC[m.priority]});g.append(el("rect",{x:x-10,y:y+30,width:20,height:14,rx:2}));
  const tt=el("text",{x,y:y+41,"text-anchor":"middle",class:"pk-t"});tt.textContent=m.priority===2?"!":"·";g.append(tt);svg.append(g);
  if(st.delivered[id]){const c=el("text",{x,y:y+58,"text-anchor":"middle",class:"done"});c.textContent="ARRIVED";svg.append(c)}}
 if(prev!==undefined&&prev<t){for(const e of runs.adaptive.events.filter(e=>e.tick===t&&e.type==="forward"))
  animate(svg,nodePos(e.source),nodePos(e.destination),PC[(messages.find(m=>m.id===e.id)||{}).priority||0])}
 const d=runs.adaptive.events.filter(e=>e.type==="decision"&&e.tick<=t).pop();
 if(d&&(selDecision===null||selDecision===d)){renderWhy(d);selDecision=d}}
function drawStatic(svg){for(const l of cur.links){const[x1,y1]=nodePos(l.a),[x2,y2]=nodePos(l.b);
  svg.append(el("line",{x1,y1,x2,y2,class:"lk "+(l.active!==false?"on":"off")}))}
 for(const n of cur.nodes){const g=el("g",{class:"nd"});g.append(el("rect",{x:n.x-48,y:n.y-24,width:96,height:48,rx:4}));
  const t1=el("text",{x:n.x,y:n.y-4,"text-anchor":"middle",class:"nid"});t1.textContent=n.id;g.append(t1);
  const t2=el("text",{x:n.x,y:n.y+12,"text-anchor":"middle",class:"nrole"});t2.textContent=n.role;g.append(t2);svg.append(g)}}
function animate(svg,[x1,y1],[x2,y2],cls){const g=el("g",{class:"pk "+cls}),r=el("rect",{width:20,height:14,rx:2,x:-10,y:-7});g.append(r);svg.append(g);
 const t0=performance.now();(function step(){const k=Math.min(1,(performance.now()-t0)/700);
  g.setAttribute("transform",`translate(${x1+(x2-x1)*k},${y1+(y2-y1)*k})`);
  if(k<1&&timer)requestAnimationFrame(step);else g.remove()})()}
let labTimer=null;
function labNodes(){const lines=$("#lab-nodes").value.split("\n").map(s=>s.trim()).filter(Boolean);
 const nodes=[];for(const ln of lines){const i=ln.indexOf("=");if(i<1)continue;nodes.push({id:ln.slice(0,i).trim(),db:ln.slice(i+1).trim()})}
 return nodes}
async function labPost(url,body){const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 const j=await r.json().catch(()=>({}));if(!r.ok)throw Error(j.detail||("API "+r.status));return j}
async function labSend(){const nodes=labNodes(),from=$("#lab-from").value,n=nodes.find(x=>x.id===from);
 if(!n){alert("Pick a FROM node from the list above");return}
 try{const j=await labPost("/v1/lab/send",{id:n.id,db:n.db,to:$("#lab-to").value.trim(),body:$("#lab-body").value.trim()||"synthetic report",priority:+$("#lab-pri").value});
  $("#lab-body").value="";labObserve();}catch(e){alert("Send failed: "+e.message)}}
async function labAct(btn){const{node,db,req,act}=btn.dataset;
 try{await labPost("/v1/lab/"+act,{id:node,db,request:req});labObserve();}catch(e){alert(act+" failed: "+e.message)}}
async function labObserve(){const out=$("#lab-out");
 const nodes=labNodes();
 if(!nodes.length){out.innerHTML='<p class="note">List at least one node as ID=store-path.</p>';return}
 let r;try{r=await fetch("/v1/lab/observe",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({nodes})});}
 catch(e){out.innerHTML=`<p class="note">Observe failed: ${esc(e.message)}</p>`;return}
 if(!r.ok){out.innerHTML=`<p class="note">Observe failed: API ${r.status}</p>`;return}
 renderLab(await r.json())}
function labRow(e){const when=new Date(e.ts*1000).toLocaleTimeString();
 const dir=e.direction==="in"?"&#8672; IN from":"OUT to &#8680;";
 let basis="";if(e.detail){try{const d=JSON.parse(e.detail);
  if(typeof d.score==="number")basis=` <span class="basis">score ${d.score} &middot; p_success ${d.p_success} &middot; load ${d.peer_load}</span>`;
  basis+=d.basis?` <span class="basis">${esc(d.basis)}</span>`:""}catch(_){}}
 return `<div class="trow"><span class="t">${when}</span><span>${dir} ${esc(e.peer)}</span><span class="mono">${esc(e.message_id)}</span><span class="res ${esc(e.result)}">${esc(e.result).toUpperCase()}</span>${basis}</div>`}
function renderLab(d){const out=$("#lab-out");
 const SYS=["rcpt-","clm-","res-"],isSys=id=>SYS.some(p=>id.startsWith(p));
 const everywhere=new Set();d.nodes.forEach(n=>{if(n.ok)(n.delivered_recent||[]).forEach(m=>everywhere.add(m.id))});
 const life=id=>({received:everywhere.has("rcpt-"+id),claimed:everywhere.has("clm-"+id),resolved:everywhere.has("res-"+id)});
 const chips=id=>{const l=life(id);return [l.received&&"RECEIVED",l.claimed&&"CLAIMED",l.resolved&&"RESOLVED"].filter(Boolean).map(c=>`<span class="res delivered">${c}</span>`).join(" ")};
 out.innerHTML=`<p class="note">Observed at ${new Date(d.observed_at*1000).toLocaleTimeString()} &mdash; ${esc(d.warning)}</p>`+
  d.nodes.map(n=>{if(!n.ok)return `<div class="lcard bad"><h4>${esc(n.id)} <span class="mono">${esc(n.db)}</span></h4><p class="note">UNAVAILABLE: ${esc(n.error)}</p></div>`;
   const pend=n.pending.map(m=>`<tr><td>${esc(m.id)}</td><td>${esc(m.source)}&rarr;${esc(m.destination)}</td><td>${PN[m.priority]||m.priority}</td><td>${esc(m.body_preview)}</td></tr>`).join("");
   const del=n.delivered_recent.map(m=>{const sys=isSys(m.id);const l=sys?null:life(m.id);
    const acts=sys?"":` <button class="ghost small" data-act="claim" data-node="${esc(n.id)}" data-db="${esc(n.db)}" data-req="${esc(m.id)}" ${l.claimed?"disabled":""}>CLAIM</button> <button class="ghost small" data-act="resolve" data-node="${esc(n.id)}" data-db="${esc(n.db)}" data-req="${esc(m.id)}" ${l.resolved?"disabled":""}>RESOLVE</button>`;
    return `<tr><td>${esc(m.id)}</td><td>${esc(m.source)}&rarr;${esc(m.destination)}</td><td>${new Date(m.delivered_at*1000).toLocaleTimeString()}</td><td>${sys?"":chips(m.id)}${acts}</td></tr>`}).join("");
   const trs=n.transfers.map(labRow).join("");
   return `<div class="lcard"><h4>${esc(n.id)} <span class="mono">${esc(n.db)}</span></h4>
    <div class="counts mono"><span>PENDING <b>${n.pending_count}</b></span><span>DELIVERED <b>${n.delivered_count}</b></span></div>
    ${pend?`<h5>CARRYING NOW</h5><table class="mono">${pend}</table>`:""}
    ${del?`<h5>ARRIVED HERE</h5><table class="mono">${del}</table>`:""}
    ${trs?`<h5>TRANSFER LOG (newest first)</h5><div class="tlog">${trs}</div>`:""}
   </div>`}).join("")}
function boot(){const sel=$("#preset");sel.innerHTML=PRESETS.map((p,i)=>`<option value="${i}">${p.name}</option>`).join("");
 sel.onchange=()=>fillPreset(PRESETS[+sel.value]);fillPreset(PRESETS[0]);
 document.body.addEventListener("change",e=>{if(e.target.dataset.m!==undefined||e.target.dataset.c!==undefined)readTables()});
 document.body.addEventListener("click",e=>{const d=e.target.dataset;
  if(d.delM!==undefined){messages.splice(+d.delM,1);msgTable()}
  if(d.delC!==undefined){changes.splice(+d.delC,1);chgTable()}});
 $("#add-msg").onclick=()=>{const ids=cur.nodes.map(n=>n.id);messages.push({id:"msg-"+(messages.length+1)+"-"+Math.floor(Math.random()*1e4),source:ids[0],destination:ids[ids.length-1],priority:1,created:0,ttl:50,label:""});msgTable()};
 $("#add-chg").onclick=()=>{const l=cur.links[0];changes.push({tick:1,a:l.a,b:l.b,active:true});chgTable()};
 $("#run").onclick=run;$("#play").onclick=play;$("#back").onclick=()=>{stop();if(runs.adaptive&&tick>0)go(tick-1)};
 $("#lab-observe").onclick=labObserve;
 $("#lab-send").onclick=labSend;
 $("#lab-nodes").addEventListener("input",()=>{const ns=labNodes(),sel=$("#lab-from");sel.innerHTML=ns.map(n=>`<option>${esc(n.id)}</option>`).join("")});
 $("#lab-from").innerHTML=labNodes().map(n=>`<option>${esc(n.id)}</option>`).join("");
 document.body.addEventListener("click",e=>{if(e.target.dataset.act)labAct(e.target)});
 $("#lab-auto").onchange=e=>{clearInterval(labTimer);labTimer=e.target.checked?setInterval(labObserve,3000):null;if(labTimer)labObserve()};
 labObserve();  // show current node state on load; unavailable stores say so
 $("#fwd").onclick=()=>{stop();if(runs.adaptive&&tick<cur.ticks){go(tick+1);if(tick>=cur.ticks)verdict()}};
 $("#scrub").oninput=e=>{stop();if(runs.adaptive){tick=+e.target.value;$("#feed").innerHTML="";for(let t=0;t<=tick;t++)log(t);draw(tick);if(tick>=cur.ticks)verdict()}};
 const q=new URLSearchParams(location.search);
 if(!q.has("auto")&&!q.has("still"))run();  // demonstrate the simulation on first load
 if(q.has("auto")){if(q.has("preset")){sel.value=q.get("preset");fillPreset(PRESETS[+sel.value])}
  if(q.has("compare"))$("#compare").checked=true;
  run().then(()=>{stop();const t=+(q.get("tick")||cur.ticks);tick=t;$("#feed").innerHTML="";for(let i=0;i<=t;i++)log(i);draw(t);verdict()})}}
boot();
