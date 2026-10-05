/* R3 uses server-derived cards and existing standard preview/approval contracts. */
let focused=null, focusedIndex=0, focusedItemId=null, focusedJobId=null, focusedBusy=false;
function focusedCard(){return focused?.cards[focusedIndex];}
function focusedPath(){return `/api/jobs/${focusedJobId}/items/${focusedItemId}/focused-review`;}
function focusedUnsaved(){return !$('focused-edit-panel').hidden||!!$('focused-note').value.trim();}
async function focusedDiscard(){
  return !focusedUnsaved()||await examConfirm({title:'카드 이동',message:'저장하지 않은 카드 수정 또는 스킵 사유를 버리고 이동할까요?',ok:'이동'});
}
async function focusedRun(fn){
  if(focusedBusy)return;
  focusedBusy=true;
  focusedButtons();
  try{await fn();}catch(e){$('focused-feedback').textContent=e.message+' 입력 내용은 유지됩니다.';}
  finally{focusedBusy=false;focusedButtons();}
}
function focusedButtons(){
  const c=focusedCard()||{}, editingCard=!$('focused-edit-panel').hidden;
  $('focused-dialog').querySelectorAll('button').forEach(b=>b.disabled=focusedBusy);
  $('focused-apply').disabled=focusedBusy||editingCard||!c.can_apply;
  $('focused-edit').disabled=focusedBusy||editingCard||!c.can_edit;
  $('focused-skip').disabled=focusedBusy||editingCard||!c.can_skip;
  $('focused-note').disabled=focusedBusy||editingCard||!c.can_skip;
  $('focused-editor').disabled=focusedBusy;
  $('focused-prev').disabled=focusedBusy||focusedIndex===0;
  $('focused-save').disabled=focusedBusy||!c.can_edit;
}
function renderFocused(){
  const c=focusedCard(),rec=job.items.find(i=>i.id===focusedItemId);
  $('focused-title').textContent=`문항 ${rec?.number||''} · 지적 카드`;
  $('focused-progress').textContent=`${focusedIndex+1} / ${focused.cards.length} · 현재 분류 ${focused.tier} · ${c.status==='skipped'?'사유 기록됨':c.status==='applied'?'적용됨 · 재대조 필요':c.kind==='issue'?'미처리 지적':'주의 사유'}`;
  $('focused-edit-panel').hidden=true;$('focused-note').value='';
  const src=c.region?`<img id="focused-source" alt="${esc(c.region.page)}쪽 원본 영역" src="${fileURL('regions/'+c.region.image)}">`:'<p>이 카드에 연결된 원본 영역을 유일하게 확인할 수 없습니다. 상세 검수에서 전체 원본을 확인하세요.</p>';
  $('focused-content').innerHTML=`<h3>${esc(c.message)}</h3><p>${esc(c.location||'')}</p>${(c.blocked||[]).map(v=>`<p class="focused-warning">${esc(v)}</p>`).join('')}${c.skip_note?`<p>보존된 스킵 사유: ${esc(c.skip_note)}</p>`:''}<div class="focused-compare"><section><h3>해당 원본 영역</h3>${src}</section><section><h3>현재 표준 본문</h3><div id="focused-standard" class="prose"></div></section></div>${c.kind==='issue'?`<div class="focused-evidence"><p>원문 지적: ${esc(c.original)}</p><p>현재 추출: <code>${esc(c.extracted)}</code></p><p>변경 제안: <code>${esc(c.suggestion)||'제안 없음'}</code></p></div>`:''}`;
  const preview=focused.preview;
  renderMarkdown($('focused-standard'),preview.status==='unavailable'?(preview.warnings||[]).join('\n'):
    c.section?preview[c.section]||'표준 본문 없음':[preview.body,preview.solution].filter(Boolean).join('\n\n---\n\n'));
  const blockers=focused.approval_gate.blockers||[];
  $('focused-approval').textContent='승인 조건: '+(blockers.length?blockers.join(' / '):'기존 서버 검사 통과 · 별도 상세 검수에서 승인 판단')+' · 카드 처리로 승인되지 않습니다.';
  if($('focused-source'))$('focused-source').onerror=()=>{
    c.can_apply=false;c.can_edit=false;focusedButtons();
    $('focused-feedback').textContent='원본 이미지를 표시할 수 없습니다. 상세 검수에서 확인하세요.';
  };
  focusedButtons();
}
async function openFocused(id=currentId){
  await save();
  focusedJobId=job.id;focusedItemId=id;
  const view=await api(focusedPath());
  currentId=id;section='all';setEditing(false);applyDefaultViewTarget();updateSection();render();
  focused=view;focusedIndex=0;$('focused-feedback').textContent='';renderFocused();
  if(!$('focused-dialog').open)$('focused-dialog').showModal();
}
async function refreshFocused(){
  job=await api(`/api/jobs/${focusedJobId}`);
  focused=await api(focusedPath());
  focusedIndex=Math.min(focusedIndex,focused.cards.length-1);
  render();renderFocused();
}
async function focusedAction(action){
  const c=focusedCard();
  const payload={revision:focused.revision,review_revision:focused.review_revision,token:c.token,action};
  if(action==='skip'){
    payload.note=$('focused-note').value.trim();
    if(!payload.note)throw Error('지적 스킵 사유를 남겨주세요.');
  }
  if(action==='edit')payload.text=$('focused-editor').value;
  job=await api(focusedPath(),payload);
  render();
  // The write response is authoritative even if the subsequent read fails.
  $('focused-edit-panel').hidden=true;$('focused-note').value='';
  focused.cards.forEach(card=>{card.can_apply=false;card.can_edit=false;card.can_skip=false;});
  $('focused-feedback').textContent=action==='skip'?'스킵 사유 저장됨 · 문항 미승인':'수정 저장됨 · 새 버전의 원본 재대조 필요 · 문항 미승인';
  try{focused=await api(focusedPath());renderFocused();}
  catch(e){$('focused-feedback').textContent='저장은 완료되었지만 카드 조회에 실패했습니다. 현재 상태를 다시 불러오세요. '+e.message;}
}
$('focused-open').onclick=()=>guard(()=>openFocused());
$('focused-queue').onclick=()=>guard(async()=>{
  const first=job?.items.find(i=>i.review==='pending'&&i.kind==='question'&&i.review_decision?.tier==='YELLOW');
  if(!first){notice('검수 대기 YELLOW 문항 0건입니다.');return;}
  await save();$('filter').value='pending';reviewTierFilter='YELLOW';await openFocused(first.id);
});
$('focused-apply').onclick=()=>focusedRun(()=>focusedAction('apply'));
$('focused-skip').onclick=()=>focusedRun(()=>focusedAction('skip'));
$('focused-save').onclick=()=>focusedRun(()=>focusedAction('edit'));
$('focused-edit').onclick=()=>{
  $('focused-editor').value=job.items.find(i=>i.id===focusedItemId)[focusedCard().section];
  $('focused-draft-preview').innerHTML='';$('focused-edit-panel').hidden=false;focusedButtons();$('focused-editor').focus();
};
$('focused-edit-cancel').onclick=()=>focusedRun(async()=>{if(await focusedDiscard())renderFocused();});
$('focused-preview').onclick=()=>focusedRun(async()=>{
  const section=focusedCard().section;
  const result=await api(`/api/jobs/${focusedJobId}/items/${focusedItemId}/preview`,{revision:focused.revision,values:{[section]:$('focused-editor').value}});
  renderMarkdown($('focused-draft-preview'),result.status==='unavailable'?(result.warnings||[]).join('\n'):result[section]);
});
$('focused-reload').onclick=()=>focusedRun(async()=>{if(await focusedDiscard()){await refreshFocused();$('focused-feedback').textContent='현재 상태를 다시 불러왔습니다.';}});
$('focused-prev').onclick=()=>focusedRun(async()=>{if(await focusedDiscard()){focusedIndex--;renderFocused();$('focused-feedback').textContent='카드 이동 · 기록 변경 없음';}});
$('focused-next').onclick=()=>focusedRun(async()=>{
  if(!await focusedDiscard())return;
  if(focusedIndex+1<focused.cards.length){focusedIndex++;renderFocused();$('focused-feedback').textContent='카드 이동 · 기록 변경 없음';return;}
  job=await api(`/api/jobs/${focusedJobId}`);render();
  const candidates=job.items.filter(i=>i.id!==focusedItemId&&i.review==='pending'&&i.kind==='question'&&i.review_decision?.tier==='YELLOW');
  const after=candidates.find(i=>job.items.indexOf(i)>job.items.findIndex(i=>i.id===focusedItemId))||candidates[0];
  if(after){await openFocused(after.id);$('focused-feedback').textContent='다음 YELLOW 문항 · 기록 변경 없음';}
  else $('focused-feedback').textContent='다음 YELLOW 문항이 없습니다. 현재 카드 확인은 문항 승인이 아닙니다.';
});
async function closeFocused(detail=false){
  if(!await focusedDiscard())return;
  const c=focusedCard();$('focused-dialog').close();
  currentId=focusedItemId;
  if(detail&&c.section){section=c.section;updateSection();}
  render();
  if(detail&&c.region){
    const regions=item()[c.section==='body'?'regions':'solution_regions']||[];
    const index=regions.findIndex(r=>r.id===c.region.id);
    if(index>=0){viewTarget=`${c.section}-region:${index}`;renderOriginal();}
  }
  if(detail)document.querySelector('.compare').scrollIntoView({block:'start'});
}
$('focused-close').onclick=()=>focusedRun(()=>closeFocused());
$('focused-detail').onclick=()=>focusedRun(()=>closeFocused(true));
$('focused-dialog').addEventListener('cancel',e=>{e.preventDefault();focusedRun(()=>closeFocused());});
window.addEventListener('beforeunload',e=>{if($('focused-dialog').open&&focusedUnsaved()){e.preventDefault();e.returnValue='';}});
