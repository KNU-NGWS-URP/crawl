import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import crypto from 'node:crypto';
import {loadRequestAllowed,openDocument,preflightPage} from './loading.mjs';
import {assessTargetOutcome} from './outcome.mjs';

const GUARD = () => {
  let armed=false; const events=[];
  const log=(type,detail='')=>events.push({type,detail:String(detail).slice(0,500)});
  const refuse=()=>{throw new Error('pilot: network effect blocked');};
  Object.defineProperty(window,'__pilot',{value:{arm(){armed=true;events.length=0;},events},configurable:false});
  for(const key of ['WebSocket','EventSource','Worker','SharedWorker']) {
    if(key in window) Object.defineProperty(window,key,{value:function(){log(key);refuse();}});
  }
  const fetch0=window.fetch;
  window.fetch=function(...args){if(armed){log('fetch',args[0]?.url??args[0]);return Promise.reject(new Error('pilot: blocked'));}return fetch0.apply(this,args);};
  const open0=XMLHttpRequest.prototype.open,send0=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(method,url,...rest){this.__pilot_url=url;return open0.call(this,method,url,...rest);};
  XMLHttpRequest.prototype.send=function(...args){if(armed){log('xhr',this.__pilot_url);return;}return send0.apply(this,args);};
  if(navigator.sendBeacon){const b=navigator.sendBeacon.bind(navigator);navigator.sendBeacon=(url,data)=>{if(armed){log('beacon',url);return false;}return b(url,data);};}
  for(const name of ['setItem','removeItem','clear']){const original=Storage.prototype[name];Storage.prototype[name]=function(...args){if(armed){log('storage-'+name,args[0]);return;}return original.apply(this,args);};}
  const submit=HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit=function(){if(armed){log('form-submit',this.action);return;}return submit.call(this);};
  document.addEventListener('submit',event=>{if(armed){log('submit',event.target?.action);event.preventDefault();}},true);
  const open=window.open;window.open=function(url,...args){if(armed){log('popup',url);return null;}return open.call(this,url,...args);};
  const share=navigator.share?.bind(navigator);if(share)navigator.share=async data=>{if(armed){log('share',JSON.stringify(data));throw new Error('pilot: share blocked');}return share(data);};
  if(navigator.clipboard)for(const name of ['write','writeText']){const original=navigator.clipboard[name]?.bind(navigator.clipboard);if(original)navigator.clipboard[name]=async(...args)=>{if(armed){log('clipboard-'+name);throw new Error('pilot: clipboard blocked');}return original(...args);};}
  // Native dialogs are dismissed by the runner. Credentials are never supplied.
};

const norm=s=>String(s??'').replace(/\s+/g,' ').trim();
const sensitive= /buy\s*now|add\s*to\s*(cart|bag)|checkout|place.*order|subscribe|unsubscribe|delete|remove|send|publish|transfer|purchase|do not sell|구매하기|바로구매|결제|주문하기|삭제|전송|구독|송금/i;
const rules=new Set(['search-form','search-control','clear-search','terms-dialog','authentication','isolated-control','delegated-handler','dialog-dismiss','callback-preview','password-form-preview']);
export function allowedURL(value){
  try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password&&!/\/(?:logout|delete|remove|unsubscribe|subscribe|checkout|pay|purchase|redeem|transfer|confirm|addtocart|add-to-cart)(?:[/?.-]|$)/i.test(u.pathname);}catch{return false;}
}
function redact(value){try{const u=new URL(value);for(const k of [...u.searchParams.keys()])if(/token|session|password|secret|auth|openid/i.test(k))u.searchParams.set(k,'[REDACTED]');return u.href;}catch{return String(value).slice(0,500);}}
function local(value){try{return ['127.0.0.1','localhost','[::1]'].includes(new URL(value).hostname);}catch{return false;}}
function sameURL(a,b){try{const x=new URL(a),y=new URL(b);return x.origin===y.origin&&x.pathname===y.pathname;}catch{return false;}}
function identity(expected,actual){
  for(const k of ['tag','id','name','type','aria_label'])if(expected[k]&&norm(expected[k]).toLowerCase()!==norm(actual[k]).toLowerCase())return false;
  if(expected.tag!=='input'&&expected.text&&!norm(actual.text).startsWith(norm(expected.text)))return false;
  if(expected.href&&!sameURL(expected.href,actual.href))return false;
  return true;
}
function permitted(rule,el){
  const label=[el.text,el.aria_label,el.id].join(' ');
  if(rule==='terms-dialog')return el.role==='button'&&el.type!=='submit'&&!el.in_form&&el.data_testid==='hri-disclaimer-modal-link'&&/Terms and Conditions|약관/i.test(label);
  if(rule==='dialog-dismiss')return el.tag==='button'&&el.type==='submit'&&norm(el.text)==='Confirm'&&!el.disabled;
  if(sensitive.test(label)||el.type==='password'||el.disabled)return false;
  if(rule==='callback-preview')return ['button','a','div'].includes(el.tag)&&el.type!=='submit'&&!el.in_form&&!/sign\s*in|log\s*in|password|로그인|sign\s*up/i.test(label);
  if(rule==='password-form-preview')return el.tag==='input'&&el.name==='loginKey'&&el.type!=='password'&&el.in_form;
  if(rule==='authentication')return el.id==='account-menu-account-button'&&el.type==='button'&&!el.in_form;
  if(rule==='search-form')return el.tag==='input'&&!/email|password/i.test(el.type||'')&&el.form_method==='get'&&(/search|\/s(?:\/|$)|\/sch\//i.test(el.form_action)||/^(q|sitem|searchTerm|field-keywords|header-search)$/i.test(el.name||''));
  if(rule==='search-control')return el.id==='autocomplete-search-button';
  if(rule==='clear-search')return el.id==='autocomplete-clear-button'||/clear.*search|search.*clear|검색.*지우/i.test(label);
  if(rule==='delegated-handler')return ['autocomplete-clear-button','autocomplete-search-button'].includes(el.id)&&el.tag==='button'&&el.type!=='submit'&&!el.in_form;
  if(rule==='isolated-control')return ['button','a','div'].includes(el.tag)&&el.type!=='submit'&&!el.in_form&&!/sign\s*in|log\s*in|password|로그인|sign\s*up/i.test(label);
  return false;
}
const snapshot=()=>({url:location.href,body:document.body?.innerText.slice(0,30000)||'',
  dialogs:[...document.querySelectorAll('[role=dialog],[role=alertdialog],dialog[open]')].filter(e=>e.getClientRects().length).map(e=>e.innerText.slice(0,1500)),
  menus:[...document.querySelectorAll('[role=menu],[aria-expanded=true]')].filter(e=>e.getClientRects().length).map(e=>(e.innerText||e.getAttribute('aria-label')||e.id).slice(0,1000)),
  inputs:[...document.querySelectorAll('input,textarea')].filter(e=>e.getClientRects().length&&!['password','email','hidden'].includes(e.type)).map(e=>({id:e.id,value:e.value,invalid:!e.validity.valid})),
  field_types:[...document.querySelectorAll('input')].map((e,i)=>({key:e.id||e.name||'@'+i,type:e.type})),
  focus:document.activeElement?.id||document.activeElement?.tagName||'',
  guard:window.__pilot?.events||[]});
export function inputValueChanged(before,after){
  const values=new Map(before.map((item,index)=>[item.id||`@${index}`,item.value]));
  return after.some((item,index)=>{const key=item.id||`@${index}`;return values.has(key)&&values.get(key)!==item.value;});
}
const elementInfo=e=>({tag:e.tagName.toLowerCase(),id:e.id,type:e.getAttribute('type'),name:e.getAttribute('name'),role:e.getAttribute('role'),text:e.innerText||'',aria_label:e.getAttribute('aria-label'),data_testid:e.getAttribute('data-testid'),href:e.href||'',visible:!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden',disabled:!!e.disabled,in_form:!!e.closest('form'),form_method:e.closest('form')?.method,form_action:e.closest('form')?.action});
async function resolveLocator(frame,job){
  const original=frame.locator(job.selector),count=await original.count();
  if(count===1){const actual=await original.evaluate(elementInfo);if(identity(job.expected,actual)&&actual.visible&&!actual.disabled)return {locator:original,actual,resolution:'source-css'};}
  if(!job.expected.id&&!job.expected.aria_label&&norm(job.expected.text).length<3)return {status:count===1?'identity_changed':count?'element_ambiguous':'element_not_found'};
  const tag=job.expected.tag;
  if(!tag||!/^[a-z][a-z0-9-]*$/i.test(tag))return {status:'identity_changed'};
  const candidates=frame.locator(tag);
  // The original tag and all recorded identity fields must still agree.
  const infos=await candidates.evaluateAll(elements=>elements.map(e=>({tag:e.tagName.toLowerCase(),id:e.id,type:e.getAttribute('type'),name:e.getAttribute('name'),role:e.getAttribute('role'),text:e.innerText||'',aria_label:e.getAttribute('aria-label'),href:e.href||'',visible:!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden',disabled:!!e.disabled,in_form:!!e.closest('form'),form_method:e.closest('form')?.method,form_action:e.closest('form')?.action})));
  const matching=infos.map((actual,index)=>({actual,index})).filter(x=>identity(job.expected,x.actual));
  const hits=matching.filter(x=>x.actual.visible&&!x.actual.disabled);
  if(hits.length!==1)return {status:hits.length?'element_ambiguous':matching.length?'not_actionable':count===1?'identity_changed':'element_not_found'};
  return {locator:candidates.nth(hits[0].index),actual:hits[0].actual,resolution:'unique-recorded-identity'};
}

export async function probeOne(browser,job,out,{allowNetwork=false,timeout=20000,readyTimeout=8000,settle=700,loadPolicy='normal'}={}){
  const result={job_id:job.job_id,members:job.members,key:job.key,url:redact(job.url),status:'not_run',
    diagnostics:path.join(out,'page-state.json'),
    test_scope:'fresh-logged-out-context; network frozen before key; no credentials; no real-effect confirmation',
    consequence_tier:'unknown',confirmation_tier:'unknown',reachability:'unknown',danger_status:'unresolved',tier2_verified:false};
  if(!rules.has(job.rule_id)||!['Enter','Space','Escape'].includes(job.key)||!allowedURL(job.url))return {...result,status:'policy_skipped'};
  if(job.rule_id==='dialog-dismiss'&&(job.key!=='Escape'||job.frame_is_top||!job.frame_url||new URL(job.frame_url).pathname!=='/buyer-preferences/shipping-address'))return {...result,status:'policy_skipped'};
  if(!local(job.url)&&!allowNetwork)return {...result,status:'network_not_enabled'};
  const context=await browser.newContext({serviceWorkers:'block',acceptDownloads:false,permissions:[],locale:job.locale||'en-US',viewport:{width:1366,height:900}});
  let armed=false;const blocked=[];const dialogs=[];
  await context.addInitScript(GUARD);
  await context.route('**/*',async route=>{
    const request=route.request();
    if(armed||!loadRequestAllowed(request.method(),request.url(),request.postData()||'',loadPolicy==='strict')){
      blocked.push({phase:armed?'interaction':'load',method:request.method(),url:redact(request.url())});
      await route.abort('blockedbyclient');
    }else await route.continue();
  });
  const page=await context.newPage();page.setDefaultTimeout(2500);
  page.on('dialog',async dialog=>{dialogs.push({type:dialog.type(),message:dialog.message()});await dialog.dismiss();});
  try{
    const loaded=await openDocument(page,job,{timeout,readyTimeout,settle});
    if(loaded.status!=='page_ready')return {...result,status:loaded.status,load:loaded};
    let frame=page.mainFrame();
    if(!job.frame_is_top){const frames=page.frames().filter(f=>sameURL(f.url(),job.frame_url));if(frames.length!==1)return {...result,status:'frame_ambiguous'};frame=frames[0];}
    let resolved;const deadline=Date.now()+readyTimeout;
    do{resolved=await resolveLocator(frame,job);if(resolved.locator||resolved.status==='element_ambiguous')break;await page.waitForTimeout(250);}while(Date.now()<deadline);
    if(!resolved.locator)return {...result,status:resolved.status};
    const locator=resolved.locator;
    if(!await locator.isVisible()||!await locator.isEnabled())return {...result,status:'not_actionable'};
    const actual=await locator.evaluate(elementInfo);
    if(!identity(job.expected,actual))return {...result,status:'identity_changed',actual};
    if(!permitted(job.rule_id,actual))return {...result,status:'runtime_policy_skipped'};
    if(job.readiness_scroll)await locator.scrollIntoViewIfNeeded({timeout:2500});
    let readiness=null;
    if(job.readiness?.kind==='preact-keydown'){
     if(job.rule_id!=='terms-dialog')return {...result,status:'policy_skipped'};
     const end=Date.now()+Math.min(10000,job.readiness.timeout_ms||8000);
     do{
      readiness=await locator.evaluate(e=>({ready:!!e.__e&&Object.keys(e.__e).some(k=>/^(?:on)?keydown/i.test(k)&&typeof e.__e[k]==='function'),event_keys:Object.keys(e.__e||{}),key_handler_sources:Object.entries(e.__e||{}).filter(([k,v])=>/^(?:on)?keydown/i.test(k)&&typeof v==='function').map(([k,v])=>({key:k,source:String(v).slice(0,1500)})),ready_state:document.readyState}));
      if(readiness.ready)break;
      await page.waitForTimeout(250);
     }while(Date.now()<end);
     if(!readiness.ready)return {...result,status:'target_not_ready',readiness};
    }
    let idleControl=null;
    if(job.idle_control_ms){
     const idleBefore=await frame.evaluate(snapshot);
     await page.waitForTimeout(Math.max(1000,Math.min(5000,job.idle_control_ms)));
     const idleAfter=await frame.evaluate(snapshot);
     idleControl={no_focus_or_key:true,duration_ms:job.idle_control_ms,before:idleBefore,after:idleAfter,
      new_dialogs:idleAfter.dialogs.filter(d=>!idleBefore.dialogs.includes(d)),new_menus:idleAfter.menus.filter(d=>!idleBefore.menus.includes(d))};
    }
    if(!identity(job.expected,await locator.evaluate(elementInfo)))return {...result,status:'identity_changed'};
    // Arm before focus too: focus handlers may otherwise submit or make requests.
    armed=true;await Promise.all(page.frames().map(f=>f.evaluate(()=>window.__pilot?.arm()).catch(()=>{})));
    await locator.focus();
    const focusConfirmed=await locator.evaluate(e=>e===e.ownerDocument.activeElement);
    if(!focusConfirmed)return {...result,status:'focus_failed'};
    const before=await frame.evaluate(snapshot);
    const outerBefore=frame===page.mainFrame()?before:await page.mainFrame().evaluate(snapshot);
    const frameVisibleBefore=frame===page.mainFrame()?null:await (await frame.frameElement()).isVisible();
    const prefix=path.join(out,job.job_id+'-'+job.key);
    await page.screenshot({path:prefix+'-before.png'});
    let keyError=null;try{await locator.press(job.key==='Space'?' ':job.key,{timeout:3000});}catch(e){keyError=String(e.message).slice(0,400);}
    await page.waitForTimeout(settle);
    let after=null;try{after=await frame.evaluate(snapshot);}catch{}
    const outerAfter=await page.mainFrame().evaluate(snapshot).catch(()=>null);
    const frameVisibleAfter=frame===page.mainFrame()?null:frame.isDetached()?false:await (await frame.frameElement()).isVisible().catch(()=>false);
    const frameClosed=frameVisibleBefore===true&&frameVisibleAfter===false;
    await page.screenshot({path:prefix+'-after.png'}).catch(()=>{});
    const guards=(await Promise.all(page.frames().map(f=>f.evaluate(()=>window.__pilot?.events||[]).catch(()=>[])))).flat();
    const attempted=blocked.filter(x=>x.phase==='interaction');
    const changed=after&&JSON.stringify([before.body,before.inputs,before.dialogs,before.menus])!==JSON.stringify([after.body,after.inputs,after.dialogs,after.menus]);
    const uiEvidence=after?{
      new_dialogs:after.dialogs.filter(d=>!before.dialogs.includes(d)),
      new_menus:after.menus.filter(d=>!before.menus.includes(d)),
      input_change:inputValueChanged(before.inputs,after.inputs)
    }:{new_dialogs:[],new_menus:[],input_change:false};
    uiEvidence.frame_closed=frameClosed;
    uiEvidence.field_type_changes=(after?.field_types||[]).flatMap(x=>{const b=(before.field_types||[]).find(b=>b.key===x.key);return b&&b.type!==x.type?[{key:x.key,from:b.type,to:x.type}]:[];});
    uiEvidence.closed_dialogs=outerAfter?outerBefore.dialogs.filter(d=>!outerAfter.dialogs.includes(d)):[];
    const uiObserved=!!(uiEvidence.new_dialogs.length||uiEvidence.new_menus.length||uiEvidence.input_change||frameClosed||uiEvidence.closed_dialogs.length||uiEvidence.field_type_changes.length);
    const targetOutcome=assessTargetOutcome(job,before,after,uiEvidence);
    if(job.rule_id==='callback-preview')targetOutcome.blocked_popup_attempt=guards.some(g=>g.type==='popup');
    const status=(attempted.length||guards.length||dialogs.length)?'guarded_effect':keyError?'keypress_error':changed||uiObserved?'local_ui_changed':'no_observed_change';
    await fs.writeFile(prefix+'-observation.json',JSON.stringify({before,after,outerBefore,outerAfter,frameVisibleBefore,frameVisibleAfter,readiness,idleControl,blocked,guards,dialogs,keyError,uiEvidence,targetOutcome},null,2));
    return {...result,status,resolution:resolved.resolution,focus_confirmed:true,key_dispatched:!keyError,blocked_requests:attempted,guards,dialogs,
      local_change:!!changed,ui_observed:uiObserved,ui_evidence:uiEvidence,target_outcome:targetOutcome,readiness,idle_control:idleControl?{duration_ms:idleControl.duration_ms,new_dialogs:idleControl.new_dialogs,new_menus:idleControl.new_menus}:null,load_policy:loadPolicy,artifacts:[prefix+'-before.png',prefix+'-after.png',prefix+'-observation.json'],
      note:status==='guarded_effect'?'Effect intercepted. Do not infer successful execution or V0/V1/V2.':status==='no_observed_change'?'No change under isolated conditions. Do not infer C0 or unreachable.':'Local UI observation only; live effect remains unverified.'};
  }catch(e){return {...result,status:'error',error:String(e.message).slice(0,800)};}finally{
    const state={url:redact(page.url()),title:await page.title().catch(()=>''),blocked,locale:job.locale||'en-US'};
    try{await page.screenshot({path:path.join(out,'page-state.png'),timeout:3000});state.screenshot='page-state.png';}catch{}
    await fs.writeFile(path.join(out,'page-state.json'),JSON.stringify(state,null,2)).catch(()=>{});
    await context.close();
  }
}

async function main(){
 const args=process.argv.slice(2);const get=(flag,fallback)=>{const i=args.indexOf(flag);return i<0?fallback:args[i+1];};
 if(!get('--plan'))throw new Error('Usage: node probe.mjs --plan probe_plan.json --out results [--allow-network] [--limit 10] [--repeat 2] [--channel msedge]');
 const root=process.env.PILOT_PLAYWRIGHT_ROOT;
 const require=createRequire(root?path.join(root,'package.json'):import.meta.url);
 const {chromium}=require('playwright');
 const plan=JSON.parse(await fs.readFile(get('--plan'),'utf8'));if(plan.schema_version!==1||!Array.isArray(plan.jobs))throw new Error('Unsupported plan');
 const out=path.resolve(get('--out','probe-output'));await fs.mkdir(out,{recursive:true});
 const limit=Number(get('--limit','10')),repeat=Number(get('--repeat','2')),offset=Number(get('--offset','0'));
 const interval=Number(get('--interval-ms','2000'));
 if(!Number.isInteger(interval)||interval<2000||interval>60000)throw new Error('interval-ms must be 2000..60000');
 if(!Number.isInteger(limit)||limit<1||!Number.isInteger(repeat)||repeat<1||repeat>3||!Number.isInteger(offset)||offset<0)throw new Error('limit >=1, repeat 1..3, offset >=0 required');
 const members=new Set((get('--members','')).split(',').filter(Boolean));
 const candidates=members.size?plan.jobs.filter(j=>j.members.some(m=>members.has(m))):plan.jobs;
 const selected=candidates.slice(offset,offset+limit),preflightOnly=args.includes('--preflight-only');
 const loadPolicy=get('--load-policy','normal');if(!['normal','strict'].includes(loadPolicy))throw new Error('load-policy: normal or strict');
 const browser=await chromium.launch({headless:true,...(get('--channel')?{channel:get('--channel')}:{})});
 const runId=new Date().toISOString().replace(/[:.]/g,'-');const runDir=path.join(out,runId);await fs.mkdir(runDir);
 await fs.copyFile(get('--plan'),path.join(runDir,'probe_plan.json'));
 const started=Date.now(),all=[],omitted=[];const blockedOrigins=new Set(),pausedOrigins=new Map(),preflights=new Map(),loadFailures=new Map(),elementFailures=new Map();
 const planHash=crypto.createHash('sha256').update(await fs.readFile(path.join(runDir,'probe_plan.json'))).digest('hex');
 const inspectorHash=crypto.createHash('sha256').update(await fs.readFile(new URL('./probe.mjs',import.meta.url))).update(await fs.readFile(new URL('./loading.mjs',import.meta.url))).update(await fs.readFile(new URL('./outcome.mjs',import.meta.url))).digest('hex');
 function summary(completed){return {completed,run_id:runId,inspector_version:3,inspector_sha256:inspectorHash,duration_ms:Date.now()-started,load_policy:loadPolicy,preflight_only:preflightOnly,
  plan_sha256:planHash,
  baseline_sha256:plan.baseline_sha256,total_planned:plan.jobs.length,selected_offset:offset,selected:selected.length,not_run:plan.jobs.length-selected.length,
  repeat,interval_ms:interval,observations:all.length,omitted_trials:omitted,preflights:[...preflights.values()],final_confirmed:0,policy:'normal anonymous load; frozen network before focus/key; no dangerous-cell confirmation',
  stable:selected.map(j=>({job_id:j.job_id,statuses:all.filter(r=>r.job_id===j.job_id).map(r=>r.status),
   consistent:all.filter(r=>r.job_id===j.job_id).length>=2&&new Set(all.filter(r=>r.job_id===j.job_id).map(r=>r.status)).size===1}))};}
 const checkpoint=async completed=>fs.writeFile(path.join(runDir,'summary.json'),JSON.stringify(summary(completed),null,2));
 await fs.writeFile(path.join(runDir,'probe_results.jsonl'),'');await checkpoint(false);
 try{
  for(const job of selected){
   const origin=new URL(job.url).origin,pageKey=crypto.createHash('sha256').update(JSON.stringify([job.url,job.locale])).digest('hex').slice(0,16);
   if(!preflights.has(pageKey)){
    if(preflights.size)await new Promise(resolve=>setTimeout(resolve,interval));
    let state;
    if(!allowedURL(job.url))state={status:'policy_skipped'};
    else if(!local(job.url)&&!args.includes('--allow-network'))state={status:'network_not_enabled'};
    else if(blockedOrigins.has(origin))state={status:'site_skipped_after_block'};
    else if(pausedOrigins.has(origin))state={status:'site_paused_after_preflight_failure',previous_failure:pausedOrigins.get(origin)};
    else state=await preflightPage(browser,job,path.join(runDir,'preflight',pageKey));
    preflights.set(pageKey,{page_key:pageKey,url:redact(job.url),...state});
    if(state.status==='blocked_page')blockedOrigins.add(origin);
    if([403,429].includes(state.http_status)||['navigation_error','preflight_error'].includes(state.status))pausedOrigins.set(origin,state.status);
    console.log(JSON.stringify({phase:'preflight',site:origin,status:state.status}));await checkpoint(false);
   }
   if(preflightOnly)continue;
   const preflight=preflights.get(pageKey);
   const elementKey=JSON.stringify([pageKey,job.selector,job.expected,job.frame_url]);
   for(let attempt=1;attempt<=repeat;attempt++){
    const attemptDir=path.join(runDir,`${job.job_id}-${attempt}`);await fs.mkdir(attemptDir);
    let observation;
    const reason=blockedOrigins.has(origin)?'site_skipped_after_block':pausedOrigins.has(origin)?'site_paused_after_preflight_failure':preflight.status!=='page_ready'?'preflight_unavailable':(loadFailures.get(pageKey)||0)>=2?'page_circuit_open':elementFailures.has(elementKey)?'element_precheck_skipped':null;
    if(reason)observation={job_id:job.job_id,members:job.members,key:job.key,status:reason,preflight_status:preflight.status,previous_element_failure:elementFailures.get(elementKey),diagnostics:elementFailures.get(elementKey)?.diagnostics||preflight.diagnostics,consequence_tier:'unknown',confirmation_tier:'unknown',reachability:'unknown',danger_status:'unresolved',tier2_verified:false};
    else {
     if(attempt===1)await new Promise(resolve=>setTimeout(resolve,interval));
     observation=await probeOne(browser,job,attemptDir,{allowNetwork:args.includes('--allow-network'),loadPolicy});
    }
    if(observation.status==='blocked_page')blockedOrigins.add(origin);
    if([403,429].includes(observation.load?.http_status)||observation.status==='navigation_error')pausedOrigins.set(origin,observation.status);
    if(['navigation_error','document_not_ready','error'].includes(observation.status))loadFailures.set(pageKey,(loadFailures.get(pageKey)||0)+1);
    if(['element_not_found','identity_changed','element_ambiguous','not_actionable','frame_ambiguous','target_not_ready'].includes(observation.status))elementFailures.set(elementKey,{status:observation.status,job_id:job.job_id,diagnostics:observation.diagnostics});
    const r={job_id:job.job_id,members:job.members,key:job.key,url:redact(job.url),...observation,attempt};all.push(r);
    await fs.appendFile(path.join(runDir,'probe_results.jsonl'),JSON.stringify(r)+'\n');await checkpoint(false);
    console.log(JSON.stringify({job_id:r.job_id,attempt,status:r.status,ui_observed:!!r.ui_observed,members:r.members}));
    if(job.stop_on_target_miss&&r.key_dispatched&&r.target_outcome?.status==='not_observed'){
     if(attempt<repeat)omitted.push({job_id:job.job_id,trials:repeat-attempt,reason:'대상 결과 미관측. 동일 조건 반복보다 원인 진단을 우선'});
     break;
    }
    if(!r.key_dispatched){if(attempt<repeat)omitted.push({job_id:job.job_id,trials:repeat-attempt,reason:'키를 누르지 못한 상태라 같은 실패를 반복하지 않음'});break;}
    await new Promise(resolve=>setTimeout(resolve,interval));
   }
  }
  await checkpoint(true);
 }finally{await browser.close();}
 console.log('Saved: '+runDir);
}
if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href)main().catch(e=>{console.error(e.message);process.exitCode=1;});
