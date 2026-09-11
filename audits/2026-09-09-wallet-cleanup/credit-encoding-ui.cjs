const fs=require('fs'),vm=require('vm'),assert=require('assert');
function node(tag){return {tag,children:[],style:{},appendChild(n){this.children.push(n);},set textContent(t){this.text=t;this.children=[];},get textContent(){return this.text||'';}};}
const nodes={'pause-ctrl-box':node('div'),'entitlement-details':node('div')};
const context={window:{fetch:()=>{}},document:{createElement:node,getElementById:id=>nodes[id],addEventListener:()=>{}},location:{pathname:'/'}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('host/static/time_controls.js','utf8').replace('function policyEditor() {','window.testRenderStatus=renderStatus; function policyEditor() {'),context);
const base={grant_id:'old',remaining_seconds:600,state:'ACTIVE',can_pause:false,pauses_left:0,pause_denial_reason:'pause_limit_reached',grants:[{id:'next',state:'UNUSED',remaining_seconds:1200,pauses_left:3}]};
function render(extra){context.window.testRenderStatus(Object.assign({},base,extra));const list=[];function walk(n){list.push(n);n.children.forEach(walk);}walk(nodes['entitlement-details']);return list;}
let list=render({});assert(!list.some(n=>n.tag==='button'));assert(!list.some(n=>n.text==='Other Credits:'));
list=render({can_pause:true,pauses_left:1,grants:[{id:'next',state:'UNUSED',remaining_seconds:107909.16089,pauses_left:3}]});
let button=list.find(n=>n.tag==='button');assert(button);assert.equal(button.text,'Use Credit');assert(list.some(n=>(n.text||'').includes('1d 05h:58m:29s (ready)')));assert(list.some(n=>(n.text||'').includes('uses one pause')));
list=render({state:'PAUSED',is_paused:true});assert(list.some(n=>n.tag==='button'));
list=render({admin_paused:true});assert(!list.some(n=>n.tag==='button'));assert(!list.some(n=>n.text==='Other Credits:'));
list=render({pause_denial_reason:'balance_too_high',pauses_left:3});assert(!list.some(n=>n.text==='Other Credits:'));
list=render({can_pause:true,grants:[{id:'held',state:'HELD',remaining_seconds:1200}]});assert(!list.some(n=>n.text==='Other Credits:'));
list=render({can_pause:true});assert(list.some(n=>n.tag==='button'));
list=render({});assert(!list.some(n=>n.text==='Other Credits:'));
assert(!/[\u00c2\u00c3\u201a]/.test(fs.readFileSync('host/static/time_controls.js','utf8')));
list=render({can_pause:true,valid_until_utc:1791478746});
assert(list.some(n=>(n.innerHTML||'').includes(' \u00b7 ')));
console.log('PASS: unavailable credit section hidden; returns when switchable; paused credit allowed; held credit hidden; full days/hours/minutes/seconds retained.');
