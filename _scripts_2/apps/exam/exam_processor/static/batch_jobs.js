const multijobExclusions={missing_job:'job 파일 없음',legacy_read_only:'legacy v1 · 읽기 전용',job_busy:'개별 실행 중',batch_reserved:'다른 실행 예약 또는 결과 불명확 기록',unfinished_job_queue:'미완료 개별 큐',unfinished_single_task:'미완료 개별 호출',not_pending_review:'검수 대기 상태 아님',current_audit_exists:'현재 revision 대조 완료',missing_evidence:'원본·의존성 근거 부족',transcription_pending:'전사 대기',missing_source_or_existing_transcription:'원본 없음 또는 기존 전사 있음',missing_or_unsupported_source_section:'지원되는 원본 영역 없음',no_pending_transcription:'전사 대기 영역 없음'};
let multijobLabels={jobs:{},items:{}},multijobReturn=null;
try{multijobReturn=JSON.parse(sessionStorage.getItem('exam-batch-return-v1')||'null');}catch{}
function multijobLabel(e,display=multijobLabels){
 const item=display.items?.[e.job_id+'/'+e.item];
 const kind={question:'문항',passage:'지문',script:'대본',concept:'개념'}[item?.kind]||'항목';
 return `${display.jobs?.[e.job_id]||e.job_id.slice(0,8)} / ${item?kind+' '+item.number:(e.item||'작업 전체')} / ${e.section==='solution'?'해설':'본문'}`;
}
function multijobPreviewText(plan,display=null){
 const d=display||{jobs:{},items:{}};
 return `선택 ${plan.job_ids.length}개 작업 · 실행 ${plan.entries.length}개 호출 단위 · 제외 ${plan.excluded.length}개 항목/작업 · 비용 미확인\n\n처리 순서\n`+
 plan.entries.map((e,i)=>`${i+1}. ${multijobLabel(e,d)}`).join('\n')+'\n\n제외 사유\n'+
 plan.excluded.map(e=>`${multijobLabel(e,d)}: ${(d.items?.[e.job_id+'/'+e.item]?.details?.length?d.items[e.job_id+'/'+e.item].details:e.reasons.map(r=>multijobExclusions[r]||r)).join(', ')}`).join('\n');
}
function multijobShowPreview(pre){
 const d=pre.display?.plan_hash===pre.plan_hash?pre.display:null;
 if(d)multijobLabels={jobs:{...multijobLabels.jobs,...d.jobs},items:{...multijobLabels.items,...d.items}};
 $('multijob-preview-result').textContent=multijobPreviewText(pre.plan,d);
}
let multijobMode='draft',multijobConfirmed=null,multijobSequence=0;
let multijobDraft={ids:[],operation:'audit',reason:'',batchId:null,pending:null,mode:'draft'};
try{multijobDraft={...multijobDraft,...JSON.parse(sessionStorage.getItem('exam-batch-draft-v1')||'{}')};}catch{}
let multijobView=null,multijobPreview=null,multijobBusy=false,multijobPending=multijobDraft.pending;
multijobMode=multijobDraft.mode;
function multijobPersist(){multijobDraft={...multijobDraft,...{ids:multijobSelection().job_ids,operation:$('multijob-operation').value,reason:$('multijob-reason').value,batchId:multijobView?.id||multijobDraft.batchId,pending:multijobPending,mode:multijobMode}};sessionStorage.setItem('exam-batch-draft-v1',JSON.stringify(multijobDraft));}
const multijobNames={planned:'계획 저장됨',running:'실행 중',interrupted:'worker 없음 · 복구 확인 필요',paused:'중단됨',finished:'처리 종료',ended:'명시 종료',pending:'미실행',completed:'완료',failed:'실패',uncertain:'결과 불명확',skipped:'변경/제외',abandoned:'미실행 종료'};
function multijobSelection(){return {job_ids:[...$('multijob-jobs').querySelectorAll('input:checked')].map(x=>x.value),operation:$('multijob-operation').value};}
function multijobButtons(){
 $('multijob-dialog').querySelectorAll('button,select,input').forEach(x=>x.disabled=multijobBusy);$('multijob-reason').readOnly=multijobBusy;
 const v=multijobView;
 $('multijob-plan').disabled=multijobBusy||!!multijobPending||!multijobPreview;
 $('multijob-execute').disabled=multijobBusy||!!multijobPending||multijobMode!=='confirm'||multijobConfirmed!==flowBasis(v)||v?.status!=='planned'||!$('multijob-confirm').checked||!v?.entries.length;
 $('multijob-pause').disabled=multijobBusy||!!multijobPending||!v||!['running','interrupted','paused'].includes(v.status);
 for(const a of ['recover','resume','retry','end'])$('multijob-'+a).disabled=multijobBusy||!!multijobPending||!v||v.worker_live||['planned','ended'].includes(v.status);
 $('multijob-recover').disabled ||= !['running','interrupted','paused'].includes(v?.status);
 $('multijob-resume').disabled ||= !v?.counts.pending||!!v?.counts.uncertain;
 $('multijob-retry').disabled ||= !v?.counts.failed||!!v?.counts.uncertain;
 $('multijob-confirm').disabled=multijobBusy||!!multijobPending||multijobMode!=='confirm'||v?.status!=='planned';
 $('multijob-editor').hidden=multijobMode!=='draft';
 $('multijob-confirm-area').hidden=multijobMode!=='confirm';
 $('multijob-result-area').hidden=multijobMode!=='result';
 $('multijob-plan-view').hidden=multijobMode==='draft';
 $('multijob-step').textContent={draft:'1. 처리 대상',confirm:'2. 실행 대상 확인',result:'3. 처리 결과'}[multijobMode];
 $('multijob-edit').hidden=multijobMode==='draft';
 $('multijob-back').hidden=!v||multijobMode!=='draft';
 $('multijob-resend').hidden=!multijobPending;$('multijob-resend').disabled=multijobBusy||!multijobPending;
 for(const id of ['multijob-pause','multijob-resume','multijob-recover','multijob-retry'])$(id).hidden=$(id).disabled&&!multijobPending;
 flowRecovery('multijob-recovery',!!(v?.counts.failed||v?.counts.uncertain||v?.status==='interrupted'));
}
async function multijobRun(fn){if(multijobBusy)return;multijobBusy=true;multijobButtons();try{await fn();}catch(e){multijobConfirmed=null;$('multijob-confirm').checked=false;$('multijob-feedback').textContent=e.message+' 선택과 사유는 유지됩니다.';}finally{multijobBusy=false;multijobPersist();multijobButtons();}}
function renderMultijob(){
 const v=multijobView;if(!v){$('multijob-summary').textContent='저장된 계획 없음 또는 미선택';for(const id of ['multijob-plan-view','multijob-entries','multijob-retry-targets','multijob-history'])$(id).textContent='';return;}
 const c=v.counts;
 flowStatus('multijob-summary',`${multijobNames[v.status]}${v.stop_requested&&v.worker_live?' · 중단 요청됨 · 현재 호출 종료 대기':''} · ${v.worker_live?'worker 동작 확인':'동작 중 worker 없음'} · 완료 ${c.completed} / 실패 ${c.failed} / 미실행 ${c.pending} / 진행 ${c.running} / 제외 ${c.skipped} / 결과 불명확 ${c.uncertain} / 미실행 종료 ${c.abandoned} · 비용 미확인`);
 $('multijob-plan-view').innerHTML=`<p>현재 표시: 저장된 ${esc(v.plan.operation==='audit'?'원본 대조':'전사')} 계획 · ${v.plan.job_ids.length}개 작업</p><pre>${esc(multijobPreviewText(v.plan,{jobs:multijobLabels.jobs,items:Object.fromEntries(Object.entries(multijobLabels.items).map(([k,x])=>[k,{...x,details:[]}]))}))}</pre><p>배치 ${esc(v.id)} · revision ${v.revision} · hash ${esc(v.plan_hash)} · 계획된 호출 단위 ${v.plan.planned_calls} · 단계 ${esc(v.plan.operation)}</p><details><summary>고정 대상·revision·원본 해시·처리 순서·제외 이유</summary><pre>${esc(JSON.stringify(v.plan,null,2))}</pre></details>`;
 const openJobs=[...$('multijob-entries').querySelectorAll('details[open]')].map(e=>e.dataset.job);
 const shown=v.entries.filter(e=>$('multijob-result-filter').value==='all'||e.status===$('multijob-result-filter').value);const groups=[...new Set(shown.map(e=>e.job_id))];
 $('multijob-entries').innerHTML=groups.map(jid=>{const es=shown.filter(e=>e.job_id===jid);return `<article><strong>${esc(multijobLabels.jobs[jid]||jid.slice(0,8))}</strong><p>완료 ${es.filter(e=>e.status==='completed').length} / 실패 ${es.filter(e=>e.status==='failed').length} / 결과 불명확 ${es.filter(e=>e.status==='uncertain').length}</p><button data-review-job="${jid}">이 작업 검수로 이동</button><details data-job="${jid}" ${openJobs.includes(jid)?'open':''}><summary>문항별 결과 · 현재 이름 조회</summary>${es.map(e=>`<div class="batch-result-row"><strong>${esc(multijobLabel(e))} · ${multijobNames[e.status]}</strong><p>${esc(e.message||'')}</p><button data-review-job="${jid}" data-review-item="${esc(e.item)}">이 항목 검수로 이동</button></div>`).join('')}</details></article>`;}).join('');
 const selected=[...$('multijob-retry-targets').selectedOptions].map(x=>x.value);
 $('multijob-retry-targets').innerHTML=v.entries.filter(e=>e.status==='failed').map(e=>`<option value="${e.id}">${esc(multijobLabel(e))}</option>`).join('');
 [...$('multijob-retry-targets').options].forEach(x=>x.selected=selected.includes(x.value));
 $('multijob-history').textContent=JSON.stringify({attempts:v.attempts,events:v.events},null,2);multijobButtons();
}
async function multijobList(){
 const runs=await api('/api/batches');$('multijob-runs').innerHTML='<option value="">선택</option>'+runs.slice().reverse().map(v=>`<option value="${v.id}">${esc(v.created_at)} · ${esc(v.id.slice(0,8))} · ${multijobNames[v.status]}</option>`).join('');$('multijob-runs').value=multijobView?.id||'';
}
async function multijobSend(action,extra={}){
 if(multijobPending)throw Error('미확인 요청의 결과를 먼저 확인하세요.');
 if(action==='execute'&&(multijobMode!=='confirm'||multijobConfirmed!==flowBasis(multijobView)))throw Error('저장된 실행 대상을 다시 확인하세요.');
 const candidate={action,...(action!=='plan'?{batch_id:multijobView.id,revision:multijobView.revision}:{}),...extra};
 multijobPending={candidate:structuredClone(candidate),payload:structuredClone({...candidate,request_id:crypto.randomUUID()})};multijobPersist();
 try{multijobView=flowResponse(await api('/api/batches',multijobPending.payload));}catch(e){if(flowRejected(e))multijobPending=null;throw e;}multijobPending=null;multijobConfirmed=null;multijobMode=multijobView.status==='planned'?'confirm':'result';$('multijob-confirm').checked=false;await multijobList();renderMultijob();$('multijob-feedback').textContent='배치 기록 반영됨 · 승인/평가 판정 자동 생성 없음';
}
$('multijob-open').onclick=()=>guard(()=>multijobRun(async()=>{
 multijobConfirmed=null;$('multijob-confirm').checked=false;const jobs=await api('/api/jobs');multijobLabels={jobs:Object.fromEntries(jobs.map(j=>[j.id,j.source||j.id])),items:{}};$('multijob-jobs').innerHTML=jobs.map(j=>`<label><input type="checkbox" value="${esc(j.id)}" ${multijobDraft.ids.includes(j.id)?'checked':''}>${esc(j.source||j.id)} · ${esc(j.id.slice(0,8))}</label>`).join('');
 $('multijob-operation').value=multijobDraft.operation;$('multijob-reason').value=multijobDraft.reason;if(!multijobView&&multijobDraft.batchId)multijobView=await api('/api/batches/'+multijobDraft.batchId);multijobPreview=null;$('multijob-preview-result').textContent='';await multijobList();if(multijobView){multijobView=await api('/api/batches/'+multijobView.id);if(multijobMode!=='draft')multijobMode=multijobView.status==='planned'?'confirm':'result';}renderMultijob();$('multijob-dialog').showModal();
}));
$('multijob-preview').onclick=()=>multijobRun(async()=>{const seq=++multijobSequence;const pre=await api('/api/batches/preview',multijobSelection());if(seq!==multijobSequence||!$('multijob-dialog').open)return;multijobPreview=pre;multijobShowPreview(multijobPreview);$('multijob-feedback').textContent='읽기 전용 미리보기 · AI 호출 없음';});
function multijobInvalidate(){multijobSequence++;multijobMode='draft';multijobConfirmed=null;$('multijob-confirm').checked=false;multijobPreview=null;$('multijob-preview-result').textContent='선택이 바뀌었습니다. 다시 미리 확인하세요.';multijobPersist();multijobButtons();}
$('multijob-jobs').onchange=multijobInvalidate;$('multijob-operation').onchange=multijobInvalidate;
$('multijob-plan').onclick=()=>multijobRun(()=>multijobSend('plan',{...multijobSelection(),preview_hash:multijobPreview.plan_hash}));
$('multijob-confirm').onchange=()=>{multijobConfirmed=$('multijob-confirm').checked?flowBasis(multijobView):null;multijobButtons();};
$('multijob-execute').onclick=()=>multijobRun(()=>multijobSend('execute',{confirmed_plan_hash:multijobView.plan_hash}));
for(const a of ['pause','resume','recover'])$('multijob-'+a).onclick=()=>multijobRun(()=>multijobSend(a));
$('multijob-retry').onclick=()=>multijobRun(()=>multijobSend('retry',{entry_ids:[...$('multijob-retry-targets').selectedOptions].map(x=>x.value),reason:$('multijob-reason').value}));
$('multijob-end').onclick=()=>multijobRun(()=>multijobSend('end',{reason:$('multijob-reason').value}));
$('multijob-runs').onchange=()=>multijobRun(async()=>{multijobConfirmed=null;multijobSequence++;multijobView=$('multijob-runs').value?await api('/api/batches/'+$('multijob-runs').value):null;$('multijob-confirm').checked=false;multijobMode=multijobView?(multijobView.status==='planned'?'confirm':'result'):'draft';multijobDraft.batchId=multijobView?.id||null;renderMultijob();});
$('multijob-reload').onclick=()=>multijobRun(async()=>{multijobLabels={jobs:{},items:{}};multijobConfirmed=null;$('multijob-confirm').checked=false;if(multijobView)multijobView=await api('/api/batches/'+multijobView.id);await multijobList();renderMultijob();});
$('multijob-close').onclick=()=>{if(!multijobBusy)$('multijob-dialog').close();};
$('multijob-dialog').addEventListener('close',()=>{multijobSequence++;multijobConfirmed=null;$('multijob-confirm').checked=false;});
$('multijob-dialog').addEventListener('cancel',e=>{if(multijobBusy)e.preventDefault();});
let multijobPolling=false;
setInterval(async()=>{
 if(!$('multijob-dialog').open||document.hidden||multijobBusy||multijobPending||multijobPolling||!['running','interrupted'].includes(multijobView?.status))return;
 const view=multijobView;multijobPolling=true;
 try{const fresh=await api('/api/batches/'+view.id);if(multijobView===view&&!multijobBusy&&!multijobPending&&$('multijob-dialog').open){multijobView=fresh;flowRefresh('multijob-dialog',renderMultijob);}}
 catch(e){flowStatus('multijob-feedback','상태 조회 실패 · 현재 기록 유지: '+e.message);}
 finally{multijobPolling=false;}
},1500);

$('multijob-dialog').addEventListener('close',async()=>{const jid=job?.id;if(!jid||dirty||saving)return;try{const fresh=await api('/api/jobs/'+jid);if(job?.id===jid&&!dirty&&!saving){job=fresh;render();updateJobButton();}}catch(e){notice('배치 결과는 저장되어 있습니다. 문항 재조회 실패: '+e.message);}});

$('multijob-reason').oninput=multijobPersist;
$('multijob-edit').onclick=()=>{multijobMode='draft';multijobConfirmed=null;$('multijob-confirm').checked=false;multijobButtons();multijobPersist();};
$('multijob-back').onclick=()=>{multijobMode=multijobView?.status==='planned'?'confirm':'result';multijobConfirmed=null;$('multijob-confirm').checked=false;renderMultijob();multijobPersist();};
$('multijob-resend').onclick=()=>multijobRun(async()=>{
 if(!multijobPending)return;
 try{multijobView=flowResponse(await api('/api/batches',multijobPending.payload));}catch(e){if(flowRejected(e))multijobPending=null;throw e;}
 multijobPending=null;multijobConfirmed=null;$('multijob-confirm').checked=false;
 multijobMode=multijobView.status==='planned'?'confirm':'result';await multijobList();renderMultijob();
 $('multijob-feedback').textContent='동일 요청 ID로 저장 결과를 확인했습니다.';
});

const multijobNamesLoading=new Set();
$('multijob-entries').addEventListener('toggle',async e=>{
 const jid=e.target.dataset.job;if(!e.target.open||!jid||multijobNamesLoading.has(jid))return;
 if(Object.keys(multijobLabels.items).some(k=>k.startsWith(jid+'/')))return;
 multijobNamesLoading.add(jid);const labels=multijobLabels;
 try{const fresh=await api('/api/jobs/'+jid);if(labels!==multijobLabels||!$('multijob-dialog').open)return;multijobLabels.jobs[jid]=fresh.source||jid;for(const i of fresh.items)multijobLabels.items[jid+'/'+i.id]={number:i.number,kind:i.kind};renderMultijob();}
 catch(error){$('multijob-feedback').textContent='현재 이름 조회 불가 · ID를 표시합니다. '+error.message;}
 finally{multijobNamesLoading.delete(jid);}
},true);
function multijobReturnButton(){
 const visible=!!(multijobReturn&&job?.id===multijobReturn.job_id);
 $('multijob-return').hidden=!visible;
}
window.multijobNavigationChanged=(id)=>{if(multijobReturn&&id!==multijobReturn.job_id){multijobReturn=null;sessionStorage.removeItem('exam-batch-return-v1');}multijobReturnButton();};
$('multijob-entries').onclick=e=>{const button=e.target.closest('[data-review-job]');if(!button)return;guard(async()=>{
 const jid=button.dataset.reviewJob,rid=button.dataset.reviewItem;
 const context={batch_id:multijobView.id,job_id:jid,item_id:rid||null,operation:$('multijob-operation').value,selected_entry_ids:[...$('multijob-retry-targets').selectedOptions].map(x=>x.value),job_ids:multijobSelection().job_ids,filter:$('multijob-result-filter').value,recovery_open:$('multijob-recovery').open,plan_details_open:!!$('multijob-plan-view').querySelector('details[open]'),open_jobs:[...$('multijob-entries').querySelectorAll('details[open]')].map(e=>e.dataset.job),scroll:$('multijob-dialog').scrollTop};
 await loadJob(jid,{itemId:rid});
 multijobReturn=context;sessionStorage.setItem('exam-batch-return-v1',JSON.stringify(context));multijobReturnButton();$('multijob-dialog').close();$('item-title').focus();
});};
$('multijob-return').onclick=()=>guard(async()=>{
 if(!multijobReturn)return;await save();const context=multijobReturn;
 const v=await api('/api/batches/'+context.batch_id),jobs=await api('/api/jobs');
 multijobLabels={jobs:Object.fromEntries(jobs.map(j=>[j.id,j.source||j.id])),items:{}};
 $('multijob-jobs').innerHTML=jobs.map(j=>`<label><input type="checkbox" value="${esc(j.id)}" ${context.job_ids.includes(j.id)?'checked':''}>${esc(j.source||j.id)} · ${esc(j.id.slice(0,8))}</label>`).join('');
 multijobView=v;multijobMode='result';multijobConfirmed=null;$('multijob-confirm').checked=false;
 for(const box of $('multijob-jobs').querySelectorAll('input'))box.checked=context.job_ids.includes(box.value);
 $('multijob-operation').value=context.operation;$('multijob-result-filter').value=context.filter;
 for(const jid of context.open_jobs||[]){
  try{const fresh=await api('/api/jobs/'+jid);for(const i of fresh.items)multijobLabels.items[jid+'/'+i.id]={number:i.number,kind:i.kind};}
  catch(e){flowStatus('multijob-feedback','현재 이름 조회 불가 · ID를 표시합니다. '+e.message);}
 }
 await multijobList();renderMultijob();
 $('multijob-recovery').open=!!context.recovery_open;
 const planDetails=$('multijob-plan-view').querySelector('details');if(planDetails)planDetails.open=!!context.plan_details_open;
 for(const o of $('multijob-retry-targets').options)o.selected=context.selected_entry_ids.includes(o.value);
 const missing=context.selected_entry_ids.filter(id=>![...$('multijob-retry-targets').options].some(o=>o.value===id));
 if(missing.length)$('multijob-feedback').textContent='현재 결과에 없는 이전 선택을 해제했습니다.';
 for(const detail of $('multijob-entries').querySelectorAll('details[data-job]'))detail.open=(context.open_jobs||[]).includes(detail.dataset.job);
 $('multijob-dialog').showModal();$('multijob-dialog').scrollTop=context.scroll;multijobPersist();
});
$('multijob-result-filter').onchange=()=>{renderMultijob();multijobPersist();};
