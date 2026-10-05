// Follow the real disclosure path before operating relocated controls.
async function reveal(page, selector){
 const target=page.locator(selector);
 for(const details of await target.locator('xpath=ancestor::details').all()){
  if(!await details.evaluate(e=>e.open))await details.locator(':scope > summary').click();
 }
}
module.exports={reveal};
