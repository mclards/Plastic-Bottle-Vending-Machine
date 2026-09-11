/* Parse shipping templates and exercise control failure paths without customer data. */
const fs=require('fs'),vm=require('vm'),assert=require('assert'),Module=require('module'),cp=require('child_process');
const compiler=new Module('control-acorn');
compiler._compile(process.binding('natives')['internal/deps/acorn/acorn/dist/acorn'],'control-acorn.js');
const acorn=compiler.exports;
const templates=JSON.parse(cp.execFileSync('python',['-B','-c',`import ast,json
from pathlib import Path
s=ast.parse(Path('host/portal.py').read_text(encoding='utf-8'))
print(json.dumps({n.targets[0].id:ast.literal_eval(n.value) for n in s.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('ADMIN_HTML','PORTAL_HTML')}))`],{encoding:'utf8'}));
const functions={},routes=new Set();let mutationCount=0;
function walk(n,visit){if(!n||typeof n!=='object')return;visit(n);Object.values(n).forEach(v=>Array.isArray(v)?v.forEach(x=>walk(x,visit)):v&&typeof v==='object'&&walk(v,visit));}
for(const [name,html] of Object.entries(templates)) {
  for(const token of ['Use Credit','OTHER CREDITS','/api/client/switch','tab-member','sec-members'])assert(!html.includes(token),token);
  for(const block of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
    const source=block[1].replace(/\{\{[\s\S]*?\}\}/g,m=>'0'+' '.repeat(m.length-1)).replace(/\{%[\s\S]*?%\}/g,m=>' '.repeat(m.length));
    const tree=acorn.parse(source,{ecmaVersion:'latest'});
    walk(tree,n=>{
      if(name==='ADMIN_HTML'&&n.type==='FunctionDeclaration')functions[n.id.name]=source.slice(n.start,n.end);
      if(n.type!=='CallExpression'||!['fetch','adminFetch'].includes(n.callee.name))return;
      if(n.arguments[0]?.type==='Literal')routes.add(n.arguments[0].value);
      const opt=n.arguments[1];const method=opt?.properties?.find(p=>(p.key.name||p.key.value)==='method');
      if(name==='ADMIN_HTML'&&method?.value.value==='POST'){assert.equal(n.callee.name,'adminFetch');mutationCount++;}
    });
  }
}
const html=templates.ADMIN_HTML;
for(const id of ['bw-tab-fixed','bw-tab-dynamic','bw-tab-qos','net-cfg-dns1','net-cfg-starlink'])assert(html.includes('id="'+id+'"'),id);
for(const id of ['bw-tab-fixed','bw-tab-dynamic','bw-tab-qos'])assert.equal(html.split('id="'+id+'"').length-1,1,id);
const controls=fs.readFileSync('host/static/time_controls.js','utf8');acorn.parse(controls,{ecmaVersion:'latest'});
for(const token of ['Use Credit','OTHER CREDITS','/api/client/switch','\u00c2','\u00c3','\ufffd'])assert(!controls.includes(token),token);
assert(controls.includes('\\u00b7'));
const notices=[],buttons=[{disabled:false}],fields={};let calls=0,loads=0,response,lastOptions;
const timers=new Map();let timerSequence=0;
const sandbox={window:{fetch:async(url,options)=>{calls++;lastOptions=options;if(response instanceof Error)throw response;return response;}},Swal:{fire:(...a)=>{notices.push(a);return Promise.resolve({isConfirmed:true});}},document:{getElementById:id=>fields[id]||{querySelectorAll:()=>buttons}},pendingClientActions:{},loadClients:()=>loads++,$:()=>({modal:()=>{}})};
sandbox.setTimeout=(callback,ms)=>{assert.equal(ms,30000);timers.set(++timerSequence,callback);return timerSequence;};
sandbox.clearTimeout=id=>timers.delete(id);
vm.createContext(sandbox);vm.runInContext(['adminFetch','clientAction','openEditClientModal','submitEditClientModal'].filter(n=>functions[n]).map(n=>functions[n]).join('\n'),sandbox);
function reply(ok,body,broken=false){return {ok,clone(){return this;},json:async()=>{if(broken)throw Error('invalid JSON');return body;}};}
(async()=>{
 for(response of [reply(false,{error:'denied'}),reply(true,{success:false,error:'rejected'}),reply(true,null,true),new Error('offline')]) {
   notices.length=0;loads=0;await sandbox.clientAction('10.0.0.2','kick');assert.equal(loads,0);assert(notices.some(n=>n[2]==='error'));assert(!notices.some(n=>n[2]==='success'));assert.equal(buttons[0].disabled,false);
 }
 response=reply(true,{success:true,network_pending:true});notices.length=0;await sandbox.clientAction('10.0.0.2','kick');assert(notices.some(n=>n[2]==='warning'));assert(!notices.some(n=>n[2]==='success'));
 response=reply(true,{success:true});notices.length=0;calls=0;const first=sandbox.clientAction('10.0.0.2','kick');sandbox.clientAction('10.0.0.2','kick');await first;assert.equal(calls,1);assert(notices.some(n=>n[2]==='success'));
 for(const id of ['ip','info','mins','dl','ul'])fields['modal-client-'+id]={value:'',dataset:{}};
 sandbox.openEditClientModal('10.0.0.2','02:00:00:00:00:01',125.567,4096,1024);
 assert.equal(fields['modal-client-mins'].value,'2.09');sandbox.submitEditClientModal();await new Promise(setImmediate);
 assert(!Object.hasOwn(JSON.parse(lastOptions.body),'minutes'));
 fields['modal-client-mins'].value='2.50';sandbox.submitEditClientModal();await new Promise(setImmediate);assert.equal(JSON.parse(lastOptions.body).minutes,2.5);
 fields['modal-client-dl'].value='0';calls=0;sandbox.submitEditClientModal();assert.equal(calls,0);
 assert.equal(timers.size,0);
 response=new Promise(()=>{});notices.length=0;
 const stalled=sandbox.clientAction('10.0.0.2','kick');assert.equal(buttons[0].disabled,true);
 assert.equal(timers.size,1);Array.from(timers.values())[0]();await stalled;
 assert.equal(buttons[0].disabled,false);assert.equal(timers.size,0);
 assert(notices.some(n=>String(n[1]).includes('may have completed')));assert(!notices.some(n=>n[2]==='success'));
 console.log('PASS: template/JS syntax, '+mutationCount+' guarded admin mutations, removed credit controls, encoding, tabs, failed requests, pending network status, duplicate Kick clicks and exact edit payloads.');
})().catch(e=>{console.error(e);process.exitCode=1;});
