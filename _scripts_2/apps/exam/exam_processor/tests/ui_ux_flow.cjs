/* UX-F1: actual UI changes, ambiguous committed requests, navigation and display. */
const {chromium}=require('playwright');
const {spawn}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'../../../../..'),python=process.env.STUDY_TOOLS_PYTHON||path.join(root,'_scripts_2/venv/bin/python');
(async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'exam-ux-f1-ui-'));
 const server=spawn(python,['-m','_scripts_2.apps.exam.exam_processor.tests.input_jobs_fixture','--root',temp,'--port','0'],{cwd:root,stdio:['ignore','pipe','pipe']});
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error(out)),30000);server.stderr.on('data',x=>{out+=x;const m=out.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});server.once('error',reject);});
  browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const ready=()=>page.waitForFunction(()=>typeof settings!=='undefined'&&!!settings?.token&&!actionPending);
  const idle=()=>page.waitForFunction(()=>!inputBusy&&!actionPending&&!multijobBusy);
  const click=async id=>{await idle();await require('./ui_helpers.cjs').reveal(page,id);await page.locator(id).click();await idle();};
  await page.goto(base);await ready();await click('#inputs-open');await click('#inputs-add');
  const row=page.locator('#inputs-units article');
  await row.locator('[data-field=format]').selectOption('kice_korean');
  await row.locator('[data-field=path]').fill(path.join(temp,'fixture.pdf'));
  await row.locator('[data-field=source]').fill('Saved A');await row.locator('[data-field=track]').selectOption('화법과 작문');
  await click('#inputs-preview');await click('#inputs-plan');await page.locator('#inputs-confirm').check();
  await click('#inputs-edit');await row.locator('[data-field=source]').fill('Draft B');
  assert(await page.locator('#inputs-execute').isDisabled());assert(!await page.locator('#inputs-confirm').isChecked());
  await click('#inputs-back');assert.match(await page.locator('#inputs-fixed').innerText(),/Saved A/);assert(!await page.locator('#inputs-confirm').isChecked());
  await page.locator('#inputs-confirm').check();
  let inputRequest;
  await page.route('**/api/input-plans',async r=>{if(r.request().method()==='POST'){inputRequest=r.request().postDataJSON();await r.fetch();await r.abort('failed');}else await r.continue();});
  await click('#inputs-execute');assert(await page.evaluate(()=>!!inputPending));
  await page.unroute('**/api/input-plans');await page.reload();await ready();await click('#inputs-open');
  assert.equal(await page.evaluate(()=>inputDraft.units[0].source),'Draft B');
  assert.deepEqual(await page.evaluate(()=>inputPending.payload),inputRequest);
  assert(await page.locator('#inputs-execute').isDisabled());await click('#inputs-resend');
  await page.waitForFunction(()=>inputView?.counts.completed===1);
  assert.equal(await page.evaluate(()=>inputView.attempts.length),1);
  await page.locator('#inputs-results input').check();await page.locator('#inputs-next-preview').click();
  await page.waitForFunction(()=>!!inputNext.audit&&!!inputNext.extract).catch(async e=>{console.log(await page.locator('#inputs-next-options').innerText(),errors);throw e;});
  assert.equal(await page.evaluate(()=>inputNext.extract.plan.planned_calls),0);
  assert.equal(await page.evaluate(()=>inputNext.audit.plan.planned_calls),2);
  assert.match(await page.locator('#inputs-next-options').innerText(),/본문 원본 영역 없음|기존.*이력|전사 revision/);
  const realPreview=await page.evaluate(()=>structuredClone(inputNext.audit));
  let matrix={extract:2,audit:0},pendingPreview=null,releasePreview=null;
  await page.route('**/batch-preview',async r=>{
   const op=r.request().postDataJSON().operation;
   if(pendingPreview){await pendingPreview;}
   if(matrix[op]==='failure')return r.fulfill({status:503,json:{error:'synthetic preview unavailable'}});
   const pre=structuredClone(realPreview);pre.plan.operation=op;pre.plan.planned_calls=matrix[op];pre.plan.entries=pre.plan.entries.slice(0,matrix[op]);
   await r.fulfill({json:pre});
  });
  for(const counts of [{extract:2,audit:0},{extract:2,audit:2},{extract:0,audit:0},{extract:0,audit:'failure'}]){
   matrix=counts;await page.locator('#inputs-next-preview').click();
   await page.waitForFunction(()=>!document.querySelector('#inputs-next-options').textContent.includes('조회 중'));
   const text=await page.locator('#inputs-next-options').innerText();
   assert.match(text,/AI 전사: 실행/);
   if(counts.audit==='failure')assert.match(text,/AI 원본 대조: 조회 실패/);
   else assert.equal(await page.evaluate(()=>inputNext.audit.plan.planned_calls),counts.audit);
   if(counts.extract===0&&counts.audit===0)assert.match(text,/처리할 항목 없음/);
  }
  matrix={extract:2,audit:2};pendingPreview=new Promise(resolve=>releasePreview=resolve);
  const delayedResponses=Promise.all(['extract','audit'].map(op=>page.waitForResponse(r=>r.url().endsWith('/batch-preview')&&r.request().postDataJSON().operation===op)));
  await page.locator('#inputs-next-preview').click();await page.locator('#inputs-results input').uncheck();
  const changedMessage=await page.locator('#inputs-next-options').innerText();releasePreview();pendingPreview=null;
  await Promise.all((await delayedResponses).map(r=>r.finished()));await page.evaluate(()=>new Promise(requestAnimationFrame));assert.equal(await page.locator('#inputs-next-options').innerText(),changedMessage);
  await page.unroute('**/batch-preview');await page.locator('#inputs-results input').check();
  await page.locator('#inputs-operation').selectOption('audit');await click('#inputs-batch');
  assert.match(await page.locator('#multijob-preview-result').innerText(),/Saved A.*문항 1/);
  await click('#multijob-plan');await page.locator('#multijob-confirm').check();await click('#multijob-edit');
  await page.locator('#multijob-operation').selectOption('extract');assert(await page.locator('#multijob-execute').isDisabled());
  await click('#multijob-back');assert(!await page.locator('#multijob-confirm').isChecked());
  await page.locator('#multijob-confirm').check();
  let batchRequest;
  await page.route('**/api/batches',async r=>{if(r.request().method()==='POST'){batchRequest=r.request().postDataJSON();await r.fetch();await r.abort('failed');}else await r.continue();});
  await click('#multijob-execute');assert(await page.evaluate(()=>!!multijobPending));
  await page.unroute('**/api/batches');await page.reload();await ready();
  // The initial page may open an existing job automatically.
  await click('#multijob-open');assert.deepEqual(await page.evaluate(()=>multijobPending.payload),batchRequest);
  await click('#multijob-resend');await page.waitForFunction(()=>multijobView?.counts.completed===2);
  assert.equal(await page.evaluate(()=>multijobView.attempts.length),2);
  const finished=await page.evaluate(()=>structuredClone(multijobView));let pollCount=0;
  await page.route('**/api/batches/'+finished.id,async r=>{pollCount++;await r.fulfill({json:{...finished,status:'running',worker_live:true}});});
  await require('./ui_helpers.cjs').reveal(page,'#multijob-reason');await page.locator('#multijob-reason').fill('Polling must retain this focus');
  const focusScroll=await page.locator('#multijob-dialog').evaluate(el=>el.scrollTop);
  await page.evaluate(()=>{multijobView.status='running';});
  await page.waitForResponse(r=>r.url().endsWith('/api/batches/'+finished.id));await page.waitForFunction(()=>!multijobPolling);
  assert(pollCount>0);assert.equal(await page.evaluate(()=>document.activeElement.id),'multijob-reason');
  assert.equal(await page.locator('#multijob-dialog').evaluate(el=>el.scrollTop),focusScroll);
  await page.unroute('**/api/batches/'+finished.id);await click('#multijob-reload');
  await page.locator('#multijob-entries summary').click();await page.waitForFunction(()=>Object.keys(multijobLabels.items).length>=2);
  const target=await page.locator('#multijob-entries [data-review-item]').last().getAttribute('data-review-item');
  await page.locator('#multijob-entries [data-review-item]').last().click();await idle();
  assert.equal(await page.evaluate(()=>currentId),target);
  assert.equal(await page.evaluate(()=>job.items[0].review),'pending');
  await page.reload();await ready();await page.waitForFunction(id=>typeof currentId!=='undefined'&&currentId===id,target);
  assert(await page.locator('#multijob-return').isVisible());
  const returnScroll=await page.evaluate(()=>multijobReturn.scroll);
  await click('#multijob-return');assert(await page.locator('#multijob-dialog').isVisible());
  assert(returnScroll>0);assert(Math.abs(await page.locator('#multijob-dialog').evaluate(el=>el.scrollTop)-returnScroll)<=1);
  await page.locator('#multijob-entries [data-review-item]').last().click();await idle();
  const normalDimensions=[];
  for(const [width,height] of [[1440,900],[1600,900],[1024,768],[390,844]]){
   await page.setViewportSize({width,height});await page.evaluate(()=>window.scrollTo(0,0));
   // Wider intrinsic font metrics must not push desktop controls onto a second row.
   for(const font of width>=1440?['default','monospace']:['default']){
    const style=font==='monospace'?await page.addStyleTag({content:':root{font-family:monospace}'}):null;
    try{
     const size=await page.evaluate(()=>{
      const rect=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};};
      const header=document.querySelector('header');
      return {width:innerWidth,height:innerHeight,y:document.querySelector('.compare').getBoundingClientRect().y,
              body:document.querySelector('#preview').getBoundingClientRect().height,
              overflow:document.documentElement.scrollWidth>innerWidth,header:rect(header),
              headerRegions:[...header.children].map(rect),
              headerControls:[...document.querySelector('.header-actions').children].map(e=>rect(e.matches('details')?e.querySelector('summary'):e))};
     });
     console.log('UX desktop geometry '+JSON.stringify({font,...size}));
     assert(!size.overflow,JSON.stringify(size));
     if(width>=1440){
      assert(Math.max(...size.headerRegions.map(r=>r.top))<Math.min(...size.headerRegions.map(r=>r.bottom)),
             'desktop header regions must share one row: '+JSON.stringify(size));
      for(const r of size.headerControls)assert(r.left>=0&&r.right<=width&&r.bottom>r.top,JSON.stringify(size));
      assert(size.y<=360,JSON.stringify(size));assert(size.body>=450,JSON.stringify(size));
     }
     if(width===1024)assert(size.body>=360,JSON.stringify(size));
     normalDimensions.push({font,...size});
    }finally{if(style)await style.evaluate(e=>e.remove());}
   }
  }
  fs.writeFileSync(path.join(temp,'normal-dimensions.json'),JSON.stringify(normalDimensions,null,2));
  await page.setViewportSize({width:1440,height:900});
  const originalJob=await page.evaluate(()=>job.id),originalItem=await page.evaluate(()=>currentId);
  await click('[data-section=body]');await click('#edit-tab');
  await page.route('**/api/jobs/*/items/*',r=>r.request().method()==='POST'?r.fulfill({status:500,json:{error:'fixture save failure'}}):r.continue());
  await page.locator('#editor').fill('Unsaved UX failure draft');
  await click('#multijob-return');
  assert(!await page.locator('#multijob-dialog').isVisible());
  assert.equal(await page.locator('#editor').inputValue(),'Unsaved UX failure draft');
  assert(await page.evaluate(()=>dirty));assert.equal(await page.evaluate(()=>currentId),originalItem);
  await page.unroute('**/api/jobs/*/items/*');await click('#save');
  await page.route('**/api/batches/*',r=>r.fulfill({status:503,json:{error:'fixture read failure'}}));
  await click('#multijob-return');assert(!await page.locator('#multijob-dialog').isVisible());
  assert.equal(await page.evaluate(()=>job.id),originalJob);assert.equal(await page.evaluate(()=>currentId),originalItem);
  assert.equal(await page.locator('#editor').inputValue(),'Unsaved UX failure draft');
  await page.unroute('**/api/batches/*');await click('#multijob-return');
  await page.route('**/api/jobs/'+originalJob,async r=>{const response=await r.fetch(),j=await response.json();j.items=j.items.filter(i=>i.id!==originalItem);await r.fulfill({response,json:j});});
  await page.locator('#multijob-entries [data-review-item]').last().click();await idle();
  assert(await page.locator('#multijob-dialog').isVisible());assert.equal(await page.evaluate(()=>currentId),originalItem);
  await page.unroute('**/api/jobs/'+originalJob);await click('#multijob-close');
  const dimensions=[];
  for(const [width,height] of [[1440,900],[1600,900],[1024,768],[390,844]]){
   await page.setViewportSize({width,height});await page.evaluate(()=>window.scrollTo(0,0));
   dimensions.push(await page.evaluate(()=>({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,comparison:(()=>{const r=document.querySelector('.compare').getBoundingClientRect();return {y:r.y,height:r.height};})()})));
   await page.screenshot({path:path.join(temp,`review-${width}.png`),fullPage:true});
  }
  fs.writeFileSync(path.join(temp,'dimensions.json'),JSON.stringify(dimensions,null,2));
  assert.deepEqual(errors,[]);console.log('UX FLOW A-E PASS',temp,dimensions);
 }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
