/* EX-N01 explicit export confirmation; no automatic retry after lost replies. */
let exportView=null,exportPreview=null,exportBusy=false,exportSequence=0;
const exportLabels={planned:'계획 저장됨',running:'실행 중 또는 중단됨 · 기록 확인 필요',finished:'처리 종료',pending:'미실행',completed:'완료',recovered:'산출물 복구 확인',failed:'실패',uncertain:'결과 불명확'};
const exportIds=()=>[...$('exports-jobs').querySelectorAll('input:checked')].map(x=>x.value);
function exportButtons(){
 for(const el of $('exports-dialog').querySelectorAll('button,input,select'))el.disabled=exportBusy;
 $('exports-plan').disabled=exportBusy||!exportPreview?.plan.entries.length;
 $('exports-confirm').disabled=exportBusy||exportView?.status!=='planned';
 $('exports-execute').disabled=exportBusy||exportView?.status!=='planned'||!$('exports-confirm').checked;
 $('exports-recover').disabled=exportBusy||!exportView||exportView.status==='planned';
}
function exportDescribe(plan){
 return `목적지: ${plan.destination}\n출력 단위: 작업 전체 / 포함 ${plan.entries.length} / 제외 ${plan.excluded.length}\n`+
 plan.entries.map(e=>`${e.source} (${e.job_id})\n출력 코드: ${e.source_code}\n세트: ${e.sets.join(', ')}\n${e.items.map(i=>i.id+' @ '+i.revision).join('\n')}`).join('\n\n')+
 '\n제외 사유\n'+plan.excluded.map(e=>`${e.job_id}: ${e.reason}`).join('\n');
}
function exportRender(){
 $('exports-fixed').textContent=exportView?`기록 ${exportView.id} · ${exportLabels[exportView.status]}\n${exportDescribe(exportView.plan)}`:'';
 $('exports-results').replaceChildren();
 for(const e of exportView?.entries||[]){
  const article=document.createElement('article'),label=document.createElement('p'),review=document.createElement('button');
  label.textContent=`${e.source}: ${exportLabels[e.status]}${e.error?' · '+e.error:''}${e.output?'\n'+e.output:''}`;
  review.type='button';review.textContent='기존 검수 화면으로 이동';
  review.onclick=()=>exportRun(async()=>{await loadJob(e.job_id);$('exports-dialog').close();});
  article.append(label,review);$('exports-results').append(article);
 }
 exportButtons();
}
async function exportRun(fn){
 if(exportBusy)return;exportBusy=true;exportButtons();$('exports-feedback').textContent='처리 중…';
 try{await fn();}
 catch(e){$('exports-feedback').textContent='요청 실패: '+e.message+' · 새 실행 대신 기록을 조회하세요.';}
 finally{exportBusy=false;exportButtons();}
}
async function exportRuns(){
 const runs=await api('/api/export-plans');$('exports-runs').replaceChildren(new Option('기록 선택',''));
 for(const r of runs)$('exports-runs').add(new Option(`${r.id.slice(0,8)} · ${exportLabels[r.status]}`,r.id));
 if(exportView)$('exports-runs').value=exportView.id;
}
function exportInvalidate(){
 exportSequence++;exportPreview=null;exportView=null;$('exports-confirm').checked=false;
 $('exports-preview-result').textContent='선택이 바뀌었습니다. 다시 미리 확인하세요.';exportRender();
}
$('exports-open').onclick=()=>exportRun(async()=>{
 const jobs=await api('/api/export-jobs');$('exports-jobs').replaceChildren();
 for(const j of jobs){
  const row=document.createElement('div'),label=document.createElement('label'),box=document.createElement('input'),review=document.createElement('button');
  box.type='checkbox';box.value=j.id;label.append(box,document.createTextNode(`${j.source} · 입력 ${j.input_ready?"준비":"확인 필요"} · 전사 대기 ${j.transcription_pending} · 현재 대조 ${j.audit_current}/${j.count} · 검수 대기 ${j.review_pending} · 승인 ${j.approved_count}/${j.count} · 보류 ${j.held_count} · 범위 경고 ${j.warnings.length} · 기존 출력 ${j.last_export?"기록 있음":"없음"}`));
  review.type='button';review.textContent='검수·처리 상태 열기';review.onclick=()=>exportRun(async()=>{await loadJob(j.id);$('exports-dialog').close();});
  row.append(label,review);$('exports-jobs').append(row);
 }
 $('exports-confirm').checked=false;exportPreview=null;$('exports-preview-result').textContent='';
 await exportRuns();exportRender();$('exports-dialog').showModal();$('exports-feedback').textContent='현재 승인 상태를 확인하고 작업을 선택하세요.';
});
$('exports-jobs').onchange=exportInvalidate;$('exports-destination').oninput=exportInvalidate;
$('exports-preview').onclick=()=>exportRun(async()=>{
 const sequence=++exportSequence;
 const result=await api('/api/export-plans/preview',{job_ids:exportIds(),destination:$('exports-destination').value});
 if(sequence!==exportSequence||!$('exports-dialog').open)return;
 exportPreview=result;exportView=null;$('exports-confirm').checked=false;
 $('exports-preview-result').textContent=exportDescribe(result.plan);exportRender();$('exports-feedback').textContent='읽기 전용 미리보기 완료 · 아직 출력하지 않았습니다.';
});
$('exports-plan').onclick=()=>exportRun(async()=>{
 const request={action:'plan',job_ids:exportIds(),destination:$('exports-destination').value,preview_hash:exportPreview.plan_hash,request_id:crypto.randomUUID().replaceAll('-','')};
 // The stable ID lets a lost plan response be found without making another plan.
 sessionStorage.setItem('exam-export-pending',JSON.stringify(request));
 exportView=await api('/api/export-plans',request);sessionStorage.removeItem('exam-export-pending');
 exportPreview=null;$('exports-preview-result').textContent='';$('exports-confirm').checked=false;await exportRuns();exportRender();$('exports-feedback').textContent='계획 저장됨 · 대상과 제외 사유를 확인한 뒤 별도로 실행하세요.';
});
$('exports-confirm').onchange=exportButtons;
$('exports-execute').onclick=()=>exportRun(async()=>{
 if(!$('exports-confirm').checked||exportView?.status!=='planned')return;
 const request={action:'execute',plan_id:exportView.id,revision:exportView.revision,confirmed_plan_hash:exportView.plan_hash};
 sessionStorage.setItem('exam-export-pending',JSON.stringify(request));$('exports-confirm').checked=false;
 exportView=await api('/api/export-plans',request);sessionStorage.removeItem('exam-export-pending');
 await exportRuns();exportRender();$('exports-feedback').textContent='처리 기록을 확인하세요. 실패·결과 불명확은 자동 재출력하지 않습니다.';
});
$('exports-recover').onclick=()=>exportRun(async()=>{
 exportView=await api('/api/export-plans',{action:'recover',plan_id:exportView.id});
 sessionStorage.removeItem('exam-export-pending');$('exports-confirm').checked=false;await exportRuns();exportRender();$('exports-feedback').textContent='산출물 근거 확인 완료 · 재출력 없음';
});
$('exports-runs').onchange=()=>exportRun(async()=>{
 exportView=$('exports-runs').value?await api('/api/export-plans/'+$('exports-runs').value):null;
 $('exports-confirm').checked=false;exportRender();$('exports-feedback').textContent='저장된 기록을 읽었습니다.';
});
$('exports-reload').onclick=()=>exportRun(async()=>{
 let pending;try{pending=JSON.parse(sessionStorage.getItem('exam-export-pending')||'null');}catch{}
 const id=pending?.plan_id||pending?.request_id||exportView?.id;
 await exportRuns();if(id)exportView=await api('/api/export-plans/'+id);
 $('exports-confirm').checked=false;exportRender();$('exports-feedback').textContent='조회 완료 · 실행 중 기록은 완료 대기 후 복구 확인하세요.';
});
$('exports-close').onclick=()=>{if(!exportBusy)$('exports-dialog').close();};
$('exports-dialog').addEventListener('cancel',e=>{if(exportBusy)e.preventDefault();});
$('exports-dialog').addEventListener('close',()=>{exportSequence++;$('exports-confirm').checked=false;});
