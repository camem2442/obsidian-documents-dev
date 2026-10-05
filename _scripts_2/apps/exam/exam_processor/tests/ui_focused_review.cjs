/* Isolated R3 workflow. Synthetic evidence; never changes existing jobs or calls AI. */
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
(async()=>{
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-r3-ui-'));
  const root=path.resolve(__dirname,'../../../../..');
  const python=process.env.STUDY_TOOLS_PYTHON||path.join(root,'_scripts_2/venv/bin/python');
  execFileSync(python,['-c','import sys; from _scripts_2.apps.exam.exam_processor.tests.focused_fixture import create_fixture; create_fixture(sys.argv[1])',path.join(temp,'data')],{cwd:root});
  const server=spawn(path.resolve(__dirname,'../run.sh'),['--port','0'],{env:{...process.env,EXAM_PROCESSOR_DATA:path.join(temp,'data'),EXAM_PROCESSOR_OUTPUT:path.join(temp,'exports')},stdio:['ignore','pipe','inherit']});
  let browser;
  try{
    const base=await new Promise((resolve,reject)=>{
      let output='';const timer=setTimeout(()=>reject(Error('startup timeout')),15000);
      server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0]);}});
      server.once('error',reject);
    });
    browser=await chromium.launch({headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);
    await page.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===8);
    const idle=()=>page.waitForFunction(()=>!actionPending&&!focusedBusy);
    const click=async id=>{await require('./ui_helpers.cjs').reveal(page,id);await page.locator(id).click();await idle();};
    const open=async name=>{
      if(await page.locator('#focused-dialog').evaluate(el=>el.open))await click('#focused-close');
      await page.evaluate(async name=>{await openFocused(name);},name);
    };
    const snapshot=()=>execFileSync(python,['-c',"import sys,hashlib,json; from pathlib import Path; r=Path(sys.argv[1]); print(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob('*') if p.is_file()},sort_keys=True))",path.join(temp,'data')],{encoding:'utf8'});
    const initial=snapshot();
    // The wrapped header must keep its disclosed controls in the usable viewport.
    for(const width of [1440,1100,900,761,760,390]){
      await page.setViewportSize({width,height:1000});
      await require('./ui_helpers.cjs').reveal(page,'#focused-queue');
      const geometry=await page.evaluate(()=>{
        const rect=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom};};
        return {viewport:innerWidth,header:rect('header'),summary:rect('#review-tools > summary'),
                menu:rect('.review-tools-menu'),button:rect('#focused-queue')};
      });
      console.log('R3 menu geometry '+JSON.stringify({width,...geometry}));
      assert(geometry.menu.left>=0&&geometry.menu.right<=geometry.viewport+1,
             'review tools menu must remain within the viewport: '+JSON.stringify(geometry));
      assert(geometry.button.left>=0&&geometry.button.right<=geometry.viewport+1,
             'focused queue must remain within the viewport: '+JSON.stringify(geometry));
      await page.locator('#focused-queue').click();await idle();
      assert.equal(await page.locator('#focused-dialog').evaluate(el=>el.open),true);
      await click('#focused-close');
      await page.locator('#review-tools > summary').click();
      assert.equal(snapshot(),initial);
    }
    await page.setViewportSize({width:1440,height:1000});
    await click('#focused-queue');
    assert.equal(await page.locator('#focused-dialog').evaluate(el=>el.open),true);
    assert.match(await page.locator('#focused-standard').innerText(),/alphx/);
    await page.locator('#focused-source').evaluate(img=>img.decode());
    await click('#focused-next'); // next question, without skip
    assert.match(await page.locator('#focused-title').innerText(),/문항 2/);
    assert.equal(snapshot(),initial);
    await click('#focused-skip');
    assert.match(await page.locator('#focused-feedback').innerText(),/사유를 남겨/);
    await page.locator('#focused-note').fill('UI에서 원본과 비교한 스킵 근거');
    let failRead=false;
    await page.route('**/items/skip/focused-review',async route=>{
      if(route.request().method()==='POST'){failRead=true;await route.continue();}
      else if(failRead){failRead=false;await route.fulfill({status:503,json:{error:'fixture read failure'}});}
      else await route.continue();
    });
    await click('#focused-skip');
    assert.match(await page.locator('#focused-feedback').innerText(),/저장은 완료되었지만 카드 조회에 실패/);
    assert.equal(await page.locator('#focused-skip').isDisabled(),true);
    await page.unroute('**/items/skip/focused-review');
    await click('#focused-reload');
    assert.match(await page.locator('#focused-content').innerText(),/보존된 스킵 사유: UI에서/);
    assert.equal(await page.evaluate(()=>item().review),'pending');
    assert.equal(await page.locator('#qc-approved').innerText(),'0');
    await open('multi');
    const beforeMove=snapshot();await click('#focused-next');await click('#focused-prev');
    assert.equal(snapshot(),beforeMove);
    await click('#focused-apply');
    assert.match(await page.locator('#focused-feedback').innerText(),/새 버전/);
    assert.match(await page.locator('#focused-approval').innerText(),/현재 버전의 AI 검수/);
    assert.equal(await page.locator('#qc-yellow').innerText(),'7');
    assert.match(await page.locator('#items').innerText(),/현재 선택: 8 · 필터 밖 \(NOT_READY\)/);
    await click('#focused-next');
    assert.equal(await page.locator('#focused-apply').isDisabled(),true);
    assert.match(await page.locator('#focused-content').innerText(),/이전 버전/);
    await open('edit');await click('#focused-edit');
    await page.locator('#focused-editor').fill('alpha + beta = gamma.\n\nUI 직접 수정 $x^2$');
    await click('#focused-preview');
    assert.equal(await page.locator('#focused-draft-preview .katex').count(),1);
    await page.route('**/items/edit/focused-review',async route=>{
      if(route.request().method()==='POST')await route.fulfill({status:500,json:{error:'fixture 저장 실패'}});
      else await route.continue();
    });
    const beforeFailure=snapshot();await click('#focused-save');
    assert.match(await page.locator('#focused-editor').inputValue(),/UI 직접 수정/);
    assert.match(await page.locator('#focused-feedback').innerText(),/저장 실패/);
    assert.equal(snapshot(),beforeFailure);
    await page.unroute('**/items/edit/focused-review');await click('#focused-save');
    assert.match(await page.locator('#focused-standard').innerText(),/UI 직접 수정/);
    assert.equal(await page.locator('#qc-yellow').innerText(),'6');
    await open('apply');
    await page.evaluate(async()=>{await api(`/api/jobs/${job.id}/items/apply`,{revision:item().revision,values:{body:'another window content'}});});
    await click('#focused-apply');
    assert.match(await page.locator('#focused-feedback').innerText(),/바뀌었습니다/);
    await click('#focused-reload');
    assert.equal(await page.locator('#focused-apply').isDisabled(),true);
    assert.match(await page.locator('#focused-standard').innerText(),/another window/);
    for(const name of ['warning','waiver','ambiguous','stale']){
      await open(name);
      assert.equal(await page.locator('#focused-apply').isDisabled(),true,name);
      assert.equal(await page.locator('#focused-edit').isDisabled(),true,name);
      assert.match(await page.locator('#focused-content').innerText(),/상세 검수/);
    }
    await open('ambiguous');await click('#focused-detail');
    assert.equal(await page.locator('#focused-dialog').evaluate(el=>el.open),false);
    assert.equal(await page.evaluate(()=>currentId),'ambiguous');
    await open('warning');
    await page.setViewportSize({width:760,height:900});
    assert(await page.locator('#focused-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    await page.screenshot({path:path.join(temp,'r3-warning.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('R3 UI workflow PASS: navigation, skip, apply, stale, direct edit, failure retention, conflict, warning/waiver, detail, queue, responsive. '+temp);
  }finally{await browser?.close();server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
