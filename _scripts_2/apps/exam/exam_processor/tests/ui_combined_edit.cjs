const assert=require('assert');
module.exports=async function(page,click,idle){
  const original=await page.evaluate(()=>({body:item().body,solution:item().solution}));
  await click('#edit-tab');
  assert(await page.locator('#combined-editor').isVisible());
  assert(await page.locator('#editor').isHidden());
  const body='문제 수정 $x+1$\n\n'+Array(30).fill('이어지는 문제 문장').join('\n');
  const solution='해설 수정 $x=2$\n\n정답: ②';
  await page.locator('#body-editor').fill(body);
  await page.locator('#solution-editor').fill(solution);
  await page.evaluate(()=>clearTimeout(timer));
  await click('#preview-tab');
  await page.waitForFunction(()=>!document.querySelector('#preview').textContent.includes('계산 중'));
  assert((await page.locator('#preview').innerText()).includes('해설 수정'));
  assert((await page.locator('#preview').innerText()).includes('문제 수정'));
  await click('#edit-tab');
  // Both drafts survive conflict, preview and attempted section navigation.
  await page.route('**/api/jobs/*/items/*',route=>route.request().method()==='POST'?route.fulfill({status:409,json:{error:'검사 저장 충돌'}}):route.continue());
  await click('#save');
  assert((await page.locator('#save-state').innerText()).includes('저장 실패'));
  await click('[data-section="body"]');
  assert.equal(await page.locator('[data-section="all"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#body-editor').inputValue(),body);
  assert.equal(await page.locator('#solution-editor').inputValue(),solution);
  await page.unroute('**/api/jobs/*/items/*');
  const writes=[];
  await page.route('**/api/jobs/*/items/*',async route=>{if(route.request().method()==='POST')writes.push(route.request().postDataJSON());await route.continue();});
  await click('#save');
  assert.equal(writes.length,1);
  assert.equal(writes[0].values.body,body);assert.equal(writes[0].values.solution,solution);
  await page.unroute('**/api/jobs/*/items/*');
  await click('[data-section="solution"]');assert.equal(await page.locator('#editor').inputValue(),solution);
  await click('[data-section="all"]');assert(await page.locator('#combined-editor').isVisible());
  for(const width of [1440,900,390]){
    await page.setViewportSize({width,height:1000});
    const sizes=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,
      editors:['body-editor','solution-editor'].map(id=>{const e=document.getElementById(id);return e.scrollHeight<=e.clientHeight+2;}),
      single:document.getElementById('combined-editor').scrollHeight>document.getElementById('combined-editor').clientHeight}));
    assert(!sizes.overflow&&sizes.editors.every(Boolean)&&sizes.single,JSON.stringify({width,...sizes}));
  }
  await page.setViewportSize({width:1440,height:1000});
  await click('[data-editor-jump="solution"]');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'solution-editor');
  assert(await page.evaluate(()=>{const p=document.getElementById('combined-editor').getBoundingClientRect(),e=document.getElementById('solution-editor').getBoundingClientRect();return e.top>=p.top&&e.top<p.bottom;}));
  assert((await page.locator('#normalize').getAttribute('title')).includes('해설'));
  const beforeScroll=await page.locator('#combined-editor').evaluate(el=>el.scrollTop);
  await page.evaluate(()=>resizeEditors());
  assert.equal(await page.locator('#combined-editor').evaluate(el=>el.scrollTop),beforeScroll);
  const normalization=[];
  await page.route('**/normalize',async route=>{normalization.push(route.request().postDataJSON());await route.continue();});
  await click('#normalize');assert.equal(normalization[0].section,'solution');
  await page.unroute('**/normalize');
  assert.equal(await page.evaluate(()=>item().body),body);
  // In-flight autosave must not replace newer edits in either field.
  let release,arrived;
  const blocked=new Promise(resolve=>release=resolve),seen=new Promise(resolve=>arrived=resolve);
  let first=true;
  await page.route('**/api/jobs/*/items/*',async route=>{if(first&&route.request().method()==='POST'){first=false;arrived();await blocked;}await route.continue();});
  await page.locator('#body-editor').fill('먼저 저장');await page.locator('#save').click();await seen;
  await page.locator('#body-editor').fill('저장 중 문제 수정');await page.locator('#solution-editor').fill('저장 중 해설 수정');
  release();await idle();
  assert.deepEqual(await page.evaluate(()=>({body:item().body,solution:item().solution})),{body:'저장 중 문제 수정',solution:'저장 중 해설 수정'});
  await page.unroute('**/api/jobs/*/items/*');
  await page.locator('#body-editor').fill(original.body);await page.locator('#solution-editor').fill(original.solution);await click('#save');
  await click('#preview-tab');
  console.log('Combined edit: atomic save, drafts/conflict, in-flight edits, preview, filters, normalization, 3 viewports PASS');
};
