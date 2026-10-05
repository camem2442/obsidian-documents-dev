/* Multi-job fixture execution only; no provider/network invocation. */
const {chromium}=require('playwright');
const {spawn}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../../../..'),python=process.env.STUDY_TOOLS_PYTHON||path.join(root,'_scripts_2/venv/bin/python');
(async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-batch-ui-'));
 const server=spawn(python,['-m','_scripts_2.apps.exam.exam_processor.tests.batch_jobs_fixture','--root',temp,'--port','0','--delay','.4','--fail-once'],{cwd:root,stdio:['ignore','pipe','pipe']});
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('startup timeout: '+output)),30000);server.stderr.on('data',chunk=>{output+=chunk;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0]);}});server.once('error',reject);});
  browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.goto(base);await page.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===5);
  const idle=()=>page.waitForFunction(()=>!multijobBusy&&!actionPending),click=async id=>{await require('./ui_helpers.cjs').reveal(page,id);await page.locator(id).click();await idle();};
  await click('#multijob-open');assert.match(await page.locator('#multijob-summary').innerText(),/계획 없음/);
  for(const box of await page.locator('#multijob-jobs input').all())await box.check();
  await click('#multijob-preview');assert.equal(await page.evaluate(()=>multijobPreview.plan.planned_calls),6);
  await click('#multijob-plan');assert.equal(await page.evaluate(()=>multijobView.attempts.length),0);assert(await page.locator('#multijob-execute').isDisabled());
  const bid=await page.evaluate(()=>multijobView.id);
  const stale=await context.newPage();await stale.goto(base);await stale.waitForFunction(()=>typeof job!=='undefined'&&!!job);
  await require('./ui_helpers.cjs').reveal(stale,'#multijob-open');await stale.locator('#multijob-open').click();await stale.waitForFunction(()=>!multijobBusy&&!actionPending);
  await stale.locator('#multijob-runs').selectOption(bid);await stale.waitForFunction(()=>!multijobBusy);await stale.locator('#multijob-confirm').check();
  await page.bringToFront();await page.locator('#multijob-confirm').check();await click('#multijob-execute');
  await page.waitForFunction(()=>multijobView?.status==='finished',null,{timeout:20000});
  await stale.locator('#multijob-execute').click();await stale.waitForFunction(()=>!multijobBusy);
  assert.match(await stale.locator('#multijob-feedback').innerText(),/다른 창|revision|변경/);assert(!await stale.locator('#multijob-confirm').isChecked());await stale.close();
  assert.equal(await page.evaluate(()=>multijobView.counts.failed),1);assert.equal(await page.evaluate(()=>multijobView.counts.completed),5);
  const failed=await page.evaluate(()=>multijobView.entries.find(e=>e.status==='failed').id);
  await page.locator('#multijob-retry-targets').selectOption([failed]);await page.locator('#multijob-reason').fill('Synthetic explicit retry');
  const failedEntry=await page.evaluate(()=>multijobView.entries.find(e=>e.status==='failed'));
  await page.locator('#multijob-result-filter').selectOption('failed');
  const targetSelector=`#multijob-entries [data-review-job="${failedEntry.job_id}"][data-review-item="${failedEntry.item}"]`;
  await require('./ui_helpers.cjs').reveal(page,targetSelector);
  await page.locator(targetSelector).click();await idle();
  assert.equal(await page.evaluate(()=>currentId),failedEntry.item);
  await click('#multijob-return');
  assert.equal(await page.locator('#multijob-result-filter').inputValue(),'failed');
  assert.deepEqual(await page.locator('#multijob-retry-targets').evaluate(el=>[...el.selectedOptions].map(o=>o.value)),[failed]);
  await page.locator('#multijob-result-filter').selectOption('all');
  await page.route('**/api/batches',async route=>{if(route.request().method()==='POST')await route.fulfill({status:500,json:{error:'fixture write failure'}});else await route.continue();});
  await click('#multijob-retry');assert.equal(await page.locator('#multijob-reason').inputValue(),'Synthetic explicit retry');assert.match(await page.locator('#multijob-feedback').innerText(),/유지/);await page.unroute('**/api/batches');
  assert(await page.locator('#multijob-retry').isDisabled());
  await click('#multijob-resend');await page.waitForFunction(()=>multijobView?.status==='finished'&&multijobView.counts.completed===6,null,{timeout:20000});
  assert.equal(await page.evaluate(()=>multijobView.attempts.length),7);
  const selected=await page.evaluate(()=>currentId);await click('#multijob-close');
  await page.getByRole('button',{name:'처리 대기 0',exact:true}).waitFor();assert.equal(await page.evaluate(()=>currentId),selected);
  await page.reload();await page.waitForFunction(()=>typeof job!=='undefined'&&!!job);await click('#multijob-open');await page.locator('#multijob-runs').selectOption(bid);await idle();
  assert.equal(await page.evaluate(()=>multijobView.counts.completed),6);
  for(const jid of await page.evaluate(()=>multijobView.plan.job_ids)){
   const j=await (await page.request.get(base+'/api/jobs/'+jid)).json();assert(j.items.filter(i=>i.kind==='question').every(i=>i.review==='pending'));
   const runtime=JSON.parse(fs.readFileSync(path.join(temp,'jobs',jid,'runtime.json'),'utf8'));assert(!runtime.evaluation?.labels?.length);
  }
  await page.setViewportSize({width:760,height:900});assert(await page.locator('#multijob-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:path.join(temp,'batch-summary.png'),fullPage:true});assert.deepEqual(errors,[]);console.log('MULTI JOB UI PASS',temp);
 }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
