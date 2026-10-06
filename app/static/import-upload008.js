'use strict';
const form=document.getElementById('upload-form');
const fileInput=document.getElementById('import-file');
const start=document.getElementById('upload-start'),pause=document.getElementById('upload-pause');
const statusText=document.getElementById('upload-status'),progress=document.getElementById('upload-progress');
let uploadId=form.dataset.importId,stopped=false,busy=false;
const token=()=>document.getElementById('import-csrf').value;
async function api(url,options={}){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),60000);
 try{
  const response=await fetch(url,{...options,credentials:'same-origin',signal:controller.signal,
   headers:{'X-CSRF-Token':token(),...(options.headers||{})}});
  if(response.redirected)throw new Error('Сеанс завершён. Войдите снова и продолжите загрузку из списка совещаний.');
  const type=response.headers.get('content-type')||'';
  const data=type.includes('application/json')?await response.json():{};
  if(!response.ok)throw new Error(data.error||`Запрос отклонён (${response.status}). Обновите страницу и продолжите загрузку.`);
  return data;
 } finally {clearTimeout(timer);}
}
pause.addEventListener('click',()=>{stopped=true;statusText.textContent='Останавливаемся после текущей части…';});
form.addEventListener('submit',async event=>{
 event.preventDefault();if(busy)return;
 const file=fileInput.files[0];if(!file)return;
 if(file.size>17179869184||file.size===0){statusText.textContent='Выберите файл от 1 байта до 16 ГиБ.';return;}
 if(!window.crypto?.subtle){statusText.textContent='Для загрузки откройте приложение через 127.0.0.1 или HTTPS.';return;}
 busy=true;stopped=false;start.disabled=true;pause.hidden=false;fileInput.disabled=true;
 try{
  if(!uploadId){
   const values=new URLSearchParams({title:form.elements.title.value,meeting_at:form.elements.meeting_at.value,name:file.name,size:String(file.size)});
   if(form.elements.group_id)values.set('group_id',form.elements.group_id.value);
   const result=await api('/meetings/uploads',{method:'POST',body:values});uploadId=result.id;
   history.replaceState(null,'','/meetings/upload?resume='+uploadId);
   form.elements.title.readOnly=true;form.elements.meeting_at.disabled=true;
  }
  const state=await api(`/meetings/uploads/${uploadId}/state`);
  if(state.status!=='UPLOADING'){location.assign(`/meetings/uploads/${uploadId}`);return;}
  if(state.total_bytes!==file.size)throw new Error('Размер отличается. Выберите исходный файл или создайте новую загрузку.');
  const chunkSize=2097152,parts=Math.ceil(file.size/chunkSize);
  for(let index=0;index<parts;index++){
   if(stopped)break;
   const block=await file.slice(index*chunkSize,Math.min(file.size,(index+1)*chunkSize)).arrayBuffer();
   const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',block)),x=>x.toString(16).padStart(2,'0')).join('');
   if(state.chunks[String(index)]){
    if(state.chunks[String(index)]!==hash)throw new Error('Содержимое отличается от ранее загруженного файла. Выберите исходный файл.');
   }else{
    await api(`/meetings/uploads/${uploadId}/chunks/${index}`,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Chunk-SHA256':hash},body:block});
   }
   const pct=Math.round(Math.min(file.size,(index+1)*chunkSize)/file.size*100);
   progress.value=pct;statusText.textContent=`Передача и проверка: ${pct}%`;
  }
  if(stopped){statusText.textContent='Загрузка приостановлена. Нажмите кнопку, чтобы продолжить.';return;}
  const result=await api(`/meetings/uploads/${uploadId}/finish`,{method:'POST'});location.assign(result.url);
 }catch(error){statusText.textContent=(error.name==='AbortError'?'Соединение прервано.':error.message)+' Полученные части сохранены; загрузку можно продолжить.';}
 finally{busy=false;start.disabled=false;fileInput.disabled=false;pause.hidden=true;start.textContent='Продолжить загрузку';}
});
