'use strict';
(()=>{
 const root=document.querySelector('#domain-onboarding');
 let generation=0,busy=false,job=null,timer=null,loadedSession='',savedAuthMode=null;
 function showAuthMode(mode){
  const browser=mode==='oauth';
  field('#domain-browser-path').hidden=!browser;field('#domain-ram-path').hidden=browser;
  field('#domain-use-browser').setAttribute('aria-pressed',String(browser));
  field('#domain-use-ram').setAttribute('aria-pressed',String(!browser));
 }
 let inventoryGeneration=0,inventoryBusy=false,inventoryKey='',inventoryLoaded=false,aliyunConfigured=false;
 const field=s=>root.querySelector(s);
 const allowed=()=>!!state&&!remoteAccess&&state.access_scope!=='sites'&&!!csrf;
 const feedback=(message,error=false)=>{field('#domain-onboarding-status').textContent=message;field('#domain-onboarding-status').className='form-feedback'+(error?' error':'');};
 function controls(){
  for(const control of root.querySelectorAll('button,input,textarea'))control.disabled=busy||!allowed();
  const active=job&&['waiting','uncertain','switching'].includes(job.phase);
  for(const control of root.querySelectorAll('#domain-oauth,#domain-prepare input,#domain-prepare button,#domain-credentials textarea,#domain-credentials button'))control.disabled=busy||!allowed()||active;
  field('#aliyun-domains-refresh').disabled=busy||inventoryBusy||!allowed()||!aliyunConfigured;
 }
 const domainsKey=()=>JSON.stringify(configuredZones(state));
 async function loadInventory(force=false,clear=false){
  if(clear){inventoryGeneration++;inventoryBusy=false;inventoryLoaded=false;inventoryKey='';field('#aliyun-domains-list').innerHTML='';}
  if(!allowed()||!root.open||!aliyunConfigured||inventoryBusy)return;
  const key=domainsKey();if(!force&&inventoryLoaded&&inventoryKey===key)return;
  if(inventoryKey!==key)field('#aliyun-domains-list').innerHTML='';
  const current=++inventoryGeneration,session=csrf;inventoryBusy=true;controls();
  const status=field('#aliyun-domains-status');status.className='small muted';status.textContent='正在读取阿里云持有且已接入的域名…';
  try{
   const result=await api('domain-onboarding/connected-domains'+(force?'?refresh=1':''));
   if(current!==inventoryGeneration||session!==csrf||!allowed()||domainsKey()!==key)return;
   if(!Array.isArray(result.domains))throw Error('域名列表响应无效，请刷新列表');
   inventoryLoaded=true;inventoryKey=key;
   field('#aliyun-domains-list').innerHTML=result.domains.map(z=>`<div class="configured-zone"><span>${esc(z.zone_name)}</span><div class="configured-zone-actions"><span class="badge success">已接入</span></div></div>`).join('');
   status.textContent=result.domains.length?'共 '+result.domains.length+' 个域名'+(result.checked_at?' · 查询时间：'+new Date(result.checked_at*1000).toLocaleString('zh-CN'):''):result.configured?'当前阿里云账户持有的域名中，暂无已接入 LanBridge 的域名。':'请先完成阿里云授权';
  }catch(error){if(current===inventoryGeneration&&session===csrf&&allowed()&&domainsKey()===key){status.className='small error';status.textContent='查询失败：'+error.message+(field('#aliyun-domains-list').children.length?' 以下仍为上次查询结果。':'');}}
  finally{if(current===inventoryGeneration&&session===csrf){inventoryBusy=false;controls();if(allowed()&&domainsKey()!==key)loadInventory();}}
 }
 const simpleConfirmation=()=>Array.isArray(job?.records)&&job.records.length===0;
 function render(result){
  job=result.job||null;
  aliyunConfigured=!!result.configured;
  state.domain_onboarding=job?{domain:job.domain,phase:job.phase}:null;
  field('#domain-credential-status').textContent=result.configured?(result.auth_mode==='oauth'?'已授权':'RAM 凭据已保存'):'未授权';
  field('#domain-credential-status').className='badge '+(result.configured?'success':'neutral');
  const oauth=result.configured&&result.auth_mode==='oauth';
  field('#domain-oauth').textContent=oauth?'重新授权 ↗':'浏览器授权 ↗';
  const mode=oauth?'oauth':result.configured?'manual':'oauth';
  if(savedAuthMode!==mode){savedAuthMode=mode;showAuthMode(mode);}
  field('#domain-ram-note').textContent=oauth?'手动接入使用阿里云 RAM 凭据（AccessKey）。保存后将切换授权方式；当前 OAuth 授权无需补填。':result.configured?'手动接入使用阿里云 RAM 凭据（AccessKey）。填写并保存新凭据以替换当前凭据。':'手动接入使用阿里云 RAM 凭据（AccessKey），也可以选择浏览器授权。';
  field('#domain-auth-description').textContent=oauth?'当前授权方式：浏览器 OAuth。可直接准备域名接入；需要切换账号或授权失效时，再重新授权。':result.configured?'当前使用 RAM 凭据。完成浏览器授权后将改用 OAuth，无需长期 AccessKey。':'通过浏览器授权后即可准备域名接入，无需填写长期 AccessKey。首次使用需安装阿里云 CLI 3.3.0 或更新版本。';
  field('#domain-preview').hidden=!job||job.phase==='done';
  field('#domain-preview').open=!!job&&job.phase!=='done';
  field('#domain-preview-summary').textContent='当前接入任务';
  const completed=job?.phase==='done';
  document.querySelector('#domain-completed-history').hidden=!completed;
  document.querySelector('#domain-completed-title').textContent=completed?job.domain:'';
  document.querySelector('#domain-completed-result').textContent=completed?'已完成接入 · 迁移记录 '+job.records.length+' 条':'';
  document.querySelector('#domain-completed-old-ns').textContent=completed?job.old_nameservers.join('、'):'';
  document.querySelector('#domain-completed-new-ns').textContent=completed?job.new_nameservers.join('、'):'';
  field('#domain-confirm').hidden=job?.phase!=='preview';
  const simple=simpleConfirmation();
  field('#domain-confirm-fields').hidden=simple;
  for(const input of root.querySelectorAll('#domain-confirm-fields input'))input.required=!simple;
  field('#domain-confirm-note').hidden=!simple;
  field('#domain-confirm-note').textContent=simple?`未发现 DNS 记录。确认后将把 ${job.domain} 的 DNS 服务器切换至上方所列的 Cloudflare 服务器，请确认该域名未承载网站或邮箱。`:'';
  field('#domain-tracking').hidden=!job||!['waiting','uncertain','switching'].includes(job.phase);
  field('#domain-finish-form').hidden=true;field('#domain-finish-note').hidden=true;
  if(job){
   field('#domain-preview-title').textContent=job.domain;
   field('#domain-old-ns').textContent=job.old_nameservers.join('、');
   field('#domain-new-ns').textContent=job.new_nameservers.join('、');
   field('#domain-records').innerHTML=job.records.map(row=>`<tr><td>${esc(row.type)}</td><td>${esc(row.name)}</td><td>${esc(row.content??JSON.stringify(row.data))}${row.priority===undefined?'':' · '+esc(row.priority)}</td><td>${esc(row.ttl)}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">没有 DNS 记录，请确认该域名未承载网站或邮箱</td></tr>';
  }
  const message=job?.phase==='preview'?'尚未切换 DNS，域名尚未完成接入。请核对预览，点击“确认接入并切换 DNS”继续。':job?.phase==='waiting'&&job.message==='变更任务已提交，正在等待 Cloudflare 激活'?'DNS 切换已提交，正在等待生效及 Cloudflare 激活。':job?.message||'';
  feedback(job?.phase==='done'?'':message);controls();schedule();renderNavigationIssues();
 }
 function schedule(){
  clearTimeout(timer);timer=null;
  if(root.open&&allowed()&&job?.phase==='waiting')timer=setTimeout(()=>{if(!document.hidden)perform('check',{});else schedule();},60000);
 }
 async function perform(action,values){
  if(!allowed()||busy)return;
  busy=true;const current=++generation,session=csrf;controls();feedback(action==='oauth'?'请在浏览器完成阿里云授权，最长等待 180 秒':action==='prepare'?'正在核对域名和 DNS 记录':action==='confirm'?'正在迁移并提交 DNS 服务器变更':'正在处理');
  try{
   const result=await api('domain-onboarding'+(action?'/'+action:''),action?values:undefined);
   if(current!==generation||session!==csrf||!allowed())return;
   const initial=loadedSession!==session;
   loadedSession=session;render(result);
   loadInventory(false,action==='oauth'||action==='credentials');
   if(result.configured&&((!action&&initial)||action==='oauth'||action==='credentials'))field('#domain-auth-details').open=false;
   if(action==='oauth')field('#domain-credentials').reset();
   if(action==='credentials'){field('#domain-credentials').reset();feedback('阿里云凭据已保存');}
   if(action==='confirm')field('#domain-confirm').reset();
   if(result.job?.phase==='done')refreshState().catch(()=>{});
  }catch(error){
   if(current===generation&&session===csrf&&allowed()){
    if(action==='confirm'){
     try{const latest=await api('domain-onboarding');if(current===generation&&session===csrf&&allowed())render(latest);}catch{}
    }
    if(current===generation&&session===csrf&&allowed()){
     const renewal=error.message.includes('Cloudflare 管理授权');
     if(renewal)await refreshState().catch(()=>{});
     if(current===generation&&session===csrf&&allowed())feedback(renewal&&state.oauth_refresh?'准备未完成，请先在上方重试 Cloudflare 授权续期':error.message,true);
    }
   }
  }
  finally{if(current===generation&&session===csrf){busy=false;controls();schedule();}}
 }
 field('#domain-use-browser').onclick=()=>showAuthMode('oauth');
 field('#domain-use-ram').onclick=()=>showAuthMode('manual');
 field('#domain-oauth').onclick=()=>perform('oauth',{});
 field('#aliyun-domains-refresh').onclick=()=>loadInventory(true);
 field('#domain-credentials').onsubmit=event=>{event.preventDefault();perform('credentials',Object.fromEntries(new FormData(event.currentTarget)));};
 field('#domain-prepare').onsubmit=event=>{event.preventDefault();perform('prepare',Object.fromEntries(new FormData(event.currentTarget)));};
 field('#domain-confirm').onsubmit=event=>{event.preventDefault();if(job&&(simpleConfirmation()||field('#domain-confirm [name=acknowledged]').checked))perform('confirm',{id:job.id,confirmed_domain:simpleConfirmation()?job.domain:field('#domain-confirm [name=confirmed_domain]').value});};
 field('#domain-cancel').onclick=()=>job&&perform('cancel',{id:job.id});
 field('#domain-check').onclick=()=>perform('check',{});
 field('#domain-finish').onclick=()=>{clearTimeout(timer);timer=null;field('#domain-finish-note').hidden=false;field('#domain-finish-form').hidden=false;field('#domain-finish-form input').focus();};
 field('#domain-finish-form').onsubmit=event=>{event.preventDefault();if(job)perform('finish',{id:job.id,confirmed_domain:field('#domain-finish-form input').value});};
 document.querySelector('#domain-task-open').onclick=()=>{if(!allowed())return;go('settings');root.open=true;root.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});};
 root.addEventListener('toggle',()=>{window.domainOnboardingUI.renderNotice();if(root.open&&allowed()&&loadedSession!==csrf)perform();else{if(root.open)loadInventory();schedule();}});
 window.domainOnboardingUI={
  renderNotice(){
   const notice=document.querySelector('#domain-task-notice'),task=allowed()?state.domain_onboarding:null;
   const messages={preview:['新域名接入待确认','尚未切换 DNS，域名尚未完成接入。请查看预览并确认；已有域名和转发不受影响。'],waiting:['新域名接入等待生效','DNS 切换已提交，正在等待生效及 Cloudflare 激活。'],switching:['新域名接入正在提交','正在提交 DNS 服务器变更，请等待结果。'],uncertain:['新域名接入结果待核验','请查看域名接入任务并检测状态，勿重复提交 DNS 切换。'],blocked:['新域名接入未完成','请查看域名接入任务中的失败说明。']};
   if(currentView==='audit'&&task?.phase==='done'&&loadedSession!==csrf&&!busy)perform();
   const message=messages[task?.phase];
   notice.hidden=!message||(currentView==='settings'&&root.open);
   document.querySelector('#domain-task-title').textContent=message?message[0]+' · '+task.domain:'';
   document.querySelector('#domain-task-message').textContent=message?message[1]:'';
  },
  renderAccess(){root.hidden=!allowed();if(!allowed()){this.reset();return;}controls();this.renderNotice();if(root.open&&loadedSession!==csrf&&!busy)perform();else if(root.open)loadInventory();},
  reset(){const history=document.querySelector('#domain-completed-history');history.hidden=true;history.open=false;for(const id of ['title','result','old-ns','new-ns'])document.querySelector('#domain-completed-'+id).textContent='';document.querySelector('#domain-task-notice').hidden=true;generation++;inventoryGeneration++;inventoryBusy=false;inventoryLoaded=false;inventoryKey='';aliyunConfigured=false;field('#aliyun-domains-list').innerHTML='';field('#aliyun-domains-status').textContent='授权后读取域名列表';busy=false;job=null;loadedSession='';savedAuthMode=null;showAuthMode('oauth');clearTimeout(timer);timer=null;root.hidden=true;root.open=false;for(const form of root.querySelectorAll('form'))form.reset();field('#domain-records').innerHTML='';field('#domain-preview').hidden=true;feedback('');}
 };
 window.domainOnboardingUI.renderAccess();
})();
