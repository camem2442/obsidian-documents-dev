/* R4 reads canonical previews; all judgments belong exclusively to runtime sessions. */
let sampleView=null,sampleSessionId=null,sampleIndex=0,sampleJobId=null,sampleBusy=false,sampleImageError=false,sampleImagesPending=0;
const sampleLabels={unreviewed:'미확인',pass:'통과',problem:'문제 발견',passed:'모든 표본 통과',incomplete:'검수 중 · 미확인 표본 있음',paused:'중단됨',stale:'무효 · 현재 근거로 사용할 수 없음'};
function sampleSession(){return sampleView?.sessions.find(s=>s.id===sampleSessionId);}
function sampleId(){return sampleSession()?.selected[sampleIndex];}
function samplePath(){return `/api/jobs/${sampleJobId}/sample-review`;}
async function sampleDiscard(){return !$('sample-note').value.trim()||await examConfirm({title:'표본 검수 이동',message:'저장하지 않은 판단 메모를 버리고 이동할까요?',ok:'이동'});}
async function sampleRun(fn){
  if(sampleBusy)return;sampleBusy=true;sampleButtons();
  try{await fn();}catch(e){$('sample-feedback').textContent=e.message+' 저장 완료로 처리하지 않았습니다. 입력은 유지됩니다.';}
  finally{sampleBusy=false;sampleButtons();}
}
function sampleButtons(){
  const s=sampleSession(),id=sampleId(),active=s?.id===sampleView?.active_session_id;
  const writable=active&&s?.valid&&!s.paused&&s.results[id]?.verdict==='unreviewed';
  $('sample-dialog').querySelectorAll('button,input,select,textarea').forEach(el=>el.disabled=sampleBusy);
  $('sample-start').disabled=sampleBusy||!sampleView?.available||!sampleView.candidate_count;
  $('sample-pause').disabled=sampleBusy||!active||!s?.valid||s.paused;
  $('sample-resume').disabled=sampleBusy||!active||!s?.valid||!s.paused;
  $('sample-pass').disabled=sampleBusy||!writable||sampleImageError||sampleImagesPending>0||!sampleView?.materials[id]?.can_pass;
  $('sample-problem').disabled=sampleBusy||!writable;
  $('sample-note').disabled=sampleBusy||!writable;
  $('sample-prev').disabled=sampleBusy||!s||sampleIndex===0;
  $('sample-next').disabled=sampleBusy||!s||sampleIndex+1>=s.selected.length;
  $('sample-detail').disabled=sampleBusy||!job?.items.some(i=>i.id===id);
}
function renderSample(){
  const s=sampleSession(),id=sampleId();sampleImageError=false;sampleImagesPending=0;
  $('sample-candidates').textContent=`현재 pending GREEN 문항 후보 ${sampleView.candidate_count}건 · 큐 GREEN 집계에는 문항 외 항목도 포함됩니다. ${sampleView.unavailable_reason}`;
  $('sample-count').max=sampleView.candidate_count;
  $('sample-sessions').innerHTML=sampleView.sessions.map(x=>`<option value="${esc(x.id)}">${esc(x.created_at)} · ${x.sample_count}건 · ${esc(sampleLabels[x.status])}${x.id===sampleView.active_session_id?' · 현재 세션':' · 보존 기록'}</option>`).reverse().join('')||'<option>저장된 세션 없음</option>';
  $('sample-sessions').value=sampleSessionId||'';
  $('sample-card').hidden=!s;
  $('sample-progress').textContent=s?`${sampleLabels[s.status]} · 통과 ${s.counts.pass} / 문제 발견 ${s.counts.problem} / 미확인 ${s.counts.unreviewed} · 총 ${s.sample_count}건${!s.valid?' · 과거 판정은 기록으로만 보존':''}`:'표본 수와 seed를 명시하여 시작하세요.';
  $('sample-basis').textContent=s?`${s.policy} · seed: ${s.seed} · 당시 후보 ${s.basis.candidates.length}건 · job 전체의 내용·검수·분류·의존성·설정·원본 영역을 검사합니다. ${s.invalidation_reasons.join(' / ')}`:'';
  $('sample-note').value='';
  if(s){
    const rec=job.items.find(i=>i.id===id),result=s.results[id];
    if(rec){currentId=id;render();}
    $('sample-item-title').textContent=`표본 ${sampleIndex+1} / ${s.selected.length} · 문항 ${rec?.number||id}`;
    $('sample-record').textContent=`보존된 판정: ${sampleLabels[result.verdict]}${result.note?' · '+result.note:''} · 판정 변경은 새 세션에서 수행합니다.`;
    const material=sampleView.materials[id];
    $('sample-content').innerHTML=(material?.parts||[]).map((part,n)=>`<h4>${esc(part.label)}${part.id===id?'':' · 연결 자료'}</h4><div class="focused-compare"><section><h4>원본</h4>${part.sections.map(sec=>`<h5>${esc(sec.label)}</h5>${sec.images.map(img=>img.available?`<img class="sample-source" alt="${esc(sec.label)} ${esc(img.page)}쪽 원본" src="${fileURL('regions/'+img.image)}">`:'<p>원본 영역 파일 없음 · 상세 검수 필요</p>').join('')}${sec.text?`<pre class="sample-reference">${esc(sec.text)}</pre>`:''}${!sec.available?'<p>원본을 확인할 수 없습니다. 상세 검수에서 확인하세요.</p>':''}`).join('')}</section><section><h4>현재 공통 표준 본문 · 해설</h4><div class="prose" id="sample-preview-${n}"></div></section></div>`).join('');
    (material?.parts||[]).forEach((part,n)=>renderMarkdown($(`sample-preview-${n}`),part.preview.status==='unavailable'?(part.preview.warnings||[]).join('\n'):[part.preview.body,part.preview.solution].filter(Boolean).join('\n\n---\n\n')));
    const images=[...$('sample-content').querySelectorAll('img.sample-source')];
    sampleImagesPending=images.length;
    images.forEach(img=>{let settled=false;const done=failed=>{
      if(settled||!img.isConnected)return;settled=true;sampleImagesPending--;
      if(failed){sampleImageError=true;$('sample-feedback').textContent='원본 이미지 표시 실패 · 통과를 기록할 수 없습니다. 상세 검수에서 확인하세요.';}
      sampleButtons();
    };img.onload=()=>done(false);img.onerror=()=>done(true);if(img.complete)done(!img.naturalWidth);});
    const blockers=rec?.approval_gate?.blockers||[];
    $('sample-approval').textContent=`문항 상태: ${rec?labels[rec.review]:'찾을 수 없음'} · 승인 조건: ${blockers.length?blockers.join(' / '):'기존 상세 검수에서 별도 판단'} · 표본 판정으로 승인되지 않습니다.`;
  }
  sampleButtons();
}
function acceptSample(view){
  sampleView=view;job=view.job;
  if(!sampleView.sessions.some(s=>s.id===sampleSessionId))sampleSessionId=sampleView.active_session_id;
  sampleIndex=Math.min(sampleIndex,Math.max(0,(sampleSession()?.selected.length||1)-1));
  render();renderSample();
}
async function openSample(){
  await save();const changed=sampleJobId!==job.id;sampleJobId=job.id;
  const view=await api(samplePath());
  if(changed){sampleSessionId=null;sampleIndex=0;$('sample-count').value='';$('sample-seed').value='';}
  section='all';setEditing(false);$('filter').value='pending';reviewTierFilter='GREEN';updateSection();
  acceptSample(view);$('sample-feedback').textContent='재조회·이동은 판정 기록을 바꾸지 않습니다.';
  if(!$('sample-dialog').open)$('sample-dialog').showModal();
}
async function sampleAction(action,verdict){
  const payload={action,revision:sampleView.revision,basis_token:sampleView.basis_token,session_id:sampleSessionId};
  if(action==='start'){
    if(!await sampleDiscard())return;
    payload.count=$('sample-count').value===''?null:Number($('sample-count').value);payload.seed=$('sample-seed').value;
  }
  if(action==='verdict'){payload.item_id=sampleId();payload.verdict=verdict;payload.note=$('sample-note').value;}
  const view=await api(samplePath(),payload);
  if(action==='start'){sampleSessionId=view.active_session_id;sampleIndex=0;}
  acceptSample(view);$('sample-feedback').textContent='세션 기록 저장됨 · 문항 승인 상태 변경 없음';
}
$('sample-queue').onclick=()=>guard(()=>openSample());
$('sample-start').onclick=()=>sampleRun(()=>sampleAction('start'));
$('sample-pass').onclick=()=>sampleRun(()=>sampleAction('verdict','pass'));
$('sample-problem').onclick=()=>sampleRun(()=>sampleAction('verdict','problem'));
for(const action of ['pause','resume'])$('sample-'+action).onclick=()=>sampleRun(async()=>{if(await sampleDiscard())await sampleAction(action);});
for(const [name,delta] of [['prev',-1],['next',1]])$('sample-'+name).onclick=()=>sampleRun(async()=>{
  if(await sampleDiscard()){sampleIndex+=delta;renderSample();$('sample-feedback').textContent='표본 이동 · 기록 변경 없음';}
});
$('sample-sessions').onchange=()=>sampleRun(async()=>{const id=$('sample-sessions').value;if(await sampleDiscard()){sampleSessionId=id;sampleIndex=0;renderSample();}else $('sample-sessions').value=sampleSessionId;});
$('sample-reload').onclick=()=>sampleRun(async()=>{if(await sampleDiscard()){acceptSample(await api(samplePath()));$('sample-feedback').textContent='현재 근거를 다시 확인했습니다.';}});
async function closeSample(detail=false){
  if(!await sampleDiscard())return;
  $('sample-note').value='';$('sample-dialog').close();
  if(detail&&sampleId()){currentId=sampleId();section='all';setEditing(false);applyDefaultViewTarget();updateSection();render();document.querySelector('.compare').scrollIntoView({block:'start'});}
}
$('sample-close').onclick=()=>sampleRun(()=>closeSample());
$('sample-detail').onclick=()=>sampleRun(()=>closeSample(true));
$('sample-dialog').addEventListener('cancel',e=>{e.preventDefault();sampleRun(()=>closeSample());});
window.addEventListener('beforeunload',e=>{if($('sample-dialog').open&&$('sample-note').value.trim()){e.preventDefault();e.returnValue='';}});
