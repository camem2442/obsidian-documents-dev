/* Explicit assessment, independent of workflow actions. Draft survives failed writes/reloads. */
let evaluationView=null,evaluationJobId=null,evaluationBusy=false,evaluationPending=null,evaluationBasisToken=null;
let evaluationVisibilityStopped=false,evaluationPauseQueued=false;
const evaluationOwner=crypto.randomUUID(); // new page/window = new observation owner
const evaluationLabels={error_found:'오류 발견',no_error_in_scope:'확인 범위 내 오류 미발견',deferred:'판단 유보',actual_error:'실제 오류',false_positive:'오탐'};
function evaluationPath(){return `/api/jobs/${evaluationJobId}/evaluation`;}
function evaluationTimer(){return evaluationView?.timers.findLast(t=>t.status!=='ended');}
function evaluationDraft(){return $('evaluation-note').value.trim()||$('evaluation-correction').value.trim();}
function evaluationButtons(){
 $('evaluation-dialog').querySelectorAll('button').forEach(b=>b.disabled=evaluationBusy);
 const t=evaluationTimer(),own=t?.owner===evaluationOwner;
 for(const id of ['save','dataset'])$('evaluation-'+id).disabled=evaluationBusy||!evaluationView?.available;
 $('evaluation-start').disabled=evaluationBusy||!evaluationView?.available||!!t;
 $('evaluation-resume').disabled=evaluationBusy||!t||t.status==='running';
 $('evaluation-pause').disabled=evaluationBusy||!t||t.status!=='running'||!own;
 $('evaluation-end').disabled=evaluationBusy||!t||(t.status==='running'&&!own);
}
async function evaluationRun(fn){
 if(evaluationBusy)return;evaluationBusy=true;evaluationButtons();
 try{await fn();}catch(e){$('evaluation-feedback').textContent=e.message+' 입력은 유지됩니다. 근거 충돌 시 재조회 후 다시 판단하세요.';}
 finally{evaluationBusy=false;evaluationButtons();if(evaluationPauseQueued){evaluationPauseQueued=false;const t=evaluationTimer();if(t?.status==='running'&&t.owner===evaluationOwner)await evaluationRun(()=>evaluationMeasure('pause','tab_hidden'));}}
}
function renderEvaluation(){
 const v=evaluationView,s=v.summary,t=evaluationTimer();
 $('evaluation-summary').textContent=`${v.origin==='synthetic_fixture'?'합성 fixture · 실제 사람 검수 결과 아님':'사람의 명시 판정 · 신원 미확인'} | 판정 ${s.recorded} / 현재 ${s.current} / 문항 미확인 ${s.unjudged_questions} / 지적 미확인 ${s.unjudged_issues} / 판단 유보 ${s.deferred} / 무효화 ${s.invalidated} / 제외 ${s.excluded}. ${v.reason}`;
 $('evaluation-timer').textContent=`측정 상태: ${t?.status||'측정 중 아님'} · 측정된 검수 구간 ${s.measured_seconds===null?'미확인':s.measured_seconds.toFixed(1)+'초'} · 누락 꼬리 구간 ${s.unmeasured_tails} · ${s.time_coverage}${t&&t.owner!==evaluationOwner?' · 이전/다른 창의 구간':''}`;
 $('evaluation-history').innerHTML=v.labels.slice().reverse().map(x=>`<article><strong>${esc(x.item_id)} · ${x.target==='issue'?'지적':'문항'} · ${esc(evaluationLabels[x.verdict])}</strong><p>${esc(x.note)}</p><small>${esc(x.id)} · ${esc(x.at)} · revision ${esc(x.revision_id)} · ${esc(x.origin)}<br>확인 범위: ${esc(x.scope.join(', '))} · ${x.current?'현재 근거':'과거 기록'} · 제외: ${esc(x.exclusions.join(', ')||'없음')}<br>대체: ${esc(x.supersedes||'없음')} · ${esc(x.correction_reason)} · 연결 행동 ${x.action_links.length}</small></article>`).join('')||'판정 기록 없음 · 레이블 없음';
 $('evaluation-action-history').textContent=JSON.stringify(v.actions,null,2);
 $('evaluation-green').innerHTML=v.green_errors.map(x=>`<button data-item="${esc(x.item_id)}">${esc(x.item_id)} · ${esc(x.origin)} · ${esc(x.scope.join(', '))}</button>`).join('')||'명시 오류 판정 없음';
 $('evaluation-green').querySelectorAll('button').forEach(b=>b.onclick=()=>evaluationRun(async()=>{if(evaluationDraft())throw Error('현재 판정 입력을 먼저 저장하거나 지우세요.');$('evaluation-item').value=b.dataset.item;await evaluationTarget();}));
 $('evaluation-metrics').innerHTML=v.metrics.map(m=>`<h4>${esc(m.origin)}</h4><p>지적 오탐: ${m.issue_false_positive.numerator} / ${m.issue_false_positive.denominator} · 제외 ${m.issue_excluded.length}<br>오류 문항 중 GREEN: ${m.question_green_miss.numerator} / ${m.question_green_miss.denominator} · 비교 가능 문항 ${m.question_comparable} · 제외 ${m.question_excluded.length}</p><details><summary>분모 정의 · 제외 이유</summary><pre>${esc(JSON.stringify(m,null,2))}</pre></details>`).join('');
 $('evaluation-datasets').innerHTML=v.datasets.slice().reverse().map(d=>`<article><a href="${evaluationPath()}/datasets/${encodeURIComponent(d.id)}" target="_blank" rel="noopener">JSON 보기 · ${esc(d.id)}</a><p>${esc(d.created_at)} · 포함 ${d.included.length} / 제외 ${d.excluded.length} · ${esc(d.selection.origin)}</p><details><summary>선정 조건 · 구성 해시 · 포함/제외</summary><pre>${esc(JSON.stringify(d,null,2))}</pre></details></article>`).join('')||'생성된 데이터셋 없음';
 evaluationOptions();evaluationButtons();
}
function evaluationOptions(){
 const rid=$('evaluation-item').value,token=$('evaluation-issue').value,target=$('evaluation-target').value,old=$('evaluation-supersedes').value;
 const candidates=evaluationView.labels.filter(x=>x.item_id===rid&&x.target===target&&(target==='question'||x.issue_token===token));
 $('evaluation-supersedes').innerHTML='<option value="">새 판정 (기존 판정이 있으면 정정 선택 필요)</option>'+candidates.slice(-1).map(x=>`<option value="${esc(x.id)}">정정: ${esc(evaluationLabels[x.verdict])} · ${esc(x.id)}</option>`).join('');
 $('evaluation-supersedes').value=candidates.at(-1)?.id===old?old:'';
 const selected=[...$('evaluation-actions').selectedOptions].map(o=>o.value);
 $('evaluation-actions').innerHTML=evaluationView.actions.filter(x=>x.item_id===rid).map(x=>`<option value="${esc(x.id)}">${esc(x.source)} · ${esc(x.action)} · ${esc(x.id)}</option>`).join('');
 [...$('evaluation-actions').options].forEach(o=>o.selected=selected.includes(o.value));
}
async function evaluationTarget(preserveInput=false){
 const previousVerdict=$('evaluation-verdict').value;
 if(!preserveInput)$('evaluation-scope').querySelectorAll('input').forEach(x=>x.checked=false);
 const item=evaluationView.items.find(i=>i.id===$('evaluation-item').value),issue=$('evaluation-target').value==='issue';
 $('evaluation-issue-label').hidden=!issue;
 $('evaluation-verdict').innerHTML='<option value="">판정을 명시적으로 선택하세요</option>'+(issue?['actual_error','false_positive','deferred']:['error_found','no_error_in_scope','deferred']).map(x=>`<option value="${x}">${evaluationLabels[x]}</option>`).join('');
 $('evaluation-issue').innerHTML=(item?.issues||[]).map(x=>`<option value="${esc(x.token)}">${x.index+1}. ${esc(x.evidence.message||JSON.stringify(x.evidence))}</option>`).join('');
 if(preserveInput&&[...$('evaluation-verdict').options].some(x=>x.value===previousVerdict))$('evaluation-verdict').value=previousVerdict;
 evaluationOptions();
 const material=evaluationView.materials[item?.id];
 $('evaluation-material').innerHTML=(material?.parts||[]).map((part,n)=>`<h4>${esc(part.label)}</h4><div class="focused-compare"><section>${part.sections.map(sec=>`<h5>${esc(sec.label)}</h5>${sec.images.map(r=>r.available?`<img alt="${esc(sec.label)} 원본 영역" src="${fileURL('regions/'+r.image)}">`:'<p>원본 파일 없음 · 재현 불가</p>').join('')}${sec.text?`<pre>${esc(sec.text)}</pre>`:''}`).join('')}</section><section id="evaluation-preview-${n}" class="prose"></section></div>`).join('')+'<pre>'+esc(JSON.stringify(item?.issues||[],null,2))+'</pre>';
 (material?.parts||[]).forEach((part,n)=>renderMarkdown($('evaluation-preview-'+n),[part.preview.body,part.preview.solution].filter(Boolean).join('\n\n---\n\n')));
}
async function openEvaluation(rid=currentId,token=null){
 await save();evaluationJobId=job.id;evaluationPending=null;
 evaluationView=await api(evaluationPath());evaluationBasisToken=evaluationView.basis_token;
 $('evaluation-item').innerHTML=evaluationView.items.map(i=>`<option value="${esc(i.id)}">문항 ${esc(i.number)} · ${esc(i.tier)} · ${esc(i.id)}</option>`).join('');
 if(evaluationView.items.some(i=>i.id===rid))$('evaluation-item').value=rid;
 $('evaluation-target').value=token?'issue':'question';
 renderEvaluation();if(evaluationView.items.length)await evaluationTarget();
 if(token){$('evaluation-issue').value=token;evaluationOptions();}
 $('evaluation-feedback').textContent='조회·이동·승인만으로 평가 판정은 생성되지 않습니다.';
 $('evaluation-dialog').showModal();
}
async function evaluationSend(action,fields={}){
 const candidate={action,revision:evaluationView.revision,basis_token:evaluationBasisToken,...fields};
 // Preserve the identical request ID on ambiguous transport failure. A refresh intentionally resets it.
 if(!evaluationPending||JSON.stringify(evaluationPending.candidate)!==JSON.stringify(candidate))evaluationPending={candidate,payload:{...candidate,request_id:crypto.randomUUID()}};
 evaluationView=await api(evaluationPath(),evaluationPending.payload);evaluationPending=null;
 renderEvaluation();$('evaluation-feedback').textContent='기록 저장됨 · tier·승인 상태 변경 없음';
}
async function evaluationMeasure(action,reason='explicit'){
 if(['start','resume'].includes(action)){if(document.hidden)throw Error('활성 화면에서 측정을 시작하세요.');evaluationVisibilityStopped=false;}
 await evaluationSend(action,{owner:evaluationOwner,timer_id:evaluationTimer()?.id,reason});
}
$('evaluation-queue').onclick=()=>guard(()=>openEvaluation());
$('focused-evaluation').onclick=()=>focusedRun(()=>openEvaluation(focusedItemId,focusedCard()?.token));
$('sample-evaluation').onclick=()=>sampleRun(()=>openEvaluation(sampleId()));
$('evaluation-save').onclick=()=>evaluationRun(async()=>{
 await evaluationSend('label',{item_id:$('evaluation-item').value,target:$('evaluation-target').value,issue_token:$('evaluation-issue').value||null,
 verdict:$('evaluation-verdict').value,note:$('evaluation-note').value,scope:[...$('evaluation-scope').querySelectorAll('input:checked')].map(x=>x.value),
 supersedes:$('evaluation-supersedes').value||null,correction_reason:$('evaluation-correction').value,action_ids:[...$('evaluation-actions').selectedOptions].map(x=>x.value)});
 $('evaluation-note').value='';$('evaluation-correction').value='';$('evaluation-verdict').value='';
});
for(const action of ['start','pause','resume','end'])$('evaluation-'+action).onclick=()=>evaluationRun(()=>evaluationMeasure(action));
$('evaluation-reload').onclick=()=>evaluationRun(async()=>{evaluationView=await api(evaluationPath());evaluationBasisToken=evaluationView.basis_token;evaluationPending=null;renderEvaluation();await evaluationTarget(true);$('evaluation-feedback').textContent='현재 근거 재조회 완료 · 입력 유지. 변경된 원본을 확인하고 다시 판단하세요.';});
for(const id of ['item','target'])$('evaluation-'+id).onchange=()=>evaluationRun(async()=>{await evaluationTarget();$('evaluation-feedback').textContent='판정 대상 변경 · 입력 메모는 유지됩니다. 선택한 문항과 확인 범위를 다시 확인하세요.';});
$('evaluation-issue').onchange=evaluationOptions;
$('evaluation-dataset').onclick=()=>evaluationRun(()=>evaluationSend('dataset',{selection:{origin:$('evaluation-origin').value,
 tiers:$('evaluation-tier').value==='all'?['GREEN','YELLOW','RED','NOT_READY']:[$('evaluation-tier').value],
 targets:$('evaluation-dataset-target').value==='all'?['question','issue']:[$('evaluation-dataset-target').value],full_scope_only:$('evaluation-full').checked}}));
async function closeEvaluation(){
 if(evaluationDraft()&&!await examConfirm({title:'입력 보존',message:'저장하지 않은 판정 입력을 지우고 닫을까요?',ok:'입력 지우고 닫기'}))return;
 const t=evaluationTimer();if(t?.status==='running'&&t.owner===evaluationOwner)await evaluationMeasure('pause','dialog_closed');
 $('evaluation-note').value='';$('evaluation-correction').value='';$('evaluation-dialog').close();
}
$('evaluation-close').onclick=()=>evaluationRun(closeEvaluation);
$('evaluation-dialog').addEventListener('cancel',e=>{e.preventDefault();evaluationRun(closeEvaluation);});
setInterval(()=>{const t=evaluationTimer();if($('evaluation-dialog').open&&!document.hidden&&!evaluationVisibilityStopped&&t?.status==='running'&&t.owner===evaluationOwner&&!evaluationPending)evaluationRun(()=>evaluationMeasure('tick','visible_checkpoint'));},5000);
document.addEventListener('visibilitychange',()=>{const t=evaluationTimer();if(document.hidden&&t?.status==='running'&&t.owner===evaluationOwner){evaluationVisibilityStopped=true;if(evaluationBusy)evaluationPauseQueued=true;else evaluationRun(()=>evaluationMeasure('pause','tab_hidden'));}});
window.addEventListener('pagehide',()=>{const t=evaluationTimer();if(t?.status==='running'&&t.owner===evaluationOwner){
 // Best effort only. Missing final response is explicitly an unmeasured tail after lease expiry.
 fetch(evaluationPath(),{method:'POST',keepalive:true,headers:{'Content-Type':'application/json','X-Exam-Token':settings?.token||''},body:JSON.stringify({action:'pause',revision:evaluationView.revision,owner:evaluationOwner,timer_id:t.id,reason:'pagehide',request_id:crypto.randomUUID()})}).catch(()=>{});
}});
window.addEventListener('beforeunload',e=>{if($('evaluation-dialog').open&&evaluationDraft()){e.preventDefault();e.returnValue='';}});
