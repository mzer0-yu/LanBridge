'use strict';
const routes=document.getElementById('routes'),button=document.getElementById('refresh'),error=document.getElementById('error');
let loading=false;
async function refresh(){
  if(loading)return;loading=true;button.disabled=true;
  try{
    const response=await fetch('/api/client/routes',{cache:'no-store'});
    if(!response.ok)throw Error('无法读取网站入口，请稍后重试。');
    const data=await response.json();routes.replaceChildren();
    if(!data.routes.length){const empty=document.createElement('p');empty.className='empty';empty.textContent='暂无启用的网站';routes.append(empty);}
    for(const route of data.routes){
      const card=document.createElement('article');card.className='route';
      card.innerHTML='<div class="route-heading"><h2></h2><span class="status"></span></div><div class="route-path"><div><small>公网入口</small><a class="public-url" target="_blank" rel="noreferrer"></a></div><span class="arrow" aria-label="转发到">→</span><div><small>局域网源站</small><span class="origin"></span></div></div>';
      card.querySelector('h2').textContent=route.name;
      const status=card.querySelector('.status');status.textContent=route.status;status.classList.toggle('pending',route.status!=='已发布');
      const link=card.querySelector('.public-url');link.textContent='https://'+route.hostname;
      if(route.published)link.href='https://'+route.hostname;else{link.removeAttribute('href');link.setAttribute('aria-disabled','true');}
      card.querySelector('.origin').textContent=route.origin;routes.append(card);
    }
    document.getElementById('updated').textContent='更新于 '+new Date(data.updated_at*1000).toLocaleTimeString('zh-CN');error.hidden=true;
  }catch(exception){error.textContent=exception.message;error.hidden=false;}finally{loading=false;button.disabled=false;}
}
button.onclick=refresh;refresh();setInterval(refresh,15000);
