/* Automated R6 fixture UI. Visible manual observation is separately recorded. */
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../../../..'),python=process.env.STUDY_TOOLS_PYTHON||path.join(root,'_scripts_2/venv/bin/python');
(async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-r6-ui-')),data=path.join(temp,'data');
 execFileSync(python,['-c','import sys; from _scripts_2.apps.exam.exam_processor.tests.evaluation_fixture import create_fixture; create_fixture(sys.argv[1])',data],{cwd:root});
 const server=spawn(path.resolve(__dirname,'../run.sh'),['--port','0'],{env:{...process.env,EXAM_PROCESSOR_DATA:data,EXAM_PROCESSOR_OUTPUT:path.join(temp,'exports')},stdio:['ignore','pipe','inherit']});
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('startup timeout')),60000);server.stdout.on('data',chunk=>{output+=chunk;const m=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});server.once('error',reject);});
  browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===5);
  const idle=()=>page.waitForFunction(()=>!actionPending&&!evaluationBusy&&!sampleBusy&&!focusedBusy);
  const click=async id=>{await require('./ui_helpers.cjs').reveal(page,id);await page.locator(id).click();await idle();};
  const queue=await page.evaluate(()=>JSON.stringify(job.review_queue_summary));
  await click('#evaluation-queue');assert.match(await page.locator('#evaluation-summary').innerText(),/판정 0/);assert.match(await page.locator('#evaluation-timer').innerText(),/미확인/);
  await click('#evaluation-save');assert.match(await page.locator('#evaluation-feedback').innerText(),/판정을 선택/);
  await page.locator('#evaluation-verdict').selectOption('error_found');await click('#evaluation-save');assert.match(await page.locator('#evaluation-feedback').innerText(),/판단 근거/);
  await page.locator('#evaluation-note').fill('SYNTHETIC UI observation; not human acceptance');await page.locator('#evaluation-scope input[value="body"]').check();
  await page.route('**/evaluation',async route=>{if(route.request().method()==='POST'&&route.request().postDataJSON().action==='label')await route.fulfill({status:500,json:{error:'fixture storage failure'}});else await route.continue();});
  await click('#evaluation-save');assert.match(await page.locator('#evaluation-note').inputValue(),/SYNTHETIC/);assert.match(await page.locator('#evaluation-feedback').innerText(),/입력은 유지/);
  await page.unroute('**/evaluation');await click('#evaluation-save');assert.match(await page.locator('#evaluation-summary').innerText(),/판정 1/);assert.match(await page.locator('#evaluation-green').innerText(),/q1/);
  await page.locator('#evaluation-verdict').selectOption('deferred');await page.locator('#evaluation-note').fill('synthetic correction');await click('#evaluation-save');assert.match(await page.locator('#evaluation-feedback').innerText(),/정정/);
  const first=await page.evaluate(()=>evaluationView.labels[0].id);await page.locator('#evaluation-supersedes').selectOption(first);await page.locator('#evaluation-correction').fill('synthetic uncertainty');await click('#evaluation-save');assert.match(await page.locator('#evaluation-history').innerText(),/superseded/);
  assert.equal(await page.evaluate(()=>JSON.stringify(job.review_queue_summary)),queue);
  await click('#evaluation-start');await page.waitForFunction(()=>evaluationView.summary.measured_seconds!==null,{},{timeout:15000});await click('#evaluation-pause');
  const seconds=await page.evaluate(()=>evaluationView.summary.measured_seconds);assert(seconds>0);
  await click('#evaluation-resume');
  // A second window cannot take over a live interval.
  const page2=await context.newPage();await page2.goto(base);await page2.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===5);await require('./ui_helpers.cjs').reveal(page2,'#evaluation-queue');await page2.locator('#evaluation-queue').click();await page2.waitForFunction(()=>!actionPending&&!evaluationBusy);
  assert(await page2.locator('#evaluation-start').isDisabled());assert(await page2.locator('#evaluation-resume').isDisabled());await page2.close();
  await click('#evaluation-end');assert.equal(await page.evaluate(()=>evaluationView.timers[0].status),'ended');
  await page.locator('#evaluation-item').selectOption('q2');await idle();await page.locator('#evaluation-target').selectOption('issue');await idle();
  await page.locator('#evaluation-scope input[value="body"]').check();await page.locator('#evaluation-verdict').selectOption('false_positive');await page.locator('#evaluation-note').fill('synthetic issue FP');await click('#evaluation-save');
  assert.match(await page.locator('#evaluation-metrics').innerText(),/지적 오탐: 1 \/ 1/);
  await page.locator('#evaluation-origin').selectOption('synthetic_fixture');await page.locator('#evaluation-full').uncheck();await click('#evaluation-dataset');assert.match(await page.locator('#evaluation-datasets').innerText(),/포함 1/);
  const firstHash=await page.evaluate(()=>evaluationView.datasets[0].composition_hash);await click('#evaluation-dataset');assert.equal(await page.evaluate(()=>evaluationView.datasets[1].composition_hash),firstHash);
  const url=await page.locator('#evaluation-datasets a').first().getAttribute('href');const dataset=await (await page.request.get(base+url)).json();assert.equal(dataset.labels[0].origin,'synthetic_fixture');assert(Object.keys(dataset.evidence).length===1);
  await page.setViewportSize({width:760,height:900});assert(await page.locator('#evaluation-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:path.join(temp,'r6-summary.png'),fullPage:true});
  await click('#evaluation-close');await click('#focused-queue');await click('#focused-evaluation');assert.equal(await page.locator('#evaluation-target').inputValue(),'issue');await click('#evaluation-close');await click('#focused-close');
  await click('#sample-queue');await page.locator('#sample-count').fill('1');await page.locator('#sample-seed').fill('r6-ui');await click('#sample-start');await click('#sample-evaluation');assert.equal(await page.locator('#evaluation-item').inputValue(),await page.evaluate(()=>sampleId()));await click('#evaluation-close');await click('#sample-close');
  await page.reload();await page.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===5);await click('#evaluation-queue');assert.match(await page.locator('#evaluation-summary').innerText(),/판정 3/);
  assert.equal(await page.evaluate(()=>JSON.stringify(job.review_queue_summary)),queue);
  // Explicit browser event fixture: hide while a checkpoint response is in flight.
  await click('#evaluation-start');
  await page.route('**/evaluation',async route=>{if(route.request().method()==='POST'&&route.request().postDataJSON().action==='tick')await new Promise(r=>setTimeout(r,150));await route.continue();});
  await page.evaluate(()=>{evaluationRun(()=>evaluationMeasure('tick'));Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  await idle();assert.equal(await page.evaluate(()=>evaluationTimer().status),'paused');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
  assert.equal(await page.evaluate(()=>evaluationTimer().status),'paused');await page.unroute('**/evaluation');await click('#evaluation-end');
  // Heartbeat may refresh telemetry but must never silently adopt new label evidence.
  await page.locator('#evaluation-item').selectOption('q4');await idle();
  await page.locator('#evaluation-verdict').selectOption('error_found');await page.locator('#evaluation-note').fill('preserved stale basis input');await page.locator('#evaluation-scope input[value="body"]').check();await click('#evaluation-start');
  execFileSync(python,['-c',"import sys; from pathlib import Path; from _scripts_2.apps.exam.exam_processor.storage.store import Store; from _scripts_2.apps.exam.exam_processor.tests.evaluation_fixture import JOB_ID; s=Store(Path(sys.argv[1]),Path(sys.argv[1])/'exports'); f=s.job_dir(JOB_ID); r=s.reviews.get(f,'q4');r.note='synthetic external change';s.reviews.save(f,r)",data],{cwd:root});
  await page.evaluate(()=>evaluationRun(()=>evaluationMeasure('tick')));await idle();await click('#evaluation-save');
  assert.match(await page.locator('#evaluation-feedback').innerText(),/근거가 변경/);assert.equal(await page.locator('#evaluation-note').inputValue(),'preserved stale basis input');
  await click('#evaluation-reload');assert.equal(await page.locator('#evaluation-verdict').inputValue(),'error_found');assert(await page.locator('#evaluation-scope input[value="body"]').isChecked());
  await click('#evaluation-save');await click('#evaluation-end');assert.match(await page.locator('#evaluation-summary').innerText(),/무효화 2/);
  assert.deepEqual(errors,[]);
  console.log('R6 UI PASS: explicit labels, scope, save failure/input retention, correction, independent queue, measured intervals, duplicate window, issue FP, dataset reproducibility/source, R3/R4 entries, reload, 760px. '+temp);
 }finally{await browser?.close();server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
