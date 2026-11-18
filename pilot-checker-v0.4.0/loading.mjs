import fs from 'node:fs/promises';
import path from 'node:path';

export function loadRequestAllowed(method,url,body='',strict=false){
  if(!['GET','HEAD','POST'].includes(method))return false;
  let parsed;try{parsed=new URL(url);}catch{return false;}
  if(!['http:','https:'].includes(parsed.protocol)||parsed.username||parsed.password)return false;
  if(/\/(?:logout|delete|remove|unsubscribe|subscribe|checkout|pay|purchase|redeem|transfer|confirm|addtocart|add-to-cart)(?:[/?.-]|$)/i.test(parsed.pathname))return false;
  if(method!=='POST')return true;
  if(strict)return false;
  // Initial page hydration may use POST to read data. No keyboard interaction is permitted in this phase.
  // Explicit mutations and supplied credentials remain blocked; unknown initial POST is not claimed read-only.
  if(/\bmutation\s*[({\w]|"operationName"\s*:\s*"(?:create|delete|submit|send|purchase|update)/i.test(body))return false;
  if(/"(?:password|passwd|creditCardNumber|cardNumber)"\s*:\s*"[^"\s]+"/i.test(body))return false;
  return true;
}

export async function openDocument(page,job,{timeout=20000,readyTimeout=8000,settle=700}={}){
  let response=null,navigationError=null;
  try{response=await page.goto(job.url,{waitUntil:'commit',timeout});}catch(error){navigationError=String(error.message).split('\n')[0];}
  if(navigationError&&(page.url()==='about:blank'||/ERR_HTTP2|ERR_NETWORK|ERR_NAME|ERR_CONNECTION|ERR_CERT/i.test(navigationError)))return {status:'navigation_error',error:navigationError};
  try{await page.waitForFunction(()=>document.body&&document.body.innerText.trim().length>20,null,{timeout:readyTimeout});}
  catch{return {status:'document_not_ready',error:navigationError||'본문 준비 시간 초과'};}
  if(settle)await page.waitForTimeout(settle);
  let title,body,url;
  for(let attempt=0;attempt<3;attempt++){
    try{title=await page.title();body=(await page.locator('body').innerText({timeout:2000})).slice(0,12000);url=page.url();break;}
    catch(error){if(attempt===2)return {status:'document_not_ready',error:String(error.message).split('\n')[0]};await page.waitForTimeout(250);}
  }
  const httpStatus=response?.status()??null;
  if(/quick verification|press\s*&\s*hold|verify you are human|robot or human|captcha|access denied|접근이 차단|로봇.*확인/i.test(body))return {status:'blocked_page',title,url,http_status:httpStatus};
  if((httpStatus&&httpStatus>=400)||/^error page\b|page not found|service unavailable/i.test(title))return {status:'page_error',title,url,http_status:httpStatus};
  try{const expected=new URL(job.url),actual=new URL(url);if(expected.pathname!=='/'&&actual.pathname==='/'&&expected.pathname!==actual.pathname)return {status:'redirected_away',title,url,http_status:httpStatus};}catch{}
  return {status:'page_ready',title,url,http_status:httpStatus,navigation_warning:navigationError};
}

export async function inventory(page){
  return page.evaluate(()=>({url:location.href,title:document.title,
    controls:[...document.querySelectorAll('button,a,input,select,textarea,[role=button]')].slice(0,2000).map(e=>({
      tag:e.tagName.toLowerCase(),id:e.id,name:e.getAttribute('name'),type:e.getAttribute('type'),role:e.getAttribute('role'),
      text:(e.innerText||'').replace(/\s+/g,' ').trim().slice(0,200),aria_label:e.getAttribute('aria-label'),
      href:e.href||null,visible:!!e.getClientRects().length,disabled:!!e.disabled,in_form:!!e.closest('form')
    }))}));
}

export async function preflightPage(browser,job,out,{timeout=20000,readyTimeout=8000,settle=700}={}){
  const context=await browser.newContext({serviceWorkers:'block',acceptDownloads:false,permissions:[],locale:job.locale||'en-US',viewport:{width:1366,height:900}});
  const requests=[],failed=[];const started=Date.now();let state;
  await context.route('**/*',async route=>{
    const r=route.request();const allowed=loadRequestAllowed(r.method(),r.url(),r.postData()||'');
    if(requests.length<1000)requests.push({method:r.method(),url:r.url().split('?')[0],allowed});
    if(allowed)await route.continue();else await route.abort('blockedbyclient');
  });
  const page=await context.newPage();page.on('dialog',d=>d.dismiss());
  page.on('requestfailed',r=>{if(failed.length<100)failed.push({url:r.url().split('?')[0],error:r.failure()?.errorText});});
  try{
    state=await openDocument(page,job,{timeout,readyTimeout,settle});
    await fs.mkdir(out,{recursive:true});
    const current=await inventory(page).catch(error=>({url:page.url(),error:String(error.message).split('\n')[0]}));
    await fs.writeFile(path.join(out,'inventory.json'),JSON.stringify(current,null,2));
    const content=await page.content().catch(()=>'');
    await fs.writeFile(path.join(out,'page.html'),content);
    await page.screenshot({path:path.join(out,'page.png'),timeout:3000}).catch(()=>{});
    state={...state,requests,failed,duration_ms:Date.now()-started,diagnostics:path.join(out,'preflight.json'),scope:'fresh logged-out page load; no focus, key, login or click; normal initial POST allowed except explicit mutations'};
    await fs.writeFile(path.join(out,'preflight.json'),JSON.stringify(state,null,2));
    return state;
  }catch(error){
    state={status:'preflight_error',error:String(error.message).split('\n')[0],url:page.url(),requests,failed,duration_ms:Date.now()-started,diagnostics:path.join(out,'preflight.json')};
    await fs.mkdir(out,{recursive:true});await fs.writeFile(path.join(out,'preflight.json'),JSON.stringify(state,null,2));return state;
  }finally{await context.close().catch(()=>{});}
}
