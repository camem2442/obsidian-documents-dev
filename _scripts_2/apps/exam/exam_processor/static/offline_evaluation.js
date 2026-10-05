/* No provider invocation. The draft and exact request survive transport/write failure. */
let offlineJob=null,offlineView=null,offlineList=null,offlineDatasets=[],offlineBusy=false,offlinePending=null;
const offlineNames={actual_error:'실제 오류',false_positive:'오탐',abstain:'응답 유보',failed:'실패',invalid_response:'형식 오류',pending:'미응답',scored:'비교 완료'};
const offlineOrigin={synthetic_fixture:'합성 fixture',human_explicit:'사람의 명시 판정',unverified_import:'출처 미확인 가져오기'};
function offlinePath(){return `/api/jobs/${offlineJob}/offline-evaluation`;}
function offlineDraft(){return $('offline-responses').value.trim()||$('offline-retry-reason').value.trim();}
function offlineButtons(){
 $('offline-dialog').querySelectorAll('button,select').forEach(b=>b.disabled=offlineBusy);
 $('offline-dialog').querySelectorAll('textarea').forEach(b=>b.readOnly=offlineBusy);
 $('offline-create').disabled=offlineBusy||!offlineList?.available||!offlineDatasets.length;
 for(const k of ['import','template'])$('offline-'+k).disabled=offlineBusy||!offlineView||offlineView.paused||!offlineView.plan.cases.length;
 $('offline-pause').disabled=offlineBusy||!offlineView||offlineView.paused;
 $('offline-resume').disabled=offlineBusy||!offlineView?.paused;
}
async function offlineRun(fn){
 if(offlineBusy)return;offlineBusy=true;offlineButtons();
 try{await fn();}catch(e){$('offline-feedback').textContent=e.message+' 응답 입력은 유지됩니다.';}
 finally{offlineBusy=false;offlineButtons();}
}
function renderOffline(){
 $('offline-runs').innerHTML='<option value="">실행 선택</option>'+offlineList.runs.slice().reverse().map(r=>`<option value="${esc(r.id)}">${esc(r.created_at)} · ${esc(r.id.slice(0,8))} · ${r.report.counts.scored}/${r.report.eligible} 비교</option>`).join('');
 $('offline-runs').value=offlineView?.id||'';
 const v=offlineView;
 if(!v){$('offline-summary').textContent=offlineList.reason||'저장된 실행을 선택하거나 R6 데이터셋으로 새 실행을 만드세요. 레이블 없는 데이터셋은 비교 분모 0입니다.';for(const k of ['links','report','case-view','excluded','attempts'])$('offline-'+k).textContent='';$('offline-case').innerHTML='';offlineButtons();return;}
 const p=v.plan,r=v.report,c=r.counts;
 $('offline-summary').textContent=`${v.paused?'중단됨':'입력 가능'} · 판정 출처: ${offlineOrigin[p.label_origin]} · 응답 출처: ${offlineOrigin[v.response_source?.origin]||'미입력'} / ${v.response_source?.producer||'미확인'} · 선정 ${r.eligible} / 제외 ${r.excluded} · 비교 ${c.scored} / 미응답 ${c.pending} / 유보 ${c.abstain} / 실패 ${c.failed} / 형식 오류 ${c.invalid_response}`;
 $('offline-links').innerHTML=`<a href="${offlinePath()}/${v.id}/inputs" target="_blank" rel="noopener">정답을 제외한 입력 JSON</a> · <a href="${offlinePath()}/${v.id}" target="_blank" rel="noopener">정답·응답을 포함한 실행 기록 JSON</a><p>데이터셋 ${esc(p.dataset_id)} · 구성 해시 ${esc(p.dataset_hash)}<br>입력 묶음 해시 ${esc(p.input_manifest_hash)}</p>`;
 const m=r.matrix,b=r.baseline_matrix_same_scored_cases;
 $('offline-report').innerHTML=`<p>명시 판정과 응답 일치 ${r.agreement.numerator} / ${r.agreement.denominator}${r.agreement.value===null?' · 점수 미확인':''} · 응답 비교 범위 ${r.coverage.numerator} / ${r.coverage.denominator}</p><div class="offline-table"><table><caption>양성 = 실제 오류 · 비교 완료 사례만 동일 분모</caption><thead><tr><th>비교</th><th>TP</th><th>FP</th><th>TN</th><th>FN</th></tr></thead><tbody><tr><th>가져온 응답</th><td>${m.tp}</td><td>${m.fp}</td><td>${m.tn}</td><td>${m.fn}</td></tr><tr><th>당시 지적 제기</th><td>${b.tp}</td><td>${b.fp}</td><td>${b.tn}</td><td>${b.fn}</td></tr></tbody></table></div><p>TP: 오류를 오류로 / FP: 오탐을 오류로 / TN: 오탐을 오탐으로 / FN: 오류를 오탐으로. 당시 지적은 모두 오류 의심을 제기한 것이므로 양성 기준선입니다.</p><p>${esc(r.boundary)}</p>`;
 const selected=$('offline-case').value;
 $('offline-case').innerHTML=p.cases.map(case_=>{const row=r.rows.find(x=>x.case_id===case_.id);return `<option value="${case_.id}">${esc(row.item_id)} · ${esc(offlineNames[row.status])} · ${case_.id.slice(0,8)}</option>`;}).join('');
 if(p.cases.some(x=>x.id===selected))$('offline-case').value=selected;
 $('offline-excluded').textContent=JSON.stringify(p.excluded,null,2);
 $('offline-attempts').textContent=JSON.stringify(v.attempts,null,2);
 renderOfflineCase();offlineButtons();
}
function renderOfflineCase(){
 const c=offlineView?.plan.cases.find(x=>x.id===$('offline-case').value);
 if(!c){$('offline-case-view').textContent='비교 가능한 사례 없음';return;}
 const row=offlineView.report.rows.find(x=>x.case_id===c.id);
 const images=Object.entries(c.input.files).map(([name,f])=>{const mime=/\.png$/i.test(name)?'image/png':/\.jpe?g$/i.test(name)?'image/jpeg':null;return `<figure><figcaption>${esc(f.role)} · ${esc(name)}</figcaption>${mime?`<img alt="고정 데이터셋의 원본 근거" src="data:${mime};base64,${esc(f.base64)}">`:'<p>미리보기 미지원 · 입력 JSON에 bytes 보존</p>'}</figure>`;}).join('');
 $('offline-case-view').innerHTML=`<h3>당시 입력 · 현재 job 내용으로 대체하지 않음</h3><pre>${esc(JSON.stringify({issue:c.input.issue,content:c.input.content},null,2))}</pre>${images}<h3>분리 보관한 사람 판정과 응답</h3><p>${esc(offlineOrigin[c.ground_truth.origin])} · ${esc(offlineNames[c.ground_truth.verdict])} · 범위 ${esc(c.ground_truth.scope.join(', '))}</p><p>판단 근거: ${esc(c.ground_truth.note)}</p><p>응답: ${esc(offlineNames[row.outcome]||offlineNames[row.status])} · ${esc(row.reason||'미입력')}</p><p>당시 tier ${esc(c.baseline.tier)} · ${esc(c.baseline.meaning)}</p>`;
}
async function offlineRefresh(){
 offlineList=await api(offlinePath());
 if(offlineView)offlineView=await api(`${offlinePath()}/${offlineView.id}`);
 offlinePending=null;renderOffline();
}
async function openOffline(){
 if(offlineDraft()&&offlineJob!==job.id)throw Error('이전 작업의 응답 입력을 먼저 지우세요.');
 if(offlineJob!==job.id)offlineView=null;
 offlineJob=job.id;
 const datasets=await api(`/api/jobs/${offlineJob}/evaluation`);
 offlineDatasets=datasets.datasets;
 $('offline-dataset').innerHTML=offlineDatasets.map(d=>`<option value="${d.id}">${esc(d.created_at)} · ${esc(offlineOrigin[d.selection.origin])} · 포함 ${d.included.length} · ${d.id.slice(0,8)}</option>`).join('');
 await offlineRefresh();$('offline-feedback').textContent='조회만으로 실행·응답·판정은 생성되지 않습니다.';$('offline-dialog').showModal();
}
async function offlineSend(action,extra={}){
 const candidate={action,...(offlineView&&action!=='create'?{run_id:offlineView.id,revision:offlineView.revision}:{}),...extra};
 if(!offlinePending||JSON.stringify(offlinePending.candidate)!==JSON.stringify(candidate))offlinePending={candidate,payload:{...candidate,request_id:crypto.randomUUID()}};
 offlineView=await api(offlinePath(),offlinePending.payload);offlinePending=null;
 offlineList=await api(offlinePath());renderOffline();$('offline-feedback').textContent='오프라인 실행 기록 저장됨 · tier·승인·사람 판정 변경 없음';
}
$('offline-open').onclick=()=>guard(()=>offlineRun(openOffline));
$('offline-create').onclick=()=>offlineRun(async()=>{if(offlineDraft())throw Error('새 실행 전에 응답 입력을 저장하거나 지우세요.');const d=offlineDatasets.find(x=>x.id===$('offline-dataset').value);await offlineSend('create',{dataset_id:d.id,dataset_hash:d.composition_hash});});
$('offline-runs').onchange=()=>offlineRun(async()=>{const id=$('offline-runs').value;if(offlineDraft()){$('offline-runs').value=offlineView?.id||'';throw Error('실행 전환 전에 응답 입력을 저장하거나 지우세요.');}offlineView=id?await api(`${offlinePath()}/${id}`):null;offlinePending=null;renderOffline();});
$('offline-reload').onclick=()=>offlineRun(async()=>{await offlineRefresh();$('offline-feedback').textContent='최신 실행 재조회 · 응답 입력 유지. 변경된 기록을 확인하세요.';});
$('offline-case').onchange=renderOfflineCase;
for(const a of ['pause','resume'])$('offline-'+a).onclick=()=>offlineRun(()=>offlineSend(a));
$('offline-import').onclick=()=>offlineRun(async()=>{const bundle=JSON.parse($('offline-responses').value);await offlineSend('import',{bundle,retry_reason:$('offline-retry-reason').value});$('offline-responses').value='';$('offline-retry-reason').value='';});
$('offline-template').onclick=()=>offlineRun(async()=>{
 if(offlineDraft())throw Error('기존 응답 입력을 먼저 지우세요.');
 const v=offlineView;
 $('offline-responses').value=JSON.stringify({schema:'offline-responses/1',input_manifest_hash:v.plan.input_manifest_hash,response_origin:v.response_source?.origin||'unverified_import',producer:v.response_source?.producer||'',responses:v.plan.cases.filter(c=>!['scored','abstain'].includes(v.report.rows.find(x=>x.case_id===c.id).status)).map(c=>({case_id:c.id,input_hash:c.input_hash,outcome:'',reason:''}))},null,2);
 $('offline-feedback').textContent='빈 양식입니다. 출처와 outcome(actual_error / false_positive / abstain / failed), 사유를 명시하세요. 정답은 채우지 않습니다.';
});
$('offline-clear').onclick=()=>offlineRun(async()=>{if(offlineDraft()&&!await examConfirm({title:'응답 입력 지우기',message:'저장하지 않은 응답 입력을 지울까요?',ok:'입력 지우기'}))return;$('offline-responses').value='';$('offline-retry-reason').value='';offlinePending=null;});
function closeOffline(){$('offline-dialog').close();} // same-page draft retained, never silently submitted
$('offline-close').onclick=()=>offlineRun(async()=>closeOffline());
$('offline-dialog').addEventListener('cancel',e=>{e.preventDefault();if(!offlineBusy)closeOffline();});
window.addEventListener('beforeunload',e=>{if(offlineDraft()){e.preventDefault();e.returnValue='';}});
