const $ = (id) => document.getElementById(id);
let settings, job, allJobs=[], currentId, section='all', editing=false, dirty=false, saving=null, generation=0, zoom=1, cropZoom=1, timer, polling=false, actionPending=false, allowEdits=false, viewTarget='', cropDialogTarget='', cropBox=[0,0,1000,1000], cropPointer=null, connectionOffline=false, reconnecting=false, reviewTierFilter='all';
const labels={pending:'대기',approved:'승인',held:'보류'};
const auditStates={no_difference:'차이 미발견',suspected_difference:'차이 의심',unreadable:'판독 불가'};
const esc=(text)=>String(text??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function auditWaived(rec){
  const w=rec?.audit_waiver;
  return !!(w&&w.status==='waived'&&w.revision===rec?.revision);
}
function auditStatus(rec){
  if(auditWaived(rec))return {a:rec.audit,stale:false,complete:true,missing:false,issueCount:0,needsNote:false,waived:true};
  const a=rec?.audit,rev=rec?.revision;
  const stale=!!(a&&rev&&a.revision!==rev);
  const complete=!!(a&&a.status==='completed'&&!stale);
  const missing=!complete;
  const issueCount=(a?.issues||[]).filter(issue=>!issue.applied&&!issue.skipped).length;
  const needsNote=complete&&a.result!=='no_difference'&&issueCount>0;
  return {a,stale,complete,missing,issueCount,needsNote,waived:false};
}
function reviewNoteText(rec){return ($('review-note')?.value??rec?.note??'').trim();}
function approvalBlockers(rec){
  const gate=rec?.approval_gate;
  if(!gate)return ['승인 조건이 계산되지 않았습니다. 서버를 확인하세요.'];
  if(!gate.available)return gate.blockers||['승인 경로를 사용할 수 없습니다.'];
  return gate.blockers||[];
}
function canApprove(rec){return rec?.approval_gate?.eligible===true&&!dirty&&!approvalBlockers(rec).length;}
function auditSummary(rec){
  const s=auditStatus(rec);
  if(s.waived)return {tone:'warn',text:`AI 원본 대조: 생략 · ${rec.audit_waiver?.reason||'사유 기록'}`};
  if(s.missing)return {tone:'block',text:'AI 원본 대조: '+(s.stale?'이전 버전 기록 · 재대조 필요':'현재 버전 대조 미완료')};
  const label=auditStates[s.a.result]||'판정 확인 필요';
  return {tone:s.needsNote?'warn':'ok',text:`AI 원본 대조: ${label} · 현재 버전 기록 (전체 품질 보증 아님)`};
}
function focusAuditUI(scrollDetails){
  const banner=$('review-alert'),panel=document.querySelector('.audit-panel');
  (banner||panel)?.scrollIntoView({behavior:'smooth',block:'nearest'});
  banner?.classList.add('highlight');panel?.classList.add('audit-highlight');
  setTimeout(()=>{banner?.classList.remove('highlight');panel?.classList.remove('audit-highlight');},2400);
  if(scrollDetails)panel?.scrollIntoView({behavior:'smooth',block:'start'});
}
function itemTierBadge(rec){
  const tier=rec?.review_decision?.tier;
  if(!tier)return '';
  const tierLabels={NOT_READY:'처리 대기',RED:'정밀',YELLOW:'확인',GREEN:'표본 후보'};
  const label=tierLabels[tier]||tier;
  const cls=tier.toLowerCase().replace('_','-');
  return `<span class="tier-badge tier-${cls}" title="검수 티어: ${tier}">${label}</span>`;
}
function itemAuditBadge(rec){
  if(auditWaived(rec))return '<span class="item-flag flag-waived" title="대조 생략"></span>';
  if(rec?.transcription_pending?.length)return '<span class="item-flag flag-transcribe" title="전사 대기"></span>';
  const s=auditStatus(rec);
  if(s.missing)return '<span class="item-flag flag-audit" title="원본 대조 필요"></span>';
  if(s.a?.result!=='no_difference')return '<span class="item-flag flag-issue" title="AI 지적"></span>';
  return '';
}
function matchesWorkflowFilter(i,filter){
  if(filter==='all')return true;
  if(filter==='issues')return !!(i.audit&&(i.audit.result!=='no_difference'||i.audit.revision!==i.revision));
  return i.review===filter;
}
function matchesReviewTierFilter(i,tier){
  if(tier==='all')return true;
  return i.review_decision?.tier===tier;
}
function visibleItems(){
  if(!job?.items)return [];
  const wf=$('filter')?.value||'all';
  return job.items.filter(i=>matchesWorkflowFilter(i,wf)&&matchesReviewTierFilter(i,reviewTierFilter));
}
function renderReviewQueueBar(){
  const bar=$('review-queue-bar');
  if(!bar)return;
  bar.hidden=!job;
  if(!job)return;
  const s=job.review_queue_summary;
  const available=s&&s.status!=='unavailable';
  const status=$('qc-status');
  status.textContent=available?'검수 대기 항목의 분류':`분류 미계산 · ${s?.reason||'서버가 분류 정보를 제공하지 않았습니다.'}`;
  for(const chip of bar.querySelectorAll('[data-tier]'))chip.disabled=!available;
  if(!available){
    for(const id of ['qc-not-ready','qc-red','qc-yellow','qc-green'])$(id).textContent='—';
    $('qc-approved').textContent=job.items.filter(i=>i.review==='approved').length;
    $('qc-held').textContent=job.items.filter(i=>i.review==='held').length;
    $('qc-reset').hidden=false;
    return;
  }
  const p=s.pending||{};
  $('qc-not-ready').textContent=p.NOT_READY||0;
  $('qc-red').textContent=p.RED||0;
  $('qc-yellow').textContent=p.YELLOW||0;
  $('qc-green').textContent=p.GREEN||0;
  $('qc-approved').textContent=s.approved?.total||0;
  $('qc-held').textContent=s.held?.total||0;

  const currentWf=$('filter')?.value||'all';
  bar.querySelectorAll('.queue-chip[data-tier]').forEach(chip=>{
    const tier=chip.dataset.tier;
    chip.classList.toggle('active',currentWf==='pending'&&reviewTierFilter===tier);
  });
  bar.querySelectorAll('.queue-chip[data-workflow]').forEach(chip=>{
    const wf=chip.dataset.workflow;
    chip.classList.toggle('active',currentWf===wf&&reviewTierFilter==='all');
  });
  const resetBtn=$('qc-reset');
  if(resetBtn){
    resetBtn.hidden=(currentWf==='all'&&reviewTierFilter==='all');
  }
}
function icons(){if(window.lucide?.icons)lucide.createIcons({icons:lucide.icons});else lucide.createIcons();}
function item(){return job?.items.find(i=>i.id===currentId);}
function updateImportFormat(){
  const math=$('input-format').value==='hanwangi_2026_probability',english=$('input-format').value==='kice_english',file=!$('sample').value;
  $('workbook-fields').hidden=!math;$('pages-field').hidden=math;
  $('english-fields').hidden=!english;
  $('solution-path').placeholder=math?'필수':'선택 사항';
  for(const id of ['structure-path','layout-path','solution-path'])$(id).required=math&&file;
  [...$('track').options].forEach(o=>o.hidden=math ? o.value!=='확률과 통계' : english ? o.value!=='영어' : ['확률과 통계','영어'].includes(o.value));
  if(math)$('track').value='확률과 통계';
  else if(english)$('track').value='영어';
  else if(['확률과 통계','영어'].includes($('track').value))$('track').value='화법과 작문';
}
function notice(message,error=false){
  document.querySelectorAll('.dialog-notice').forEach(el=>el.remove());
  const dialog=document.querySelector('dialog[open]');
  const target=dialog ? dialog.appendChild(Object.assign(document.createElement('p'),{className:'dialog-notice'})) : $('notice');
  $('notice').hidden=true;
  target.textContent=message;target.hidden=!message;target.classList.toggle('error',error);target.setAttribute('role',error?'alert':'status');
}
function connectionState(state,text){
  const el=$('connection-state');
  if(!el)return;
  el.dataset.state=state;
  el.textContent=text;
  connectionOffline=state==='offline';
}
async function refreshConnection(){
  if(reconnecting)return false;
  reconnecting=true;
  connectionState('connecting','재연결 중');
  try{
    const health=await fetch('/api/health',{cache:'no-store'});
    if(!health.ok)throw Error('health');
    const status=await health.json();
    if(status.app!=='exam-processor')throw Error('wrong-app');
    const configResponse=await fetch('/api/config',{cache:'no-store'});
    if(!configResponse.ok)throw Error('config');
    settings=await configResponse.json();
    connectionState('online','서버 연결됨');
    return true;
  }catch{
    connectionState('offline','서버 연결 끊김');
    return false;
  }finally{
    reconnecting=false;
  }
}
function syncUI(){updateJobButton();$('save-finding').disabled=!job||actionPending;if(item()){$('state').textContent=labels[item().review]+(item().published_revision===item().revision?' · 저장 배치 있음':'');if($('audit-banner'))renderAuditBanner();}if(item()&&$('quality-checks'))renderQualityChecks();if(item()&&$('rule-run-summary'))renderRuleRunTrace();window.dispatchEvent(new Event('exam:state'));}
function renderQualityChecks(){const checks=item()?.quality_checks||[];const fixed=checks.filter(c=>c.result==='auto_fixed').length;const promoted=checks.filter(c=>c.promoted_finding_id).length;$('quality-checks').innerHTML=`<p class="quality-summary">자동 보정 ${fixed}건 · 재사용 규칙 ${promoted}건</p>`+(checks.map((c,index)=>`<article class="quality-check"><span>${esc(c.section)} · ${esc(c.result||'확인')}</span><code>${esc(c.source||'')}</code><span>→ ${esc(c.normalized||'')}</span>${c.promoted_finding_id?'<b>재사용 규칙에 반영됨</b>':`<button type="button" data-quality="${index}">사람 승인 후 규칙화</button>`}</article>`).join('')||'<p>자동 품질 검사 기록이 없습니다.</p>');$('quality-checks').querySelectorAll('[data-quality]').forEach(button=>button.onclick=()=>guard(async()=>{const check=checks[Number(button.dataset.quality)];if(!await examConfirm({title:'규칙화',message:`다음 보정을 재사용 규칙으로 등록할까요?\n\n${check.source||''} → ${check.normalized||''}`,ok:'등록',dismissKey:'quality-promote'}))return;await save();job=await api(`/api/jobs/${job.id}/items/${currentId}/quality-checks/${button.dataset.quality}/promote`,{revision:item().revision});render();notice('승인한 보정 규칙을 다음 수식 전사 지침에 반영했습니다.');}));}
function renderRuleRunTrace(){const target=$('rule-run-summary');if(!target)return;const latest=[...(item()?.ai_runs||[])].reverse().find(run=>run.operation==='extract'&&run.section===(section==='solution'?'solution':'body'));const stats=latest?.human_rule_stats;if(!stats){target.textContent='이 영역에서 재사용 규칙을 사용한 AI 전사 기록이 없습니다.';return;}const included=stats.included_rules||[];const ids=stats.included_finding_ids||[];target.innerHTML=`최근 전사 규칙: 포함 ${stats.included_count||0}개 · 충돌 제외 ${(stats.excluded_conflict_finding_ids||[]).length}개 · 중복 정리 ${(stats.deduplicated_finding_ids||[]).length}개 <details><summary>실행에 사용된 규칙과 제외 기록</summary>${included.length?included.map(rule=>`<article><strong>${esc(rule.category||'규칙')}</strong><p>${esc(rule.lesson||'')}</p><small>ID ${esc(rule.finding_id||'')} · ${esc(rule.source_excerpt||'')} → ${esc(rule.correction||'')}</small></article>`).join(''):ids.map(id=>`<small>포함 ID ${esc(id)}</small>`).join('')||'<p>포함된 규칙 없음</p>'}${(stats.excluded_conflict_finding_ids||[]).map(id=>`<p class="rule-status conflict">충돌로 제외: ${esc(id)}</p>`).join('')}${(stats.excluded_resolved_finding_ids||[]).map(id=>`<p class="rule-status duplicate">해소된 충돌에서 비선택: ${esc(id)}</p>`).join('')}${(stats.deduplicated_finding_ids||[]).map(id=>`<p class="rule-status duplicate">중복으로 제외: ${esc(id)}</p>`).join('')}${(stats.excluded_limit_finding_ids||[]).map(id=>`<p class="rule-status duplicate">지침 개수 제한으로 제외: ${esc(id)}</p>`).join('')}</details>`;}
function openImport(path=''){
  $('import-form').reset();$('source').value='';$('path').value=path;
  $('sample').onchange();notice('');$('import-dialog').showModal();
}
async function api(path,data,retryAuth=true){
  let response;
  try{
    response=await fetch(path,{method:data===undefined?'GET':'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Exam-Token':settings?.token||''},body:data===undefined?undefined:JSON.stringify(data)});
  }catch{
    connectionState('offline','서버 연결 끊김');
    throw Error('Exam Processor 서버에 연결할 수 없습니다. 서버가 다시 실행되면 자동으로 연결합니다. 작성 중인 내용은 이 화면에 유지됩니다.');
  }
  if(response.status===403&&retryAuth&&await refreshConnection())return api(path,data,false);
  let value={};try{value=await response.json();}catch{value={};}
  if(!response.ok){const detail=value.error||value.detail||'요청 실패';if(response.status===404&&String(path).includes('audit-waiver'))throw Error('대조 생략 API를 찾지 못했습니다. Exam Processor를 런처에서 다시 열거나 서버를 재시작한 뒤 페이지를 새로고침하세요.');if(response.status===404)throw Error(typeof detail==='string'&&detail?detail:'요청 경로를 찾지 못했습니다. 서버를 재시작한 뒤 새로고침하세요.');throw Object.assign(Error(typeof detail==='string'?detail:JSON.stringify(detail)),{status:response.status});}
  connectionState('online','서버 연결됨');
  return value;
}
function flowBasis(view){return view?JSON.stringify([view.id,view.revision,view.plan_hash]):null;}
function flowResponse(v){if(!v?.id||!v.revision||!v.plan_hash||!Array.isArray(v.entries))throw Error('저장 응답을 확인할 수 없습니다. 동일 요청의 결과를 다시 확인하세요.');return v;}
function flowRejected(error){return [400,409,422].includes(error.status);}
function flowStatus(id,text){if($(id).textContent!==text)$(id).textContent=text;}
function flowRecovery(id,needed){const panel=$(id);if(needed&&panel.dataset.recoveryNeeded!=='true')panel.open=true;panel.dataset.recoveryNeeded=String(needed);}
function flowRefresh(id,render){
 const root=$(id),active=document.activeElement,scroll=root.scrollTop;
 let selector=null;
 if(root.contains(active)){
  if(active.id)selector='#'+CSS.escape(active.id);
  else if(active.matches('input[value]'))selector='input[value="'+CSS.escape(active.value)+'"]';
  else if(active.dataset.reviewJob)selector='button[data-review-job="'+CSS.escape(active.dataset.reviewJob)+'"]'+(active.dataset.reviewItem?'[data-review-item="'+CSS.escape(active.dataset.reviewItem)+'"]':':not([data-review-item])');
  else if(active.dataset.job)selector='button[data-job="'+CSS.escape(active.dataset.job)+'"]';
  else if(active.matches('summary')&&active.parentElement.dataset.job)selector='details[data-job="'+CSS.escape(active.parentElement.dataset.job)+'"]>summary';
 }
 render();root.scrollTop=scroll;
 if(selector&&!active.isConnected){const next=root.querySelector(selector);if(next&&!next.disabled)next.focus({preventScroll:true});}
}

function fileURL(path){return `/api/jobs/${job.id}/file/${path.split('/').map(encodeURIComponent).join('/')}?v=${encodeURIComponent(item()?.revision||'')}`;}
function renderMarkdown(target,text){
  ExamPreview.render(target,text,fileURL,job.id);
  renderAuditBanner();
  bindPreviewFigureCrops(target);
}
function assetIndexFromFileUrl(src){
  if(!src||!src.includes('/file/assets/'))return null;
  const path=decodeURIComponent(src.split('/file/')[1]?.split('?')[0]||'');
  const index=(item()?.assets||[]).findIndex(asset=>asset.path===path);
  return index<0?null:index;
}
function openCropDialog(initialTarget){
  if(!cropTargets().length){
    notice('자를 그림이나 영역이 없습니다.',true);
    return;
  }
  cropZoom=1;
  const targets=cropTargets();
  cropDialogTarget=initialTarget&&targets.some(target=>target.value===initialTarget)
    ?initialTarget
    :defaultCropDialogTarget();
  notice('');
  renderCropDialog();
  $('crop-dialog').showModal();
  icons();
}
function openCropDialogForAsset(assetIndex){
  if(assetIndex==null){
    notice('자를 그림을 찾지 못했습니다.',true);
    return;
  }
  openCropDialog(`asset-index:${assetIndex}`);
}
function bindPreviewFigureCrops(target){
  if(!target||!item())return;
  target.querySelectorAll('img').forEach(img=>{
    const assetIndex=assetIndexFromFileUrl(img.getAttribute('src')||'');
    if(assetIndex==null)return;
    const asset=item().assets[assetIndex];
    img.classList.add('preview-fig-crop');
    img.dataset.cropAsset=String(assetIndex);
    img.title=`${asset.section==='solution'?'해설':'문제·지문'} ${asset.id} — 클릭하여 자르기`;
    img.onclick=event=>{
      event.preventDefault();
      openCropDialogForAsset(assetIndex);
    };
  });
}
$('normalize').onclick=()=>guard(async()=>{const target=section==='all'?editorFocus:section;if(!target)return;await save();job=await api(`/api/jobs/${job.id}/items/${currentId}/normalize`,{revision:item().revision,section:target});render();});
$('save-finding').onclick=()=>guard(async()=>{await save();const values={section:activeSection(),category:$('finding-category').value,source_excerpt:$('finding-source').value,extracted_excerpt:$('finding-extracted').value,correction:$('finding-correction').value,lesson:$('finding-lesson').value};await api(`/api/jobs/${job.id}/items/${currentId}/human-findings`,{revision:item().revision,values});['finding-source','finding-extracted','finding-correction','finding-lesson'].forEach(id=>$(id).value='');await loadHumanFindings();notice('사람 판정을 별도 학습 파일에 누적했습니다. 다음 같은 형식의 전사 요청부터 참고합니다.');});
async function loadList(){
    allJobs = await api('/api/jobs');
    $('jobs').innerHTML = allJobs.length ? allJobs.map(j => `<option value="${j.id}">${esc(j.source)} · ${esc(j.track)} · ${j.count}항목</option>`).join('') : '<option value="">작업 없음</option>';
    if (job) {
        $('jobs').value = job.id;
        updateJobButton();
    } else if (allJobs.length) {
        $('job-btn-label').textContent = '저장된 작업 선택';
    }
    return allJobs;
}
async function loadJob(id,options={}){
    await save();
    const nextJob=await api(`/api/jobs/${id}`);
    if(options.itemId&&!nextJob.items.some(i=>i.id===options.itemId))throw Error('선택한 문항이 현재 작업에 없습니다. 결과 목록에서 다시 확인하세요.');
    job=nextJob;
    currentId = job.items.some(i => i.id === currentId) ? currentId : job.items[0]?.id;
    section='all';setEditing(false);zoom=1;cropZoom=1;cropDialogTarget='';reviewTierFilter='all';$('filter').value='all';updateSection();
    if(options.itemId)currentId=options.itemId;
    applyDefaultViewTarget();
    window.multijobNavigationChanged?.(id);
    $('jobs').value = job.id;
    updateJobButton();
    render();
}
function updateJobButton() {
    if (!job) {
        $('job-btn-label').textContent = '저장된 작업 선택';
        return;
    }
    const approved = job.items.filter(i => i.review === 'approved').length;
    $('job-btn-label').textContent = `${job.source} · ${job.track} (${approved}/${job.items.length} 승인)`;
}

function renderList(){
  const items=visibleItems();
  const outside=item()&&!items.some(i=>i.id===currentId);
  $('count').textContent=`${job.items.filter(i=>i.review==='approved').length}/${job.items.length}`;
  $('items').innerHTML=items.map(i=>`<button data-item="${i.id}" class="${i.id===currentId?'active':''}"><span>${i.kind==='passage'?'지문 '+esc(i.number):i.kind==='script'?'듣기 대본 '+esc(i.number):i.kind==='concept'?'개념 '+esc(i.number):esc(i.number)+'번'}</span><span class="badge-group">${itemTierBadge(i)}${itemAuditBadge(i)}<span class="badge">${labels[i.review]}</span></span></button>`).join('')||`<p class="empty">${reviewTierFilter!=='all'&&(!job.review_queue_summary||job.review_queue_summary.status==='unavailable')?'분류 미계산 · 해당 등급의 건수를 확인할 수 없습니다.':'현재 필터에 해당하는 항목 0건'}</p>`;
  if(outside)$('items').insertAdjacentHTML('afterbegin',`<p class="empty">현재 선택: ${esc(item().number)} · 필터 밖 (${esc(item().review_decision?.tier||'미계산')})</p>`);
  $('items').querySelectorAll('button').forEach(b=>b.onclick=()=>guard(async()=>{
    await save();
    currentId=b.dataset.item;
    zoom=1;cropZoom=1;cropDialogTarget='';
    applyDefaultViewTarget();
    render();
  }));
  renderReviewQueueBar();
  syncUI();
}
function renderAuditBanner(){
  const banner=$('audit-banner'),alert=$('review-alert');if(!banner||!alert||!item())return;
  const summary=auditSummary(item());
  banner.hidden=false;
  alert.hidden=false;
  alert.classList.toggle('ok',summary.tone==='ok');
  alert.classList.toggle('block',summary.tone==='block');
  banner.classList.toggle('ok',summary.tone==='ok');
  banner.classList.toggle('block',summary.tone==='block');
  $('audit-banner-text').textContent=summary.text;
  const preview=$('preview');
  $('preview-status').textContent=editing?'현재 표시: 원전사 편집 · 미리보기에서 수식·그림 확인':preview?.dataset.mathStatus==='error'
    ?'현재 표시: 수식 렌더링 오류 · 원본과 확인 필요'
    :'현재 표시: 미리보기 · 원본과 직접 확인 필요';
  const blockers=approvalBlockers(item());
  $('approval-status').textContent='승인 조건: '+(dirty?'수정 저장 후 다시 확인':blockers.length?blockers.join(' / '):'서버 검사 통과 · 원본과 표시를 확인한 뒤 승인');
  $('approve').disabled=actionPending||!canApprove(item());
  $('approve').title=$('approval-status').textContent;
  const pending=!!item().transcription_pending?.length;
  const waived=auditWaived(item());
  $('audit-banner-run').disabled=pending||waived||job?.task?.status==='running';
  const waiveBtn=$('audit-banner-waive');
  if(waiveBtn){waiveBtn.hidden=waived||pending;waiveBtn.disabled=pending||job?.task?.status==='running';}
  $('audit-banner-scroll').disabled=false;
}
function renderTaskState(){
  const el=$('task-state');
  if(!el||!job)return;
  const busy=job.task?.status==='running'||job.queue?.status==='running';
  el.classList.toggle('ai-busy',busy);
  if(busy){
    const msg=job.ai_progress?.message||(
      job.queue?.status==='running'
        ?`일괄 ${job.queue.operation==='extract'?'변환':'대조'} ${job.queue.entries.filter(e=>e.status==='completed').length}/${job.queue.entries.length}`
        :'AI 처리 중…'
    );
    el.textContent=msg;
    el.title=msg;
    return;
  }
  el.textContent=job.task?.message||'';
  el.title=job.task?.message||'';
}
function render(){if(!job)return;renderList();const i=item();if(!i)return;$('item-title').textContent=(i.kind==='passage'?'공통 지문 ':i.kind==='script'?'듣기 대본 ':i.kind==='concept'?'개념 ':'문항 ')+i.number;const origin=i.workbook_view?.metadata?.origin;const standard=i.standard_preview;const originLabel=[origin?.raw_label?'기출 표기: '+origin.raw_label:'',standard?.origin_status==='unknown'?'원출처 미확인':'',standard?.solution_source?'수록·해설: '+standard.solution_source:''].filter(Boolean).join(' · ');$('workbook-origin').hidden=!originLabel;$('workbook-origin').textContent=originLabel;$('workbook-origin').title=originLabel;const auditLine=auditSummary(i).text;$('state').textContent=labels[i.review]+(i.published_revision===i.revision?' · 내보냄':'')+' · '+auditLine.split(' · ')[0];const materialWarnings=(i.material_validation_errors||[]).map(v=>'자료 전사 누락: '+v);$('warnings').textContent=[...job.warnings,...(i.errors||[]),...i.warnings,...(i.workbook_view?.warnings||[]),...(i.standard_preview?.warnings||[]),...materialWarnings].join(' / ');renderAuditBanner();$('unit').value=i.unit;$('q-type').value=i.q_type;$('points').value=i.points??'';$('correct-rate').value=(i.correct_rate??i.workbook_view?.metadata?.correct_rate)==null?'':(i.correct_rate??i.workbook_view?.metadata?.correct_rate)*100;$('answer').value=i.answer;$('answer').placeholder=(!i.answer&&i.standard_preview?.answer)?'인쇄 정답: '+i.standard_preview.answer:'미제공';$('review-note').value=i.note||'';loadEditors(i);$('save-state').textContent='저장됨';dirty=false;renderMarkdown($('preview'),previewText(i,true));renderOriginal();renderAudit();renderCandidates();loadHumanFindings();renderTaskState();window.renderAiActivity?.();const busy=job.task?.status==='running'||job.queue?.status==='running';$('convert').disabled=busy;renderAuditBanner();icons();}
async function loadHumanFindings(){if(!job||!item())return;try{const data=await api(`/api/human-findings?format_id=${encodeURIComponent(job.format||'')}&section=${encodeURIComponent(activeSection())}&track=${encodeURIComponent(job.track||'')}`);$('finding-count').textContent=`적용 ${data.prompt_rule_count}개 · 충돌 제외 ${data.conflict_count}개 · 충돌 해소 ${data.resolved_count}개 · 중복 정리 ${data.duplicate_count}개`;$('finding-list').innerHTML=data.findings.map(f=>`<article class="finding-entry"><strong>${esc(f.category)}</strong>${f.rule_status==='conflict'?'<span class="rule-status conflict">충돌: AI 지침에서 제외됨</span>':f.rule_status==='resolved_selected'?'<span class="rule-status resolved">충돌 해소: 선택됨</span>':f.rule_status==='resolved_excluded'?'<span class="rule-status duplicate">충돌 해소: 선택 규칙만 적용</span>':f.rule_status==='duplicate'?'<span class="rule-status duplicate">중복: AI 지침에는 한 번만 포함</span>':''}<p>${esc(f.lesson)}</p>${f.source_excerpt||f.correction?`<small>${esc(f.source_excerpt||'')} → ${esc(f.correction||'')}${f.related_finding_id?' · 관련 규칙 있음':''}</small>`:''}${f.rule_status==='conflict'?`<button type="button" data-resolve="${f.finding_id}">이 교정을 기준으로 충돌 해소</button>`:''}<button type="button" data-finding="${f.finding_id}">사용 중지</button></article>`).join('')||'<p class="finding-empty">아직 누적된 판정이 없습니다.</p>';$('finding-list').querySelectorAll('[data-resolve]').forEach(button=>button.onclick=()=>guard(async()=>{const rationale=await examPrompt({title:'충돌 해소',message:'이 교정을 기준으로 선택하는 이유를 적어 주세요.',label:'판단 근거 (10자 이상)',minLength:10,ok:'저장'});if(rationale===null)return;await api(`/api/human-findings/${button.dataset.resolve}/resolve-conflict`,{rationale});await loadHumanFindings();notice('선택한 규칙과 판단 근거를 기록했습니다. 선택된 교정만 다음 AI 지침에 포함됩니다.');}));$('finding-list').querySelectorAll('[data-finding]').forEach(button=>button.onclick=()=>guard(async()=>{await api(`/api/human-findings/${button.dataset.finding}/deactivate`,{});await loadHumanFindings();notice('판정을 보존한 채 이후 추출 지침에서 제외했습니다.');}));}catch(e){$('finding-count').textContent='판정 목록을 불러오지 못했습니다.';}}
function activeSection(){return section==='all'?'body':section;}
function editableSource(i,clean){return clean&&i?.workbook_view&&!i.workbook_view.warnings?.length?i.workbook_view:i;}
function editableText(i,clean=false){const source=editableSource(i,clean);if(section!=='all')return source?.[section]||'';return [source?.body||'',source?.solution||''].filter(Boolean).join('\n\n---\n\n');}
function problemPreviewHeader(i){
  if(i?.kind!=='question'||!i?.display_title)return '';
  const title=`## ${i.display_title}`;
  return `${title}\n\n`;
}
function withProblemPreviewHeader(text,i){
  const body=String(text||'');
  if(!body||section==='solution')return body;
  const header=problemPreviewHeader(i);
  if(!header||body.startsWith(header.trim())||/^##\s+/m.test(body.slice(0,120)))return body;
  return header+body;
}
function englishScriptOutputStem(number){return /^\d+$/.test(String(number||''))?String(number).padStart(2,'0'):String(number||'');}
function englishListeningScriptPreview(i){
  if(job?.format!=='kice_english'||i?.kind!=='question'||!i?.listening_script_id)return '';
  const script=job.items.find(entry=>entry.id===i.listening_script_id);
  if(!script||script.kind!=='script')return '';
  const embed=`![[../scripts/${englishScriptOutputStem(script.number)}.md]]`;
  const body=(script.body||'').trim();
  return `\n\n### 듣기 대본\n\n${embed}${body?`\n\n${body}`:''}`;
}
function previewText(i,clean=false){
  const source=editableSource(i,clean);
  const scriptBlock=englishListeningScriptPreview(i);
  if(clean&&i?.standard_preview?.status==='unavailable'){
    return '표준 표시 미확정: '+(i.standard_preview.warnings||[]).join(' / ')+'\n\n'+editableText(i);
  }
  if(clean&&i?.standard_preview?.status==='computed'){
    const view=i.standard_preview;
    const text=view[section]||'';
    return text;
  }
  const body=withProblemPreviewHeader(source?.body||'',i);
  if(!scriptBlock){
    if(section==='body')return body;
    if(section==='solution')return source?.solution||'';
    const sol=source?.solution||'';
    return [body,sol].filter(Boolean).join('\n\n---\n\n');
  }
  if(section==='body')return body;
  if(section==='solution'){
    const sol=(source?.solution||'').trim();
    return sol?sol+scriptBlock:scriptBlock.trim();
  }
  const solPart=[(source?.solution||'').trim(),scriptBlock.trim()].filter(Boolean).join('\n\n');
  return [body,solPart].filter(Boolean).join('\n\n---\n\n');
}
function displayText(i,clean=false){return editableText(i,clean);}
function sourceRegions(){if(!item())return [];if(section==='all')return [...(item().regions||[]),...(item().solution_regions||[])];return item()[section==='solution'?'solution_regions':'regions']||[];}
function sourceAssets(){if(!item())return [];const assets=item().assets||[];if(section==='all')return assets;return assets.filter(a=>a.section===(section==='solution'?'solution':'body'));}
function isStackViewTarget(value){
  return value==='combined-regions'||value==='body-regions-all'||value==='solution-regions-all';
}
function defaultStackViewTarget(targets){
  const combined=targets.find(target=>target.value==='combined-regions');
  if(combined)return combined.value;
  return targets.find(target=>isStackViewTarget(target.value))?.value||'';
}
function applyDefaultViewTarget(){
  if(!item()){viewTarget='';return;}
  const targets=viewTargets();
  viewTarget=defaultStackViewTarget(targets)||targets[0]?.value||'';
}
function viewTargets(){
  if(!item())return [];
  const targets=[];
  const addRegions=(regions,kind,label)=>{
    (regions||[]).forEach((region,index)=>{
      targets.push({value:`${kind}:${index}`,label:`${label} ${index+1} · ${region.page||'?'}쪽`});
    });
  };
  const bodyRegions=item().regions||[];
  const solutionRegions=item().solution_regions||[];
  addRegions(bodyRegions,'body-region','문제 영역');
  addRegions(solutionRegions,'solution-region','해설 영역');
  if(bodyRegions.length&&solutionRegions.length){
    targets.unshift({value:'combined-regions',label:'문제·해설 영역 모두'});
  }else if(bodyRegions.length>1){
    targets.unshift({value:'body-regions-all',label:'문제 영역 모두'});
  }else if(solutionRegions.length>1){
    targets.unshift({value:'solution-regions-all',label:'해설 영역 모두'});
  }
  (item().assets||[]).forEach((asset,index)=>{
    const sec=asset.section==='solution'?'해설':'본문';
    targets.push({value:`asset-view:${index}`,label:`${asset.id} · ${sec} 삽입 그림`});
  });
  return targets;
}
function stackedViewRegions(target=currentViewTarget()){
  if(!item())return [];
  const rows=[];
  if(target==='combined-regions'){
    (item().regions||[]).forEach((region,index)=>rows.push({region,label:`문제 영역 ${index+1}`}));
    (item().solution_regions||[]).forEach((region,index)=>rows.push({region,label:`해설 영역 ${index+1}`}));
    return rows;
  }
  if(target==='body-regions-all'){
    (item().regions||[]).forEach((region,index)=>rows.push({region,label:`문제 영역 ${index+1}`}));
    return rows;
  }
  if(target==='solution-regions-all'){
    (item().solution_regions||[]).forEach((region,index)=>rows.push({region,label:`해설 영역 ${index+1}`}));
    return rows;
  }
  return rows;
}
function currentViewTarget(){
  const targets=viewTargets();
  if(!targets.length)return '';
  const stack=defaultStackViewTarget(targets);
  if(stack&&(!viewTarget||isStackViewTarget(viewTarget)||!targets.some(target=>target.value===viewTarget)))
    return stack;
  if(viewTarget&&targets.some(target=>target.value===viewTarget))return viewTarget;
  return stack||targets[0].value;
}
function cropTargets(){
  if(!item())return [];
  const targets=[];
  const addRegions=(regions,kind,label)=>{
    (regions||[]).forEach((region,index)=>{
      targets.push({value:`${kind}:${index}`,label:`${label} ${index+1} · ${region.page||'?'}쪽`});
    });
  };
  if(section==='all'){
    addRegions(item().regions,'body-region','문제 영역');
    addRegions(item().solution_regions,'solution-region','해설 영역');
  }else{
    addRegions(sourceRegions(),section==='solution'?'solution-region':'body-region','영역');
  }
  allCropAssets().forEach((asset,index)=>{
    const title=asset.structured?.title||'';
    const sec=asset.section==='solution'?'해설':'문제·지문';
    targets.push({value:`asset-index:${index}`,label:`${sec} · ${asset.id}${title?' · '+title:''}`});
  });
  return targets;
}
function allCropAssets(){
  if(!item())return [];
  return item().assets||[];
}
function regionForAsset(asset){
  if(!item()||!asset)return null;
  const crop=asset.crop||{};
  const pool=asset.section==='solution'?item().solution_regions:item().regions;
  const document=crop.document;
  const page=crop.page;
  let index=crop.region_index;
  if(index!=null){
    index=Number(index);
    if(Number.isInteger(index)&&index>=0&&index<(pool||[]).length){
      const region=pool[index];
      if((!document||region.document===document)&&(page==null||region.page===page))return region;
    }
  }
  const source=crop.source_region;
  const matches=(pool||[]).filter(region=>{
    if(document&&region.document!==document)return false;
    if(page!=null&&region.page!==page)return false;
    if(source!=null&&!sameBox(region.bbox,source))return false;
    return true;
  });
  if(matches.length===1)return matches[0];
  if(source!=null){
    const loose=(pool||[]).filter(region=>{
      if(document&&region.document!==document)return false;
      if(page!=null&&region.page!==page)return false;
      return true;
    });
    if(loose.length===1)return loose[0];
    return null;
  }
  if(source==null&&(pool||[]).length===1)return pool[0];
  return null;
}
function currentCropTarget(){
  const targets=cropTargets();
  if(!targets.length)return '';
  return targets.some(target=>target.value===cropDialogTarget)?cropDialogTarget:targets[0].value;
}
function regionForViewTarget(target){
  if(!item()||!target||isStackViewTarget(target))return null;
  const index=Number(target.split(':').pop());
  if(target.startsWith('solution-region:'))return (item().solution_regions||[])[index]||null;
  if(target.startsWith('body-region:'))return (item().regions||[])[index]||null;
  return null;
}
function sameBox(left,right){
  return Array.isArray(left)&&Array.isArray(right)&&left.length===4&&right.length===4&&left.every((value,index)=>Number(value)===Number(right[index]));
}
function regionForTarget(target){
  if(!item()||!target)return null;
  if(target.startsWith('asset-index:')){
    const asset=(item().assets||[])[Number(target.slice(12))];
    return regionForAsset(asset);
  }
  const index=Number(target.split(':').pop());
  if(target.startsWith('solution-region:'))return (item().solution_regions||[])[index]||null;
  if(target.startsWith('body-region:'))return (item().regions||[])[index]||null;
  return sourceRegions()[index]||null;
}
function defaultBoxForTarget(target){
  if(target.startsWith('asset-index:')){
    const asset=(item().assets||[])[Number(target.slice(12))];
    const box=asset?.crop?.proposed_box;
    if(Array.isArray(box)&&box.length===4)return box.map(Number);
  }
  return [0,0,1000,1000];
}
function clampBox(box){
  let [ymin,xmin,ymax,xmax]=box.map(Number);
  xmin=Math.min(1000,Math.max(0,xmin));xmax=Math.min(1000,Math.max(0,xmax));
  ymin=Math.min(1000,Math.max(0,ymin));ymax=Math.min(1000,Math.max(0,ymax));
  if(xmax-xmin<8)xmax=Math.min(1000,xmin+8);
  if(ymax-ymin<8)ymax=Math.min(1000,ymin+8);
  if(xmax<=xmin)xmin=Math.max(0,xmax-8);
  if(ymax<=ymin)ymin=Math.max(0,ymax-8);
  return [Math.round(ymin),Math.round(xmin),Math.round(ymax),Math.round(xmax)];
}
function applyCropOverlay(){
  const box=$('crop-box');
  if(!box)return;
  const [ymin,xmin,ymax,xmax]=cropBox;
  box.style.left=`${xmin/10}%`;
  box.style.top=`${ymin/10}%`;
  box.style.width=`${(xmax-xmin)/10}%`;
  box.style.height=`${(ymax-ymin)/10}%`;
}
function pointerOnImage(event,image){
  const rect=image.getBoundingClientRect();
  const x=rect.width?((event.clientX-rect.left)/rect.width)*1000:0;
  const y=rect.height?((event.clientY-rect.top)/rect.height)*1000:0;
  return {x:Math.min(1000,Math.max(0,x)),y:Math.min(1000,Math.max(0,y))};
}
function bindCropOverlay(){
  const stage=$('crop-stage'),image=$('crop-image'),box=$('crop-box');
  if(!stage||!image||!box)return;
  applyCropOverlay();
  const startDrag=(mode,event)=>{
    event.preventDefault();
    const point=pointerOnImage(event,image);
    cropPointer={mode,x:point.x,y:point.y,box:cropBox.slice()};
    stage.setPointerCapture?.(event.pointerId);
  };
  box.onpointerdown=event=>{if(event.target===box){event.stopPropagation();startDrag('move',event);}};
  stage.querySelectorAll('.crop-handle').forEach(handle=>{
    handle.onpointerdown=event=>{event.stopPropagation();startDrag(handle.dataset.handle,event);};
  });
  stage.onpointerdown=event=>{
    if(event.target===image||event.target===stage)startDrag('draw',event);
  };
  stage.onpointermove=event=>{
    if(!cropPointer)return;
    const point=pointerOnImage(event,image);
    const [ymin,xmin,ymax,xmax]=cropPointer.box;
    if(cropPointer.mode==='draw'){
      cropBox=clampBox([Math.min(cropPointer.y,point.y),Math.min(cropPointer.x,point.x),Math.max(cropPointer.y,point.y),Math.max(cropPointer.x,point.x)]);
    }else if(cropPointer.mode==='move'){
      const dx=point.x-cropPointer.x,dy=point.y-cropPointer.y;
      const width=xmax-xmin,height=ymax-ymin;
      const nextX=Math.min(1000-width,Math.max(0,xmin+dx));
      const nextY=Math.min(1000-height,Math.max(0,ymin+dy));
      cropBox=clampBox([nextY,nextX,nextY+height,nextX+width]);
    }else{
      let next=[ymin,xmin,ymax,xmax];
      if(cropPointer.mode.includes('n'))next[0]=point.y;
      if(cropPointer.mode.includes('s'))next[2]=point.y;
      if(cropPointer.mode.includes('w'))next[1]=point.x;
      if(cropPointer.mode.includes('e'))next[3]=point.x;
      cropBox=clampBox(next);
    }
    applyCropOverlay();
  };
  const stopDrag=()=>{cropPointer=null;};
  stage.onpointerup=stopDrag;
  stage.onpointercancel=stopDrag;
}
function renderOriginal(){
  const targets=viewTargets();
  const select=$('regions');
  viewTarget=currentViewTarget();
  select.hidden=!targets.length;
  select.innerHTML=targets.map(target=>`<option value="${esc(target.value)}">${esc(target.label)}</option>`).join('');
  if(viewTarget)select.value=viewTarget;
  $('open-crop').disabled=!cropTargets().length;
  if(isStackViewTarget(viewTarget)){
    const rows=stackedViewRegions(viewTarget);
    if(!rows.length){
      $('original').innerHTML='<pre></pre>';
      $('original').firstChild.textContent='원본 없음';
      $('open-pdf').disabled=true;
      return;
    }
    $('open-pdf').disabled=false;
    $('open-pdf').title='첫 영역 PDF 페이지 열기';
    $('original').innerHTML=rows.map(({region,label})=>`<figure class="original-stack"><figcaption>${esc(label)} · ${region.page||'?'}쪽</figcaption><img class="original-image" alt="${esc(label)}" src="${fileURL('regions/'+region.image)}" draggable="false" style="width:${zoom*100}%;max-width:none"></figure>`).join('');
    return;
  }
  if(viewTarget.startsWith('asset-view:')){
    const asset=(item().assets||[])[Number(viewTarget.slice(11))];
    const sourceRegion=regionForAsset(asset);
    $('open-pdf').disabled=!sourceRegion;
    $('open-pdf').title=sourceRegion?'삽입 그림의 원본 PDF 페이지 열기':'연결된 원본 PDF 영역 없음';
    if(!asset){
      $('original').innerHTML='<pre>삽입 그림 없음</pre>';
      return;
    }
    $('original').innerHTML=`<figure class="original-stack"><figcaption>${esc(asset.id)} · ${asset.section==='solution'?'해설':'본문'} 삽입 그림</figcaption><img class="original-image" alt="${esc(asset.id)} 삽입 그림" src="${fileURL(asset.path)}" draggable="false" style="width:${zoom*100}%;max-width:none"></figure>`;
    return;
  }
  const region=regionForViewTarget(viewTarget);
  $('open-pdf').disabled=!region;
  $('open-pdf').title='원본 PDF 열기';
  if(!region){
    $('original').innerHTML='<pre></pre>';
    $('original').firstChild.textContent=section==='solution'?item()?.solution_reference||'연결된 원본 해설 없음':item()?.reference_text||'원본 없음';
    return;
  }
  $('original').innerHTML=`<img class="original-image" alt="${region.page||'?'}쪽 원본" src="${fileURL('regions/'+region.image)}" draggable="false" style="width:${zoom*100}%;max-width:none">`;
}
function defaultCropDialogTarget(){
  const targets=cropTargets();
  const asset=targets.find(target=>target.value.startsWith('asset-index:'));
  return asset?asset.value:targets[0]?.value||'';
}
function renderCropDialog(options={}){
  const targets=cropTargets();
  if(!cropDialogTarget||!targets.some(target=>target.value===cropDialogTarget)){
    cropDialogTarget=defaultCropDialogTarget();
  }
  $('crop-target').innerHTML=targets.map(target=>`<option value="${esc(target.value)}">${esc(target.label)}</option>`).join('');
  $('crop-target').value=cropDialogTarget;
  if(options.resetBox!==false)cropBox=clampBox(defaultBoxForTarget(cropDialogTarget));
  const region=regionForTarget(cropDialogTarget);
  const hint=$('crop-hint');
  const workspace=$('crop-workspace');
  $('crop-bounds-numeric').hidden=cropDialogTarget.startsWith('asset-index:');
  if(!region){
    workspace.innerHTML='<p class="empty">원본 영역을 찾지 못했습니다. 대상을 바꾸거나 항목을 새로고침하세요.</p>';
    hint.textContent=cropDialogTarget.startsWith('asset-index:')
      ? '선택한 그림의 PDF 영역 정보가 없거나 여러 영역과 겹칩니다. AI 재변환 후 다시 시도하거나 다른 그림을 먼저 확인하세요.'
      :'';
    return;
  }
  hint.textContent=cropDialogTarget.startsWith('asset-index:')
    ?'삽입 그림만 남기도록 박스를 맞춘 뒤 저장합니다. 바깥 회색은 잘리는 영역입니다.'
    :'PDF 영역 bbox를 줄이거나 늘립니다. 영역 좌표(pt) 버튼으로 숫자 입력도 가능합니다.';
  workspace.innerHTML=`<div id="crop-stage" class="crop-stage" style="width:${cropZoom*100}%"><img id="crop-image" alt="${region.page||'?'}쪽 원본" src="${fileURL('regions/'+region.image)}" draggable="false"><div id="crop-box" class="crop-box"><span class="crop-handle" data-handle="nw"></span><span class="crop-handle" data-handle="ne"></span><span class="crop-handle" data-handle="sw"></span><span class="crop-handle" data-handle="se"></span></div></div>`;
  bindCropOverlay();
  icons();
}
async function saveCrop(){
  const target=currentCropTarget();
  if(!target)throw Error('자르기 대상이 없습니다.');
  if(!regionForTarget(target))throw Error('원본 영역을 찾지 못했습니다.');
  await save();
  job=await api(`/api/jobs/${job.id}/items/${currentId}/crop`,{revision:item().revision,target,box:cropBox});
  cropDialogTarget=target;
  $('crop-dialog').close();
  render();
  notice(target.startsWith('asset-index:')?'삽입 그림 크롭을 저장했습니다. 원본 재대조가 필요합니다.':'원본 영역을 저장했습니다. 원본 재대조가 필요합니다.');
}
function suggestionSection(record,issue){
  if(!issue.extracted||!issue.suggestion||issue.extracted===issue.suggestion)return null;
  const matches=['body','solution'].flatMap(key=>Array(record[key].split(issue.extracted).length-1).fill(key));
  return matches.length===1?matches[0]:null;
}
function renderAudit(){
  const i=item(),a=i.audit,stale=a&&a.revision!==i.revision;
  $('focused-open').hidden=!(i.review==='pending'&&i.kind==='question'&&i.review_decision?.tier==='YELLOW');
  const stateEl=$('audit-state');
  if(auditWaived(i)){
    stateEl.textContent='대조 생략 · '+ (i.audit_waiver?.reason||'사유 기록');
    stateEl.classList.remove('audit-stale','audit-ok');
    $('issues').innerHTML='<p class="audit-waiver-note">AI 원본 대조 없이 승인할 수 있습니다. 내용을 수정하면 생략은 무효화됩니다.</p>';
    return;
  }
  stateEl.textContent=a?(stale?'재대조 필요 · 이전 버전 · ':'')+auditStates[a.result]+(a.scope==='markdown_reference'?' · Markdown 참조 대조':' · PDF 대조'):'미실행';
  stateEl.classList.toggle('audit-stale',!!stale||!a||a.status!=='completed');
  stateEl.classList.toggle('audit-ok',!!a&&a.status==='completed'&&!stale&&a.result==='no_difference');
  $('issues').innerHTML=(a?.issues||[]).map((v,n)=>{
    const target=suggestionSection(i,v),blocked=!target||stale||a?.status!=='completed';
    const classes=['issue',v.applied?'issue-applied':'',v.skipped?'issue-skipped':''].filter(Boolean).join(' ');
    const actions=v.applied
      ? `<div class="issue-actions"><span class="issue-status">적용됨 · 재대조 필요</span><button type="button" data-revert-issue="${n}"><i data-lucide="undo-2"></i>AI 제안 되돌리기</button></div>`
      : v.skipped
        ? `<div class="issue-actions"><span class="issue-status">스킵됨</span><label class="issue-skip-note"><span>스킵 메모</span><input type="text" data-skip-note="${n}" maxlength="500" value="${esc(v.skip_note||'')}" placeholder="스킵한 판단 근거"><button type="button" data-save-skip-note="${n}">메모 저장</button></label><button type="button" data-skip-issue="${n}" data-skipped="false"><i data-lucide="undo-2"></i>스킵 취소</button></div>`
        : `<div class="issue-actions"><button data-issue="${n}" data-blocked="${Boolean(blocked)}" ${blocked?'disabled':''}>${target==='solution'?'해설에 AI 제안 적용':'AI 제안 적용'}</button><button type="button" data-skip-issue="${n}" data-skipped="true">스킵</button>${blocked?'<span class="issue-status">자동 적용 불가 · 편집에서 확인</span>':''}</div>`;
    return `<div class="${classes}"><div class="issue-meta"><span class="issue-category">${esc(v.category||'텍스트')}</span><strong>${esc(v.location)}</strong></div><p>${esc(v.message)}</p><p>원본: ${esc(v.original)}</p><p>추출: ${esc(v.extracted)}</p><p>제안: ${esc(v.suggestion)}</p>${actions}</div>`;
  }).join('');
  $('issues').querySelectorAll('button[data-issue]').forEach(b=>b.onclick=()=>guard(async()=>{
    const issue=a.issues[Number(b.dataset.issue)];await save();
    if(a.revision!==item().revision||a.status!=='completed')throw Error('이전 버전 지적은 적용할 수 없습니다. 재대조가 필요합니다.');
    const target=suggestionSection(item(),issue);
    if(!target)throw Error('수정 위치를 유일하게 찾지 못했습니다. 편집에서 확인하세요.');
    b.textContent='적용 중...';
    job=await api(`/api/jobs/${job.id}/items/${currentId}`,{revision:item().revision,values:{[target]:item()[target].replace(issue.extracted,()=>issue.suggestion)}});
    section=target;updateSection();render();
    const applied=item().audit?.issues[Number(b.dataset.issue)]?.applied;
    notice(applied?'AI 제안 적용됨 · 새 버전 저장 · 원본 재대조 필요':'저장 후 서식 검사를 거쳤습니다. 제안 반영 여부를 확인하고 재대조하세요.',!applied);
  }));
  $('issues').querySelectorAll('button[data-revert-issue]').forEach(b=>b.onclick=()=>guard(async()=>{
    await save();
    const index=Number(b.dataset.revertIssue);
    const sectionHint=a.issues[index]?.applied_section;
    job=await api(`/api/jobs/${job.id}/items/${currentId}/audit-issues/${index}/revert`,{revision:item().revision});
    if(sectionHint==='body'||sectionHint==='solution'){section=sectionHint;updateSection();}
    render();
    notice('AI 제안을 되돌렸습니다. 원본 대조를 다시 실행할 수 있습니다.');
  }));
  $('issues').querySelectorAll('button[data-skip-issue]').forEach(b=>b.onclick=()=>guard(async()=>{
    await save();
    const skipped=b.dataset.skipped==='true';
    job=await api(`/api/jobs/${job.id}/items/${currentId}/audit-issues/${b.dataset.skipIssue}/skip`,{revision:item().revision,skipped});
    render();
    notice(skipped?'AI 지적을 스킵했습니다.':'AI 지적의 스킵을 취소했습니다.');
  }));
  $('issues').querySelectorAll('button[data-save-skip-note]').forEach(b=>b.onclick=()=>guard(async()=>{
    await save();
    const index=b.dataset.saveSkipNote;
    const note=$('issues').querySelector(`[data-skip-note="${index}"]`).value;
    job=await api(`/api/jobs/${job.id}/items/${currentId}/audit-issues/${index}/skip`,{revision:item().revision,skipped:true,note});
    render();
    notice(note.trim()?'스킵 메모와 AI 검수 판정 이력을 저장했습니다.':'스킵 메모를 비웠습니다. 판정 이력은 유지됩니다.');
  }));
  $('issues').querySelectorAll('input[data-skip-note]').forEach(input=>input.onkeydown=event=>{
    if(event.key==='Enter'){
      event.preventDefault();
      $('issues').querySelector(`[data-save-skip-note="${input.dataset.skipNote}"]`).click();
    }
  });
  icons();
}
function renderCandidates(){
  const i=item(),candidates=i.solution_candidates||[],match=i.solution_match;
  if(!match&&!candidates.length){$('candidates').innerHTML='';return;}
  const statuses={matched:'단일 후보',ambiguous:'복수 후보 · 선택 필요',unmatched:'후보 없음'};
  const basisLabels={academic_year:'학년도',track:'선택과목',question_number:'문항 번호',workbook:'교재',chapter:'목차 구간',book_number:'교재 번호'};
  const basis=(match?.basis||[]).map(v=>basisLabels[v]||v).join(' + ')||'일치 근거 없음';
  const selected=candidates.find(c=>c.id===i.selected_solution_id);
  const options=candidates.map(c=>{
    const meta=[c.academic_year&&`${c.academic_year}학년도`,c.track,c.number&&`${c.number}번`].filter(Boolean).join(' · ');
    return `<option value="${esc(c.id)}" ${c.id===i.selected_solution_id?'selected':''}>${esc(meta||c.number+'번')} · ${esc(c.body.slice(0,70))}</option>`;
  }).join('');
  $('candidates').innerHTML=`<div class="solution-match-head"><strong>해설 후보</strong><span class="match-status match-${esc(match?.status||'unknown')}">${selected?'연결됨':esc(statuses[match?.status]||'상태 확인')}</span></div><p class="match-basis">매칭 근거: ${esc(basis)}</p>${selected?`<p class="selected-solution">현재 연결: ${esc(selected.academic_year?selected.academic_year+'학년도 · ':'')}${esc(selected.track?selected.track+' · ':'')}${esc(selected.number)}번</p>`:''}${candidates.length?`<div class="solution-match-actions"><select id="candidate-choice" aria-label="해설 후보">${options}</select><button id="link-solution"><i data-lucide="link"></i>${selected?'다른 후보 연결':'선택 후보 연결'}</button>${selected?'<button id="unlink-solution"><i data-lucide="unlink"></i>연결 해제</button>':''}</div>`:'<p class="match-empty">현재 문제와 연결할 해설을 찾지 못했습니다.</p>'}`;
  if(candidates.length)$('link-solution').onclick=()=>guard(async()=>{await save();const candidate=$('candidate-choice').value;if(candidate===item().selected_solution_id){notice('이미 이 해설이 연결되어 있습니다.');return;}job=await api(`/api/jobs/${job.id}/items/${currentId}/link`,{revision:item().revision,candidate});section='solution';updateSection();render();notice('해설 후보를 연결했습니다. 원본 해설과 결과를 대조하세요.');});
  if(selected)$('unlink-solution').onclick=()=>guard(async()=>{await save();if(!await examConfirm({title:'해설 연결 해제',message:'해설 연결을 해제하고 연결 전 해설과 정답으로 되돌릴까요? 연결 후 수정 내용은 이력에 보관됩니다.',ok:'연결 해제',dismissKey:'unlink-solution'}))return;job=await api(`/api/jobs/${job.id}/items/${currentId}/unlink`,{revision:item().revision});section='solution';updateSection();render();notice('해설 연결을 해제했습니다. 다시 후보를 확인해야 승인할 수 있습니다.');});
}
async function save(){clearTimeout(timer);if(saving){await saving;return save();}if(!dirty)return;const sent=generation;const values={unit:$('unit').value,q_type:$('q-type').value,points:$('points').value===''?null:Number($('points').value),correct_rate:$('correct-rate').value===''?null:Number($('correct-rate').value)/100,answer:$('answer').value,note:$('review-note').value};Object.assign(values,editorValues());$('save-state').textContent='저장 중';saving=api(`/api/jobs/${job.id}/items/${currentId}`,{revision:item().revision,values});try{job=await saving;dirty=generation!==sent;$('save-state').textContent=dirty?'수정 중':'저장됨';renderList();renderAudit();if(!dirty){loadEditors(item());renderMarkdown($('preview'),previewText(item(),true));}}catch(e){$('save-state').textContent='저장 실패 · 수정본 유지';throw e;}finally{saving=null;syncUI();}if(dirty)return save();}
function changed(){generation++;dirty=true;$('save-state').textContent='수정 중';clearTimeout(timer);timer=setTimeout(()=>guard(save),1400);syncUI();}
async function guard(action){if(actionPending)return;actionPending=true;allowEdits=action===save;notice('');syncUI();try{await action();}catch(e){notice(e.message,true);if(/AI 검수|AI 지적|원본 대조/.test(String(e.message||'')))focusAuditUI();}finally{actionPending=false;allowEdits=false;syncUI();}}
let editorFocus=null;
function editorValues(){return section==='all'?{body:$('body-editor').value,solution:$('solution-editor').value}:{[section]:$('editor').value};}
function resizeEditors(){
  if(!editing||section!=='all')return;
  const panel=$('combined-editor'),scroll=panel.scrollTop;
  for(const id of ['body-editor','solution-editor']){const el=$(id);el.style.height='0px';el.style.height=Math.max(180,el.scrollHeight+2)+'px';}
  panel.scrollTop=scroll;
}
function syncEditorView(){
  $('editor').hidden=!editing||section==='all';
  $('combined-editor').hidden=!editing||section!=='all';
  $('preview').hidden=editing;
  $('preview-tab').setAttribute('aria-pressed',String(!editing));$('edit-tab').setAttribute('aria-pressed',String(editing));
  resizeEditors();
}
function loadEditors(i){
  const source=editableSource(i);
  for(const [id,value] of [['editor',editableText(i)],['body-editor',source?.body||''],['solution-editor',source?.solution||'']]){
    // Do not reset the caret on an unchanged autosave response.
    if($(id).value!==value)$(id).value=value;
  }
  syncEditorView();
}
function updateEditorTools(){
  const target=section==='all'?editorFocus:section;
  $('normalize').disabled=!item()||(section==='all'&&(!editing||!target));
  $('normalize').title=target?`${target==='body'?'문제·지문':'해설'} 인쇄용 줄바꿈 정리`:'편집할 문제·지문 또는 해설에 커서를 놓으세요';
}
function updateSection(){
  document.querySelectorAll('[data-section]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.section===section)));
  editorFocus=null;$('edit-tab').disabled=false;$('edit-tab').title='문제·지문과 해설 편집';
  updateEditorTools();
  $('convert').title=section==='all'?'전체 보기에서 AI 변환은 문제·지문에 적용됩니다. 해설 변환은 해설 탭에서 실행하세요.':'';
}
async function setEditing(value){
  editing=value;syncEditorView();syncUI();
  if(value||!job)return;
  if(!dirty){renderMarkdown($('preview'),previewText(item(),true));return;}
  const id=currentId, selected=section, sent=generation;
  const values={...editorValues(),answer:$('answer').value};
  $('preview').textContent='표준 미리보기 계산 중…';
  try{
    const view=await api(`/api/jobs/${job.id}/items/${id}/preview`,{revision:item().revision,values});
    if(editing||currentId!==id||section!==selected||generation!==sent)return;
        renderMarkdown($('preview'),previewText({...item(),...values,standard_preview:view},true));
  }catch(e){if(!editing&&currentId===id)$('preview').textContent='미리보기 실패: '+e.message;}
}
window.addEventListener('resize',resizeEditors);
for(const name of ['body','solution']){
  $(name+'-editor').addEventListener('focus',()=>{editorFocus=name;syncUI();});
  $(name+'-editor').addEventListener('input',resizeEditors);
}
document.querySelectorAll('[data-editor-jump]').forEach(button=>button.onclick=()=>{
  const target=$(button.dataset.editorJump+'-editor');target.focus({preventScroll:true});
  const panel=$('combined-editor');panel.scrollTop+=target.getBoundingClientRect().top-panel.getBoundingClientRect().top-85;
});
async function runAI(operation){await save();const extract=operation==='extract';if(!await examConfirm({title:extract?'AI 변환':'AI 원본 대조',message:extract?'현재 항목을 AI로 다시 변환합니다. 현재 내용은 이력에 보관되고 대조·승인은 해제됩니다. 원본 전송 및 호출 비용이 발생할 수 있습니다.':'현재 항목의 문제·지문과 해설을 AI로 대조합니다. 원본 전송 및 호출 비용이 발생할 수 있습니다.',ok:extract?'변환 실행':'대조 실행',dismissKey:extract?'ai-item-extract':'ai-item-audit'}))return;await api(`/api/jobs/${job.id}/items/${currentId}/ai`,{revision:item().revision,operation,section:activeSection()});job=await api(`/api/jobs/${job.id}`);render();if($('ai-log-dialog')&&!$('ai-log-dialog').open)$('ai-log-open')?.click();}
async function waiveAudit(){
  await save();
  if(!examDismissed('audit-waiver-intro')){
    if(!await examConfirm({title:'대조 생략',message:'AI 원본 대조를 건너뛰고 승인할 수 있습니다. 사유는 선택입니다.',ok:'계속',dismissKey:'audit-waiver-intro',dismissLabel:'이 안내 다시 보지 않기'}))return;
  }
  const entered=await examPrompt({title:'대조 생략',message:'승인 목록에 표시할 사유를 적을 수 있습니다. 비우면 기본 문구로 저장됩니다.',label:'사유 (선택)',placeholder:'예: 개념 블록 텍스트만 정리 · 원본과 육안 대조 완료',minLength:0,ok:'사유 생략',cancel:'취소',emptyDefault:'원본 대조 생략'});
  if(entered===null)return;
  const reason=(entered||'').trim()||'원본 대조 생략';
  job=await api(`/api/jobs/${job.id}/items/${currentId}/audit-waiver`,{revision:item().revision,reason});
  render();
  notice('원본 대조를 생략했습니다.');
}
async function review(action){
  await save();
  if(action==='approve'&&!canApprove(item())){
    focusAuditUI();
    const s=auditStatus(item());
    throw Error(approvalBlockers(item()).join(' / ')||'저장 후 승인 조건을 다시 확인하세요.');
  }
  const note=$('review-note').value;
  job=await api(`/api/jobs/${job.id}/items/${currentId}/review`,{revision:item().revision,action,note});
  if(action==='approve'){
    const vis=visibleItems();
    const nextVis=vis.find(i=>i.review==='pending'&&i.id!==currentId);
    const next=nextVis||job.items.find(i=>i.review==='pending'&&i.id!==currentId);
    if(next){
      currentId=next.id;
      zoom=1;
    }
  }
  render();
}
async function init(){
  connectionState('connecting','연결 확인 중');
  settings=await api('/api/config');
  $('path').value=settings.sample_pdf;
  Object.entries(settings.samples).forEach(([key,title])=>{
    const option=new Option(title,key);
    $('sample').add(option);
  });
  $('review-queue-bar')?.addEventListener('click',e=>{
    const chip=e.target.closest('.queue-chip');
    if(!chip)return;
    if(chip.id==='qc-reset'){
      $('filter').value='all';
      reviewTierFilter='all';
      renderList();
      return;
    }
    if(chip.dataset.tier){
      $('filter').value='pending';
      reviewTierFilter=chip.dataset.tier;
      renderList();
      return;
    }
    if(chip.dataset.workflow){
      $('filter').value=chip.dataset.workflow;
      reviewTierFilter='all';
      renderList();
      return;
    }
  });
  const jobs=await loadList();
  if(jobs.length){
    let prior=null;try{prior=JSON.parse(sessionStorage.getItem('exam-batch-return-v1')||'null');}catch{}
    const target=prior&&jobs.some(j=>j.id===prior.job_id)?prior:null;
    await loadJob(target?.job_id||jobs[0].id,target?.item_id?{itemId:target.item_id}:{});
  }
  updateSection();
  icons();
}
$('import').onclick=()=>openImport();$('cancel-import').onclick=()=>$('import-dialog').close();$('sample').onchange=()=>{$('file-fields').hidden=!!$('sample').value;$('path').required=!$('sample').value;$('source').required=!$('sample').value;updateImportFormat();};
$('input-format').onchange=updateImportFormat;
$('import-form').onsubmit=e=>{e.preventDefault();guard(async()=>{const b=$('submit-import');b.disabled=true;b.textContent='가져오는 중';try{await save();const math=$('input-format').value==='hanwangi_2026_probability',english=$('input-format').value==='kice_english';job=await api('/api/import',{sample:$('sample').value,path:$('path').value,source:$('source').value,track:$('track').value,pages:math?'':$('pages').value,solution:$('solution-path').value,format:$('input-format').value,structure:math?$('structure-path').value:'',layout:math?$('layout-path').value:'',script:english?$('script-path').value:'',answer:english?$('answer-path').value:''});currentId=job.items[0]?.id;section='all';setEditing(false);zoom=1;reviewTierFilter='all';$('filter').value='all';updateSection();await loadList();render();$('import-dialog').close();notice('');}finally{b.disabled=false;b.textContent='가져오기';}});};
$('jobs').onchange=()=>guard(()=>loadJob($('jobs').value));$('filter').onchange=()=>{if(job){reviewTierFilter='all';renderList();}};
$('regions').onchange=()=>{viewTarget=$('regions').value;renderOriginal();};
$('zoom-in').onclick=()=>{zoom=Math.min(3,zoom+.25);renderOriginal();};
$('zoom-out').onclick=()=>{zoom=Math.max(.5,zoom-.25);renderOriginal();};
$('open-crop').onclick=()=>openCropDialog();
$('open-pdf').onclick=()=>{
  const target=currentViewTarget();
  const viewAsset=target.startsWith('asset-view:')?(item().assets||[])[Number(target.slice(11))]:null;
  let r=viewAsset?regionForAsset(viewAsset):regionForViewTarget(target);
  if(!r&&isStackViewTarget(target))r=stackedViewRegions(target)[0]?.region;
  if(r)window.open(fileURL(job.documents[r.document])+'#page='+r.page,'_blank','noopener');
};
$('crop-target').onchange=()=>{cropDialogTarget=$('crop-target').value;renderCropDialog({resetBox:true});};
$('crop-zoom-in').onclick=()=>{cropZoom=Math.min(3,cropZoom+.25);renderCropDialog({resetBox:false});};
$('crop-zoom-out').onclick=()=>{cropZoom=Math.max(.5,cropZoom-.25);renderCropDialog({resetBox:false});};
$('cancel-crop').onclick=()=>$('crop-dialog').close();
$('crop-form').onsubmit=e=>{e.preventDefault();guard(saveCrop);};
$('crop-bounds-numeric').onclick=()=>{
  const target=currentCropTarget();
  if(!target||target.startsWith('asset-index:'))return;
  const r=regionForTarget(target);
  if(!r)return;
  ['x0','y0','x1','y1'].forEach((key,n)=>$('bounds-form').elements[key].value=r.bbox[n]);
  $('bounds-dialog').showModal();
};
document.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>guard(async()=>{await save();section=b.dataset.section;cropDialogTarget='';applyDefaultViewTarget();updateSection();render();}));$('preview-tab').onclick=()=>setEditing(false);$('edit-tab').onclick=()=>setEditing(true);$('save').onclick=()=>guard(save);['editor','body-editor','solution-editor','unit','q-type','points','correct-rate','answer','review-note'].forEach(id=>$(id).addEventListener('input',changed));$('convert').onclick=()=>guard(()=>runAI('extract'));$('audit-banner-run').onclick=()=>guard(()=>runAI('audit'));$('audit-banner-waive').onclick=()=>guard(waiveAudit);$('audit-banner-scroll').onclick=()=>focusAuditUI(true);$('approve').onclick=()=>guard(()=>review('approve'));$('hold').onclick=()=>guard(()=>review('hold'));
$('cancel-bounds').onclick=()=>$('bounds-dialog').close();
$('bounds-form').onsubmit=e=>{e.preventDefault();guard(async()=>{await save();const target=currentCropTarget();const key=target.startsWith('solution-region:')?'solution_regions':'regions';const index=Number(target.split(':').pop());const rs=structuredClone(item()[key]);rs[index].bbox=['x0','y0','x1','y1'].map(k=>Number($('bounds-form').elements[k].value));job=await api(`/api/jobs/${job.id}/items/${currentId}`,{revision:item().revision,values:{[key]:rs}});$('bounds-dialog').close();cropDialogTarget=target;renderCropDialog();render();});};
setInterval(()=>{if(!job||polling||dirty||saving||actionPending||job.task?.status!=='running')return;polling=true;const requestedJob=job.id;api(`/api/jobs/${requestedJob}`).then(fresh=>{if(job.id===requestedJob&&!dirty&&!saving&&!actionPending){job=fresh;render();syncUI();}}).catch(e=>notice(e.message,true)).finally(()=>polling=false);},1500);
setInterval(async()=>{
  if(!connectionOffline)return;
  const restored=await refreshConnection();
  if(!restored)return;
  if(job&&!dirty&&!saving&&!actionPending){
    try{job=await api(`/api/jobs/${job.id}`);render();notice('서버 연결이 복구되어 최신 작업을 다시 불러왔습니다.');}
    catch(e){notice(e.message,true);}
  }else if(dirty){
    notice('서버 연결이 복구되었습니다. 작성 중인 내용을 확인한 뒤 저장하세요.');
  }
},2000);
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
function updateMainTop(){
  const main=document.querySelector('main');
  if(main)document.documentElement.style.setProperty('--exam-main-top',`${Math.max(0,main.getBoundingClientRect().top)}px`);
}
window.addEventListener('resize',updateMainTop);
window.addEventListener('scroll',updateMainTop,{passive:true});
if(window.ResizeObserver){
  const sidebarObserver=new ResizeObserver(()=>requestAnimationFrame(updateMainTop));
  ['header','#notice','.batch-tools','#review-alert','#ai-activity-panel','#batch-details','#review-queue-bar'].forEach(selector=>{
    const element=document.querySelector(selector);
    if(element)sidebarObserver.observe(element);
  });
}
requestAnimationFrame(updateMainTop);
if(location.protocol==='file:'){
  document.body.innerHTML=`<main style="max-width:620px;margin:12vh auto;padding:28px;font-family:-apple-system,BlinkMacSystemFont,sans-serif"><h1>Exam Processor 접속 주소가 아닙니다</h1><p>이 파일을 직접 열면 서버 API에 연결할 수 없습니다.</p><p><a href="${window.EXAM_PROCESSOR_URL||'http://127.0.0.1:7893/'}" style="display:inline-block;padding:10px 14px;border-radius:8px;background:#176c53;color:white;text-decoration:none">검수 화면 열기</a></p><p>서버가 실행 중이 아니면 Study Tools Launcher에서 Exam Processor를 먼저 실행하세요.</p></main>`;
}else{
  guard(init);
}
