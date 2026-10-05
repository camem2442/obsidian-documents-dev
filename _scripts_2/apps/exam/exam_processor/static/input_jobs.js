/* Per-window durable drafts and request IDs survive reload without overwriting other windows. */
let inputView=null,inputPreview=null,inputBusy=false,inputPending=null,inputCatalog=[];
let inputMode='draft',inputConfirmed=null,inputSequence=0;
const inputKey='exam-input-draft-v1';
const inputNames={ready:'신규 생성 가능',missing_conditions:'조건 부족',unsupported:'미지원 형식',ambiguous:'형식 미확인',duplicate:'중복 의심',existing:'기존 작업 관련',planned:'계획 저장됨',pending:'대기',running:'생성 중',interrupted:'중단됨 · 복구 확인 필요',paused:'중단됨',finished:'준비 처리 종료',ended:'명시 종료',completed:'job 준비 완료',failed:'실패',uncertain:'결과 불명확',excluded:'제외',stale:'원본 변경 · 새 계획 필요',abandoned:'미실행 종료'};
let inputDraft={units:[],planId:null,reason:'',pending:null,mode:'draft'};
try{inputDraft={...inputDraft,...JSON.parse(sessionStorage.getItem(inputKey)||'{}')};inputPending=inputDraft.pending;inputMode=inputDraft.mode||'draft';}catch{}
function inputPersist(){inputDraft.planId=inputView?.id||inputDraft.planId;inputDraft.reason=$('inputs-reason').value;inputDraft.pending=inputPending;inputDraft.mode=inputMode;sessionStorage.setItem(inputKey,JSON.stringify(inputDraft));}
function inputField(k,label,v){return `<label>${label}<input data-field="${k}" value="${esc(v[k]||'')}"></label>`;}
function inputDrawUnits(){
 $('inputs-units').innerHTML=inputDraft.units.map((v,i)=>{const study=['numbered-qa-v1','numbered-table-v1'].includes(v.format),math=v.format==='hanwangi_2026_probability',english=v.format==='kice_english';return `<article data-unit="${i}"><strong>입력 ${i+1}</strong><div class="inputs-actions"><button data-move="-1">위로</button><button data-move="1">아래로</button><button data-remove="1">삭제</button></div><div class="inputs-grid"><label>형식<select data-field="format"><option value="">명시적으로 선택</option>${inputCatalog.map(f=>`<option value="${f.id}" ${v.format===f.id?'selected':''}>${esc(f.label)}</option>`).join('')}</select></label>${inputField('path','원본 전체 경로',v)}${study?inputField('repo_root','공부 원본 루트 (자료를 포함하는 폴더)',v)+inputField('subject','과목',v)+inputField('question_type','문항 유형',v)+inputField('unit','단원 ( / 로 구분 · 선택)',v):inputField('source','출처 (필수)',v)+`<label>과목<select data-field="track"><option value="">선택</option>${(inputCatalog.find(f=>f.id===v.format)?.tracks||[]).map(t=>`<option ${t===v.track?'selected':''}>${esc(t)}</option>`).join('')}</select></label>`+(!math&&v.format!=='korean_notes'?inputField('pages','연속 페이지 범위 (빈 값: 전체)',v):'')+inputField('solution',math?'해설 PDF (필수)':'해설 전체 경로 (선택)',v)+(math?inputField('structure','교재 구조 기록 (필수)',v)+inputField('layout','확인된 영역 기록 (필수)',v):'')+(english?inputField('script','대본 PDF (선택)',v)+inputField('answer','정답 PDF/PNG (선택)',v):'')}</div>${!study?`<label><input data-field="relationship_confirmed" type="checkbox" ${v.relationship_confirmed?'checked':''}> 관련 파일의 역할과 이 원본과의 관계를 직접 확인했습니다.</label>`:''}<label><input type="checkbox" data-field="included" ${v.included!==false?'checked':''}> 이 입력 포함</label>${inputField('exclude_reason','제외 사유 (제외 시 필수)',v)}</article>`;}).join('');inputButtons();
}
function inputInvalidate(){inputSequence++;inputMode='draft';inputConfirmed=null;$('inputs-confirm').checked=false;inputPreview=null;$('inputs-preview-result').textContent='조건/선택 변경됨 · 미리보기를 다시 확인하세요. 저장된 기존 계획은 바뀌지 않습니다.';inputPersist();inputButtons();}
$('inputs-units').oninput=e=>{const row=e.target.closest('[data-unit]'),key=e.target.dataset.field;if(!row||!key)return;inputDraft.units[+row.dataset.unit][key]=e.target.type==='checkbox'?e.target.checked:e.target.value;inputInvalidate();};
$('inputs-units').onchange=e=>{if(e.target.dataset.field==='format'){const row=e.target.closest('[data-unit]'),v=inputDraft.units[+row.dataset.unit];for(const k of ['track','source','pages','solution','script','answer','structure','layout','repo_root','subject','question_type','unit'])v[k]='';v.relationship_confirmed=false;inputDrawUnits();inputPersist();}};
$('inputs-units').onclick=e=>{const row=e.target.closest('[data-unit]');if(!row||inputBusy)return;const i=+row.dataset.unit;if(e.target.dataset.remove)inputDraft.units.splice(i,1);else if(e.target.dataset.move){const j=i+Number(e.target.dataset.move);if(j>=0&&j<inputDraft.units.length)[inputDraft.units[i],inputDraft.units[j]]=[inputDraft.units[j],inputDraft.units[i]];}else return;inputDrawUnits();inputInvalidate();};
$('inputs-add').onclick=()=>{inputDraft.units.push({id:crypto.randomUUID(),included:true,format:''});inputDrawUnits();inputInvalidate();};
function inputButtons(){
 const v=inputView;$('inputs-dialog').querySelectorAll('button,input,select').forEach(e=>e.disabled=inputBusy);
 $('inputs-plan').disabled=inputBusy||!!inputPending||!inputPreview;
 $('inputs-resend').disabled=inputBusy||!inputPending;
 $('inputs-execute').disabled=inputBusy||!!inputPending||inputMode!=='confirm'||inputConfirmed!==flowBasis(v)||v?.status!=='planned'||!$('inputs-confirm').checked||!v?.counts.pending;
 $('inputs-pause').disabled=inputBusy||!!inputPending||v?.status!=='running';
 for(const a of ['recover','resume','retry','end'])$('inputs-'+a).disabled=inputBusy||!!inputPending||!v||v.worker_live||['planned','ended'].includes(v.status);
 $('inputs-resume').disabled ||= !v?.counts.pending||!!v?.counts.uncertain;
 $('inputs-retry').disabled ||= !v?.counts.failed||!!v?.counts.uncertain;
 $('inputs-batch').disabled=inputBusy||!!inputPending||!v?.counts.completed||!$('inputs-operation').value;
 $('inputs-confirm').disabled=inputBusy||!!inputPending||inputMode!=='confirm'||v?.status!=='planned';
 $('inputs-editor').hidden=inputMode!=='draft';
 $('inputs-preview-result').hidden=inputMode!=='draft';
 $('inputs-fixed').hidden=inputMode!=='confirm';
 $('inputs-confirm-area').hidden=inputMode!=='confirm';
 $('inputs-results-area').hidden=inputMode!=='result';
 $('inputs-back').hidden=!v||inputMode!=='draft';
 $('inputs-edit').hidden=inputMode==='draft';
 $('inputs-step').textContent={draft:'1. 원본 지정',confirm:'2. 생성 대상 확인',result:'3. 생성 결과'}[inputMode];
 for(const id of ['inputs-pause','inputs-recover','inputs-resume','inputs-retry'])$(id).hidden=$(id).disabled&&!inputPending;
 $('inputs-recover').hidden ||= !v||!['interrupted','paused'].includes(v.status)&&!v.counts.uncertain;
 flowRecovery('inputs-recovery',!!(v?.counts.failed||v?.counts.uncertain||v?.status==='interrupted'));
 $('inputs-resend').hidden=!inputPending;

}
async function inputRun(fn){if(inputBusy)return;inputBusy=true;inputButtons();try{await fn();}catch(e){inputConfirmed=null;$('inputs-confirm').checked=false;$('inputs-feedback').textContent=e.message+' 입력·선택·사유와 요청 ID를 보존했습니다.';}finally{inputBusy=false;inputPersist();inputButtons();}}
function inputRender(){
 const v=inputView;const summary=v?`${inputNames[v.status]} · 완료 ${v.counts.completed} / 대기 ${v.counts.pending} / 생성 중 ${v.counts.running} / 실패 ${v.counts.failed} / 불명확 ${v.counts.uncertain} / 제외 ${v.counts.excluded} / 원본 변경 ${v.counts.stale}${v.stop_requested?' · 현재 입력 완료 후 중단 요청됨':''}`:'저장된 입력 계획 없음';flowStatus('inputs-summary',summary);
 $('inputs-fixed').innerHTML=v?v.plan.entries.map(e=>`<article><strong>${esc(e.input.source||e.input.path)} · ${e.included?'포함':'제외'}</strong><p>${esc(e.input.format)} · ${esc(e.input.path)}</p><p>범위: ${esc(e.input.pages||'전체/형식 지정')} · 과목: ${esc(e.input.track||e.input.subject||'')}</p><p>${esc(['solution','script','answer','structure','layout','repo_root','question_type','unit','relationship_confirmed'].filter(k=>e.input[k]!==undefined&&e.input[k]!=='').map(k=>({solution:'해설',script:'대본',answer:'정답',structure:'교재 구조',layout:'영역 기록',repo_root:'원본 루트',question_type:'문항 유형',unit:'단원',relationship_confirmed:'관련 파일 관계 확인'}[k]||k)+': '+(typeof e.input[k]==='boolean'?(e.input[k]?'확인':'미확인'):e.input[k])).join(' · '))}</p><p>${esc(e.reasons.join(' · ')||e.input.exclude_reason||'조건 확인됨')}</p></article>`).join(''):'';
 const checks=[...$('inputs-results').querySelectorAll('input:checked')].map(x=>x.value);
 $('inputs-results').innerHTML=v?v.entries.map((e,i)=>`<article><strong>${i+1}. ${esc(e.input.source||e.input.path)} · ${inputNames[e.status]}</strong><p>${esc((e.status==='completed'?'생성 완료 기록입니다. 현재 전사·대조·승인 상태는 job에서 확인하세요.':e.message)||e.reasons.join(' · ')||e.input.exclude_reason)}</p>${e.status==='failed'||e.status==='completed'?`<label><input type="checkbox" value="${esc(e.id)}" ${checks.includes(e.id)?'checked':''}>${e.status==='failed'?'재시도 대상':'배치로 넘길 job'}</label>`:''}${e.job_id?`<button data-job="${e.job_id}">job 열기 · ${esc(e.job_id.slice(0,12))}</button>`:''}</article>`).join(''):'';
 $('inputs-history').textContent=v?JSON.stringify({id:v.id,revision:v.revision,created_at:v.created_at,plan_hash:v.plan_hash,plan:v.plan,attempts:v.attempts,events:v.events},null,2):'';inputButtons();
}
async function inputList(){const runs=await api('/api/input-plans');$('inputs-runs').innerHTML='<option value="">새 계획</option>'+runs.slice().reverse().map(v=>`<option value="${v.id}">${esc(v.created_at)} · ${inputNames[v.status]} · ${v.id.slice(0,8)}</option>`).join('');$('inputs-runs').value=inputView?.id||'';}
async function inputOpen(pid){inputConfirmed=null;$('inputs-confirm').checked=false;inputCatalog=await api('/api/input-formats');inputDrawUnits();$('inputs-reason').value=inputDraft.reason||'';const id=pid||inputDraft.planId;if(id){inputView=await api('/api/input-plans/'+id);if(pid||inputMode!=='draft')inputMode=inputView.status==='planned'?'confirm':'result';}await inputList();inputRender();$('inputs-dialog').showModal();}
$('inputs-open').onclick=()=>guard(()=>inputRun(()=>inputOpen()));
$('jobs-inputs').onclick=()=>{$('jobs-dialog').close();$('inputs-open').click();};
$('inputs-origin').onclick=()=>guard(()=>inputRun(async()=>{const runs=await api('/api/input-plans');const found=runs.filter(v=>v.entries.some(e=>e.job_id===job?.id));if(!found.length){notice('현재 job에 연결된 신규 입력 생성 기록이 없습니다. 기존/legacy job은 자동 전환하지 않습니다.');return;}await inputOpen(found.at(-1).id);}));
$('inputs-preview').onclick=()=>inputRun(async()=>{const seq=++inputSequence;const pre=await api('/api/input-plans/preview',{inputs:structuredClone(inputDraft.units)});if(seq!==inputSequence||!$('inputs-dialog').open)return;inputPreview=pre;$('inputs-preview-result').innerHTML=inputPreview.plan.entries.map(e=>`<article><strong>${esc(e.input.source||e.input.path)} · ${inputNames[e.state]} · ${e.included?'포함':'제외'}</strong><p>${esc(e.reasons.join(' · ')||'형식·필수 조건 확인됨. 실제 파싱/원문 정확성은 생성·검수에서 확인합니다.')}</p><p>${esc(e.input.exclude_reason)}</p>${e.existing_jobs.map(j=>`<button data-job="${j.job_id}">기존 작업 참조 · ${esc(j.basis)}${j.legacy?' · legacy 읽기 전용':''}</button>`).join('')}</article>`).join('')||'입력 0건';$('inputs-feedback').textContent='읽기 전용 미리보기 · job 생성/AI 호출 없음';});
async function inputSend(action,extra={}){
 if(inputPending)throw Error('미확인 요청의 저장 결과를 먼저 확인하세요.');
 if(action==='execute'&&(inputMode!=='confirm'||inputConfirmed!==flowBasis(inputView)))throw Error('저장된 생성 대상을 다시 확인하세요.');
 const candidate={action,...(action==='plan'?{}:{plan_id:inputView.id,revision:inputView.revision}),...extra};
 inputPending={candidate:structuredClone(candidate),payload:structuredClone({...candidate,request_id:crypto.randomUUID()})};
 inputPersist();try{inputView=flowResponse(await api('/api/input-plans',inputPending.payload));}catch(e){if(flowRejected(e))inputPending=null;throw e;}inputPending=null;inputMode=inputView.status==='planned'?'confirm':'result';inputConfirmed=null;inputDraft.planId=inputView.id;$('inputs-confirm').checked=false;await inputList();inputRender();$('inputs-feedback').textContent='기록 반영됨 · job 준비와 AI 실행/검수/승인은 별개입니다.';
}
$('inputs-plan').onclick=()=>inputRun(()=>inputSend('plan',{inputs:inputDraft.units,preview_hash:inputPreview.plan_hash}));
$('inputs-confirm').onchange=()=>{inputConfirmed=$('inputs-confirm').checked?flowBasis(inputView):null;inputButtons();};
$('inputs-execute').onclick=()=>inputRun(()=>inputSend('execute',{confirmed_plan_hash:inputView.plan_hash}));
for(const a of ['pause','recover','resume'])$('inputs-'+a).onclick=()=>inputRun(()=>inputSend(a));
$('inputs-retry').onclick=()=>inputRun(()=>inputSend('retry',{entry_ids:[...$('inputs-results').querySelectorAll('input:checked')].map(x=>x.value).filter(id=>inputView.entries.some(e=>e.id===id&&e.status==='failed')),reason:$('inputs-reason').value}));
$('inputs-end').onclick=()=>inputRun(()=>inputSend('end',{reason:$('inputs-reason').value}));
$('inputs-reason').oninput=inputPersist;
$('inputs-reload').onclick=()=>inputRun(async()=>{inputConfirmed=null;$('inputs-confirm').checked=false;if(inputView)inputView=await api('/api/input-plans/'+inputView.id);await inputList();inputRender();});
$('inputs-runs').onchange=()=>inputRun(async()=>{inputConfirmed=null;inputSequence++;inputView=$('inputs-runs').value?await api('/api/input-plans/'+$('inputs-runs').value):null;inputDraft.planId=inputView?.id||null;inputMode=inputView?(inputView.status==='planned'?'confirm':'result'):'draft';$('inputs-confirm').checked=false;inputRender();});
async function inputJobClick(e){const jid=e.target.dataset.job;if(!jid)return;await guard(async()=>{await save();await loadList();await loadJob(jid);$('inputs-dialog').close();});}
$('inputs-results').onclick=inputJobClick;$('inputs-results').onchange=()=>{$('inputs-operation').value='';inputButtons();inputNextSequence++;inputNext={};$('inputs-next-options').textContent='선택 변경됨 · 다음 처리 대상을 확인하세요.';};$('inputs-preview-result').onclick=inputJobClick;
$('inputs-close').onclick=()=>{if(!inputBusy){inputPersist();$('inputs-dialog').close();}};
$('inputs-dialog').addEventListener('close',()=>{inputSequence++;inputConfirmed=null;$('inputs-confirm').checked=false;});
$('inputs-dialog').addEventListener('cancel',e=>{if(inputBusy)e.preventDefault();});
$('inputs-batch').onclick=()=>inputRun(async()=>{
 if(multijobPending)throw Error('기존 배치의 미확인 요청을 먼저 확인하세요.');
 const ids=[...$('inputs-results').querySelectorAll('input:checked')].map(x=>x.value).filter(id=>inputView.entries.some(e=>e.id===id&&e.status==='completed'));
 const pre=await api('/api/input-plans/'+inputView.id+'/batch-preview',{entry_ids:ids,operation:$('inputs-operation').value});
 const all=await api('/api/jobs');const jobs=[...pre.plan.job_ids.map(id=>all.find(j=>j.id===id)),...all.filter(j=>!pre.plan.job_ids.includes(j.id))];$('multijob-jobs').innerHTML=jobs.map(j=>`<label><input type="checkbox" value="${esc(j.id)}" ${pre.plan.job_ids.includes(j.id)?'checked':''}>${esc(j.source||j.id)} · ${j.id.slice(0,8)}</label>`).join('');
 multijobView=null;multijobDraft.batchId=null;multijobMode='draft';multijobConfirmed=null;multijobPreview=pre;$('multijob-operation').value=pre.plan.operation;multijobShowPreview(pre);$('multijob-confirm').checked=false;await multijobList();renderMultijob();multijobButtons();multijobPersist();$('inputs-dialog').close();$('multijob-dialog').showModal();
});
let inputPolling=false;
setInterval(async()=>{
 if(!$('inputs-dialog').open||document.hidden||inputBusy||inputPending||inputPolling||!['running','interrupted'].includes(inputView?.status))return;
 const view=inputView;inputPolling=true;
 try{const fresh=await api('/api/input-plans/'+view.id);if(inputView===view&&!inputBusy&&!inputPending&&$('inputs-dialog').open){inputView=fresh;flowRefresh('inputs-dialog',inputRender);}}
 catch(e){flowStatus('inputs-feedback','상태 조회 실패 · 현재 기록 유지: '+e.message);}
 finally{inputPolling=false;}
},1200);

$('inputs-resend').onclick=()=>inputRun(async()=>{if(!inputPending)return;try{inputView=flowResponse(await api('/api/input-plans',inputPending.payload));}catch(e){if(flowRejected(e))inputPending=null;throw e;}inputPending=null;inputMode=inputView.status==='planned'?'confirm':'result';inputConfirmed=null;$('inputs-confirm').checked=false;inputDraft.planId=inputView.id;await inputList();inputRender();$('inputs-feedback').textContent='동일 요청 ID로 저장 결과를 확인했습니다.';});

$('inputs-edit').onclick=()=>{inputMode='draft';inputConfirmed=null;$('inputs-confirm').checked=false;inputButtons();inputPersist();};
$('inputs-back').onclick=()=>{inputMode=inputView?.status==='planned'?'confirm':'result';inputConfirmed=null;$('inputs-confirm').checked=false;inputRender();inputPersist();};
$('inputs-copy').onclick=()=>{if(!inputView)return;inputDraft.units=inputView.plan.entries.map(e=>structuredClone(e.input));inputDrawUnits();inputInvalidate();};

let inputNext={},inputNextSequence=0;
$('inputs-next-preview').onclick=async()=>{
 const seq=++inputNextSequence,pid=inputView?.id;
 const ids=[...$('inputs-results').querySelectorAll('input:checked')].map(x=>x.value).filter(id=>inputView?.entries.some(e=>e.id===id&&e.status==='completed'));
 if(!ids.length){$('inputs-next-options').textContent='넘길 완료 작업을 선택하세요.';return;}
 $('inputs-next-options').textContent='전사·대조 대상 조회 중 (실행 없음)';
 const results=await Promise.allSettled(['extract','audit'].map(operation=>api('/api/input-plans/'+pid+'/batch-preview',{entry_ids:ids,operation})));
 if(seq!==inputNextSequence||pid!==inputView?.id||!$('inputs-dialog').open)return;
 inputNext={};$('inputs-next-options').innerHTML=results.map((r,i)=>{
 const op=['extract','audit'][i],label=i?'AI 원본 대조':'AI 전사';
 if(r.status==='rejected')return `<p>${label}: 조회 실패 · ${esc(r.reason.message)}</p>`;
 inputNext[op]=r.value;return `<article><strong>${label}: 실행 ${r.value.plan.planned_calls} / 제외 ${r.value.plan.excluded.length}</strong><pre>${esc(multijobPreviewText(r.value.plan,r.value.display?.plan_hash===r.value.plan_hash?r.value.display:null))}</pre><button data-next-operation="${op}" ${r.value.plan.planned_calls?'':'disabled'}>${label} 선택</button></article>`;
 }).join('');
 if(results.every(r=>r.status==='fulfilled'&&!r.value.plan.planned_calls))$('inputs-next-options').insertAdjacentHTML('beforeend','<p>처리할 항목 없음 · 제외 근거를 확인하거나 작업 검수 화면을 여세요.</p>');
};
$('inputs-next-options').onclick=e=>{if(e.target.dataset.nextOperation){$('inputs-operation').value=e.target.dataset.nextOperation;inputButtons();$('inputs-batch').focus();}};
$('inputs-dialog').addEventListener('close',()=>{inputNextSequence++;});
$('inputs-operation').onchange=()=>{inputConfirmed=null;$('inputs-confirm').checked=false;inputButtons();};
