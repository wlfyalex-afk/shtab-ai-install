'use strict';
const audio=document.getElementById('meeting-audio'),message=document.getElementById('audio-message');
const toggle=document.getElementById('audio-toggle'),position=document.getElementById('audio-position');
const clock=document.getElementById('audio-clock'),volume=document.getElementById('audio-volume');
let stopAt=null,seeking=false;
const mmss=value=>{
 if(!Number.isFinite(value))return '--:--';
 const rounded=Math.max(0,Math.floor(value));
 return `${String(Math.floor(rounded/60)).padStart(2,'0')}:${String(rounded%60).padStart(2,'0')}`;
};
const updateClock=()=>clock.value=`${mmss(audio.currentTime)} / ${mmss(audio.duration)}`;
document.getElementById('audio-speed').addEventListener('change',event=>audio.playbackRate=Number(event.target.value));
volume.addEventListener('input',event=>audio.volume=Number(event.target.value));
document.getElementById('audio-rewind').addEventListener('click',()=>{audio.currentTime=Math.max(0,audio.currentTime-10);stopAt=null;});
document.getElementById('audio-forward').addEventListener('click',()=>{audio.currentTime=Math.min(audio.duration||Infinity,audio.currentTime+10);stopAt=null;});
toggle.addEventListener('click',async()=>{
 try{if(audio.paused)await audio.play();else audio.pause();}
 catch(error){message.textContent='Не удалось начать воспроизведение. Обновите страницу и повторите.';}
});
audio.addEventListener('play',()=>{toggle.textContent='Ⅱ';toggle.setAttribute('aria-label','Пауза');});
audio.addEventListener('pause',()=>{toggle.textContent='▶';toggle.setAttribute('aria-label','Воспроизвести');});
audio.addEventListener('loadedmetadata',()=>{position.max=String(audio.duration);updateClock();});
audio.addEventListener('durationchange',()=>{if(Number.isFinite(audio.duration))position.max=String(audio.duration);updateClock();});
position.addEventListener('pointerdown',()=>{seeking=true;});
position.addEventListener('input',()=>{clock.value=`${mmss(Number(position.value))} / ${mmss(audio.duration)}`;});
position.addEventListener('change',()=>{audio.currentTime=Number(position.value);stopAt=null;seeking=false;updateClock();});
audio.addEventListener('error',()=>message.textContent='Запись недоступна. Проверьте архив и подключение диска.');
audio.addEventListener('timeupdate',()=>{
 if(stopAt!==null&&audio.currentTime>=stopAt){audio.pause();stopAt=null;}
 if(!seeking)position.value=String(audio.currentTime);
 updateClock();
});
const playFragment=async(startValue,endValue)=>{
 const start=Math.max(0,Number(startValue)-2),end=Number(endValue)+2;
 if(!Number.isFinite(start)||!Number.isFinite(end))return;
 stopAt=end;
 try{audio.currentTime=start;await audio.play();message.textContent=`Фрагмент ${mmss(start)}–${mmss(end)}`;}
 catch(error){message.textContent='Не удалось начать воспроизведение. Нажмите Play или обновите страницу.';}
};
document.querySelectorAll('[data-audio-start]').forEach(button=>button.addEventListener('click',()=>playFragment(button.dataset.audioStart,button.dataset.audioEnd)));
const draftNav=document.getElementById('audio-draft-nav');
if(draftNav)draftNav.addEventListener('change',async()=>{
 const option=draftNav.selectedOptions[0];
 if(!option||!option.value)return;
 const card=document.getElementById(option.dataset.target);
 if(card){history.replaceState(null,'',`#${option.dataset.target}`);card.scrollIntoView({behavior:'smooth',block:'start'});}
 await playFragment(option.dataset.start,option.dataset.end);
});
