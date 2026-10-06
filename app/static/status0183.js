document.addEventListener('DOMContentLoaded',()=>{
  const root=document.querySelector('[data-pipeline-active="1"]');
  if(!root)return;
  window.setTimeout(()=>window.location.reload(),20000);
});
