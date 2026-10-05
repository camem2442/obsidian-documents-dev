// Loaded after deferred app.js so these handlers share its save/revision guards.
window.addEventListener('DOMContentLoaded', () => {
  window.addEventListener('exam:state', renderWorkflow);
  $('batch-operation').onchange=renderWorkflow;

  function isAiBusy() {
    return job?.task?.status === 'running' || job?.queue?.status === 'running';
  }
  function formatAiTime(ts) {
    if (!ts) return '--:--:--';
    return new Date(ts * 1000).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  function aiProgressSummary() {
    const prog = job?.ai_progress || {};
    const q = job?.queue;
    const task = job?.task;
    if (prog.message) return prog.message;
    if (q?.status === 'running') {
      const done = q.entries.filter(e => e.status === 'completed').length;
      const running = q.entries.find(e => e.status === 'running');
      const op = q.operation === 'extract' ? 'AI 변환' : '원본 대조';
      return `${op} · ${done}/${q.entries.length} 완료${running ? ' · 실행 중' : ''}`;
    }
    if (task?.status === 'running') {
      const op = task.operation === 'extract' ? 'AI 변환' : '원본 대조';
      const sec = task.section === 'solution' ? '해설' : '본문';
      return `${op} · ${sec} · API 처리 중…`;
    }
    return '';
  }
  function renderAiLogLines(target, tail) {
    const lines = (job?.ai_log || []).slice(tail ? -tail : undefined);
    const atBottom=target.scrollTop+target.clientHeight>=target.scrollHeight-2,previousScroll=target.scrollTop;
    const html = lines.map(entry => {
      const level = entry.level === 'error' ? 'ai-log-line-error' : entry.level === 'success' ? 'ai-log-line-success' : '';
      return `<div class="${level}">[${formatAiTime(entry.t)}] ${esc(entry.message)}</div>`;
    }).join('') || '<div>아직 로그가 없습니다.</div>';
    if(target.innerHTML!==html){target.innerHTML=html;target.scrollTop=atBottom?target.scrollHeight:previousScroll;}
  }
  function renderAiActivity() {
    const panel = $('ai-activity-panel');
    const openBtn = $('ai-log-open');
    if (!panel) return;
    const busy = isAiBusy();
    const logs = job?.ai_log || [];
    // Keep completed activity compact while preserving access to logs and failures.
    panel.hidden = !job || (!busy && !logs.length);
    if (openBtn) openBtn.disabled = !job;
    const status = aiProgressSummary();
    const details=$('ai-activity-details');
    const failure=job?.task?.status==='failed'||job?.queue?.entries?.some(e=>['failed','uncertain'].includes(e.status));
    const key=job?.id+':'+String(busy)+':'+String(!!failure);
    if(details&&details.dataset.state!==key){details.open=!!failure;details.dataset.state=key;}
    flowStatus('ai-activity-status',status);
    flowStatus('ai-log-dialog-status',status);
    const prog = job?.ai_progress || {};
    const barWrap = $('ai-activity-progress');
    const bar = $('ai-activity-bar');
    const total = prog.queue_total || job?.queue?.entries?.length;
    const done = prog.queue_done ?? (job?.queue ? job.queue.entries.filter(e => e.status === 'completed').length : null);
    if (barWrap && bar && total && busy && job?.queue?.status === 'running') {
      barWrap.hidden = false;
      bar.style.width = `${Math.min(100, Math.round(((done || 0) / total) * 100))}%`;
    } else if (barWrap) {
      barWrap.hidden = true;
    }
    renderAiLogLines($('ai-activity-preview'), 8);
    if ($('ai-log-dialog')?.open) renderAiLogLines($('ai-log-body'), 0);
  }
  window.renderAiActivity = renderAiActivity;

  function renderWorkflow() {
    const q = job?.queue;
    const approved = job?.items.filter(i=>i.review==='approved').length || 0;
    const total = job?.items.length || 0;
    $('export').disabled = !job || actionPending || dirty || !approved || job.task?.status==='running' || q?.status==='running';
    $('export').title = approved ? '승인된 세트를 KS에 Markdown 폴더로 저장' : '지문과 소속 문제를 먼저 승인하세요';
    $('export-label').textContent = job ? `Markdown 폴더 저장 · 승인 ${approved}/${total}` : 'Markdown 폴더 저장';
    $('jobs').disabled = !$('jobs').value;
    $('job-summary').textContent = job ? `${job.workbook?'개념 '+job.items.filter(i=>i.kind==='concept').length:'지문 '+job.items.filter(i=>i.kind==='passage').length}개 · 문제 ${job.items.filter(i=>i.kind==='question').length}개 · 승인 ${approved}/${total} · 대조 ${job.items.filter(i=>{const w=i.audit_waiver;if(w&&w.status==='waived'&&w.revision===i.revision)return true;const a=i.audit;return a&&a.status==='completed'&&a.revision===i.revision;}).length}/${total}` : '';
    const running = q?.status === 'running';
    const busy=running || job?.task?.status==='running';
    const remaining=q?.entries.some(e=>['pending','running'].includes(e.status));
    const retryQueue=q?.operation===$('batch-operation').value ? q : [...(job?.queue_history||[])].reverse().find(x=>x.operation===$('batch-operation').value);
    $('batch-pause').disabled = !running || actionPending || q?.stop_requested;
    $('batch-start').disabled = !job || busy || actionPending || remaining;
    $('batch-resume').disabled = !q || busy || actionPending || !remaining;
    $('batch-resume').title = q ? `${q.operation==='extract'?'AI 변환':'원본 대조'} 남은 항목 이어서 실행` : '중단된 작업 없음';
    $('batch-retry').disabled = busy || actionPending || !retryQueue?.entries.some(e=>e.status==='failed');
    $('batch-operation').disabled=!job||actionPending||busy;
    for(const id of ['vault-files','import','open-jobs-dialog'])$(id).disabled=!settings||actionPending;
    document.querySelectorAll('#items button, .workspace button, .workspace input, .workspace textarea, .workspace select').forEach(el=>{
      const editingField=el.matches('input,textarea');
      const auditAction=el.id==='audit-banner-run'||el.id==='audit-banner-scroll'||el.id==='audit-banner-waive';
      el.disabled=!item()||busy||(actionPending&&!(editingField&&allowEdits)&&!auditAction);
    });
    if(item()&&!busy&&!actionPending)updateEditorTools();
    // Source-specific and stale-result guards refine the common interaction state.
    if(item()&&!actionPending&&!busy){
      const hasOriginal=viewTargets().length>0;
      const canCrop=cropTargets().length>0;
      for(const id of ['open-crop','open-pdf','zoom-in','zoom-out','regions'])$(id).disabled=!hasOriginal;
      $('open-crop').disabled=!canCrop;
      $('open-pdf').disabled=!regionForViewTarget(currentViewTarget())&&!(isStackViewTarget(currentViewTarget())&&stackedViewRegions(currentViewTarget()).length);
      $('convert').disabled=!sourceRegions().length;
      $('audit-banner-run').disabled=!!item().transcription_pending?.length||busy;
      const approveOk=typeof canApprove==='function'&&canApprove(item());
      $('approve').disabled=!approveOk;
      $('approve').title=$('approval-status')?.textContent||'승인 조건을 확인하세요.';
      document.querySelectorAll('[data-issue]').forEach(el=>el.disabled=el.dataset.blocked==='true');
    }
    $('filter').disabled=!job||actionPending;
    $('import-dialog').querySelectorAll('input,select,button').forEach(el=>el.disabled=actionPending);
    $('batch-status').textContent = q ? `${q.operation==='extract'?'1단계 AI 변환':'2단계 원본 대조'} · ${q.entries.filter(e=>e.status==='completed').length}/${q.entries.length} 완료 · ${q.entries.filter(e=>e.status==='failed').length} 실패 · ${q.status==='paused'?'일시정지':running?(q.stop_requested?'중지 대기':'처리 중'):'종료'}` : '';
    $('batch-log').innerHTML = (q?.entries||[]).map(e=>`<p>${esc(job.items.find(i=>i.id===e.item)?.number)}${e.section?' · '+(e.section==='body'?'본문':'해설'):''} · ${esc({pending:'대기',running:'실행 중',completed:'완료',failed:'실패',skipped:'수정본 보호'}[e.status])} ${esc(e.message)}</p>`).join('');
    renderAiActivity();
  }
  async function start(mode) {
    if (!job) throw Error('작업을 선택하세요.');
    await save();
    const operation = mode==='resume' ? job.queue.operation : $('batch-operation').value;
    const extract = operation === 'extract';
    const count=job.workbook&&operation==='extract'?job.items.reduce((n,i)=>n+(i.transcription_pending||[]).filter(s=>i[s==='solution'?'solution_regions':'regions']?.length).length,0):job.items.length;
    if (!await examConfirm({
      title: extract ? '일괄 AI 변환' : '일괄 원본 대조',
      message: `최대 ${count}회 순차 ${extract?'전사':'대조'}합니다. 원본이 AI 제공자로 전송되고 호출 비용이 발생할 수 있습니다.`,
      ok: '실행',
      dismissKey: extract ? 'batch-extract' : 'batch-audit',
    })) return;
    job = await api(`/api/jobs/${job.id}/queue`,{operation,mode});
    render();
    $('ai-log-open')?.click();
  }
  $('batch-start').onclick = ()=>guard(()=>start('new'));
  $('batch-resume').onclick = ()=>guard(()=>start('resume'));
  $('batch-retry').onclick = ()=>guard(()=>start('retry'));
  $('batch-pause').onclick = ()=>guard(async()=>{await api(`/api/jobs/${job.id}/queue/pause`,{});job.queue.stop_requested=true;notice('현재 호출이 끝나면 멈춥니다.');});
  let fetching = false;
  setInterval(()=>{
    if (!job || job.queue?.status!=='running' || dirty || saving || fetching || actionPending) return;
    fetching = true;
    const id=job.id;
    api(`/api/jobs/${id}`).then(fresh=>{if(job.id===id&&!dirty&&!saving&&!actionPending){job=fresh;render();syncUI();}}).catch(e=>notice(e.message,true)).finally(()=>fetching=false);
  },1500);
  $('ai-log-open').onclick = () => {
    if (!job) return;
    renderAiActivity();
    $('ai-log-dialog').showModal();
    icons();
  };
  $('ai-log-close').onclick = () => $('ai-log-dialog').close();
  let scanning=false;
  async function scanFiles(){
    if(scanning)return;
    scanning=true;$('vault-scan').disabled=true;$('vault-select').disabled=true;
    $('vault-list').replaceChildren();$('vault-status').textContent='조회 중';
    try{
      const result=await api('/api/vault/files?folder='+encodeURIComponent($('vault-folder').value));
      $('vault-list').replaceChildren(...result.files.map(f=>new Option(`${f.name}${f.jobs.length?' · 작업 기록 있음':''}`,f.path)));
      $('vault-status').textContent=result.files.length?`${result.files.length}개 PDF${result.limited?' · 최대 1,000개 표시':''}`:'PDF 없음';
      $('vault-select').disabled=!result.files.length;
    }catch(e){$('vault-status').textContent=e.message;}
    finally{scanning=false;$('vault-scan').disabled=false;}
  }
  $('vault-files').onclick=()=>{ $('vault-folder').value ||= settings.ks_root; notice('');$('vault-dialog').showModal();scanFiles(); };
  $('vault-close').onclick=()=>$('vault-dialog').close();
  $('vault-form').onsubmit=e=>{e.preventDefault();scanFiles();};
  $('vault-select').onclick=()=>{if(!$('vault-list').value)return;const path=$('vault-list').value;$('vault-dialog').close();openImport(path);};
  let exportJobId=null;
  $('export').onclick=()=>guard(async()=>{
    if(!job)throw Error('작업을 선택하세요.');
    await save();
    $('export-destination').value ||= settings.ks_root+'/00 미분류/Exam Processor';
    if(exportJobId!==job.id){$('export-code').value='';exportJobId=job.id;}
    notice('');
    $('export-dialog').showModal();
  });
  $('export-close').onclick=()=>$('export-dialog').close();
  $('export-form').onsubmit=e=>{e.preventDefault();guard(async()=>{
    $('export-submit').disabled=true;
    try { await save(); job=await api(`/api/jobs/${job.id}/export`,{destination:$('export-destination').value,source_code:$('export-code').value});$('export-dialog').close();render();notice('저장 위치: '+job.last_export); }
    finally { $('export-submit').disabled=false; }
  });};

  // Jobs Dialog Management
  let currentJobsFilter = 'all';
  function renderJobsDialog() {
    const list = $('jobs-list');
    if (!list) return;
    const query = ($('jobs-search').value || '').trim().toLowerCase();
    const filtered = allJobs.filter(j => {
      const matchText = !query || j.source.toLowerCase().includes(query) || j.track.toLowerCase().includes(query);
      if (!matchText) return false;
      const isCompleted = j.count > 0 && j.approved_count === j.count;
      if (currentJobsFilter === 'completed') return isCompleted;
      if (currentJobsFilter === 'active') return !isCompleted;
      return true;
    });

    if (!filtered.length) {
      list.innerHTML = '<div class="empty">일치하는 작업이 없습니다.</div>';
      return;
    }

    list.innerHTML = filtered.map(j => {
      const isCurrent = job && job.id === j.id;
      const pct = j.count ? Math.round((j.approved_count / j.count) * 100) : 0;
      const dateStr = j.updated_at ? new Date(j.updated_at * 1000).toLocaleDateString('ko-KR', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
      return `
        <div class="job-card ${isCurrent ? 'current' : ''}" data-job-id="${j.id}" role="listitem">
          <div class="job-card-header">
            <span class="job-card-title">${esc(j.source)} · ${esc(j.track)}</span>
            <span class="job-card-badge">${isCurrent ? '현재 작업' : pct === 100 ? '승인 완료' : '검수 중'}</span>
          </div>
          <div class="job-card-meta">
            <span>총 ${j.count}항목</span>
            <span>승인 ${j.approved_count}/${j.count} (${pct}%)</span>
            ${j.issue_count ? `<span style="color:#b35900">차이 의심 ${j.issue_count}항목</span>` : ''}
            <span style="margin-left:auto">${dateStr}</span>
          </div>
          <div class="job-card-progress">
            <div class="job-card-progress-bar" style="width:${pct}%"></div>
          </div>
        </div>
      `;
    }).join('');

    list.querySelectorAll('.job-card').forEach(card => {
      card.tabIndex=0;card.setAttribute('role','button');
      card.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();card.click();}};
      card.onclick = () => guard(async () => {
        const id = card.dataset.jobId;
        if (!id || (job && job.id === id)) {
          $('jobs-dialog').close();
          return;
        }
        await loadJob(id);
        $('jobs-dialog').close();
      });
    });
  }

  $('open-jobs-dialog').onclick = () => guard(async()=>{
    await save();await loadList();notice('');
    renderJobsDialog();
    $('jobs-dialog').showModal();
    $('jobs-search').focus();
    icons();
  });
  $('jobs-close').onclick = () => $('jobs-dialog').close();
  $('jobs-search').oninput = () => renderJobsDialog();
  document.querySelectorAll('.jobs-tab').forEach(tab => {
    tab.onclick = () => {
      document.querySelectorAll('.jobs-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      currentJobsFilter = tab.dataset.filter;
      renderJobsDialog();
    };
  });
  $('jobs-new-import').onclick = () => {
    $('jobs-dialog').close();
    $('import').click();
  };

  renderWorkflow();
});
