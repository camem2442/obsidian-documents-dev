/* Automated isolated R5 UI; real visible observation is a separate acceptance layer. */
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../../../..'),python=process.env.STUDY_TOOLS_PYTHON||path.join(root,'_scripts_2/venv/bin/python');
(async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-r5-ui-')),data=path.join(temp,'data');
 execFileSync(python,['-c','import sys; from _scripts_2.apps.exam.exam_processor.tests.bulk_fixture import create_fixture; create_fixture(sys.argv[1])',data],{cwd:root});
 const server=spawn(path.resolve(__dirname,'../run.sh'),['--port','0'],{env:{...process.env,EXAM_PROCESSOR_DATA:data,EXAM_PROCESSOR_OUTPUT:path.join(temp,'exports')},stdio:['ignore','pipe','inherit']});
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('startup timeout')),60000);server.stdout.on('data',chunk=>{output+=chunk;const m=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});server.once('error',reject);});
  browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===5);
  const idle=()=>page.waitForFunction(()=>!actionPending&&!sampleBusy&&!bulkBusy);
  const click=async id=>{await require('./ui_helpers.cjs').reveal(page,id);await page.locator(id).click();await idle();};
  await click('#sample-queue');await click('#sample-bulk');assert(await page.locator('#bulk-dialog').isVisible());assert(await page.locator('#bulk-confirm').evaluate(el=>el.getBoundingClientRect().width<25));
  assert.match(await page.locator('#bulk-basis').innerText(),/모든 표본 통과/);
  await click('#bulk-plan');assert.match(await page.locator('#bulk-feedback').innerText(),/1건 이상/);
  await page.locator('#bulk-targets input[value="q1"]').check();await page.locator('#bulk-targets input[value="q2"]').check();await click('#bulk-plan');
  assert.match(await page.locator('#bulk-confirmed-list').innerText(),/확인 대상 2건/);
  assert.equal(await page.locator('#qc-approved').innerText(),'1');assert(await page.locator('#bulk-execute').isDisabled());
  await page.locator('#bulk-confirm').check();await page.locator('#bulk-targets input[value="q2"]').uncheck();assert(await page.locator('#bulk-execute').isDisabled());
  await page.locator('#bulk-targets input[value="q2"]').check();await click('#bulk-plan');await page.locator('#bulk-confirm').check();
  await page.route('**/bulk-approval',async route=>{if(route.request().method()==='POST'&&route.request().postDataJSON().action==='execute')await route.fulfill({status:500,json:{error:'fixture 저장 실패'}});else await route.continue();});
  await click('#bulk-execute');assert.match(await page.locator('#bulk-feedback').innerText(),/성공으로 처리하지 않았습니다/);assert.equal(await page.locator('#qc-approved').innerText(),'1');
  await page.unroute('**/bulk-approval');await click('#bulk-reload');assert(await page.locator('#bulk-execute').isDisabled());
  await page.locator('#bulk-confirm').check();await click('#bulk-execute');assert.match(await page.locator('#bulk-progress').innerText(),/실행 완료 · 완료 2/);
  assert.equal(await page.locator('#qc-approved').innerText(),'3');assert.equal(await page.locator('#qc-green').innerText(),'2');assert.match(await page.locator('#bulk-basis').innerText(),/무효/);
  assert(await page.locator('#bulk-execute').isDisabled());await click('#bulk-close');
  assert.equal(await page.evaluate(()=>currentId),await page.evaluate(()=>sampleId()));
  await click('#sample-queue');assert.match(await page.locator('#sample-progress').innerText(),/무효/);await click('#sample-close');
  await page.reload();await page.waitForFunction(()=>typeof job!=='undefined'&&job?.items.length===5);await click('#bulk-queue');assert.match(await page.locator('#bulk-progress').innerText(),/실행 완료/);
  // A new explicit isolated fixture with an actual approval/checkpoint failure.
  execFileSync(python,['-c',`import sys
from unittest.mock import patch
from _scripts_2.apps.exam.exam_processor.tests.bulk_fixture import create_fixture,JOB_ID
from _scripts_2.apps.exam.exam_processor.storage.bulk_approval import BulkApproval
store=create_fixture(sys.argv[1]); svc=BulkApproval(store)
v=svc.get(JOB_ID); v=svc.act(JOB_ID,dict(action='plan',targets=['q1','q2'],revision=v['revision'],basis_token=v['basis_token']));p=v['plans'][-1]
original=store.runtime.save
def fail(folder,runtime):
 runs=runtime.bulk_approval.get('executions',[])
 if runs and runs[-1]['items']['q1']['status']=='done': raise OSError('explicit UI checkpoint failure')
 return original(folder,runtime)
try:
 with patch.object(store.runtime,'save',side_effect=fail): svc.act(JOB_ID,dict(action='execute',revision=v['revision'],plan_id=p['id'],targets=p['targets'],confirmed=True,confirmation_token=p['confirmation_token']))
except OSError: pass
`,data],{cwd:root});
  await click('#bulk-reload');assert.match(await page.locator('#bulk-progress').innerText(),/복구 확인 필요/);assert(await page.locator('#bulk-retry').isDisabled());
  await click('#bulk-recover');assert.match(await page.locator('#bulk-progress').innerText(),/부분 완료 · 재검사 필요 · 완료 1/);
  const runPlan=await page.evaluate(()=>bulkRun().plan_id);await page.locator('#bulk-plans').selectOption(runPlan);await page.locator('#bulk-confirm').check();await click('#bulk-retry');
  assert.match(await page.locator('#bulk-progress').innerText(),/실행 완료 · 완료 2/);assert.equal(await page.locator('#qc-approved').innerText(),'3');
  await page.locator('#bulk-results [data-detail="q1"]').click();assert.equal(await page.evaluate(()=>currentId),'q1');
  await page.evaluate(()=>{reviewTierFilter='GREEN';$('filter').value='pending';render();});assert.match(await page.locator('#items').innerText(),/필터 밖/);
  await click('#bulk-queue');await page.setViewportSize({width:760,height:900});assert(await page.locator('#bulk-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:path.join(temp,'r5-completed.png'),fullPage:true});assert.deepEqual(errors,[]);
  console.log('R5 UI PASS: R2/R4 entry, exact confirmation/reset, preview/no approval, failure/no false success, execution, queue/filter/selection, stale R4, reload, real checkpoint recovery/retry, detail and responsive. '+temp);
 }finally{await browser?.close();server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
