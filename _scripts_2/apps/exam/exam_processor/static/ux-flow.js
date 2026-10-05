/* Presentation focus handling only; dialogs retain their own data/operation guards. */
let flowLastTrigger=null;
document.addEventListener('click',e=>{const button=e.target.closest('button,summary');if(button)flowLastTrigger=button;},true);
for(const dialog of document.querySelectorAll('dialog')){
 let opener=null;
 dialog.addEventListener('beforetoggle',e=>{if(e.newState==='open')opener=document.activeElement===document.body?flowLastTrigger:document.activeElement;});
 dialog.addEventListener('close',()=>{
  let target=opener?.isConnected?opener:flowLastTrigger;
  const closedDetails=target?.closest('details:not([open])');
  if(closedDetails)target=closedDetails.querySelector('summary');
  if(target?.isConnected&&!target.disabled&&!target.closest('dialog:not([open])'))target.focus();
 });
}
const toolsMenu=$('review-tools');
toolsMenu.addEventListener('keydown',e=>{if(e.key==='Escape'){toolsMenu.open=false;toolsMenu.querySelector('summary').focus();}});
document.addEventListener('click',e=>{if(!toolsMenu.contains(e.target))toolsMenu.open=false;});
toolsMenu.addEventListener('click',e=>{if(e.target.closest('button'))toolsMenu.open=false;});
