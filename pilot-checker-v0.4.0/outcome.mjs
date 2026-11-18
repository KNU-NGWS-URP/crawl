// Target-specific evidence is separate from any UI change or intercepted request.
export function assessTargetOutcome(job,before,after,evidence={}){
 const id=job.expected?.id;
 if(job.rule_id==='password-form-preview')return {status:(evidence.field_type_changes||[]).some(x=>x.key==='password'&&x.from==='password'&&x.to==='text')?'matched':'not_observed',basis:'password field visibility changed; no credentials entered or submitted'};
 if(job.rule_id==='dialog-dismiss')return {status:evidence.frame_closed||evidence.closed_dialogs?.length?'matched':'not_observed',basis:'original iframe hidden/detached or its containing dialog closed'};
 if(job.rule_id==='terms-dialog'){
  const signals=['Offer valid','IKEA Family and IKEA Business Network Offer','U.S. only while supplies last.'];
  const texts=(evidence.new_dialogs||[]).filter(t=>!/digital partners use cookies|Your privacy choices/i.test(t));
  const matched=signals.some(s=>texts.some(t=>t.includes(s))||(!(before?.body||'').includes(s)&&(after?.body||'').includes(s)));
  return {status:matched?'matched':'not_observed',basis:'new offer-terms content; cookie/privacy notices do not qualify'};
 }
 if(job.expected?.aria_label==='Share'||job.expected?.title==='Share'){
  const matched=(evidence.new_dialogs||[]).some(t=>t==='Share Popup'||/Share (?:this|product|item)/i.test(t))||(evidence.new_menus||[]).some(t=>t==='Share');
  return {status:matched?'matched':'not_observed',basis:'new sharing menu or sharing dialog'};
 }
 if(['autocomplete-clear-button','autocomplete-search-button'].includes(id))return {status:'not_specified',basis:'input-specific correlation required before target-effect confirmation'};
 return {status:'not_specified',basis:'UI delta alone is not target-effect proof'};
}
