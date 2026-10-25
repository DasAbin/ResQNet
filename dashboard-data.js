"use strict";
// Illustrative flood field-report scenarios. All people, places and events are synthetic.
const PRESETS=[
{id:"supply-run",name:"Scheduled supply run",story:"No internet anywhere. A supply vehicle already drives its normal round: base, north shelter, south shelter, back to base. Field reports and the supply manifest ride along - nobody travels just to carry a message. In EXPERIMENTS.md (24 seeds, same contact trace): policies that relay arrive at 32.3 ticks mean vs 44.5 waiting for the late direct contact - and without any relay custody, nothing moves at all.",
 nodes:[{id:"BASE",role:"relief base",x:80,y:210},{id:"MULE",role:"supply vehicle",x:290,y:210},{id:"SHELTER-N",role:"north shelter",x:520,y:80},{id:"SHELTER-S",role:"south shelter",x:520,y:340}],
 links:[{a:"MULE",b:"BASE",active:false,loss:0.2,reliability:0.9,latency_ms:30,bandwidth_bytes_per_tick:300,why:"in range only while loading"},{a:"MULE",b:"SHELTER-N",active:false,loss:0.2,reliability:0.9,latency_ms:30,bandwidth_bytes_per_tick:300,why:"in range only at the north stop"},{a:"MULE",b:"SHELTER-S",active:false,loss:0.2,reliability:0.9,latency_ms:30,bandwidth_bytes_per_tick:300,why:"in range only at the south stop"}],
 messages:[{id:"rpt-north-road",source:"SHELTER-N",destination:"BASE",priority:1,created:2,ttl:55,size_bytes:120,label:"Report: road blocked at km 4"},{id:"rpt-south-water",source:"SHELTER-S",destination:"BASE",priority:1,created:3,ttl:55,size_bytes:120,label:"Report: water for 40, 2 days left"},{id:"supplies-manifest",source:"BASE",destination:"SHELTER-N",priority:1,created:2,ttl:55,size_bytes:200,label:"Supply manifest for north shelter"},{id:"rpt-north-count",source:"SHELTER-N",destination:"BASE",priority:0,created:18,ttl:55,label:"Report: headcount 40, all safe"}],
 changes:[{tick:1,a:"MULE",b:"BASE",active:true},{tick:11,a:"MULE",b:"BASE",active:false},{tick:15,a:"MULE",b:"SHELTER-N",active:true},{tick:26,a:"MULE",b:"SHELTER-N",active:false},{tick:30,a:"MULE",b:"SHELTER-S",active:true},{tick:41,a:"MULE",b:"SHELTER-S",active:false},{tick:45,a:"MULE",b:"BASE",active:true},{tick:56,a:"MULE",b:"BASE",active:false}],ticks:60,seed:11},

{id:"footbridge",name:"Footbridge out",story:"A resident needs a damage report to reach the relief camp across the river; a volunteer on a bike crosses whenever the footbridge link is usable. The footbridge link starts down.",
 nodes:[{id:"MAYA",role:"resident",x:90,y:210},{id:"DEV",role:"volunteer",x:350,y:210},{id:"CAMP",role:"relief camp",x:610,y:210}],
 links:[{a:"MAYA",b:"DEV"},{a:"DEV",b:"CAMP",active:false,why:"footbridge flooded"}],
 messages:[{id:"rpt-1",source:"MAYA",destination:"CAMP",priority:2,created:0,ttl:50,label:"Report: footbridge out, 3 households cut off"}],
 changes:[{tick:3,a:"DEV",b:"CAMP",active:true}],ticks:6,seed:7},
{id:"two-routes",name:"Two routes to camp",story:"Field reports can reach camp through a boat (lossy radio) or a tower (slow but steady). Watch the model learn which to trust.",
 nodes:[{id:"MAYA",role:"resident",x:90,y:210},{id:"BOAT",role:"boat relay",x:350,y:80},{id:"TOWER",role:"tower",x:350,y:340},{id:"CAMP",role:"relief camp",x:620,y:210}],
 links:[{a:"MAYA",b:"BOAT",loss:0.35,reliability:0.85,why:"choppy radio on water"},{a:"MAYA",b:"TOWER",latency_ms:90,why:"long detour"},{a:"BOAT",b:"CAMP",loss:0.35,reliability:0.85},{a:"TOWER",b:"CAMP",latency_ms:60}],
 messages:[{id:"rpt-1",source:"MAYA",destination:"CAMP",priority:2,created:0,ttl:50,label:"Report: road waterlogged, homes cut off"},{id:"chk-1",source:"MAYA",destination:"CAMP",priority:0,created:1,ttl:50,label:"Check-in: 6 people, need water"}],
 changes:[],ticks:10,seed:11},
{id:"causeway",name:"Causeway flapping",story:"Two neighbourhoods cut off by a causeway that fails mid-scenario. Field reports and supply requests must wait out the outage.",
 nodes:[{id:"ASHA",role:"resident",x:80,y:110},{id:"RAVI",role:"resident",x:80,y:320},{id:"RLY1",role:"volunteer",x:250,y:210},{id:"RLY2",role:"volunteer",x:460,y:210},{id:"DOC",role:"medic post",x:630,y:110},{id:"CAMP",role:"relief camp",x:630,y:320}],
 links:[{a:"ASHA",b:"RLY1"},{a:"RAVI",b:"RLY1"},{a:"RLY1",b:"RLY2",why:"causeway"},{a:"RLY2",b:"DOC"},{a:"RLY2",b:"CAMP"}],
 messages:[{id:"rpt-1",source:"ASHA",destination:"CAMP",priority:2,created:0,ttl:50,label:"Report: houses flooded, families upstairs"},{id:"sup-1",source:"RAVI",destination:"DOC",priority:1,created:1,ttl:50,label:"Supply: insulin for 2 residents"},{id:"chk-1",source:"ASHA",destination:"DOC",priority:0,created:0,ttl:50,label:"Check-in: family safe"}],
 changes:[{tick:2,a:"RLY1",b:"RLY2",active:false},{tick:5,a:"RLY1",b:"RLY2",active:true}],ticks:9,seed:21},
{id:"convoy",name:"Evacuation convoy",story:"The shelter's direct convoy link is busy. The medical-van detour is slower but clean - watch WHY THIS HOP show congestion drag the convoy route under, and every message take the detour.",
 nodes:[{id:"SHELTER",role:"shelter",x:80,y:210},{id:"TRUCK1",role:"convoy relay",x:270,y:90},{id:"TRUCK2",role:"convoy relay",x:430,y:90},{id:"MEDVAN",role:"medical van",x:270,y:330},{id:"JUNCTION",role:"junction relay",x:430,y:330},{id:"DEPOT",role:"supply depot",x:620,y:210}],
 links:[{a:"SHELTER",b:"TRUCK1",congestion:0.75,why:"convoy net busy"},{a:"TRUCK1",b:"TRUCK2"},{a:"TRUCK2",b:"DEPOT"},{a:"SHELTER",b:"MEDVAN",latency_ms:55},{a:"MEDVAN",b:"JUNCTION",latency_ms:55},{a:"JUNCTION",b:"DEPOT",latency_ms:55}],
 messages:[{id:"rpt-1",source:"SHELTER",destination:"DEPOT",priority:2,created:0,ttl:50,label:"Report: shelter roof damaged, 12 inside"},{id:"sup-1",source:"SHELTER",destination:"DEPOT",priority:1,created:1,ttl:50,label:"Supply: insulin for 2 residents"},{id:"chk-1",source:"SHELTER",destination:"DEPOT",priority:0,created:2,ttl:50,label:"Check-in: water at stairs"},{id:"chk-2",source:"SHELTER",destination:"DEPOT",priority:0,created:0,ttl:50,label:"Check-in: 8 evacuees ready"},{id:"sup-2",source:"SHELTER",destination:"DEPOT",priority:1,created:1,ttl:50,label:"Request: assisted move, 1 resident"}],
 changes:[],ticks:14,seed:5},
{id:"blackout",name:"Blackout neighbourhood",story:"A neighbourhood hub routes around staged link failures. One weak mesh link is also narrow: big check-ins cross one per tick, and one waits out an outage.",
 nodes:[{id:"HOME1",role:"residence",x:70,y:100},{id:"HOME2",role:"residence",x:70,y:320},{id:"HUB",role:"community hub",x:240,y:210,battery:0.6},{id:"GEN",role:"generator shed",x:400,y:90},{id:"BOAT",role:"boat relay",x:400,y:330},{id:"CLINIC",role:"clinic",x:560,y:100},{id:"CAMP",role:"relief camp",x:640,y:320}],
 links:[{a:"HOME1",b:"HUB"},{a:"HOME2",b:"HUB"},{a:"HUB",b:"GEN"},{a:"HUB",b:"BOAT",bandwidth_bytes_per_tick:300,why:"weak mesh, flooded street"},{a:"GEN",b:"CLINIC"},{a:"BOAT",b:"CAMP"},{a:"CLINIC",b:"CAMP",latency_ms:40}],
 messages:[{id:"rpt-1",source:"HOME1",destination:"CAMP",priority:2,created:0,ttl:60,label:"Report: water in ground floor, family upstairs"},{id:"sup-1",source:"HOME2",destination:"CLINIC",priority:1,created:1,ttl:60,label:"Supply: insulin for 1 resident"},{id:"chk-1",source:"HOME1",destination:"CAMP",priority:0,created:2,ttl:60,size_bytes:250,label:"Check-in: family safe, photo"},{id:"chk-2",source:"HOME2",destination:"CAMP",priority:0,created:3,ttl:60,size_bytes:250,label:"Check-in: 6 people, need water"}],
 changes:[{tick:2,a:"HUB",b:"GEN",active:false},{tick:6,a:"HUB",b:"GEN",active:true},{tick:4,a:"HUB",b:"BOAT",active:false},{tick:8,a:"HUB",b:"BOAT",active:true},{tick:5,a:"CLINIC",b:"CAMP",active:false},{tick:7,a:"CLINIC",b:"CAMP",active:true}],ticks:16,seed:13},
{id:"hills",name:"Weak-signal hills",story:"Eight messages cross the hills. The ridge repeater reads stronger than the congested valley detour, but its drops are real: attempts fail and retry, the failed-attempt counter climbs, and the ridge's learned success estimate sinks in the model panel. Everything still arrives.",
 nodes:[{id:"VILLAGE",role:"village",x:80,y:210},{id:"RIDGE",role:"ridge repeater",x:290,y:80},{id:"VALLEY",role:"valley relay",x:290,y:340},{id:"RELAY",role:"relay",x:480,y:210},{id:"TOWN",role:"town",x:640,y:210}],
 links:[{a:"VILLAGE",b:"RIDGE",loss:0.4,reliability:0.9,latency_ms:15,why:"reads strong, drops in practice"},{a:"RIDGE",b:"RELAY",loss:0.4,reliability:0.9,latency_ms:15},{a:"VILLAGE",b:"VALLEY",latency_ms:80,congestion:0.8,why:"crowded detour"},{a:"VALLEY",b:"RELAY",latency_ms:80,congestion:0.8},{a:"RELAY",b:"TOWN"}],
 messages:[{id:"rpt-1",source:"VILLAGE",destination:"TOWN",priority:2,created:0,ttl:60,label:"Report: bridge washed out"},{id:"sup-1",source:"VILLAGE",destination:"TOWN",priority:1,created:1,ttl:60,label:"Supply: antibiotics"},{id:"chk-1",source:"VILLAGE",destination:"TOWN",priority:0,created:1,ttl:60,label:"Check-in: school shelter full"},{id:"chk-2",source:"VILLAGE",destination:"TOWN",priority:0,created:2,ttl:60,label:"Check-in: need boats"},{id:"rpt-2",source:"VILLAGE",destination:"TOWN",priority:2,created:3,ttl:60,label:"Report: landslide risk on north road"},{id:"chk-3",source:"VILLAGE",destination:"TOWN",priority:0,created:3,ttl:60,label:"Check-in: 40 people OK"},{id:"sup-2",source:"VILLAGE",destination:"TOWN",priority:1,created:4,ttl:60,label:"Request: assisted move, 1 resident"},{id:"chk-4",source:"VILLAGE",destination:"TOWN",priority:0,created:5,ttl:60,label:"Check-in: road to pass cleared"}],
 changes:[],ticks:20,seed:5}];
const PRIORITY_NAME={0:"CHECK-IN",1:"REPORT",2:"HIGH"};
function scenarioFrom(preset,messages,changes,policy){
 return {nodes:preset.nodes.map(n=>{const o={id:n.id};for(const k of["battery","capacity"])if(n[k]!==undefined)o[k]=n[k];return o}),
  links:preset.links.map(l=>{const o={a:l.a,b:l.b,active:l.active!==false};for(const k of["latency_ms","bandwidth_bytes_per_tick","loss","congestion","reliability"])if(l[k]!==undefined)o[k]=l[k];return o}),
  messages:messages.map(m=>{const o={id:m.id,source:m.source,destination:m.destination,created:m.created,ttl:m.ttl,priority:m.priority};if(m.size_bytes!==undefined)o.size_bytes=m.size_bytes;return o}),
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
