/* Automated isolated R4 workflow; direct human-visible observation is recorded separately. */
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
(async()=>{
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-r4-ui-'));
  const root=path.resolve(__dirname,'../../../../..'),python=process.env.STUDY_TOOLS_PYTHON||path.join(root,'_scripts_2/venv/bin/python');
  execFileSync(python,['-c','import sys; from _scripts_2.apps.exam.exam_processor.tests.sample_fixture import create_fixture; create_fixture(sys.argv[1])',path.join(temp,'data')],{cwd:root});
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
    await page.goto(base);await page.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===5);
    const idle=()=>page.waitForFunction(()=>!actionPending&&!sampleBusy);
    const click=async id=>{await require('./ui_helpers.cjs').reveal(page,id);await page.locator(id).click();await idle();};
    const snapshot=()=>execFileSync(python,['-c',"import sys,hashlib,json; from pathlib import Path; r=Path(sys.argv[1]); print(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob('*') if p.is_file()},sort_keys=True))",path.join(temp,'data')],{encoding:'utf8'});
    const initial=snapshot();await click('#sample-queue');
    assert.match(await page.locator('#sample-candidates').innerText(),/후보 4건/);
    assert.equal(snapshot(),initial);
    await click('#sample-start');assert.match(await page.locator('#sample-feedback').innerText(),/표본 수/);
    await page.locator('#sample-count').fill('2');await page.locator('#sample-seed').fill('ui-seed');await click('#sample-start');
    const firstId=await page.evaluate(()=>sampleId());
    await page.locator('.sample-source').first().evaluate(img=>img.decode());
    assert(await page.locator('#sample-content .katex').count()>0);
    const start=snapshot();await click('#sample-next');await click('#sample-prev');assert.equal(snapshot(),start);
    await click('#sample-problem');assert.match(await page.locator('#sample-feedback').innerText(),/사유/);
    await page.locator('#sample-note').fill('보존할 실패 입력');
    await page.route('**/sample-review',async route=>{
      if(route.request().method()==='POST')await route.fulfill({status:500,json:{error:'fixture 저장 실패'}});else await route.continue();
    });
    await click('#sample-problem');assert.equal(await page.locator('#sample-note').inputValue(),'보존할 실패 입력');assert.equal(snapshot(),start);
    await page.unroute('**/sample-review');await click('#sample-problem');
    assert.match(await page.locator('#sample-progress').innerText(),/문제 발견/);
    assert.match(await page.locator('#sample-record').innerText(),/보존할 실패 입력/);
    assert.equal(await page.locator('#sample-pass').isDisabled(),true);
    await click('#sample-next');await click('#sample-pass');
    assert.equal(await page.evaluate(()=>sampleSession().status),'problem');
    assert.equal(await page.locator('#qc-approved').innerText(),'1'); // pre-existing synthetic passage only
    assert.equal(await page.locator('#qc-green').innerText(),'4');
    await click('#sample-start');const secondId=await page.evaluate(()=>sampleSessionId);
    assert.equal(await page.evaluate(()=>sampleId()),firstId);
    await click('#sample-pass');assert.match(await page.locator('#sample-progress').innerText(),/미확인 1/);
    await click('#sample-pause');assert.equal(await page.evaluate(()=>sampleSession().status),'paused');
    await page.reload();await page.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===5);await click('#sample-queue');
    assert.equal(await page.evaluate(()=>sampleSessionId),secondId);assert.equal(await page.evaluate(()=>sampleSession().status),'paused');
    await click('#sample-resume');await click('#sample-next');await click('#sample-pass');
    assert.match(await page.locator('#sample-progress').innerText(),/모든 표본 통과/);
    assert.equal(await page.locator('#qc-approved').innerText(),'1');
    assert.equal(await page.locator('#qc-green').innerText(),'4');
    await page.locator('#sample-count').fill('2');await page.locator('#sample-seed').fill('ui-seed');await click('#sample-start');
    await page.evaluate(async()=>{const view=await api(samplePath());await api(samplePath(),{action:'pause',revision:view.revision,basis_token:view.basis_token,session_id:view.active_session_id});});
    await click('#sample-pass');assert.match(await page.locator('#sample-feedback').innerText(),/바뀌었습니다/);
    await click('#sample-reload');await click('#sample-resume');
    const detailId=await page.evaluate(()=>sampleId());await click('#sample-detail');
    assert.equal(await page.evaluate(()=>currentId),detailId);assert.equal(await page.locator('#sample-dialog').evaluate(el=>el.open),false);
    // Use the actual detail editor save path, including canonical normalization and invalidation.
    await page.evaluate(()=>{section='body';updateSection();setEditing(true);render();});
    await page.locator('#editor').fill('alpha + beta = gamma.\n\nR4 상세 검수에서 직접 수정');
    await page.evaluate(async()=>{dirty=true;await save();});
    await click('#sample-queue');assert.match(await page.locator('#sample-progress').innerText(),/무효/);
    assert.equal(await page.locator('#sample-pass').isDisabled(),true);
    assert.match(await page.locator('#sample-content').innerText(),/R4 상세 검수에서 직접 수정/);
    assert.equal(await page.locator('#qc-green').innerText(),'3');assert.equal(await page.locator('#qc-approved').innerText(),'1');
    assert.match(await page.locator('#items').innerText(),/필터 밖 \(NOT_READY\)/);
    await page.setViewportSize({width:760,height:900});
    assert(await page.locator('#sample-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    await page.screenshot({path:path.join(temp,'r4-stale.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('R4 UI workflow PASS: no-write navigation, explicit count/seed, reason, save failure, history, pause/resume/reload, all-pass/no approval, conflict, detail edit/stale, queue/selection, responsive. '+temp);
  }finally{await browser?.close();server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
