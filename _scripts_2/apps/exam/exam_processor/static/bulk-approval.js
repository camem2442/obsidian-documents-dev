/* R5: exact target confirmation is separate from planning and execution. */
let bulkView=null,bulkJobId=null,bulkPlanId=null,bulkRunId=null,bulkBusy=false;
const bulkLabels={pending:'미처리',prepared:'승인 시도 저장됨 · 복구 확인 필요',done:'완료',failed:'실패 · 승인 변경 없음',uncertain:'결과 불명확 · 재승인 금지',running:'복구 확인 필요',partial:'부분 완료 · 재검사 필요',completed:'실행 완료',blocked:'차단됨',recovery_required:'복구 확인 필요',closed:'종료 · 결과 보존'};
const bulkPlan=()=>bulkView?.plans.find(x=>x.id===bulkPlanId);
const bulkRun=()=>bulkView?.executions.find(x=>x.id===bulkRunId);
const bulkPath=()=>`/api/jobs/${bulkJobId}/bulk-approval`;
function bulkButtons(){
  const p=bulkPlan(),r=bulkRun(),active=r?.id===bulkView?.active_execution_id;
  $('bulk-dialog').querySelectorAll('button,input,select').forEach(e=>e.disabled=bulkBusy);
  $('bulk-targets').querySelectorAll('input[data-excluded]').forEach(e=>e.disabled=true);
  $('bulk-plan').disabled=bulkBusy||!bulkView?.available||!!bulkView.blockers.length||!!bulkView.active_execution_id;
  $('bulk-execute').disabled=bulkBusy||!p||!$('bulk-confirm').checked||!!bulkView.active_execution_id||bulkView.executions.some(x=>x.plan_id===p.id);
  $('bulk-retry').disabled=bulkBusy||!active||!$('bulk-confirm').checked||p?.id!==r?.plan_id||r?.status==='recovery_required';
  $('bulk-recover').disabled=bulkBusy||!active;
  $('bulk-end').disabled=bulkBusy||!active||r?.status==='recovery_required';
}
function renderBulk(){
  const p=bulkPlan(),r=bulkRun();
  $('bulk-basis').textContent=`${bulkView.policy} · R4: ${bulkView.sessions.map(s=>sampleLabels[s.status]).join(' / ')||'세션 없음'} · 전체 job 근거 및 기존 승인 조건을 실행 직전에 재검사합니다.`;
  $('bulk-sample-evidence').innerHTML=bulkView.sessions.filter(s=>!p||s.id===p.session_id).map(s=>`<p>${esc(s.created_at)} · ${esc(s.policy)} · seed ${esc(s.seed)} · 후보 ${s.basis.candidates.length}건 / 표본 ${s.sample_count}건 · ${esc(sampleLabels[s.status])}</p><ul>${s.selected.map(id=>`<li>${esc(id)} · ${esc(sampleLabels[s.results[id].verdict])} · ${esc(s.results[id].note||'')}</li>`).join('')}</ul>`).join('')||'현재 표본 근거 없음';
  $('bulk-blockers').textContent=bulkView.blockers.length?'새 승인 계획 차단: '+bulkView.blockers.join(' / ')+(bulkView.active_execution_id?' 기존 실행의 복구·재시도는 아래에서 저장된 실행 근거를 별도로 검사합니다.':''):'';
  $('bulk-targets').innerHTML=bulkView.candidates.map(c=>`<label><input type="checkbox" value="${esc(c.id)}" ${c.reasons.length?'data-excluded disabled':''} ${p?.targets.includes(c.id)?'checked':''}> 문항 ${esc(c.number)} · ${esc(c.id)} ${c.reasons.length?'— 제외: '+esc(c.reasons.join(' / ')):''}</label>`).join('');
  $('bulk-targets').querySelectorAll('input').forEach(el=>el.onchange=()=>{bulkPlanId=null;$('bulk-confirm').checked=false;$('bulk-confirmed-list').textContent='대상이 바뀌었습니다. 새 계획을 만들고 목록·건수를 다시 확인하세요.';$('bulk-plans').value='';bulkButtons();});
  $('bulk-plans').innerHTML='<option value="">계획 선택</option>'+bulkView.plans.map(x=>`<option value="${x.id}">${esc(x.created_at)} · ${x.targets.length}건</option>`).reverse().join('');
  $('bulk-plans').value=bulkPlanId||'';
  $('bulk-confirmed-list').textContent=p?`확인 대상 ${p.targets.length}건: ${p.targets.map(id=>{const c=bulkView.candidates.find(c=>c.id===id);return `문항 ${c?.number||id} (${id})`;}).join(', ')} · 계획 ${p.id} · R4 세션 ${p.session_id}`:'계획 미리보기는 문항을 승인하지 않습니다.';
  $('bulk-runs').innerHTML='<option value="">실행 선택</option>'+bulkView.executions.map(x=>`<option value="${x.id}">${esc(x.confirmed_at)} · ${bulkLabels[x.status]}</option>`).reverse().join('');
  $('bulk-runs').value=bulkRunId||'';
  $('bulk-progress').textContent=r?`${bulkLabels[r.status]} · 완료 ${r.counts.done} / 실패 ${r.counts.failed} / 미처리 ${r.counts.pending} / 복구 필요 ${r.counts.prepared+r.counts.uncertain} · ${r.error||''}`:'아직 명시적으로 실행하지 않았습니다.';
  $('bulk-results').innerHTML=r?`<ul>${Object.entries(r.items).map(([id,x])=>`<li>${esc(id)} · ${bulkLabels[x.status]} ${esc(x.error||'')} <button type="button" data-detail="${esc(id)}">같은 문항 상세 검수</button></li>`).join('')}</ul>`:'';
  $('bulk-results').querySelectorAll('[data-detail]').forEach(el=>el.onclick=()=>{if(bulkBusy)return;$('bulk-dialog').close();currentId=el.dataset.detail;section='all';setEditing(false);applyDefaultViewTarget();updateSection();render();});
  bulkButtons();
}
function acceptBulk(view){
  bulkView=view;job=view.job;
  if(!view.plans.some(p=>p.id===bulkPlanId))bulkPlanId=null;
  if(view.active_execution_id){bulkRunId=view.active_execution_id;bulkPlanId=view.executions.find(r=>r.id===bulkRunId)?.plan_id||null;}
  else if(!view.executions.some(r=>r.id===bulkRunId))bulkRunId=view.executions.at(-1)?.id||null;
  if(!bulkPlanId&&bulkRun())bulkPlanId=bulkRun().plan_id;
  $('bulk-confirm').checked=false;render();renderBulk();
}
async function bulkBusyRun(fn){
  if(bulkBusy)return;bulkBusy=true;bulkButtons();
  try{await fn();}catch(e){$('bulk-feedback').textContent=e.message+' 성공으로 처리하지 않았습니다. 저장 상태를 다시 불러와 확인하세요.';}
  finally{bulkBusy=false;bulkButtons();}
}
async function openBulk(){
  await save();if(bulkJobId!==job.id){bulkPlanId=null;bulkRunId=null;}bulkJobId=job.id;
  acceptBulk(await api(bulkPath()));$('bulk-feedback').textContent='조회만으로 승인되지 않습니다.';
  $('bulk-dialog').showModal();
}
async function bulkAction(action){
  const p=bulkPlan(),r=bulkRun(),payload={action,revision:bulkView.revision,basis_token:bulkView.basis_token,plan_id:p?.id,execution_id:r?.id};
  if(action==='plan')payload.targets=[...$('bulk-targets').querySelectorAll('input:checked:not([data-excluded])')].map(e=>e.value);
  if(action==='execute'||action==='retry')Object.assign(payload,{targets:p.targets,confirmation_token:p.confirmation_token,confirmed:$('bulk-confirm').checked});
  if(action==='close'&&!await examConfirm({title:'실행 종료',message:'이미 저장된 결과를 보존하고 추가 승인 없이 종료할까요?',ok:'종료'}))return;
  const view=await api(bulkPath(),payload);
  if(action==='plan')bulkPlanId=view.plans.at(-1).id;
  if(action==='execute')bulkRunId=view.executions.at(-1).id;
  acceptBulk(view);$('bulk-feedback').textContent=action==='plan'?'승인 계획 저장됨 · 문항 승인 변경 없음':`저장된 실행 상태: ${bulkLabels[bulkRun()?.status]||'기록 확인'} · 결과와 복구 안내를 확인하세요.`;
}
$('bulk-queue').onclick=()=>guard(openBulk);
$('sample-bulk').onclick=()=>sampleRun(async()=>{await closeSample();if(!$('sample-dialog').open)await openBulk();});
for(const action of ['plan','execute','retry','recover'])$('bulk-'+action).onclick=()=>bulkBusyRun(()=>bulkAction(action));
$('bulk-end').onclick=()=>bulkBusyRun(()=>bulkAction('close'));
$('bulk-confirm').onchange=bulkButtons;
$('bulk-plans').onchange=()=>{bulkPlanId=$('bulk-plans').value;$('bulk-confirm').checked=false;renderBulk();};
$('bulk-runs').onchange=()=>{bulkRunId=$('bulk-runs').value;bulkPlanId=bulkRun()?.plan_id||null;$('bulk-confirm').checked=false;renderBulk();};
$('bulk-reload').onclick=()=>bulkBusyRun(async()=>{acceptBulk(await api(bulkPath()));$('bulk-feedback').textContent='저장 상태 재조회 완료 · 자동 승인 없음';});
$('bulk-close').onclick=()=>{if(!bulkBusy)$('bulk-dialog').close();};
$('bulk-dialog').addEventListener('cancel',e=>{if(bulkBusy)e.preventDefault();});
