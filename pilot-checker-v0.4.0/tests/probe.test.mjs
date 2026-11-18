// Local fixtures only. No commerce website is visited by this test suite.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {probeOne,allowedURL,inputValueChanged} from '../probe.mjs';
import {loadRequestAllowed,preflightPage} from '../loading.mjs';
import {assessTargetOutcome} from '../outcome.mjs';

test('cookie notices cannot satisfy a terms-dialog target outcome',()=>{
 const j={rule_id:'terms-dialog'};
 assert.equal(assessTargetOutcome(j,{body:'Home'},{body:'Home cookie policy'},{new_dialogs:['IKEA US and our digital partners use cookies. Your privacy choices']}).status,'not_observed');
 assert.equal(assessTargetOutcome(j,{body:'Home'},{body:'Home Offer valid 11/5-11/24/26'},{new_dialogs:['Offer valid 11/5-11/24/26']}).status,'matched');
 assert.equal(assessTargetOutcome(j,{body:'Offer valid 11/5-11/24/26'},{body:'Offer valid 11/5-11/24/26'},{new_dialogs:[]}).status,'not_observed');
 assert.equal(assessTargetOutcome({rule_id:'dialog-dismiss'},null,null,{frame_closed:true}).status,'matched');
});

test('hydration readiness and idle control separate notices from target actions; empty login preview cannot submit',async()=>{
 const require=createRequire(process.env.PILOT_PLAYWRIGHT_ROOT?path.join(process.env.PILOT_PLAYWRIGHT_ROOT,'package.json'):import.meta.url);
 const {chromium}=require('playwright');let writes=0;
 const markup='<body>Hydration and empty login fixture<button id="terms" role="button" data-testid="hri-disclaimer-modal-link">Terms and Conditions at checkout</button><div role="dialog" id="cookie" hidden>Your privacy choices: digital partners use cookies</div><div role="dialog" id="offer" hidden>Offer valid 11/5-11/24</div><form action="/write"><input name="loginKey" type="text"><input name="password" type="password"><button aria-label="Show password" onclick="event.preventDefault();document.querySelector(\'input[name=password]\').type=\'text\'">Show password</button></form><script>setTimeout(()=>{const t=document.querySelector("#terms");t.__e={onKeyDown:()=>{}};t.onkeydown=e=>{if(e.key==="Enter")document.querySelector("#offer").hidden=false}},100);setTimeout(()=>document.querySelector("#cookie").hidden=false,400)</script></body>';
 const server=http.createServer((req,res)=>{if(req.url.startsWith('/write'))writes++;res.setHeader('Content-Type','text/html');res.end(markup);});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin=`http://127.0.0.1:${server.address().port}`;
 const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'pilotcheck-test-'));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.PILOT_BROWSER_CHANNEL?{channel:process.env.PILOT_BROWSER_CHANNEL}:{})});
  const terms={job_id:'hydrated-terms',members:['R1'],url:origin+'/',selector:'#terms',key:'Enter',rule_id:'terms-dialog',frame_is_top:true,expected:{tag:'button',id:'terms'},readiness:{kind:'preact-keydown',timeout_ms:1000},idle_control_ms:1000};
  const td=path.join(tmp,'terms');await fs.mkdir(td);const t=await probeOne(browser,terms,td,{settle:100});
  assert.equal(t.readiness.ready,true);assert.equal(t.key_dispatched,true);assert.equal(t.target_outcome.status,'matched');assert.ok(t.idle_control);assert.ok(!t.ui_evidence.new_dialogs.some(x=>x.includes('privacy choices')));
  const login={job_id:'empty-login',members:['R2'],url:origin+'/',selector:'input[name=loginKey]',key:'Enter',rule_id:'password-form-preview',frame_is_top:true,expected:{tag:'input',name:'loginKey',type:'text'}};
  const ld=path.join(tmp,'login');await fs.mkdir(ld);const l=await probeOne(browser,login,ld,{settle:100});
  assert.equal(l.key_dispatched,true);assert.equal(l.target_outcome.status,'matched');assert.equal(writes,0);
 }finally{
  await browser?.close();await new Promise(resolve=>server.close(resolve));
  const target=await fs.realpath(tmp),base=await fs.realpath(os.tmpdir()),relative=path.relative(base,target);
  if(relative.startsWith('..')||path.isAbsolute(relative)||!path.basename(target).startsWith('pilotcheck-test-'))throw new Error('Unexpected temporary directory');
  await fs.rm(target,{recursive:true,force:true});
 }
});

test('added fields and validity styling are not counted as input value changes',()=>{
 assert.equal(inputValueChanged([],[{id:'',value:'',invalid:false}]),false);
 assert.equal(inputValueChanged([{id:'q',value:'',invalid:false}],[{id:'q',value:'',invalid:true}]),false);
 assert.equal(inputValueChanged([{id:'q',value:'earbuds'}],[{id:'q',value:''}]),true);
});

test('load phase permits hydration POST but rejects explicit mutations',()=>{
 assert.equal(loadRequestAllowed('POST','https://example.com/catalog','{"query":"query Catalog { id }"}'),true);
 assert.equal(loadRequestAllowed('POST','https://example.com/catalog','{}',true),false);
 assert.equal(loadRequestAllowed('POST','https://example.com/catalog','{"query":"mutation Purchase { id }"}'),false);
 assert.equal(loadRequestAllowed('POST','https://example.com/login','{"password":"secret"}'),false);
 assert.equal(loadRequestAllowed('POST','https://example.com/purchase','{}'),false);
 assert.equal(loadRequestAllowed('DELETE','https://example.com/catalog'),false);
});

test('sensitive navigation URLs are refused',()=>{
 assert.equal(allowedURL('https://example.com/checkout'),false);
 assert.equal(allowedURL('file:///C:/private.txt'),false);
 assert.equal(allowedURL('https://user:pass@example.com/search'),false);
 assert.equal(allowedURL('https://example.com/product/1'),true);
});

test('qualified terms controls and Escape-only frame dismissal are observed without form submission',async()=>{
 const require=createRequire(process.env.PILOT_PLAYWRIGHT_ROOT?path.join(process.env.PILOT_PLAYWRIGHT_ROOT,'package.json'):import.meta.url);
 const {chromium}=require('playwright');let writes=0;
 const server=http.createServer((req,res)=>{
  res.setHeader('Content-Type','text/html; charset=utf-8');
  if(req.url.startsWith('/buyer-preferences/shipping-address')){res.end('<body>Shipping address test frame<form action="/write"><input name="postalCode"><button type="submit" onkeydown="if(event.key===\'Escape\'){event.preventDefault();parent.postMessage(\'close-shipping\',\'*\')}">Confirm</button></form></body>');return;}
  if(req.url.startsWith('/write'))writes++;
  res.end('<body>Qualified terms and shipping frame fixture<section data-testid="offer-one"><div role="button" tabindex="0" data-testid="hri-disclaimer-modal-link" onkeydown="if(event.key===\'Enter\')document.querySelector(\'#terms\').hidden=false">See full Terms and Conditions. Discount applied at checkout.</div></section><section data-testid="offer-two"><div role="button" tabindex="0" data-testid="hri-disclaimer-modal-link">See full Terms and Conditions. Discount applied at checkout.</div></section><div role="dialog" id="terms" hidden>Terms details</div><iframe src="/buyer-preferences/shipping-address"></iframe><script>addEventListener("message",e=>{if(e.data==="close-shipping")document.querySelector("iframe").hidden=true})</script></body>');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin=`http://127.0.0.1:${server.address().port}`;
 const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'pilotcheck-test-'));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.PILOT_BROWSER_CHANNEL?{channel:process.env.PILOT_BROWSER_CHANNEL}:{})});
  const terms={job_id:'terms',members:['R1'],url:origin+'/',selector:'section[data-testid="offer-one"] [data-testid="hri-disclaimer-modal-link"]',key:'Enter',rule_id:'terms-dialog',frame_is_top:true,expected:{tag:'div',text:'See full Terms and Conditions. Discount applied at checkout.'}};
  const td=path.join(tmp,'terms');await fs.mkdir(td);
  const t=await probeOne(browser,terms,td,{settle:100});assert.equal(t.key_dispatched,true);assert.equal(t.ui_observed,true);
  const close={job_id:'close',members:['R2'],url:origin+'/',selector:'button',key:'Escape',rule_id:'dialog-dismiss',frame_is_top:false,frame_url:origin+'/buyer-preferences/shipping-address',expected:{tag:'button',type:'submit',text:'Confirm'}};
  const cd=path.join(tmp,'close');await fs.mkdir(cd);
  const c=await probeOne(browser,close,cd,{settle:100});assert.equal(c.key_dispatched,true);assert.equal(c.ui_evidence.frame_closed,true);assert.equal(c.ui_observed,true);assert.equal(writes,0);
  const blocked=await probeOne(browser,{...close,key:'Enter'},cd,{settle:100});assert.equal(blocked.status,'policy_skipped');assert.equal(writes,0);
 }finally{
  await browser?.close();await new Promise(resolve=>server.close(resolve));
  const target=await fs.realpath(tmp),base=await fs.realpath(os.tmpdir()),relative=path.relative(base,target);
  if(relative.startsWith('..')||path.isAbsolute(relative)||!path.basename(target).startsWith('pilotcheck-test-'))throw new Error('Unexpected temporary directory');
  await fs.rm(target,{recursive:true,force:true});
 }
});

test('hydration loads normally, key effects remain intercepted, and preflight records expired pages',async()=>{
 const require=createRequire(process.env.PILOT_PLAYWRIGHT_ROOT?path.join(process.env.PILOT_PLAYWRIGHT_ROOT,'package.json'):import.meta.url);
 const {chromium}=require('playwright');let reads=0,writes=0;
 const markup=`<!doctype html><meta charset="utf-8"><body>Loading keyboard verification fixture. Please wait.
 <script>fetch('/catalog',{method:'POST',body:JSON.stringify({query:'query Catalog { id }'})}).then(r=>r.json()).then(()=>{
 const b=document.createElement('button');b.id='hydrated';b.textContent='Details';
 b.onclick=()=>{document.getElementById('details').hidden=false;fetch('/mutate',{method:'POST'}).catch(()=>{});};
 document.body.append(b);
 });</script><div id="details" role="dialog" hidden>Hydrated detail panel</div></body>`;
 const server=http.createServer((req,res)=>{
  if(req.url==='/catalog'){reads++;res.setHeader('Content-Type','application/json');res.end('{}');return;}
  if(req.url==='/mutate')writes++;
  if(req.url==='/old-product'){res.writeHead(302,{Location:'/'});res.end();return;}
  if(req.url==='/rapid'){res.setHeader('Content-Type','text/html');res.end('<body>Client navigation is starting. Please wait.<script>setTimeout(()=>location.href="/",20)</script></body>');return;}
  if(req.url==='/missing'){res.writeHead(404,{'Content-Type':'text/html'});res.end('<title>Error Page</title><body>The requested product does not exist anymore.</body>');return;}
  res.setHeader('Content-Type','text/html; charset=utf-8');res.end(markup);
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin=`http://127.0.0.1:${server.address().port}`;
 const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'pilotcheck-test-'));let browser;
 const job={job_id:'hydration',members:['R1'],url:origin+'/',selector:'#hydrated',key:'Enter',rule_id:'isolated-control',frame_is_top:true,expected:{tag:'button',id:'hydrated',text:'Details'}};
 try{
  browser=await chromium.launch({headless:true,...(process.env.PILOT_BROWSER_CHANNEL?{channel:process.env.PILOT_BROWSER_CHANNEL}:{})});
  const strict=path.join(tmp,'strict');await fs.mkdir(strict);
  const old=await probeOne(browser,job,strict,{settle:100,readyTimeout:500,loadPolicy:'strict'});
  assert.equal(old.status,'element_not_found');assert.equal(reads,0);
  const normal=path.join(tmp,'normal');await fs.mkdir(normal);
  const improved=await probeOne(browser,job,normal,{settle:100,readyTimeout:1000});
  assert.equal(improved.status,'guarded_effect');assert.equal(improved.ui_observed,true);assert.equal(reads,1);assert.equal(writes,0);
  assert.equal(improved.confirmation_tier,'unknown');assert.equal(improved.danger_status,'unresolved');
  const ready=await preflightPage(browser,job,path.join(tmp,'ready'),{settle:100,readyTimeout:1000});assert.equal(ready.status,'page_ready');
  const expired=await preflightPage(browser,{...job,url:origin+'/old-product'},path.join(tmp,'expired'),{settle:100,readyTimeout:1000});assert.equal(expired.status,'redirected_away');
  const missing=await preflightPage(browser,{...job,url:origin+'/missing'},path.join(tmp,'missing'),{settle:100,readyTimeout:1000});assert.equal(missing.status,'page_error');
  const rapid=await preflightPage(browser,{...job,url:origin+'/rapid'},path.join(tmp,'rapid'),{settle:100,readyTimeout:1000});assert.equal(rapid.status,'redirected_away');
 }finally{
  await browser?.close();await new Promise(resolve=>server.close(resolve));
  const target=await fs.realpath(tmp),root=await fs.realpath(os.tmpdir()),relative=path.relative(root,target);
  if(relative.startsWith('..')||path.isAbsolute(relative)||!path.basename(target).startsWith('pilotcheck-test-'))throw new Error('Unexpected temporary directory');
  await fs.rm(target,{recursive:true,force:true});
 }
});

test('isolated keyboard inspector records UI effects and blocks writes',async()=>{
 const require=createRequire(process.env.PILOT_PLAYWRIGHT_ROOT?path.join(process.env.PILOT_PLAYWRIGHT_ROOT,'package.json'):import.meta.url);
 const {chromium}=require('playwright');
 let mutations=0;
 const markup=`<!doctype html><meta charset="utf-8"><body>
 <button id="open" onclick="document.querySelector('#dialog').hidden=false">Details</button><div id="dialog" hidden>Opened details</div>
 <button id="write" onclick="fetch('/mutate',{method:'POST'}).catch(()=>{});localStorage.setItem('x','y')">Save local</button>
 <button id="native" onclick="confirm('Really?')">Show question</button>
 <button id="buy" onclick="fetch('/mutate',{method:'POST'})">Buy now</button>
 <button id="two">Duplicate</button><button id="two">Duplicate</button>
 <button id="hidden" hidden>Hidden</button>
 <button id="visibility" hidden>Visibility</button><button id="visibility" onclick="this.textContent='Visible control opened'">Visibility</button>
 <input id="q" value="earbuds"><button id="autocomplete-clear-button" type="button" aria-label="Clear" onclick="document.querySelector('#q').value=''">Clear search</button>
 <a id="next" href="/mutate">Read next</a>
 <form method="get" action="/search"><input id="query" name="q" value="test"><button>Search</button></form>
 </body>`;
 const server=http.createServer((req,res)=>{if(req.url.startsWith('/mutate')||req.url.startsWith('/search'))mutations++;res.setHeader('Content-Type','text/html; charset=utf-8');res.end(markup);});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin=`http://127.0.0.1:${server.address().port}`;
 const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'pilotcheck-test-'));
 const channel=process.env.PILOT_BROWSER_CHANNEL;
 let browser;
 function job(id,rule='isolated-control',expected={tag:'button',id,text:id==='open'?'Details':id==='write'?'Save local':id==='native'?'Show question':''}){
  return {job_id:id,members:['fixture-'+id],url:origin+'/',selector:'#'+id,key:'Enter',rule_id:rule,frame_is_top:true,expected};
 }
 async function check(j){const out=path.join(tmp,j.job_id);await fs.mkdir(out);return probeOne(browser,j,out,{settle:100});}
 try{
  browser=await chromium.launch({headless:true,...(channel?{channel}:{})});
  const opened=await check(job('open'));assert.equal(opened.status,'local_ui_changed');assert.equal(opened.danger_status,'unresolved');
  const write=await check(job('write'));assert.equal(write.status,'guarded_effect');assert.equal(mutations,0);assert.equal(write.confirmation_tier,'unknown');
  const native=await check(job('native'));assert.equal(native.status,'guarded_effect');assert.equal(native.dialogs.length,1);assert.equal(native.confirmation_tier,'unknown');
  const buy=await check(job('buy','isolated-control',{tag:'button',id:'buy',text:'Buy now'}));assert.equal(buy.status,'runtime_policy_skipped');assert.equal(mutations,0);
  assert.equal((await check(job('two'))).status,'element_ambiguous');
  assert.equal((await check(job('hidden'))).status,'not_actionable');
  const visible=await check(job('visibility','isolated-control',{tag:'button',id:'visibility',text:'Visibility'}));assert.equal(visible.status,'local_ui_changed');assert.equal(visible.key_dispatched,true);
  const changed=await check({...job('open'),job_id:'changed',expected:{tag:'button',id:'open',text:'Wrong label'}});assert.equal(changed.status,'identity_changed');
  const relocated=await check({...job('open'),job_id:'relocated',selector:'#old-selector'});assert.equal(relocated.status,'local_ui_changed');assert.equal(relocated.resolution,'unique-recorded-identity');
  const clear=await check(job('autocomplete-clear-button','clear-search',{tag:'button',id:'autocomplete-clear-button',type:'button',aria_label:'Clear'}));assert.equal(clear.status,'local_ui_changed');
  const delegated=await check({...job('autocomplete-clear-button','delegated-handler',{tag:'button',id:'autocomplete-clear-button',type:'button',aria_label:'Clear'}),job_id:'delegated-clear'});
  assert.equal(delegated.key_dispatched,true);assert.equal(delegated.ui_observed,true);assert.equal(delegated.consequence_tier,'unknown');
  const unknownDelegated=await check({...job('open','delegated-handler'),job_id:'unknown-delegated'});assert.equal(unknownDelegated.status,'runtime_policy_skipped');
  const nav=await check(job('next','isolated-control',{tag:'a',id:'next',text:'Read next',href:origin+'/mutate'}));assert.equal(nav.status,'guarded_effect');assert.equal(mutations,0);
  const form=await check(job('query','search-form',{tag:'input',id:'query',name:'q'}));assert.equal(form.status,'guarded_effect');assert.equal(mutations,0);
 }finally{
  await browser?.close();await new Promise(resolve=>server.close(resolve));
  const target=await fs.realpath(tmp),root=await fs.realpath(os.tmpdir()),relative=path.relative(root,target);
  if(relative.startsWith('..')||path.isAbsolute(relative)||!path.basename(target).startsWith('pilotcheck-test-'))throw new Error('Unexpected temporary directory');
  await fs.rm(target,{recursive:true,force:true});
 }
});
