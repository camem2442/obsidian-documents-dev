/* EX-N01 synthetic UI: explicit confirmation, interruption/reopen and recovery. */
const {chromium}=require('playwright');
const {spawn}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../../../..');
const python=process.env.STUDY_TOOLS_PYTHON||path.join(root,'_scripts_2/venv/bin/python');
(async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-export-ui-'));
 let browser,server;
 const start=()=>new Promise((resolve,reject)=>{
  server=spawn(python,['-m','_scripts_2.apps.exam.exam_processor.tests.export_jobs_fixture','--root',temp,'--port','0'],{cwd:root,stdio:['ignore','pipe','pipe']});
  let output='';const timer=setTimeout(()=>reject(Error('startup timeout: '+output)),30000);
  server.stderr.on('data',chunk=>{output+=chunk;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0]);}});
  server.once('error',e=>{clearTimeout(timer);reject(e);});
 });
 try{
  const base=await start();browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.goto(base);
  const idle=()=>page.waitForFunction(()=>typeof exportBusy!=='undefined'&&!exportBusy);
  const open=async()=>{await require('./ui_helpers.cjs').reveal(page,'#exports-open');await page.locator('#exports-open').click();await idle();};
  await page.waitForFunction(()=>typeof job!=='undefined'&&!!job);await open();
  assert.match(await page.locator('#exports-jobs').innerText(),/전사 대기/);
  for(const box of await page.locator('#exports-jobs input').all())await box.check();
  await page.locator('#exports-preview').click();await idle();
  assert.match(await page.locator('#exports-preview-result').innerText(),/포함 2/);
  await page.locator('#exports-plan').click();await idle();
  assert(await page.locator('#exports-execute').isDisabled());
  const id=await page.evaluate(()=>exportView.id);
  // Synthetic-only visual evidence travels with the exact CI job log.
  const snapshot=async name=>console.log('EX_N01_VISUAL '+JSON.stringify({name,viewport:page.viewportSize(),png_base64:(await page.screenshot()).toString('base64')}));
  await page.locator('#exports-confirm').scrollIntoViewIfNeeded();
  const checkboxBox=await page.locator('#exports-confirm').boundingBox();
  console.log('EX_N01_CHECKBOX_GEOMETRY '+JSON.stringify(checkboxBox));
  assert(checkboxBox.width<32&&checkboxBox.height<32,'checkbox must remain compact');
  await snapshot('desktop-plan');
  await page.locator('#exports-confirm').check();await page.locator('#exports-close').click();
  await open();assert(await page.locator('#exports-execute').isDisabled());
  await page.locator('#exports-confirm').check();await page.locator('#exports-execute').click();await idle();
  assert.equal(await page.locator('#exports-results article').count(),2);
  assert.match(await page.locator('#exports-results').innerText(),/완료/);
  await page.locator('#exports-results').scrollIntoViewIfNeeded();await snapshot('desktop-result');
  await page.setViewportSize({width:390,height:844});await page.locator('#exports-results').scrollIntoViewIfNeeded();await snapshot('mobile-result');
  assert(await page.locator('#exports-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth+1),'mobile export results must not overflow');
  await page.setViewportSize({width:1440,height:1000});
  assert(await page.locator('#exports-execute').isDisabled());
  const before=JSON.stringify(fs.readdirSync(path.join(temp,'exports')));
  await page.reload();await page.waitForFunction(()=>typeof job!=='undefined'&&!!job);await open();
  await page.locator('#exports-runs').selectOption(id);await idle();
  await page.locator('#exports-recover').click();await idle();
  assert.match(await page.locator('#exports-feedback').innerText(),/재출력 없음/);
  assert.equal(JSON.stringify(fs.readdirSync(path.join(temp,'exports'))),before);
  await page.locator('#exports-results button').first().click();await idle();
  assert(!(await page.locator('#exports-dialog').isVisible()));
  server.kill('SIGTERM');await new Promise(resolve=>server.once('exit',resolve));
  const restarted=await start();await page.goto(restarted);await page.waitForFunction(()=>typeof job!=='undefined'&&!!job);await open();
  await page.locator('#exports-runs').selectOption(id);await idle();await page.locator('#exports-recover').click();await idle();
  assert.match(await page.locator('#exports-results').innerText(),/완료/);assert.deepEqual(errors,[]);
  console.log('EX-N01 browser workflow PASS');
 }finally{if(browser)await browser.close();if(server)server.kill('SIGTERM');fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
