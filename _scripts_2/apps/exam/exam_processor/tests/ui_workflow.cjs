/* Network-free workflow checks. Synthetic AI results and exports stay in a temporary KS. */
const {chromium}=require('playwright');
const {spawn}=require('child_process');
const fs=require('fs');
const os=require('os');
const path=require('path');
const assert=require('assert');

(async()=>{
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-workflow-test-'));
  const ks=path.join(temp,'KS');fs.mkdirSync(ks);
  const source=path.join(ks,'fixture.md');
  fs.writeFileSync(source,'# [38~39번]\n## (가)\n검수용 공통 지문입니다.\n## 문제\n### 38. 첫째 문제?\n① 첫째\n② 둘째\n### 39. 둘째 문제?\n① 셋째\n② 넷째\n');
  const server=spawn(path.resolve(__dirname,'../run.sh'),['--port','0'],{
    env:{...process.env,EXAM_PROCESSOR_DATA:path.join(temp,'data'),EXAM_PROCESSOR_KS:ks,EXAM_PROCESSOR_OUTPUT:path.join(ks,'export')},
    stdio:['ignore','pipe','inherit']
  });
  let browser;
  try{
    const base=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error('Server startup timeout')),15000);
      let output='';server.stdout.on('data',chunk=>{
        output+=chunk;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);
        if(match){clearTimeout(timer);resolve(match[0]);}
      });
      server.once('error',e=>{clearTimeout(timer);reject(e);});
      server.once('exit',code=>{clearTimeout(timer);reject(Error('Server exited '+code));});
    });
    const launchOptions={headless:true};
    if(process.env.EXAM_TEST_BROWSER)launchOptions.channel=process.env.EXAM_TEST_BROWSER;
    browser=await chromium.launch(launchOptions);
    const staticHtml=fs.readFileSync(path.resolve(__dirname,'../static/index.html'),'utf8');
    const staticApp=fs.readFileSync(path.resolve(__dirname,'../static/app.js'),'utf8');
    assert(staticHtml.includes("location.protocol==='file:'"));
    assert(staticHtml.includes("http://127.0.0.1:7893/"));
    assert(staticApp.includes("if(location.protocol==='file:')"));
    assert(staticApp.includes('검수 화면 열기'));
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const idle=()=>page.waitForFunction(()=>{
      try {
        return Boolean(settings) && !actionPending;
      } catch (e) {
        return false;
      }
    });
    const click=async selector=>{await require('./ui_helpers.cjs').reveal(page,selector);await page.locator(selector).click();await idle();};
    await page.goto(base);await idle();
    let forcedAuthFailure=true;
    await page.route('**/api/jobs',async route=>{
      if(forcedAuthFailure){forcedAuthFailure=false;await route.fulfill({status:403,json:{error:'fixture expired token'}});}
      else await route.continue();
    });
    const recoveredJobs=await page.evaluate(()=>api('/api/jobs'));
    assert(Array.isArray(recoveredJobs));
    assert.equal(await page.locator('#connection-state').innerText(),'서버 연결됨');
    await page.unroute('**/api/jobs');
    const mathPreview=await page.evaluate(()=>{
      const target=document.createElement('div');
      ExamPreview.render(target,'문장 표지 \\textcircled{A}. Inline $n$ and $0 \\le r \\le n$. Escaped \\( (\\text{양 끝에 대문자를 넣는 경우의 수}) \\cdots \\textcircled{A} \\).\n\n$$\\underbrace{n(n-1)}_{r\\text{개}} = \\frac{n!}{(n-r)!}$$\n\nCode: `$a_b$`');
      const walker=document.createTreeWalker(target,NodeFilter.SHOW_TEXT);
      let raw=false,node;
      while((node=walker.nextNode())){
        if(!node.parentElement?.closest('.katex, code')&&node.nodeValue.includes('\\textcircled{A}'))raw=true;
      }
      return {inline:target.querySelectorAll('.exam-math-inline .katex').length,
        display:target.querySelectorAll('.exam-math-display .katex').length,
        marker:target.textContent.includes('문장 표지 Ⓐ.'),raw,
        code:target.querySelector('code')?.textContent};
    });
    assert.deepEqual(mathPreview,{inline:3,display:1,marker:true,raw:false,code:'$a_b$'});
    const mathCases=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/math_preview_cases.json'),'utf8'));
    for(const entry of mathCases){
      const rendered=await page.evaluate(entry=>{
        const target=document.createElement('div');
        ExamPreview.render(target,entry.source);
        return {inline:target.querySelectorAll('.exam-math-inline .katex').length,
          display:target.querySelectorAll('.exam-math-display .katex').length,
          errors:target.querySelectorAll('.katex-error').length};
      },entry);
      assert.deepEqual(rendered,{inline:entry.inline,display:entry.display,errors:0},entry.name);
    }
    const conditionBox=await page.evaluate(()=>{
      const target=document.createElement('div');
      ExamPreview.render(target,'> [!quote]\n> (가) $n(A) \\le 3$ (나) $n(A)=n(B)$ (다) $f(x) \\ne x$');
      return {box:target.querySelectorAll('.source-box').length,breaks:target.querySelectorAll('.source-box br').length,
        math:target.querySelectorAll('.source-box .katex').length};
    });
    assert.deepEqual(conditionBox,{box:1,breaks:3,math:3});
    await page.screenshot({path:path.join(temp,'empty-desktop.png'),fullPage:true});
    await click('#vault-files');
    await page.waitForFunction(()=>document.querySelector('#vault-status').textContent==='PDF 없음');
    assert(await page.locator('#vault-select').isDisabled());
    await click('#vault-close');
    await click('#import');
    await page.locator('#input-format').selectOption('hanwangi_2026_probability');
    assert.equal(await page.locator('#track').inputValue(),'확률과 통계');
    assert(await page.locator('#workbook-fields').isVisible());
    assert(await page.locator('#pages-field').isHidden());
    for(const id of ['structure-path','layout-path','solution-path'])assert(await page.locator('#'+id).evaluate(e=>e.required));
    await page.screenshot({path:path.join(temp,'math-import-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(temp,'math-import-mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('#input-format').selectOption('kice_english');
    assert.equal(await page.locator('#track').inputValue(),'영어');
    assert(await page.locator('#english-fields').isVisible());
    assert(await page.locator('#pages-field').isVisible());
    assert(await page.locator('#workbook-fields').isHidden());
    for(const id of ['structure-path','layout-path','solution-path'])assert(!(await page.locator('#'+id).evaluate(e=>e.required)));
    await page.locator('#input-format').selectOption('');
    assert.equal(await page.locator('#track').inputValue(),'화법과 작문');
    assert(await page.locator('#english-fields').isHidden());
    assert(await page.locator('#workbook-fields').isHidden());
    for(const id of ['structure-path','layout-path','solution-path'])assert(!(await page.locator('#'+id).evaluate(e=>e.required)));
    await page.locator('#source').fill('UX 첫 작업');
    await page.locator('#path').fill(path.join(ks,'missing.md'));
    await click('#submit-import');
    assert(await page.locator('#import-dialog .dialog-notice.error').isVisible());
    await page.locator('#pages').fill('2');await page.locator('#solution-path').fill('이전 해설');
    await click('#cancel-import');await click('#import');
    for(const id of ['path','source','pages','solution-path'])assert.equal(await page.locator('#'+id).inputValue(),'');
    await page.locator('#path').fill(source);await page.locator('#source').fill('UX 첫 작업');
    await click('#submit-import');
    const firstId=await page.evaluate(()=>job.id);
    assert(await page.locator('#review-alert').isVisible());
    assert(await page.evaluate(()=>document.querySelector('#review-alert').nextElementSibling.id==='ai-activity-panel'));
    assert(await page.evaluate(()=>document.querySelector('#audit-banner').nextElementSibling.classList.contains('compare')));
    assert(await page.evaluate(()=>document.querySelector('#review-alert').contains(document.querySelector('#warnings'))));
    const queueUnavailable=await page.evaluate(()=>{
      const original=job.review_queue_summary;
      job.review_queue_summary={status:'unavailable',reason:'검수 근거 누락'};
      reviewTierFilter='YELLOW';renderList();
      const missing={text:$('qc-status').textContent,count:$('qc-yellow').textContent,
        disabled:document.querySelector('[data-tier="YELLOW"]').disabled,empty:$('items').textContent};
      delete job.review_queue_summary;renderList();
      const absent=$('qc-status').textContent;
      job.review_queue_summary=original;reviewTierFilter='all';renderList();
      return {missing,absent};
    });
    assert(queueUnavailable.missing.text.includes('분류 미계산'));
    assert.equal(queueUnavailable.missing.count,'—');
    assert(queueUnavailable.missing.disabled);
    assert(queueUnavailable.missing.empty.includes('분류 미계산'));
    assert(queueUnavailable.absent.includes('서버가 분류 정보를 제공하지 않았습니다'));
    const rendererFailure=await page.evaluate(()=>{
      ExamPreview.render($('preview'),'$\\unknowncommand{n}$');renderAuditBanner();
      const message=$('preview-status').textContent;
      renderMarkdown($('preview'),previewText(item(),true));
      return message;
    });
    assert(rendererFailure.includes('수식 렌더링 오류'));
    const stableStatus=await page.evaluate(()=>{
      const alert=document.querySelector('#review-alert'),activity=document.querySelector('#ai-activity-panel');
      const warnings=document.querySelector('#warnings'),preview=document.querySelector('#ai-activity-preview');
      const measure=()=>({alert:alert.getBoundingClientRect().height,activity:activity.getBoundingClientRect().height,main:document.querySelector('main').getBoundingClientRect().top});
      const before=measure(),originalWarnings=warnings.textContent,originalLogs=job.ai_log,originalProgress=job.ai_progress,originalQueue=job.queue;
      warnings.textContent='긴 알림 내용 '.repeat(250);
      job.ai_log=Array.from({length:16},(_,n)=>({t:1700000000,level:'info',message:`처리 로그 ${n} `.repeat(14)}));
      job.ai_progress={message:'긴 진행 상태 '.repeat(100),queue_total:16,queue_done:3};
      job.queue={status:'running',operation:'extract',entries:Array.from({length:16},(_,n)=>({status:n<3?'completed':'pending'}))};
      renderAiActivity();
      const after=measure(),warningsReadable=warnings.scrollHeight<=warnings.clientHeight+1,logsScrollable=preview.scrollHeight>preview.clientHeight;
      warnings.textContent=originalWarnings;job.ai_log=originalLogs;job.ai_progress=originalProgress;job.queue=originalQueue;
      renderAiActivity();
      return {before,after,warningsReadable,logsScrollable,restored:measure()};
    });
    assert(stableStatus.after.alert>stableStatus.before.alert);
    assert.deepEqual(stableStatus.before,stableStatus.restored);
    assert(stableStatus.warningsReadable&&stableStatus.logsScrollable);
    await page.setViewportSize({width:390,height:844});
    const mobileStatus=await page.evaluate(()=>{
      const alert=document.querySelector('#review-alert'),warnings=document.querySelector('#warnings');
      const before=alert.getBoundingClientRect().height;
      const original=warnings.textContent;warnings.textContent='좁은 화면 경고 '.repeat(150);
      const after=alert.getBoundingClientRect().height,scrolls=warnings.scrollHeight>warnings.clientHeight;
      warnings.textContent=original;
      return {before,after,scrolls,noHorizontalOverflow:document.documentElement.scrollWidth<=innerWidth};
    });
    assert(mobileStatus.after>mobileStatus.before);
    assert(!mobileStatus.scrolls&&mobileStatus.noHorizontalOverflow);
    await page.setViewportSize({width:1440,height:1000});
    assert.deepEqual(await page.locator('#section-tabs button').allInnerTexts(),['전체','문제·지문','해설']);
    assert.equal(await page.locator('[data-section="all"]').getAttribute('aria-pressed'),'true');
    assert(await page.locator('#edit-tab').isEnabled());
    assert(await page.locator('#normalize').isDisabled());
    await require('./ui_combined_edit.cjs')(page,click,idle);
    const originalSections=await page.evaluate(()=>({body:item().body,solution:item().solution}));
    await page.locator('#review-note').fill('전체 보기에서 남긴 검수 메모');
    await click('#save');
    assert.deepEqual(await page.evaluate(()=>({body:item().body,solution:item().solution})),originalSections);
    assert.equal(await page.locator('#review-note').inputValue(),'전체 보기에서 남긴 검수 메모');
    const sidebar=await page.evaluate(()=>{
      const nav=document.querySelector('#items');
      const extras=Array.from({length:45},(_,index)=>{const button=document.createElement('button');button.textContent=`임시 문항 ${index}`;nav.append(button);return button;});
      const side=document.querySelector('main>aside').getBoundingClientRect();
      const metrics={bottom:side.bottom,viewport:innerHeight,scrollable:nav.scrollHeight>nav.clientHeight};
      nav.scrollTop=nav.scrollHeight;
      metrics.scrolled=nav.scrollTop>0;
      extras.forEach(button=>button.remove());
      return metrics;
    });
    assert(Math.abs(sidebar.bottom-sidebar.viewport)<2,JSON.stringify(sidebar));
    assert(sidebar.scrollable&&sidebar.scrolled,JSON.stringify(sidebar));
    await page.evaluate(()=>window.scrollTo(0,80));
    await page.waitForFunction(()=>Math.abs(document.querySelector('main>aside').getBoundingClientRect().bottom-innerHeight)<2);
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.evaluate(()=>{
      const record=item();
      record.assets=[
        {id:'fig1',section:'body',path:'assets/test/body/fig1.png'},
        {id:'fig1',section:'solution',path:'assets/test/solution/fig1.png'},
      ];
      render();
    });
    await page.locator('#open-crop').click();
    assert.deepEqual(await page.locator('#crop-target option').allInnerTexts(),['문제·지문 · fig1','해설 · fig1']);
    assert.deepEqual(await page.locator('#crop-target option').evaluateAll(options=>options.map(option=>option.value)),['asset-index:0','asset-index:1']);
    await page.locator('#crop-target').selectOption('asset-index:1');
    assert.equal(await page.locator('#crop-target').inputValue(),'asset-index:1');
    await page.locator('#cancel-crop').click();
    assert.equal(await page.evaluate(()=>assetIndexFromFileUrl('/api/jobs/test/file/assets/test/solution/fig1.png?v=1')),1);
    await page.evaluate(()=>openCropDialogForAsset(1));
    assert.equal(await page.locator('#crop-target').inputValue(),'asset-index:1');
    await page.locator('#cancel-crop').click();
    await page.evaluate(()=>{item().assets=[];render();});
    const fixturePath=path.join(temp,'data/jobs',firstId,'job.json');
    await page.locator('.learning-panel summary').click();
    await page.locator('#finding-category').selectOption('layout');
    await page.locator('#finding-source').fill('조건이 테두리 안에 있음');
    await page.locator('#finding-extracted').fill('조건을 일반 문장으로 출력');
    await page.locator('#finding-correction').fill('조건을 인용 박스로 보존');
    await page.locator('#finding-lesson').fill('원문에서 조건이 테두리로 묶인 경우 Markdown 인용 박스로 경계를 보존한다.');
    await click('#save-finding');
    const findingsPath=path.join(temp,'data/learning/human-findings.json');
    assert(fs.existsSync(findingsPath));
    assert.equal(JSON.parse(fs.readFileSync(findingsPath,'utf8')).findings.length,1);
    assert((await page.locator('#finding-count').innerText()).includes('1개'));
    const conflictIds=await page.evaluate(async()=>{
      const current=item();
      const values=[
        {section:'body',category:'math',source_excerpt:'2imes',extracted_excerpt:'2imes',correction:'2\\times',lesson:'수식에서 빠진 곱셈 명령어를 원문에 맞게 복원한다.'},
        {section:'body',category:'math',source_excerpt:'2imes',extracted_excerpt:'2imes',correction:'2 ×',lesson:'수식의 곱셈 기호를 원문에 맞게 기호로 복원한다.'}
      ];
      const results=[];
      for(const value of values){
        const response=await fetch(`/api/jobs/${job.id}/items/${current.id}/human-findings`,{
          method:'POST',headers:{'Content-Type':'application/json','X-Exam-Token':settings.token},
          body:JSON.stringify({revision:current.revision,values:value})
        });
        if(!response.ok)throw Error((await response.json()).error);
        results.push(await response.json());
      }
      await loadHumanFindings();
      return results.map(value=>value.finding_id);
    });
    await page.waitForFunction(()=>document.querySelector('#finding-count').textContent.includes('충돌 제외 2개'));
    assert.equal(await page.locator('[data-resolve]').count(),2);
    await page.locator(`[data-resolve="${conflictIds[0]}"]`).click();
    await page.locator('#prompt-dialog[open]').waitFor();
    await page.locator('#prompt-input').fill('원문 PDF에서 곱셈 기호를 확인해 이 표기를 기준으로 선택한다.');
    await page.locator('#prompt-ok').click();await idle();
    assert((await page.locator('#finding-count').innerText()).includes('충돌 해소 1개'), await page.locator('#finding-count').innerText());
    await page.reload();await idle();
    await page.waitForFunction(()=>document.querySelector('#finding-count').textContent.includes('충돌 해소 1개'));
    await page.locator('.learning-panel summary').click();
    assert((await page.locator('#finding-count').innerText()).includes('충돌 해소 1개'), `reloaded count: ${await page.locator('#finding-count').innerText()}`);
    assert.equal(await page.locator('.rule-status.resolved').count(),1);
    assert.equal(await page.locator('.rule-status.duplicate').count(),1);
    assert.equal(await page.locator('[data-resolve]').count(),0);
    const resolution=JSON.parse(fs.readFileSync(findingsPath,'utf8')).findings
      .find(f=>f.finding_id===conflictIds[0]).conflict_resolution;
    assert.equal(resolution.selected_finding_id,conflictIds[0]);
    assert(resolution.rationale.includes('원문 PDF'));
    assert.deepEqual(resolution.member_finding_ids.sort(),conflictIds.sort());
    const mutate=(fn,targetPath=fixturePath)=>{
      const jobDir=path.dirname(targetPath);
      const manifest=JSON.parse(fs.readFileSync(targetPath));
      if(manifest.record_ids){
        const recordsDir=path.join(jobDir,'records');
        const reviewDir=path.join(jobDir,'review');
        const items=manifest.record_ids.map(rid=>{
          const rec=JSON.parse(fs.readFileSync(path.join(recordsDir,`${rid}.json`)));
          const revPath=path.join(reviewDir,`${rid}.json`);
          const rev=fs.existsSync(revPath)?JSON.parse(fs.readFileSync(revPath)):{record_id:rid,applies_to_revision_id:rec.provenance?.revision_id||''};
          return {
            id:rid,
            number:rec.question.question_number,
            revision:rec.provenance?.revision_id||'',
            body:rec.body,
            solution:rec.solution,
            answer:rec.question.answer,
            audit:rev.audit,
            warnings:rev.warnings||[],
            solution_candidates:rev.solution_candidates||[],
            solution_match:rev.solution_match,
            review:rec.review?.human_approval||'pending',
            _rec:rec,
            _rev:rev,
          };
        });
        const jobData={...manifest,items};
        fn(jobData);
        items.forEach(i=>{
          if(i._rec){
            if(i.body!==undefined)i._rec.body=i.body;
            if(i.solution!==undefined)i._rec.solution=i.solution;
            if(i.answer!==undefined)i._rec.question.answer=i.answer;
            if(i.review!==undefined)i._rec.review.human_approval=i.review;
            fs.writeFileSync(path.join(recordsDir,`${i.id}.json`),JSON.stringify(i._rec,null,2));
          }
          if(i._rev){
            if(i.audit!==undefined)i._rev.audit=i.audit;
            if(i.warnings!==undefined)i._rev.warnings=i.warnings;
            if(i.solution_candidates!==undefined)i._rev.solution_candidates=i.solution_candidates;
            if(i.solution_match!==undefined)i._rev.solution_match=i.solution_match;
            fs.writeFileSync(path.join(reviewDir,`${i.id}.json`),JSON.stringify(i._rev,null,2));
          }
        });
        delete jobData.items;
        fs.writeFileSync(targetPath,JSON.stringify(jobData,null,2));
      } else {
        fn(manifest);
        fs.writeFileSync(targetPath,JSON.stringify(manifest,null,2));
      }
    };
    // Audit responses are seeded, never obtained from a real provider or treated as human QA.
    mutate(data=>{
      data.items.forEach(i=>i.audit={revision:i.revision,status:'completed',result:'no_difference',issues:[],scope:'markdown_reference',provider:'fixture'});
      const question=data.items.find(i=>i.number==='38');
      question.solution='기존 해설';question.solution_reference='기존 원문';question.answer='⑤';
      question.solution_candidates=[{id:'ebs-38',number:'38',body:'EBS 정답 해설',answer:'①',regions:[],academic_year:'2026',track:'화법과 작문'}];
      question.solution_match={status:'matched',basis:['academic_year','track','question_number']};
      question.warnings.push('해설 후보를 확인해 연결하세요.');
    });
    await page.reload();await idle();
    await page.locator('#items button').filter({hasText:'38번'}).click();await idle();
    assert((await page.locator('#review-alert #warnings').innerText()).includes('해설 후보를 확인해 연결하세요.'));
    assert((await page.locator('.match-status').innerText()).includes('단일 후보'));
    assert.equal(await page.locator('.match-basis').innerText(),'매칭 근거: 학년도 + 선택과목 + 문항 번호');
    assert.equal(await page.locator('#unlink-solution').count(),0);
    await click('#link-solution');
    assert((await page.locator('.match-status').innerText()).includes('연결됨'));
    assert.equal(await page.evaluate(()=>item().answer),'①');
    assert((await page.locator('#notice').innerText()).includes('연결했습니다'));
    await page.locator('#unlink-solution').click();
    await page.locator('#confirm-dialog[open]').waitFor();
    await page.locator('#confirm-ok').click();await idle();
    assert.equal(await page.evaluate(()=>item().answer),'⑤');
    assert.equal(await page.evaluate(()=>item().solution),'기존 해설');
    assert((await page.locator('#notice').innerText()).includes('해제했습니다'));
    assert((await page.evaluate(()=>item().errors)).includes('해설 후보의 연결을 확인하세요.'));
    await click('#link-solution');
    mutate(data=>data.items.forEach(i=>i.audit={revision:i.revision,status:'completed',result:'no_difference',issues:[],scope:'markdown_reference',provider:'fixture'}));
    await page.reload();await idle();
    await click('#approve');
    assert((await page.locator('#job-btn-label').innerText()).includes('(1/3 승인)'));
    assert((await page.locator('#item-title').innerText()).includes('38'));
    await click('#approve');
    await click('#export');await page.locator('#export-code').fill('TEST1');await click('#export-submit');
    assert(await page.locator('#export-dialog .dialog-notice.error').isVisible());
    await click('#export-close');await click('#approve');
    assert((await page.locator('#job-btn-label').innerText()).includes('(3/3 승인)'));
    await click('#open-jobs-dialog');
    assert((await page.locator('.job-card-meta').innerText()).includes('승인 3/3'));
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(temp,'jobs-mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await click('#jobs-close');await page.setViewportSize({width:1440,height:1000});
    await click('#export');await click('#export-submit');
    const exported=await page.evaluate(()=>job.last_export);
    assert(exported.startsWith(fs.realpathSync(ks)+path.sep));
    assert(fs.existsSync(path.join(exported,'TEST1 1','TEST1_1.md')));
    assert(fs.existsSync(path.join(exported,'TEST1 1','TEST138.md')));
    assert((await page.locator('#notice').innerText()).includes(exported));
    // A solution-only proposal applies to the solution even while the body tab is open.
    mutate(data=>{
      const i=data.items[0];i.review='pending';i.solution='해설 오기';
      i.audit={revision:i.revision,status:'completed',result:'suspected_difference',issues:[{location:'solution',extracted:'해설 오기',original:'해설 수정',suggestion:'해설 수정',message:'fixture'}]};
    });
    await page.reload();await idle();
    const body=await page.evaluate(()=>item().body);
    await click('[data-issue="0"]');
    assert.equal(await page.locator('#editor').inputValue(),'해설 수정');
    assert.equal(await page.evaluate(()=>item().body),body);
    assert((await page.locator('.issue-status').innerText()).includes('적용됨'));
    assert((await page.locator('#audit-state').innerText()).includes('재대조 필요'));
    assert.equal(await page.evaluate(()=>item().review),'pending');
    await click('[data-revert-issue="0"]');
    await click('[data-skip-issue="0"]');
    assert((await page.locator('.issue-status').innerText()).includes('스킵됨'));
    assert.equal(await page.evaluate(()=>item().audit.issues[0].skipped),true);
    await page.locator('[data-skip-note="0"]').fill('원본 표현이 맞아 제안을 적용하지 않음');
    await click('[data-save-skip-note="0"]');
    assert.equal(await page.evaluate(()=>item().audit.issues[0].skip_note),'원본 표현이 맞아 제안을 적용하지 않음');
    const skipDecisions=JSON.parse(fs.readFileSync(path.join(temp,'data/learning/audit-skip-decisions.json'),'utf8')).decisions;
    assert.equal(skipDecisions[0].note,'원본 표현이 맞아 제안을 적용하지 않음');
    assert.equal(skipDecisions[0].active,true);
    await click('[data-skip-issue="0"]');
    assert.equal(await page.evaluate(()=>Boolean(item().audit.issues[0].skipped)),false);
    assert.equal(JSON.parse(fs.readFileSync(path.join(temp,'data/learning/audit-skip-decisions.json'),'utf8')).decisions[0].active,false);
    assert(await page.locator('#approve').isDisabled());
    assert.equal(await page.locator('#approve').getAttribute('title'),await page.locator('#approval-status').innerText());
    assert((await page.locator('#approval-status').innerText()).includes('현재 버전의 AI 검수를 완료하세요.'));
    // Failed autosave keeps the user's text and stops item navigation.
    await click('#edit-tab');await page.locator('#editor').fill('저장되지 않은 수정');
    await page.route('**/api/jobs/*/items/*',route=>route.request().method()==='POST'?route.fulfill({status:409,json:{error:'fixture 저장 충돌'}}):route.continue());
    await click('#save');
    assert((await page.locator('#save-state').innerText()).includes('저장 실패'));
    assert.equal(await page.locator('#editor').inputValue(),'저장되지 않은 수정');
    await page.locator('#items button').filter({hasText:'38번'}).click();await idle();
    assert((await page.locator('#item-title').innerText()).includes('공통 지문'));
    await page.unroute('**/api/jobs/*/items/*');await click('#save');
    let releaseSave,seenSave;
    const saveArrived=new Promise(resolve=>seenSave=resolve);
    const saveReleased=new Promise(resolve=>releaseSave=resolve);
    await page.route('**/api/jobs/*/items/*',async route=>{
      if(route.request().method()==='POST'){seenSave();await saveReleased;}
      await route.continue();
    });
    await page.locator('#editor').fill('첫 저장');await page.locator('#save').click();await saveArrived;
    assert(await page.locator('#items button').first().isDisabled());
    assert(await page.locator('#editor').isEnabled());
    await page.locator('#editor').fill('저장 중 이어 쓴 문장');releaseSave();await idle();
    assert.equal(await page.locator('#editor').inputValue(),'저장 중 이어 쓴 문장');
    assert.equal(await page.evaluate(()=>item()[section]),'저장 중 이어 쓴 문장');
    await page.unroute('**/api/jobs/*/items/*');
    // Busy state blocks duplicate commands and paused queues expose the correct resume/retry controls.
    mutate(data=>{
      data.items.forEach(i=>{i.review='approved';i.audit={revision:i.revision,status:'completed',result:'no_difference',issues:[]};});
      data.queue={operation:'extract',status:'paused',entries:[{item:data.items[0].id,status:'pending'}]};
      data.queue_history=[{operation:'audit',entries:[{item:data.items[0].id,status:'failed'}]}];
    });
    await page.reload();await idle();
    assert(await page.locator('#batch-start').isDisabled());
    assert(await page.locator('#batch-resume').isEnabled());
    assert(await page.locator('#batch-retry').isDisabled());
    await require('./ui_helpers.cjs').reveal(page,'#batch-operation');
    await page.locator('#batch-operation').selectOption('audit');
    assert(await page.locator('#batch-retry').isEnabled());
    await click('#export');await page.locator('#export-code').fill('OLD_CODE');await click('#export-close');
    await page.locator('[data-section="solution"]').click();await idle();
    await page.locator('#filter').selectOption('held');
    await click('#import');await page.locator('#source').fill('UX 두 번째 작업');await page.locator('#path').fill(source);await click('#submit-import');
    assert.equal(await page.locator('#filter').inputValue(),'all');
    assert.equal(await page.locator('[data-section="all"]').getAttribute('aria-pressed'),'true');
    const secondId=await page.evaluate(()=>job.id);
    const secondPath=path.join(temp,'data/jobs',secondId,'job.json');
    mutate(second=>{second.items.forEach(i=>{i.review='approved';i.audit={revision:i.revision,status:'completed',result:'no_difference',issues:[]};});},secondPath);
    await page.evaluate(id=>guard(()=>loadJob(id)),secondId);await idle();await click('#export');
    assert.equal(await page.locator('#export-code').inputValue(),'');
    await click('#export-close');
    for(const width of [320,390,768,1440]){
      await page.setViewportSize({width,height:900});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
    }
    await click('#open-jobs-dialog');
    await page.locator(`[data-job-id="${firstId}"]`).focus();await page.keyboard.press('Enter');await idle();
    assert.equal(await page.evaluate(()=>job.id),firstId);
    for(const width of [320,390,760]){
      await page.setViewportSize({width,height:900});
      const list=await page.locator('#items').evaluate(el=>({direction:getComputedStyle(el).flexDirection,
        overflowY:getComputedStyle(el).overflowY,maxHeight:getComputedStyle(el).maxHeight,
        scrollWidth:el.scrollWidth,clientWidth:el.clientWidth}));
      assert.equal(list.direction,'column');assert.equal(list.overflowY,'auto');
      assert.notEqual(list.maxHeight,'none');assert(list.scrollWidth<=list.clientWidth);
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({artifacts:temp,checks:['math preview protection/rendering','mobile vertical review list','empty state','modal error','new-file reset','solution match basis','solution link/unlink restore','approval next/counts','partial export blocked','isolated set export','solution proposal','reaudit required','save failure preservation','queue controls','job switch reset','keyboard job selection']}));
  }finally{
    if(browser)await browser.close();
    if(server.exitCode===null){const stopped=new Promise(resolve=>server.once('exit',resolve));server.kill('SIGTERM');await stopped;}
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
