'use strict';
const node=document.getElementById('import-job'),label=document.getElementById('job-status');
const progress=document.getElementById('job-progress'),detail=document.getElementById('job-detail');
async function refresh(){
 try{
  const response=await fetch(node.dataset.stateUrl,{credentials:'same-origin',cache:'no-store'});
  if(!response.ok||response.redirected)throw new Error();
  const state=await response.json();label.textContent=state.label;
  if(state.status==='UPLOADING'){
   progress.value=state.uploaded_bytes/state.total_bytes*100;detail.textContent=`Получено ${Math.round(progress.value)}%. Продолжите загрузку файла.`;
  }else if(state.status==='FETCHING'){
   const downloaded=state.downloaded_bytes||0,total=state.expected_download_bytes;
   if(total){progress.value=Math.min(99,Math.floor(downloaded/total*100));}else{progress.removeAttribute('value');}
   detail.textContent=`Скачано ${Math.round(downloaded/1024/1024)} МиБ`+(total?` из ${Math.round(total/1024/1024)} МиБ.`:'. Размер ещё не известен.');
  }else if(['CONVERTING','TRANSCRIBING'].includes(state.status)&&state.duration_seconds){
   progress.value=Math.min(99,Math.floor(state.processed_seconds/state.duration_seconds*100));
   detail.textContent=`Обработано ${Math.round(state.processed_seconds/60*10)/10} из ${Math.round(state.duration_seconds/60*10)/10} мин. ${progress.value}% текущего этапа.`;
  }else if(state.status==='REVIEW'){
   progress.value=100;detail.textContent='Стенограмма сохранена. Можно открыть совещание.';
  }else{progress.removeAttribute('value');detail.textContent=state.error||'Обработка выполняется на сервере. Браузер можно закрыть.';}
  if(state.meeting_id){
   const result=document.getElementById('job-result');
   if(!result.querySelector('a')){const link=document.createElement('a');link.className='button';link.textContent='Открыть совещание';link.href='/meetings/'+state.meeting_id;result.appendChild(link);}
  }
  if(['REVIEW','DUPLICATE','FAILED','CANCELLED'].includes(state.status)){
   if(state.status==='DUPLICATE')detail.textContent='Такой файл уже есть в базе. Откройте существующее совещание.';
   if(state.status==='FAILED')detail.textContent+=' Обновите страницу для повторной обработки.';
   return;
  }
 }catch(error){detail.textContent='Не удалось обновить статус. Проверьте соединение или обновите страницу.';}
 setTimeout(refresh,5000);
}
refresh();
