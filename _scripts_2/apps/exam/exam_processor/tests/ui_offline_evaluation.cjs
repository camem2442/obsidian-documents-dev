/* R7 automated offline fixture checks; actual observed UI is recorded separately. */
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../../../..'),python=process.env.STUDY_TOOLS_PYTHON||path.join(root,'_scripts_2/venv/bin/python');
(async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-r7-ui-')),data=path.join(temp,'data');
 execFileSync(python,['-c','import sys; from _scripts_2.apps.exam.exam_processor.tests.offline_fixture import create_fixture; create_fixture(sys.argv[1])',data],{cwd:root});
 const server=spawn(path.resolve(__dirname,'../run.sh'),['--port','0'],{env:{...process.env,EXAM_PROCESSOR_DATA:data,EXAM_PROCESSOR_OUTPUT:path.join(temp,'exports')},stdio:['ignore','pipe','inherit']});
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('startup timeout')),60000);server.stdout.on('data',chunk=>{output+=chunk;const m=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});server.once('error',reject);});
  browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.goto(base);await page.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===5);
  const idle=()=>page.waitForFunction(()=>!actionPending&&!offlineBusy),click=async id=>{await require('./ui_helpers.cjs').reveal(page,id);await page.locator(id).click();await idle();};
  const queue=await page.evaluate(()=>JSON.stringify(job.review_queue_summary));
  await click('#offline-open');assert.match(await page.locator('#offline-summary').innerText(),/저장된 실행/);
  await click('#offline-create');assert.match(await page.locator('#offline-summary').innerText(),/선정 4 \/ 제외 6/);
  const run=await page.evaluate(()=>offlineView);assert.equal(run.report.agreement.value,null);
  const inputs=await (await page.request.get(base+await page.locator('#offline-links a').first().getAttribute('href'))).json();
  assert(!JSON.stringify(inputs).includes('HUMAN_NOTE_SECRET'));assert(!JSON.stringify(inputs).includes('ground_truth'));
  await click('#offline-template');const template=JSON.parse(await page.locator('#offline-responses').inputValue());assert(template.responses.every(x=>x.outcome===''));assert(!JSON.stringify(template).includes('HUMAN_NOTE_SECRET'));
  const outcomes={synthetic_case_0:'actual_error',synthetic_case_1:'false_positive',synthetic_case_2:'false_positive',synthetic_case_3:'actual_error'};
  const bundle={...template,response_origin:'synthetic_fixture',producer:'ui-fixed/1',responses:run.plan.cases.map(c=>({case_id:c.id,input_hash:c.input_hash,outcome:outcomes[c.input.issue.message],reason:'Synthetic UI response only'}))};
  const full=JSON.stringify(bundle);await page.locator('#offline-responses').fill(full);
  await page.route('**/offline-evaluation',async route=>{if(route.request().method()==='POST'&&route.request().postDataJSON().action==='import')await route.fulfill({status:500,json:{error:'fixture write failure'}});else await route.continue();});
  await click('#offline-import');assert.equal(await page.locator('#offline-responses').inputValue(),full);assert.match(await page.locator('#offline-feedback').innerText(),/입력은 유지/);await page.unroute('**/offline-evaluation');
  await click('#offline-pause');assert(await page.locator('#offline-import').isDisabled());assert.equal(await page.locator('#offline-responses').inputValue(),full);
  await click('#offline-resume');
  const partial=structuredClone(bundle);partial.responses=partial.responses.slice(0,2);partial.responses[0].outcome='failed';
  await page.locator('#offline-responses').fill(JSON.stringify(partial));await click('#offline-import');assert.match(await page.locator('#offline-summary').innerText(),/비교 1 \/ 미응답 2 \/ 유보 0 \/ 실패 1/);
  const rest=structuredClone(bundle);rest.responses=rest.responses.filter(x=>x.case_id!==partial.responses[1].case_id);
  await page.locator('#offline-responses').fill(JSON.stringify(rest));await click('#offline-import');assert.match(await page.locator('#offline-feedback').innerText(),/재시도에는 사유/);
  // A second window changes the run; stale first-window draft is preserved through requery.
  const other=await context.newPage();await other.goto(base);await other.waitForFunction(()=>typeof job!=='undefined'&&!!job);await require('./ui_helpers.cjs').reveal(other,'#offline-open');await other.locator('#offline-open').click();await other.waitForFunction(()=>!offlineBusy&&!actionPending);
  await other.locator('#offline-runs').selectOption(run.id);await other.waitForFunction(()=>!offlineBusy);await other.locator('#offline-pause').click();await other.waitForFunction(()=>!offlineBusy);
  await page.locator('#offline-retry-reason').fill('Retry fixed failed response');await click('#offline-import');assert.match(await page.locator('#offline-feedback').innerText(),/다른 창/);
  await click('#offline-reload');assert.equal(await page.locator('#offline-responses').inputValue(),JSON.stringify(rest));assert(await page.locator('#offline-import').isDisabled());
  await click('#offline-resume');await click('#offline-import');assert.match(await page.locator('#offline-report').innerText(),/일치 2 \/ 4/);
  assert.deepEqual(await page.evaluate(()=>offlineView.report.matrix),{tp:1,fp:1,tn:1,fn:1});assert.equal(await page.evaluate(()=>offlineView.attempts.length),5);assert.equal(await page.locator('#offline-responses').inputValue(),'');await other.close();
  await click('#offline-create');assert.equal(await page.evaluate(()=>offlineView.plan_hash),run.plan_hash);assert.notEqual(await page.evaluate(()=>offlineView.id),run.id);
  await page.locator('#offline-runs').selectOption(run.id);await idle();assert.match(await page.locator('#offline-report').innerText(),/일치 2 \/ 4/);
  await page.reload();await page.waitForFunction(()=>typeof job!=='undefined'&&!!job);await click('#offline-open');await page.locator('#offline-runs').selectOption(run.id);await idle();assert.match(await page.locator('#offline-report').innerText(),/일치 2 \/ 4/);
  assert.equal(await page.evaluate(()=>JSON.stringify(job.review_queue_summary)),queue);
  await page.setViewportSize({width:760,height:900});assert(await page.locator('#offline-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));await page.screenshot({path:path.join(temp,'r7-summary.png'),fullPage:true});
  assert.deepEqual(errors,[]);console.log('R7 OFFLINE UI PASS',temp);
 }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exit(1);});
