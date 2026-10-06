'use strict';
(()=>{
 const root=document.getElementById('metrics011');if(!root)return;
 const time=v=>v==null?'—':`${String(Math.floor(v/60)).padStart(2,'0')}:${String(Math.floor(v%60)).padStart(2,'0')}`;
 const size=v=>v==null?'неизвестно':`${(v/1073741824).toFixed(2)} ГиБ`;
 const labels={RUNNING:'В работе',DONE:'Готово',FAILED:'Ошибка',INTERRUPTED:'Прервано; время до последнего измерения',OBSERVED:'Загрузка / пауза'};
 function node(tag,text){const n=document.createElement(tag);n.textContent=text;return n;}
 async function refresh(){
  try{
   const r=await fetch(root.dataset.url,{credentials:'same-origin',cache:'no-store'});if(!r.ok||r.redirected)throw Error();
   const data=await r.json();
   if(data.meeting_id){const result=document.getElementById('job-result');if(result&&!result.querySelector('a')){const a=node('a','Открыть совещание');a.href='/meetings/'+data.meeting_id;result.append(a);}}
   if(['REVIEW','DUPLICATE'].includes(data.status))document.getElementById('job-status').textContent='Обработка завершена';document.getElementById('metrics-disk').textContent=`Свободно в хранилище: ${size(data.free_bytes)}`;
   const container=document.getElementById('metrics-parts');container.replaceChildren();
   data.parts.forEach((p,i)=>{
    const section=node('section','');section.append(node('h3',`Часть ${i+1}: ${p.name}`),node('p',`Получено ${size(p.received)} из ${size(p.expected)}`));
    if(!p.stages.length)section.append(node('p','Измерений пока нет: старое задание или обработка ещё не началась.'));
    p.stages.forEach(s=>{
     let text=`${s.stage} · ${labels[s.state]||s.state} · ${time(s.elapsed)} · запуск ${s.run}`;
     if(s.processed!=null)text+=` · запись ${time(s.processed)} / ${time(s.duration)}`;
     if(s.speed!=null)text+=` · ${(s.speed/1048576).toFixed(2)} МиБ/с`;
     if(s.stale)text+=' · новых измерений более 30 секунд';
     section.append(node('p',text));
    });container.append(section);
   });
  }catch(e){document.getElementById('metrics-disk').textContent='Не удалось получить статистику. Проверьте соединение.';}
  setTimeout(refresh,5000);
 }
 refresh();
})();
