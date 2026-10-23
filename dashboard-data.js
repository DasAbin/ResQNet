"use strict";
// Illustrative flood-rescue scenarios. All people, places and events are synthetic.
const PRESETS=[
{id:"footbridge",name:"Footbridge out",story:"One stranded resident, one volunteer on a bike, relief camp across the river. The footbridge link starts down.",
 nodes:[{id:"MAYA",role:"resident",x:90,y:210},{id:"DEV",role:"volunteer",x:350,y:210},{id:"CAMP",role:"relief camp",x:610,y:210}],
 links:[{a:"MAYA",b:"DEV"},{a:"DEV",b:"CAMP",active:false,why:"footbridge flooded"}],
 messages:[{id:"sos-1",source:"MAYA",destination:"CAMP",priority:2,created:0,ttl:50,label:"SOS: water rising, 3 people on terrace"}],
 changes:[{tick:3,a:"DEV",b:"CAMP",active:true}],ticks:6,seed:7},
{id:"two-routes",name:"Two routes to camp",story:"A stranded resident can reach camp through a boat (lossy radio) or a tower (slow but steady). Watch the model learn which to trust.",
 nodes:[{id:"MAYA",role:"resident",x:90,y:210},{id:"BOAT",role:"boat relay",x:350,y:80},{id:"TOWER",role:"tower",x:350,y:340},{id:"CAMP",role:"relief camp",x:620,y:210}],
 links:[{a:"MAYA",b:"BOAT",loss:0.35,reliability:0.85,why:"choppy radio on water"},{a:"MAYA",b:"TOWER",latency_ms:90,why:"long detour"},{a:"BOAT",b:"CAMP",loss:0.35,reliability:0.85},{a:"TOWER",b:"CAMP",latency_ms:60}],
 messages:[{id:"sos-1",source:"MAYA",destination:"CAMP",priority:2,created:0,ttl:50,label:"SOS: water rising"},{id:"chk-1",source:"MAYA",destination:"CAMP",priority:0,created:1,ttl:50,label:"Check-in: 6 people, need water"}],
 changes:[],ticks:10,seed:11},
{id:"causeway",name:"Causeway flapping",story:"Two neighbourhoods cut off by a causeway that fails mid-scenario. Medical and SOS traffic must wait out the outage.",
 nodes:[{id:"ASHA",role:"resident",x:80,y:110},{id:"RAVI",role:"resident",x:80,y:320},{id:"RLY1",role:"volunteer",x:250,y:210},{id:"RLY2",role:"volunteer",x:460,y:210},{id:"DOC",role:"medic post",x:630,y:110},{id:"CAMP",role:"relief camp",x:630,y:320}],
 links:[{a:"ASHA",b:"RLY1"},{a:"RAVI",b:"RLY1"},{a:"RLY1",b:"RLY2",why:"causeway"},{a:"RLY2",b:"DOC"},{a:"RLY2",b:"CAMP"}],
 messages:[{id:"sos-1",source:"ASHA",destination:"CAMP",priority:2,created:0,ttl:50,label:"SOS: trapped on terrace"},{id:"med-1",source:"RAVI",destination:"DOC",priority:1,created:1,ttl:50,label:"Medical: insulin needed"},{id:"chk-1",source:"ASHA",destination:"DOC",priority:0,created:0,ttl:50,label:"Check-in: family safe"}],
 changes:[{tick:2,a:"RLY1",b:"RLY2",active:false},{tick:5,a:"RLY1",b:"RLY2",active:true}],ticks:9,seed:21}];
const PRIORITY_NAME={0:"ROUTINE",1:"MEDICAL",2:"SOS"};
function scenarioFrom(preset,messages,changes,policy){
 return {nodes:preset.nodes.map(n=>({id:n.id})),
  links:preset.links.map(l=>{const o={a:l.a,b:l.b,active:l.active!==false};for(const k of["latency_ms","bandwidth_bytes_per_tick","loss","congestion","reliability"])if(l[k]!==undefined)o[k]=l[k];return o}),
  messages:messages.map(m=>({id:m.id,source:m.source,destination:m.destination,created:m.created,ttl:m.ttl,priority:m.priority})),
  changes:changes.map(c=>({tick:c.tick,a:c.a,b:c.b,active:c.active})),policy,ticks:preset.ticks,seed:preset.seed};
}
// Rebuild world state at a tick purely from API events. Nothing here is invented.
function stateAt(events,tick,links){
 const up={};links.forEach(l=>up[[l.a,l.b].sort().join("|")]=l.active!==false);
 const at={},delivered={},gone={};
 for(const e of events){if(e.tick>tick)break;
  if(e.type==="created")at[e.id]=e.node;
  else if(e.type==="forward")at[e.id]=e.destination;
  else if(e.type==="link_changed")up[[e.a,e.b].sort().join("|")]=e.active;
  else if(e.type==="delivered")delivered[e.id]=e.node;
  else if(e.type==="expired"||e.type==="cleared"){delete at[e.id];gone[e.id]=1}}
 return{up,at,delivered,gone};
}
