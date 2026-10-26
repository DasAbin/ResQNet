function verdict(){const m=runs.adaptive.metrics;
 $("#metrics").innerHTML=[["DELIVERED",`${m.delivered}/${m.created}`],["RATIO",Math.round(m.delivery_ratio*100)+"%"],["MEAN DELAY",m.mean_delay_ticks===null?"&mdash;":m.mean_delay_ticks.toFixed(1)+" ticks"],["ATTEMPTS",m.attempts],["FAILED",m.failed_attempts]].map(([k,v])=>`<div><b>${v}</b><span>${k}</span></div>`).join("");
 $("#verdict").textContent=m.delivered===m.created?"All messages reached their destination despite the outage.":"Some messages were still waiting or expired - extend the run or change the plan.";
 if(runs.compare){const all={adaptive:runs.adaptive.metrics,...Object.fromEntries(Object.entries(runs.compare).map(([k,v])=>[k,v.metrics]))};
  const names=Object.keys(all),fmt=v=>v===null||v===undefined?"&mdash;":(+v).toFixed(1);
  const row=(label,f)=>`<tr><td>${label}</td>${names.map(n=>`<td>${f(all[n])}</td>`).join("")}</tr>`;
  $("#cmp-table").innerHTML=`<table class="mono cmp-t"><tr><th></th>${names.map(n=>`<th>${n.replace("_"," ").toUpperCase()}</th>`).join("")}</tr>`+
   row("delivered",x=>`${x.delivered}/${x.created}`)+
   row("mean delay",x=>fmt(x.mean_delay_ticks))+
   row("attempts / delivered",x=>fmt(x.attempts_per_delivered))+
   row("copies / delivered",x=>fmt(x.copies_per_delivered))+
   row("failed attempts",x=>x.failed_attempts)+
   `</table><p class="note">Same scenario, same seed, one run each - a demonstration, not the evaluation. The repeatable multi-seed table is in EXPERIMENTS.md (python3 experiments.py). Snapshot-shortest needs a currently-complete path, so it stalls whenever contacts are scheduled.</p>`}}
function bar(v,max){const w=Math.min(100,Math.abs(v)/max*100);return`<div class="barrow"><div class="bar ${v<0?"neg":"pos"}" style="width:${w}%"></div></div>`}
function renderWhy(d){const c=d.components,w=$("#why");
 if(d.policy==="snapshot_shortest"){w.innerHTML=`<p class="mono big">T+${d.tick} &middot; ${esc(d.id)} at ${esc(d.source)} &rarr; ${esc(d.chosen)}</p><p class="note">Baseline rule: pass to the neighbour with the fewest active hops left (${c.onward_hops}). It does not learn from past failures.</p>`;return}
 const rows=[["PROGRESS TOWARD TARGET",2*c.progress,`${c.progress} hop(s) closer`],
  [`PRIORITY x LINK SUCCESS`,`${c.urgency*(2*c.p_success-1)}`,`${c.urgency} x (2 x ${(c.p_success*100).toFixed(1)}% - 1), so a 50% link would score 0`],
  ["CONGESTION",-0.7*c.congestion,`${(c.congestion*100).toFixed(0)}% busy`],
  ["LATENCY",-c.latency_ms/1000,`${c.latency_ms} ms`],
  ["RELAY QUEUE",-0.5*c.peer_load,`buffer ${(c.peer_load*100).toFixed(0)}% full`]];
 const max=Math.max(1,...rows.map(r=>Math.abs(r[1])));
 w.innerHTML=`<p class="mono big">T+${d.tick} &middot; ${esc(d.id)} at ${esc(d.source)} &rarr; <b>${esc(d.chosen)}</b></p>`
  +rows.map(([k,v,note])=>`<div class="wrow"><span class="mono">${k}</span><span class="mono val">${(+v).toFixed(2)}</span></div>${bar(v,max)}<p class="note">${note}</p>`).join("")
  +`<div class="wrow total mono"><span>TOTAL SCORE</span><span class="val">${c.total.toFixed(2)}</span></div>`
  +(d.alternatives.length?`<h4>PASSED OVER</h4>`+d.alternatives.map(a=>`<p class="note mono">${esc(a.peer)}: ${a.total.toFixed(2)} (P ${(a.p_success*100).toFixed(0)}%)</p>`).join(""):"")
  +`<p class="note">Every hop choice during playback appears here. Numbers come from the simulator, not field radios.</p>`;
}
function renderModel(){const m=runs.adaptive.metrics,w=$("#model");
 const max=Math.max(0.5,...m.predictor_weights.map(Math.abs));
 const rows=m.predictor_features.map((f,i)=>`<div class="wrow"><span class="mono">${f}</span><span class="mono val">${m.predictor_weights[i].toFixed(2)}</span></div>${bar(m.predictor_weights[i],max)}`).join("");
 const sig=z=>1/(1+Math.exp(-Math.max(-30,Math.min(30,z))));
 const links=cur.links.map(l=>{const f=[1,l.reliability??1,l.loss??0,l.congestion??0,1,1/(1+(l.latency_ms??20)/100)];
  const p=sig(f.reduce((s,x,i)=>s+x*m.predictor_weights[i],0));return`<p class="note mono">P(success) ${l.a} &ndash; ${l.b}: ${(p*100).toFixed(0)}%</p>`}).join("");
 w.innerHTML=`<p class="note">Estimator weights after ${m.predictor_samples} observed transfer attempt(s). Weights start from built-in priors, so after only a few attempts most of each value is the prior, not learning:</p>${rows}<h4>PER-LINK ESTIMATE NOW</h4>${links}`}
