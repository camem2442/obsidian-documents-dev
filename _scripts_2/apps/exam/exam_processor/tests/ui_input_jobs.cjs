/* Explicit synthetic inputs -> canonical job -> existing batch -> review UI. */
const {chromium}=require('playwright');
const {spawn}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../../../..'),python=process.env.STUDY_TOOLS_PYTHON||path.join(root,'_scripts_2/venv/bin/python');
(async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-input-ui-'));
 const server=spawn(python,['-m','_scripts_2.apps.exam.exam_processor.tests.input_jobs_fixture','--root',temp,'--port','0','--delay','.6','--fail-once'],{cwd:root,stdio:['ignore','pipe','pipe']});
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error(out)),30000);server.stderr.on('data',x=>{out+=x;const m=out.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});server.once('error',reject);});
  browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1050}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.goto(base);await page.waitForFunction(()=>typeof settings!=='undefined'&&!!settings?.token&&!actionPending);
  const idle=()=>page.waitForFunction(()=>!inputBusy&&!actionPending&&!multijobBusy),click=async id=>{await idle();await require('./ui_helpers.cjs').reveal(page,id);await page.locator(id).click();await idle();};
  await click('#inputs-open');await click('#inputs-preview');assert.match(await page.locator('#inputs-preview-result').innerText(),/0건/);
  for(const source of ['Synthetic first','Synthetic fail once']){
   await click('#inputs-add');const row=page.locator('#inputs-units article').last();await row.locator('[data-field=format]').selectOption('kice_korean');
   await row.locator('[data-field=path]').fill(path.join(temp,'fixture.pdf'));await row.locator('[data-field=source]').fill(source);await row.locator('[data-field=track]').selectOption('화법과 작문');
  }
  await click('#inputs-preview');assert.equal(await page.evaluate(()=>inputPreview.plan.included.length),2);
  await page.route('**/api/input-plans',async r=>{if(r.request().method()==='POST')await r.fulfill({status:500,json:{error:'synthetic storage failure'}});else await r.continue();});
  await click('#inputs-plan');assert.equal(await page.locator('#inputs-units article').count(),2);assert(await page.evaluate(()=>!!inputPending));
  await page.unroute('**/api/input-plans');await page.reload();await page.waitForFunction(()=>typeof settings!=='undefined'&&!!settings?.token&&!actionPending);await click('#inputs-open');assert.equal(await page.locator('#inputs-units article').count(),2);
  await click('#inputs-resend');assert.equal(await page.evaluate(()=>inputView.attempts.length),0);assert(!fs.existsSync(path.join(temp,'data/jobs')));assert(await page.locator('#inputs-execute').isDisabled());
  const pid=await page.evaluate(()=>inputView.id);
  const second=await context.newPage();await second.goto(base);await second.waitForFunction(()=>typeof settings!=='undefined'&&!!settings?.token&&!actionPending);await second.locator('#inputs-open').click();await second.waitForFunction(()=>!inputBusy);await second.locator('#inputs-runs').selectOption(pid);await second.waitForFunction(()=>!inputBusy);
  await page.bringToFront();await page.locator('#inputs-confirm').check();await click('#inputs-execute');await page.waitForFunction(()=>inputView?.status==='finished',null,{timeout:30000});
  assert.equal(await page.evaluate(()=>inputView.counts.completed),1);assert.equal(await page.evaluate(()=>inputView.counts.failed),1);
  await second.locator('#inputs-confirm').check();await second.locator('#inputs-execute').click();await second.waitForFunction(()=>!inputBusy);assert.match(await second.locator('#inputs-feedback').innerText(),/다른 창/);await second.close();
  await page.locator('#inputs-results article').filter({hasText:'Synthetic fail once'}).locator('input').check();await page.locator('#inputs-reason').fill('명시 합성 재시도');await click('#inputs-retry');
  await page.waitForFunction(()=>inputView?.counts.completed===2,null,{timeout:20000});assert.equal(await page.evaluate(()=>inputView.attempts.length),3);
  for(const checkbox of await page.locator('#inputs-results input').all())await checkbox.check();
  await page.locator('#inputs-operation').selectOption('audit');await click('#inputs-batch');assert.equal(await page.evaluate(()=>multijobPreview.plan.job_ids.length),2);assert(await page.evaluate(()=>multijobPreview.plan.planned_calls>0));
  await page.locator('#multijob-plan').click();await idle();assert.equal(await page.evaluate(()=>multijobView.attempts.length),0);
  await page.locator('#multijob-confirm').check();await page.locator('#multijob-execute').click();await idle();await page.waitForFunction(()=>multijobView?.status==='finished',null,{timeout:30000});
  assert.equal(await page.evaluate(()=>multijobView.counts.failed),0);assert(await page.evaluate(()=>multijobView.counts.completed>0));
  await page.locator('#multijob-close').click();await idle();await click('#inputs-open');await click('#inputs-reload');
  await page.locator('#inputs-results [data-job]').first().click();await page.waitForFunction(()=>!!job&&!!currentId&&!actionPending);assert.equal(await page.evaluate(()=>job.items[0].review),'pending');
  await click('#inputs-origin');assert.equal(await page.evaluate(()=>inputView.id),pid);
  await page.setViewportSize({width:390,height:844});assert(await page.locator('#inputs-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth+1));await page.screenshot({path:path.join(temp,'input-narrow.png'),fullPage:true});
  await page.reload();await page.waitForFunction(()=>typeof settings!=='undefined'&&!!settings?.token&&!actionPending);await click('#inputs-open');await page.waitForFunction(id=>inputView?.id===id&&!inputBusy,pid);assert.equal(await page.evaluate(()=>inputView.counts.completed),2);
  assert.deepEqual(errors,[]);console.log('INPUT JOBS UI PASS',temp);
 }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
