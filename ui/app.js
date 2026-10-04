'use strict';
const $ = s => document.querySelector(s);
let csrf = '', initialized = false, state = null, currentView = 'overview', plan = null, toastTimer, stateSequence=0, appliedStateSequence=0;
const formLocks=new WeakMap();
function lockForm(form){const saved=[...form.elements].map(el=>[el,el.disabled]);formLocks.set(form,saved);for(const [el] of saved)el.disabled=true;}
function unlockForm(form){for(const [el,disabled] of formLocks.get(form)||[])el.disabled=disabled;formLocks.delete(form);}
const titles = {interfaces:['调用方式','','调用方式'],overview:['总览','','总览'],sites:['网站管理','','网站管理'],connector:['连接器','','连接器'],audit:['操作日志','','操作日志'],settings:['账户与配置','','账户与配置']};
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(message, error=false){clearTimeout(toastTimer);const el=$('#toast');el.textContent=message;el.className=error?'error':'';el.hidden=false;toastTimer=setTimeout(()=>el.hidden=true,6500)}
async function api(path, data){const requestSession=csrf;const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)};const r=await fetch('/api/'+path,options);let body;try{body=await r.json()}catch{throw Error('服务器暂不可用')}if(!r.ok){if(r.status===401&&path!=='login'&&requestSession===csrf)showAuth();throw Error(typeof body.detail==='string'?body.detail:'输入格式无效')}return body}
function showAuth(){window.localLoginUI?.show(initialized);csrf='';state=null;stateSequence++;appliedStateSequence=stateSequence;invalidatePlan();$('#credentials-form').reset();$('#token-manager-form').reset();$('#site-form [name=passcode]').value='';replacingWriteToken=false;tokenWizardStep=null;$('#auth').hidden=false;$('#shell').hidden=true;$('#auth-title').textContent=initialized?'欢迎回来。':'创建管理员账户。';$('#auth-desc').textContent=window.localLoginUI?.approvalId?'请先登录，再确认另一浏览器的登录请求。':initialized?'登录管理员平台，管理公网入口与访问权限。':'首次使用，请设置本机管理员。密码至少 12 位。';$('#auth-submit').textContent=initialized?'登录管理台 →':'创建账户并登录 →';$('#auth-form [name=password]').minLength=initialized?1:12;$('#auth-form [name=password]').autocomplete=initialized?'current-password':'new-password'}
async function loadState(){if(!csrf)return;const session=csrf,sequence=++stateSequence;try{const next=await api('state');if(session!==csrf||sequence<appliedStateSequence)return;state=next;appliedStateSequence=sequence;$('#state-refresh-warning').hidden=true;render();$('#last-refresh').textContent='本机状态更新于 '+new Date().toLocaleTimeString('zh-CN');}catch(err){if(session===csrf&&sequence>=appliedStateSequence)$('#state-refresh-warning').hidden=false;throw err;}}
function openShell(){window.localLoginUI?.stop();if(window.localLoginUI?.approvalId)return window.localLoginUI.showApproval();$('#auth').hidden=true;$('#shell').hidden=false;loadState().catch(e=>toast(e.message,true))}
function go(view){currentView=view;document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!=='view-'+view);document.querySelectorAll('nav button').forEach(el=>el.classList.toggle('active',el.dataset.view===view));const t=titles[view];$('#page-title').textContent=t[0];$('#page-desc').textContent=t[1];$('#page-desc').hidden=!t[1];$('#breadcrumb').textContent='工作空间 / '+t[2];$('#add-site').hidden=!['overview','sites'].includes(view);if(view==='settings'&&state&&!$('#settings-form').dataset.dirty)fillSettings();if(state){renderSetup();renderNavigationIssues();}}
function empty(){const ready=!!state?.cloudflare_setup?.ready;return '<div class="empty"><span class="empty-icon">↗</span><b>第一个公网入口，从这里开始。</b><p>'+ (ready?'账户配置已就绪，添加你想发布的局域网网页。':'先配置 Cloudflare 账户，再添加你想发布的局域网网页。')+'</p>'+(ready?'<button class="text-button" data-add-site="true">添加第一个网站 →</button>':'<button class="text-button" data-goto="settings">配置账户 →</button>')+'</div>'}
function publicationStatus(site,current){
  const published=(current.published_hosts||[]).includes(site.hostname);
  if(!site.enabled)return {label:published?'待停用':'已停用',kind:published?'warning':'neutral'};
  if(site.paused)return {label:'已暂停',kind:'neutral'};
  if(!published)return {label:'待发布',kind:'warning'};
  if(current.publication_needs_review)return {label:'待核验',kind:'warning'};
  return {label:'已发布',kind:'success'};
}
function table(sites){if(!sites.length)return empty();return '<div class="table-wrap"><table><thead><tr><th>网站 / 公网域名</th><th>局域网源站</th><th>访问策略</th><th>发布状态</th><th>操作</th></tr></thead><tbody>'+sites.map(s=>`<tr><td><b>${esc(s.name)}</b><small><a href="https://${esc(s.hostname)}" target="_blank" rel="noreferrer">${esc(s.hostname)} ↗</a></small></td><td>${esc(s.origin)}<small>${(s.protocols||['http','websocket']).map(p=>p==='http'?'HTTP / HTTPS':'WebSocket / WSS').join(' · ')}</small></td><td>${s.human_check?'<span class="badge '+(state.settings.turnstile_sitekey&&state.credentials.turnstile_secret?'success':'warning')+'">人类验证'+(state.settings.turnstile_sitekey&&state.credentials.turnstile_secret?'':'待配置')+'</span> ':''}${s.passcode_required?'<span class="badge warning">口令</span>':!s.human_check?'<span class="badge neutral">公开访问</span>':''}<small>${s.allowed_countries.length?esc(s.allowed_countries.join(' · ')):'国家不限'} · ${s.requests_per_minute}/分钟</small></td><td><span class="badge ${publicationStatus(s,state).kind}">${publicationStatus(s,state).label}</span></td><td><button class="row-action" data-edit="${s.id}">编辑</button><button class="row-action" data-probe="${s.id}">探测</button>${s.enabled?`<button class="row-action" data-pause-site="${s.id}" title="${s.paused?'恢复该网站的转发':'立即暂停该网站的转发'}">${s.paused?'恢复转发':'暂停转发'}</button>`:''}</td></tr>`).join('')+'</tbody></table></div>'}
function render(){if(!state)return;renderSetup();renderPermissionIssues();renderTokenManager();renderConnectorReadiness();renderNavigationIssues();renderActionAvailability();const sites=state.sites,cfg=state.settings,conn=state.connector,cf=state.cloudflare;$('#nav-count').textContent=sites.length;$('#stat-sites').textContent=sites.length;$('#stat-enabled').textContent=sites.filter(s=>s.enabled&&!s.paused).length;$('#stat-human').textContent=sites.filter(s=>s.enabled&&s.human_check).length;const fresh=cf&&cf.tunnel_id===cfg.tunnel_id&&Date.now()/1000>=cf.checked_at&&(Date.now()/1000-cf.checked_at<150);$('#stat-edge').textContent=fresh?cf.connections:'—';$('#stat-edge-desc').textContent=cf?(fresh?'API 核验 · '+cf.edge_status:'上次核查已过期'):'尚未通过 API 核验';$('#flow-status').textContent=conn.running?'连接器已启动':'连接器未启动';$('#flow-status').className='badge '+(conn.running?'success':'neutral');$('#overview-sites').innerHTML=table(sites.slice(0,5));$('#sites-table').innerHTML=table(sites);$('#connector-status').textContent=conn.running?'运行中':conn.last_exit!=null?'已退出（'+conn.last_exit+'）':'未启动';$('#connector-status').className='badge '+(conn.running?'success':'neutral');$('#connector-path').textContent=cfg.cloudflared_path||(conn.installed?'已从 PATH 找到':'尚未找到 cloudflared.exe');$('#tunnel-id').textContent=cfg.tunnel_id||'尚未创建';$('#gateway-address').textContent='127.0.0.1:'+cfg.gateway_port;$('#edge-detail').textContent=cf?`边缘状态：${cf.edge_status} · 连接数：${cf.connections} · 核查时间：${new Date(cf.checked_at*1000).toLocaleString('zh-CN')}${fresh?'':'（已过期）'}`:'尚未核查远端状态。';renderHumanVerification(state);$('#step-sites').textContent=sites.length?'✓':'2';$('#step-tunnel').textContent=cfg.tunnel_id?'✓':'3';const actionNames={admin_initialized:'初始化管理员',admin_login:'管理员登录',site_saved:'保存网站策略',site_paused:'暂停网站转发',site_resumed:'恢复网站转发',settings_saved:'保存账户配置',admin_local_login:'本机授权登录',local_login_approved:'允许本机授权登录',local_login_denied:'拒绝本机授权登录',credentials_updated:'更新加密凭据',tunnel_created:'创建Tunnel',turnstile_updated:'同步 Turnstile',publish_verified:'发布并核验成功',publish_incomplete:'发布未完成，需核对',connector_prepared:'检测 / 安装连接器',connector_started:'启动连接器',connector_stopped:'停止连接器',admin_password_changed:'修改管理员密码'};$('#audit-table').innerHTML=state.audit.length?'<div class="table-wrap"><table><thead><tr><th>时间</th><th>操作</th><th>资源摘要</th></tr></thead><tbody>'+state.audit.map(a=>`<tr><td>${new Date(a.at*1000).toLocaleString('zh-CN')}</td><td>${esc(actionNames[a.action]||a.action)}</td><td class="audit-detail">${esc(JSON.stringify(a.detail))}</td></tr>`).join('')+'</tbody></table></div>':'<div class="empty">暂无操作日志。</div>';$('#read-token-status').textContent=state.credentials.cf_read_token?'已保存':'未配置';$('#read-token-remove').hidden=!state.credentials.cf_read_token;$('#secret-status').textContent=state.credentials.turnstile_secret?'已保存':'未配置';if(currentView==='settings'&&!$('#settings-form').dataset.dirty)fillSettings();}
function fillSettings(){const form=$('#settings-form');for(const el of form.elements)if(el.name)el.value=state.settings[el.name]??'';form.dataset.dirty='';}
const setupFields = [
  {key:'account_id', label:'Account ID', selector:'#settings-form [name=account_id]', help:'填写 Cloudflare 账户 ID，并点击保存配置。'},
  {key:'zone_id', label:'Zone ID', selector:'#settings-form [name=zone_id]', help:'填写域名的区域 ID，并点击保存配置。'},
  {key:'zone_name', label:'Zone 名称', selector:'#settings-form [name=zone_name]', help:'填写已接入 Cloudflare 的域名，例如 example.com。'},
  {key:'cf_write_token', label:'写入 API Token', selector:'#credentials-form [name=cf_write_token]', help:'粘贴 Cloudflare API Token，再点击“加密保存凭据”。令牌不会回显。'}
];
let replacingWriteToken=false, tokenWizardStep=null;
function renderHumanVerification(current){
  const hosts=current.sites.filter(site=>site.enabled&&site.human_check).map(site=>site.hostname);
  const configured=!!current.settings.turnstile_sitekey&&!!current.credentials.turnstile_secret;
  const status=$('#widget-status');
  status.textContent=configured?'已配置':current.settings.turnstile_sitekey?'缺少密钥':'未配置';
  status.className='badge '+(configured?'success':'neutral');
  $('#widget-scope').textContent=hosts.length?'配置范围（已启用人类验证）：'+hosts.join('、'):'暂无启用人类验证的网站';
  $('#widget-create').textContent='自动配置人类验证';
  $('#widget-create').hidden=configured;
}
function renderCredentialsGuide(){
  const saved=!!state.credentials.cf_write_token;
  const oauth=state.token_management?.managed?.kind==='oauth';
  $('#write-token-saved h3').textContent=oauth?'✓ 浏览器授权已加密保存':'✓ 写入 API Token 已加密保存';
  $('#write-token-saved p').textContent=oauth?'Cloudflare 已连接。':'已保存，留空保留当前凭据。';
  const accountReady=!!tokenTemplateURL(state.settings)&&!!state.settings.zone_name;
  if(tokenWizardStep===null)tokenWizardStep=accountReady?1:0;
  $('#write-token-saved').hidden=!saved||replacingWriteToken;
  $('#write-token-editor').hidden=saved&&!replacingWriteToken;
  $('#write-token-cancel').hidden=!saved;
  $('#write-token-status').textContent=saved&&replacingWriteToken?'新令牌待保存':saved?'已保存':'待配置';
  $('#token-wizard-title').textContent=saved?'更换写入 API Token':'连接 Cloudflare · 配置写入令牌';
  $('#token-account-summary').textContent=accountReady?'账户和域名已保存：'+state.settings.zone_name+'。':'请先在左侧填写并保存 Account ID、Zone ID 和 Zone 名称。';
  $('#token-account-next').disabled=!accountReady;
  for(let i=0;i<3;i++){
    $('#token-step-'+i).hidden=i!==tokenWizardStep;
    $('#token-step-indicator-'+i).classList.toggle('active',i===tokenWizardStep);
    $('#token-step-indicator-'+i).setAttribute('aria-current',i===tokenWizardStep?'step':'false');
  }
}
function setTokenStep(step){
  tokenWizardStep=step;renderSetup();
  if(step===2&&!$('#write-token-editor').hidden)$('#credentials-form [name=cf_write_token]').focus();
}
function tokenTemplateURL(cfg){
  if(!/^[a-f0-9]{32}$/i.test(cfg.account_id||'')||!/^[a-f0-9]{32}$/i.test(cfg.zone_id||''))return null;
  const params=new URLSearchParams({permissionGroupKeys:JSON.stringify([{key:'dns',type:'edit'},{key:'zone',type:'read'}]),accountId:cfg.account_id,zoneId:cfg.zone_id,name:'LanBridge'});
  return 'https://dash.cloudflare.com/profile/api-tokens?'+params.toString();
}
function renderTokenTemplate(){
  const link=$('#token-template-link'),url=tokenTemplateURL(state.settings);
  if(url)link.href=url;else link.removeAttribute('href');
  link.setAttribute('aria-disabled',String(!url));
  link.title=url?'在 Cloudflare 确认权限后生成令牌':'请先保存有效的 Account ID 和 Zone ID';
  $('#token-template-status').textContent=url?'创建页已填写 DNS 和 Zone 权限；请补充 Tunnel、Turnstile 编辑权限。':'请先填写并保存有效的 Account ID 和 Zone ID；保存后此入口自动启用。';
}
function pendingSetup(){return setupFields.filter(field=>!state?.cloudflare_setup || state.cloudflare_setup.missing.includes(field.label));}
function setupMessage(){return '还需完成：'+pendingSetup().map(field=>field.label).join('、');}
function locateSetup(key){
  const field=setupFields.find(item=>item.key===key)||pendingSetup()[0];
  if(!field)return;
  go('settings');
  if(field.key==='cf_write_token'){replacingWriteToken=!!state.credentials.cf_write_token;setTokenStep(2);}
  const input=$(field.selector);
  input.scrollIntoView({behavior:'smooth',block:'center'});
  input.focus({preventScroll:true});
}
function setupProgress(current){
  const count=current.sites.length,title=count?'已登记 '+count+' 个网站':'必填配置已保存';
  if(!count)return {title,detail:'添加第一个网站，填写公网域名和局域网地址。',label:'添加网站 →',next:'add'};
  const enabled=current.sites.filter(site=>site.enabled),cfg=current.settings;
  if(!enabled.length)return {title,detail:'现有网站均未启用，可在网站管理中调整。',label:'管理网站 →',next:'sites'};
  if(!cfg.tunnel_id)return {title,detail:current.tunnel_pending?'上次隧道创建结果需要核对，请恢复原请求。':'网站已登记，下一步创建隧道。',label:current.tunnel_pending?'核对并恢复隧道 →':'创建隧道 →',next:'connector'};
  if(!current.credentials.tunnel_token)return {title,detail:'隧道已创建，请获取连接令牌。',label:'获取连接令牌 →',next:'connector'};
  if(enabled.some(site=>site.human_check)&&(!cfg.turnstile_sitekey||!current.credentials.turnstile_secret))return {title,detail:'现有网站启用了人类验证，请先完成 Turnstile 密钥配置。',label:'自动配置人类验证 →',next:'verification'};
  const desired=enabled.map(site=>site.hostname).sort(),published=[...(current.published_hosts||[])].sort();
  if(current.publication_needs_review||JSON.stringify(desired)!==JSON.stringify(published))return {title,detail:'网站映射尚需发布或核对，请在连接器中预览并应用配置。',label:'发布网站映射 →',next:'connector'};
  const connector=connectorReadiness(current);
  if(connector.attention)return {title,detail:connector.detail,label:'完成连接器配置 →',next:'connector'};
  return {title,detail:'网站映射已登记并发布。可管理现有网站，或在连接器中核查公网连接。',label:'管理网站 →',next:'sites'};
}
function renderSetup(){
  renderTokenTemplate();
  renderCredentialsGuide();
  const missing=pendingSetup(),ready=!!state?.cloudflare_setup?.ready,button=$('#add-site');
  button.disabled=!ready;
  button.textContent=ready?'＋ 添加网站':'添加网站（请先完成配置）';
  button.title=ready?'添加网站':setupMessage();
  $('#setup-required').hidden=ready;
  $('#setup-required-message').textContent=missing.length===1?'只差一步：保存'+missing[0].label+'，即可添加网站。':`已完成 ${setupFields.length-missing.length}/4 项，完成下面的必填配置后即可添加网站。`;
  $('#setup-required-list').innerHTML=setupFields.map(field=>{
    const pending=missing.includes(field);
    return `<li class="${pending?'pending':'complete'}">${pending?`<button type="button" data-setup="${field.key}"><span>○ 待配置</span> ${esc(field.label)} →</button>`:`<span>✓ 已保存</span> ${esc(field.label)}`}</li>`;
  }).join('');
  $('#setup-next').textContent=missing[0]?'去配置'+missing[0].label+' →':'配置已完成';
  $('#setup-next').dataset.setup=missing[0]?.key||'';
  $('#step-settings').textContent=ready?'✓':'1';
  const progress=ready?setupProgress(state):null;
  $('#setup-progress').textContent=ready?progress.title:`账户配置 · ${setupFields.length-missing.length}/4 项已保存`;
  $('#setup-progress-detail').textContent=ready?progress.detail:'完成并保存必填配置。';
  const nextButton=$('#setup-progress-add');
  nextButton.hidden=!ready;
  nextButton.textContent=progress?.label||'查看网站 →';
  nextButton.dataset.next=progress?.next||'';
  for(const field of setupFields){
    const input=$(field.selector),pending=missing.includes(field),label=input.closest('label'),hint=$('#hint-'+field.key);
    const unsaved=field.key==='cf_write_token'?!!input.value.trim():!!$('#settings-form').dataset.dirty&&input.value!==(state.settings[field.key]||'');
    label.classList.toggle('needs-setup',pending||unsaved);
    input.setAttribute('aria-required','true');
    input.setAttribute('aria-describedby','hint-'+field.key);
    hint.textContent=unsaved?'已填写，尚未保存。'+field.help:pending?field.help:'✓ 已保存'+(field.key==='cf_write_token'?'，留空保留当前令牌。':'。');
    if(field.key==='cf_write_token'&&replacingWriteToken&&!input.value.trim())hint.textContent='粘贴新令牌并保存，或取消更换。';
    hint.className='field-hint '+(pending||unsaved?'pending':'complete');
  }
  $('#credentials-form').classList.toggle('needs-attention',missing.some(field=>field.key==='cf_write_token'));
}
function invalidatePlan(){plan=null;$('#plan-content').hidden=true;$('#plan-empty').hidden=false;}
function selectedProtocols(form){return ['http','websocket'].filter(key=>form.elements['protocol_'+key].checked);}
function editSite(id){if(!state)return;if(!id&&!state.cloudflare_setup?.ready){locateSetup();toast(setupMessage(),true);return;}const site=state.sites.find(s=>s.id===id);const form=$('#site-form');form.reset();form.elements.id.value='';form.elements.hostname.readOnly=false;$('#site-error').textContent='';$('#site-dialog-title').textContent=site?'编辑网站与策略':'添加网站';$('#policy-save-hint').hidden=!site;if(site){for(const el of form.elements){if(!el.name||el.name==='passcode'||el.name.startsWith('protocol_'))continue;const v=site[el.name];if(el.type==='checkbox')el.checked=!!v;else el.value=Array.isArray(v)?v.join(', '):v??'';}form.elements.hostname.readOnly=true;}const protocols=site?.protocols||['http','websocket'];for(const key of ['http','websocket'])form.elements['protocol_'+key].checked=protocols.includes(key);$('#site-dialog').showModal();}
async function action(button,path,message,data={}){if(button.dataset.busy)throw Error('操作正在进行，请稍候');button.dataset.busy='true';button.disabled=true;try{const result=await api(path,data);if(state){for(const key of ['settings','credentials','cloudflare_setup','cloudflare_permission_issues'])if(result[key])state[key]=result[key];}if(message)toast(message);try{await loadState()}catch{if(state)render();toast('操作已完成，但状态刷新失败。请刷新状态，勿重复提交。',true)}return result}catch(e){toast(e.message,true);await loadState().catch(()=>{});throw e}finally{button.dataset.busy='';button.disabled=false;if(state)renderActionAvailability();}}
$('#auth-form').addEventListener('submit',async e=>{e.preventDefault();const button=$('#auth-submit');button.disabled=true;$('#auth-error').textContent='';try{const data=Object.fromEntries(new FormData(e.target));if(!initialized){await api('setup',data);initialized=true;}const result=await api('login',data);csrf=result.csrf;e.target.elements.password.value='';openShell();}catch(err){$('#auth-error').textContent=err.message}finally{button.disabled=false}});
document.addEventListener('click',async e=>{const el=e.target.closest('button');if(!el)return;if(el.dataset.copy){try{await navigator.clipboard.writeText($('#'+el.dataset.copy).textContent);toast('已复制，请按说明调整项目路径或账号')}catch{toast('复制失败，请手动选择示例内容复制',true)}}if(el.dataset.addSite){el.disabled=true;try{await $('#add-site').onclick()}finally{el.disabled=false}}if(el.dataset.view)go(el.dataset.view);if(el.dataset.goto)go(el.dataset.goto);if(el.dataset.setup)locateSetup(el.dataset.setup);if(el.dataset.tokenManager)locateTokenManager();if(el.dataset.browserAuthorize)await startBrowserAuthorization(el);if(el.dataset.tokenStep!==undefined)setTokenStep(Number(el.dataset.tokenStep));if(el.dataset.edit)editSite(el.dataset.edit);if(el.dataset.pauseSite){const site=state.sites.find(s=>s.id===el.dataset.pauseSite);if(site){try{await action(el,'sites/'+site.id+'/pause',site.paused?'网站转发已恢复':'网站转发已暂停',{paused:!site.paused});invalidatePlan()}catch{}}}if(el.dataset.probe){try{const r=await action(el,'sites/'+el.dataset.probe+'/probe');toast(r.reachable?'源站可达 · HTTP '+r.http_status:'局域网源站暂不可达',!r.reachable)}catch{}}});
$('#add-site').onclick=async()=>{const button=$('#add-site');button.disabled=true;try{await loadState();editSite();}catch(e){toast('无法检查配置，请刷新后重试：'+e.message,true)}finally{if(state)renderSetup();}};$('#close-dialog').onclick=$('#cancel-site').onclick=()=>$('#site-dialog').close();$('#refresh').onclick=()=>loadState().catch(e=>toast(e.message,true));
$('#logout').onclick=async()=>{try{await api('logout',{});showAuth()}catch(e){toast(e.message,true)}};
$('#shutdown').onclick=()=>{$('#shutdown-error').textContent='';$('#shutdown-dialog').showModal();};
$('#cancel-shutdown').onclick=()=>$('#shutdown-dialog').close();
$('#confirm-shutdown').onclick=async e=>{const button=e.currentTarget;button.disabled=true;$('#cancel-shutdown').disabled=true;try{await api('shutdown',{});$('#shutdown-dialog').close();showAuth();$('#auth').hidden=true;$('#platform-stopped').hidden=false;}catch(err){$('#shutdown-error').textContent='退出请求未确认：'+err.message+'。请检查平台是否仍在运行；也可在启动终端按 Ctrl+C。';}finally{button.disabled=false;$('#cancel-shutdown').disabled=false;}};
$('#settings-form').oninput=e=>{e.currentTarget.dataset.dirty='true';$('#settings-feedback').textContent='有未保存的修改。';renderSetup();};
$('#credentials-form').oninput=()=>{$('#credentials-feedback').textContent='凭据尚未保存，请点击“加密保存凭据”。';renderSetup();};
$('#setup-progress-add').onclick=()=>{const next=$('#setup-progress-add').dataset.next;if(next==='add')return $('#add-site').onclick();if(next==='verification'){go('settings');$('#human-verification-panel').scrollIntoView({behavior:'smooth',block:'center'});return $('#widget-create').onclick({currentTarget:$('#widget-create')});}if(next)go(next);};
$('#settings-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,data=Object.fromEntries(new FormData(form));lockForm(form);try{await action(e.submitter,'settings','配置已保存',data);form.dataset.dirty='';invalidatePlan();await loadState().catch(()=>{});if(state)fillSettings();$('#settings-feedback').textContent='配置已保存。';}catch(err){$('#settings-feedback').textContent=err.message}finally{unlockForm(form);if(state)renderSetup();}};
$('#credentials-form').onsubmit=async e=>{e.preventDefault();const values=Object.fromEntries(new FormData(e.target));if(!Object.values(values).some(value=>String(value).trim())||(replacingWriteToken&&!String(values.cf_write_token||'').trim())){$('#credentials-feedback').textContent='请先粘贴要保存的新令牌；无需更换时点击取消更换。';if(!state.credentials.cf_write_token||replacingWriteToken)setTokenStep(2);return;}lockForm(e.target);try{await action(e.submitter,'credentials','凭据已加密保存',values);e.target.reset();replacingWriteToken=false;invalidatePlan();renderSetup();$('#credentials-feedback').textContent=state.cloudflare_setup.ready?'凭据已加密保存，现在可以添加网站。':'凭据已加密保存。'+setupMessage();}catch(err){$('#credentials-feedback').textContent=err.message}finally{unlockForm(e.target);if(state)renderSetup();}};
$('#password-form').onsubmit=async e=>{e.preventDefault();try{await api('password',Object.fromEntries(new FormData(e.target)));e.target.reset();showAuth();toast('密码已修改，请重新登录')}catch(err){toast(err.message,true)}};
$('#site-form').onsubmit=async e=>{e.preventDefault();const f=e.target;const data=Object.fromEntries(new FormData(f));data.protocols=selectedProtocols(f);delete data.protocol_http;delete data.protocol_websocket;if(!data.protocols.length){$('#site-error').textContent='请至少选择一种转发协议';return;}for(const k of ['enabled','human_check','passcode_required'])data[k]=f.elements[k].checked;for(const k of ['allowed_countries','allowed_ips'])data[k]=data[k].split(/[,，\s]+/).filter(Boolean);for(const k of ['requests_per_minute','session_minutes'])data[k]=Number(data[k]);const old=state.sites.find(site=>site.id===data.id);const message=old&&old.enabled===data.enabled?'网站配置已生效':'网站配置已保存；请到连接器页发布路由';lockForm(f);try{await action(e.submitter,'sites',message,data);f.elements.passcode.value='';$('#site-dialog').close();invalidatePlan()}catch(err){$('#site-error').textContent=err.message}finally{unlockForm(f);}};
for(const [id,path,msg] of [['cf-check','cloudflare/check','已获取 Cloudflare 边缘状态'],['connector-start','connector/start','已启动连接器，请通过 API 核查边缘状态'],['connector-stop','connector/stop','连接器已停止']])$('#'+id).onclick=async e=>{try{await action(e.currentTarget,path,msg)}catch{}};
$('#widget-create').onclick=async e=>{
  try{
    const result=await action(e.currentTarget,'cloudflare/turnstile-auto',null,{});
    if(['preparing','authorizing','creating'].includes(result.phase)){go('settings');$('#browser-authorize').scrollIntoView({behavior:'smooth',block:'center'});}
    else toast('人类验证已自动配置，可继续预览并发布网站');
  }catch{}
};
$('#preview').onclick=async e=>{invalidatePlan();try{plan=await action(e.currentTarget,'cloudflare/preview');$('#plan-json').textContent=JSON.stringify(plan,null,2);$('#apply').textContent=plan.routes_changed||plan.dns.some(record=>record.create)?'发布变更并核验':'核验现有路由';$('#plan-empty').hidden=true;$('#plan-content').hidden=false;}catch{}};
$('#apply').onclick=async e=>{if(!plan)return;try{await action(e.currentTarget,'cloudflare/apply','网站路由已发布。',{revision:plan.revision});invalidatePlan()}catch{invalidatePlan()}};
api('bootstrap').then(r=>{initialized=r.initialized;csrf=r.csrf;if(r.authenticated)openShell();else showAuth()}).catch(e=>$('#auth-error').textContent=e.message);
setInterval(()=>{if(csrf)loadState().catch(()=>{})},15000);

$('#connector-ensure').onclick=async e=>{
 const button=e.currentTarget,feedback=$('#connector-install-feedback'),input=$('#settings-form [name=cloudflared_path]');
 button.disabled=true;feedback.textContent='正在检测；如需下载，请稍候（取决于网络速度）…';
 try{
   const result=await api('connector/ensure',{path:input.value.trim()});
   input.value=result.path;
   await loadState();
   feedback.textContent=({'download':'已从官方来源下载并校验','PATH':'已在系统 PATH 中找到','local':'已找到项目工具','configured':'指定程序已核验'}[result.source]||'已就绪')+' · '+result.version+'。路径已保存。';
 }catch(err){feedback.textContent=err.message+'。可再次点击重试。'}
 finally{button.disabled=false;}
};

$('#write-token-replace').onclick=()=>{replacingWriteToken=true;setTokenStep(2);};
$('#write-token-cancel').onclick=()=>{const input=$('#credentials-form [name=cf_write_token]');input.value='';replacingWriteToken=false;$('#credentials-feedback').textContent='已取消更换，继续使用原令牌。';renderSetup();};
$('#token-account-next').onclick=()=>setTokenStep(1);

$('#create-tunnel').onclick=$('#tunnel-token-refresh').onclick=async e=>{
  const feedback=$('#tunnel-feedback'), help=$('#tunnel-permission-help');
  feedback.hidden=false;feedback.className='form-feedback';feedback.textContent=state.settings.tunnel_id?'正在获取连接令牌…':state.tunnel_pending?'正在核对并恢复上次创建结果…':'正在创建隧道…';help.hidden=true;
  try{await action(e.currentTarget,'cloudflare/create-tunnel');feedback.textContent='隧道已配置，连接令牌已保存。';}
  catch(err){feedback.className='form-feedback error';feedback.textContent=err.message;help.hidden=!err.message.includes('HTTP 403');await loadState().catch(()=>{});}
};

function renderPermissionIssues(){
  const issues=state.cloudflare_permission_issues||[];
  $('#cloudflare-permission-alert').hidden=!issues.length;
  $('#cloudflare-permission-details').innerHTML=issues.map(issue=>'<p>'+ (issue.status==='needs_recheck'?'凭据已更新，需重试原操作验证。上次记录：':'上次操作失败记录：')+esc(issue.detail)+'<br><small>上次失败：'+new Date(issue.checked_at*1000).toLocaleString('zh-CN')+'</small></p>').join('');
  $('#credentials-form [name=cf_write_token]').closest('label').classList.toggle('permission-field-error',issues.some(issue=>issue.credential==='cf_write_token'&&issue.status!=='needs_recheck'));
  $('#credentials-form [name=cf_read_token]').closest('label').classList.toggle('permission-field-error',issues.some(issue=>issue.credential==='cf_read_token'&&issue.status!=='needs_recheck'));
}

function locateTokenManager(){
  go('settings');
  const form=$('#token-manager-form'),feedback=$('#token-manager-feedback');
  const readOnly=(state?.cloudflare_permission_issues||[]).length>0&&(state.cloudflare_permission_issues||[]).every(issue=>issue.credential==='cf_read_token');
  const target=readOnly?'只读':'写入';
  feedback.className='form-feedback';
  feedback.textContent=(state?.token_management?.authority_saved?'授权令牌已加密保存，输入框可留空。':'请在“授权令牌”输入框粘贴 API Tokens Write 授权令牌。')+'可创建新的'+target+'令牌，或修复当前'+target+'令牌权限。新建会替换本机凭据；修复保留令牌值。';
  form.elements.authority.focus({preventScroll:true});
  form.scrollIntoView({behavior:'smooth',block:'start'});
}

async function startBrowserAuthorization(button){
  go('settings');
  $('#browser-authorize').scrollIntoView({behavior:'smooth',block:'center'});
  try{await action(button,'cloudflare/browser-authorize',null,{});}
  catch(err){$('#browser-auth-status').textContent=err.message;$('#browser-auth-status').className='form-feedback error';}
}

let browserAuthPoll;
function browserAuthMessage(job){
  if(job.phase!=='authorizing'||!Number.isFinite(job.updated_at))return job.message;
  const seconds=Math.max(0,Math.ceil(120-(Date.now()/1000-job.updated_at)));
  return job.message+(seconds>0?' 预计剩余约 '+seconds+' 秒。':' 即将超时，可重新打开授权页或取消。');
}
function renderBrowserAuth(){
  const job=state.browser_auth||{phase:"idle",message:"在浏览器授权后自动接入 Cloudflare。"},active=["preparing","authorizing","creating","cancelling"].includes(job.phase);
  $("#browser-auth-status").textContent=browserAuthMessage(job);
  $("#browser-auth-status").className="form-feedback"+(job.phase==="error"?" error":"");
  for(const button of document.querySelectorAll('#browser-authorize, [data-browser-authorize]')){
    button.disabled=active||!!button.dataset.busy||!tokenTemplateURL(state.settings)||!state.settings.zone_name||!!state.token_management?.pending;
    button.title=active?'浏览器授权正在进行':state.token_management?.pending?'先前创建结果未知，请先核对':!tokenTemplateURL(state.settings)||!state.settings.zone_name?'请先保存账户和域名配置':'授权后自动接入并刷新凭据';
  }
  const controls=[$('#browser-authorize-restart'),$('#browser-authorize-cancel')];
  const recoveryBusy=controls.some(button=>!!button.dataset.busy);
  for(const button of controls){button.hidden=job.phase!=='authorizing'&&job.phase!=='cancelling';button.disabled=job.phase!=='authorizing'||recoveryBusy;}
  if(active&&!browserAuthPoll)browserAuthPoll=setInterval(()=>{if(state)loadState().catch(()=>{});else{clearInterval(browserAuthPoll);browserAuthPoll=null;}},2000);
  if(!active&&browserAuthPoll){clearInterval(browserAuthPoll);browserAuthPoll=null;}
}
$("#browser-authorize").onclick=e=>startBrowserAuthorization(e.currentTarget);
async function recoverBrowserAuthorization(button,operation){
  if(button.dataset.busy||[$('#browser-authorize-restart'),$('#browser-authorize-cancel')].some(control=>!!control.dataset.busy))return;
  try{await action(button,'cloudflare/browser-authorize-'+operation,null,{});}
  catch(err){$('#browser-auth-status').textContent=err.message;$('#browser-auth-status').className='form-feedback error';}
}
$('#browser-authorize-restart').onclick=e=>recoverBrowserAuthorization(e.currentTarget,'restart');
$('#browser-authorize-cancel').onclick=e=>recoverBrowserAuthorization(e.currentTarget,'cancel');
function renderTokenManager(){
  renderBrowserAuth();
  const management=state.token_management||{},cfg=state.settings;
  const ready=!!tokenTemplateURL(cfg)&&!!cfg.zone_name;
  $('#token-manager-submit').disabled=!ready||!!management.pending||!!$('#token-manager-form').dataset.busy||!!$('#token-manager-submit').dataset.busy;
  if(management.managed&&!$('#token-manager-form').dataset.dirty)$('#token-manager-form [name=human_check]').checked=management.managed.human_check;
  $('#token-manager-submit').textContent='自动创建新 API Token';
  const busy=!!$('#token-manager-form').dataset.busy;
  $('#token-create-read').disabled=!ready||busy||!!management.pending_read;
  $('#token-create-read').textContent='自动创建新只读 API Token';
  $('#token-repair-read').disabled=!ready||busy||!state.credentials.cf_read_token;
  $('#token-repair-write').disabled=!ready||busy||!state.credentials.cf_write_token||['account','oauth'].includes(management.managed?.kind);
  $('#token-repair-write').title=['account','oauth'].includes(management.managed?.kind)?'浏览器授权请重新授权；账户令牌请在 Cloudflare 管理':'为当前令牌补齐权限，保留令牌值';
  $('#token-manager-forget').disabled=busy;
  $('#read-manager-status').textContent=management.pending_read?'先前创建结果未知，请在 Cloudflare 核对 '+management.pending_read.name+'；平台不会重复创建。':management.managed_read?'只读令牌已托管，可自动更新权限。':state.credentials.cf_read_token?'只读令牌已保存，可选择修复当前权限。':'未配置只读令牌；创建后读取操作会使用它。';
  $('#token-manager-forget').hidden=!management.authority_saved;
  $('#token-manager-form [name=authority]').placeholder=management.authority_saved?'授权令牌已加密保存；留空使用已保存授权':'粘贴授权令牌，仅用于令牌管理';
  $('#token-manager-status').textContent=management.pending?'先前创建结果未知，请在 Cloudflare 核对 '+management.pending.name+'；通过下方手动配置接入已有令牌。':!ready?'请先保存左侧的 Account ID、Zone ID 和 Zone 名称。':management.managed?.kind==='oauth'?'Cloudflare 已连接。':management.managed?'业务令牌已托管 · '+cfg.zone_name+(management.authority_saved?'；授权令牌已加密保存。':'；再次更新需提供授权令牌。'):management.authority_saved?'授权令牌已加密保存，可自动创建业务令牌。':'账户和域名已准备好，请提供授权令牌。';
}
$('#token-manager-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget,feedback=$('#token-manager-feedback');
  if(form.dataset.busy)return;
  const authority=form.elements.authority.value.trim();
  if(!authority&&!state.token_management?.authority_saved){feedback.textContent='请先粘贴 API Tokens Write 授权令牌。';form.elements.authority.focus();return;}
  const button=e.submitter||$('#token-manager-submit'),target=button.dataset.target||'write',repair_existing=button.dataset.repair==='true';
  const data={authority,remember:form.elements.remember.checked,human_check:form.elements.human_check.checked,target,repair_existing,force_new:button.dataset.new==='true'};
  form.dataset.busy='true';lockForm(form);renderTokenManager();feedback.className='form-feedback';feedback.textContent='正在核对并配置'+(target==='read'?'只读':'写入')+'令牌，请稍候…';
  try{const result=await action(button,'cloudflare/provision-token',null,data);form.elements.authority.value='';form.dataset.dirty='';replacingWriteToken=false;invalidatePlan();if(state){renderSetup();renderTokenManager();}const name=target==='read'?'只读令牌':'写入令牌';feedback.textContent=result.action==='created'?name+'已创建并加密保存。':name+'权限已更新，请重试失败的操作。';}
  catch(err){form.elements.authority.value='';feedback.className='form-feedback error';feedback.textContent=err.message;await loadState().catch(()=>{});}
  finally{form.dataset.busy='';unlockForm(form);if(state)renderTokenManager();}
};
$('#token-manager-forget').onclick=async e=>{try{await action(e.currentTarget,'cloudflare/forget-token-authority');$('#token-manager-feedback').textContent='本机授权令牌已移除，业务令牌仍可使用。';}catch(err){$('#token-manager-feedback').textContent=err.message;}};

$('#token-manager-form').oninput=e=>{e.currentTarget.dataset.dirty='true';};

function connectorReadiness(current){
  const cfg=current.settings,conn=current.connector,reasons=[];
  if(conn.running)return {label:'运行中',kind:'success',detail:'连接器运行中。',attention:false};

  if(!conn.installed)reasons.push('尚未找到 cloudflared，可在账户与配置中自动检测或下载');
  if(!cfg.tunnel_id&&current.cloudflare_setup?.ready)reasons.push(current.tunnel_pending?'上次隧道创建结果未知，请核对并恢复，勿重复创建':'尚未创建隧道');
  else if(cfg.tunnel_id&&!current.credentials.tunnel_token)reasons.push('缺少连接令牌，请点击“获取连接令牌”');
  if(reasons.length)return {label:!conn.installed?'待安装连接器':!cfg.tunnel_id?(current.tunnel_pending?'待恢复隧道':'待创建隧道'):'待获取连接令牌',kind:'warning',detail:reasons.join('；')+'。',attention:true};
  if(!current.cloudflare_setup?.ready)return {label:'待配置',kind:'neutral',detail:'请先完成账户与配置，再创建隧道。',attention:true};
  return {label:'未启动',kind:'neutral',detail:'发布路由后启动连接器。',attention:true};
}
function renderConnectorReadiness(){
  const readiness=connectorReadiness(state),badge=$('#connector-nav-status');
  badge.textContent=readiness.label;badge.className='nav-status '+readiness.kind;
  $('[data-view=connector]').title=readiness.detail;
  $('#connector-readiness').hidden=!readiness.attention;
  $('#connector-readiness strong').textContent=readiness.kind==='warning'?'连接器需要处理':!state.cloudflare_setup?.ready?'先完成账户配置':'连接器尚未启动';
  $('#connector-readiness-detail').textContent=readiness.detail;
  $('#connector-readiness [data-goto=settings]').hidden=!!state.cloudflare_setup?.ready&&!!state.connector.installed;
}

function navigationIssues(current){
  const issues={overview:[],sites:[],connector:[],audit:[],settings:[],interfaces:[]};
  const cfg=current.settings,enabled=current.sites.filter(site=>site.enabled);
  const missing=current.cloudflare_setup?.missing||[];
  if(!current.cloudflare_setup?.ready){
    issues.settings.push('账户配置未完成：'+missing.join('、'));
  }
  for(const issue of current.cloudflare_permission_issues||[]){
    if(issue.status!=='needs_recheck')issues.settings.push('上次操作失败，待重试核验：'+issue.detail);
  }
  if(current.token_management?.error)issues.settings.push(current.token_management.error.detail);
  if(current.token_management?.pending)issues.settings.push('业务令牌创建结果未知，请在 Cloudflare 核对后接入，勿重复创建');
  if(current.token_management?.pending_read)issues.settings.push('只读令牌创建结果未知，请在 Cloudflare 核对后接入，勿重复创建');
  const readiness=connectorReadiness(current);
  if(readiness.kind==='warning')issues.connector.push(readiness.detail);
  const humanSites=enabled.filter(site=>site.human_check&&(current.published_hosts||[]).includes(site.hostname));
  if(humanSites.length&&(!cfg.turnstile_sitekey||!current.credentials.turnstile_secret))issues.settings.push('人类验证缺少配置或服务端密钥，请自动配置人类验证');
  for(const site of enabled){
    const probe=current.site_probes?.[site.id];
    if(probe?.reachable===false&&probe.origin===site.origin)issues.sites.push(site.name+'：上次源站检查不可达，请检查局域网地址并重新检查');
  }
  if(Array.isArray(current.published_hosts)){
    const desired=enabled.map(site=>site.hostname).sort(),published=[...current.published_hosts].sort();
    if(JSON.stringify(desired)!==JSON.stringify(published)){
      issues.connector.push('网站路由有待发布变更，请预览并应用配置');
    }
  }
  if(current.publication_needs_review){
    issues.connector.push('上次发布未完成或核验失败，请重新预览并核对远端变更');
  }
  const edge=current.cloudflare;
  if(current.connector.running&&edge&&Date.now()/1000>=edge.checked_at&&Date.now()/1000-edge.checked_at<150&&edge.tunnel_id===cfg.tunnel_id&&edge.edge_status!=='healthy')issues.connector.push('最近 API 核查的 Tunnel 状态为 '+edge.edge_status+'，请检查连接并重新核查');
  issues.overview=[...new Set(['settings','sites','connector','audit'].flatMap(view=>issues[view]))];
  return issues;
}
function renderNavigationIssues(){
  const issues=navigationIssues(state);
  for(const [view,reasons] of Object.entries(issues)){
    const button=$('[data-view='+view+']'),badge=$('#'+view+'-nav-status');
    if(view==='connector'){
      const readiness=connectorReadiness(state);
      badge.hidden=false;badge.textContent=readiness.kind==='warning'?readiness.label:reasons.length?'待处理':readiness.label;
      badge.className='nav-status '+(reasons.length?'warning':readiness.kind);
      button.title=reasons.length?[...new Set(reasons)].join('；'):readiness.detail;
    }else{
      const recheck=view==='settings'&&!reasons.length&&(state.cloudflare_permission_issues||[]).some(issue=>issue.status==='needs_recheck');
      const permissions=view==='settings'&&state.cloudflare_setup?.ready&&!state.token_management?.error&&!state.token_management?.pending&&!state.token_management?.pending_read&&(state.cloudflare_permission_issues||[]).length>0;
      badge.textContent=permissions?'权限待核验':recheck?'待核验':'待处理';badge.className='nav-status '+(recheck?'neutral':'warning');
      badge.hidden=view==='overview'||(!reasons.length&&!recheck);
      button.title=reasons.length&&view!=='overview'?[...new Set(reasons)].join('；'):recheck?'凭据已更新，请重试原操作核验权限':'';
    }
  }
  const reasons=[...new Set(issues[currentView]||[])].filter(reason=>currentView!=='connector'||reason!==connectorReadiness(state).detail);
  $('#view-issues').hidden=!reasons.length;
  $('#view-issues-list').innerHTML=reasons.map(reason=>'<li>'+esc(reason)+'</li>').join('');
}

function renderActionAvailability(){
  const cfg=state.settings,credentials=state.credentials,conn=state.connector;
  const humanSites=state.sites.filter(site=>site.enabled&&site.human_check);
  const ready=!!state.cloudflare_setup?.ready;
  $('#create-tunnel').textContent=cfg.tunnel_id?(credentials.tunnel_token?'隧道已配置':'获取连接令牌'):state.tunnel_pending?'核对并恢复隧道':'创建隧道';
  $('#tunnel-maintenance').hidden=!(cfg.tunnel_id&&credentials.tunnel_token);
  $('#connector-start').textContent=conn.running?'连接器运行中':'启动连接器';
  const rules={
    'create-tunnel':[!(cfg.tunnel_id&&credentials.tunnel_token)&&(cfg.tunnel_id?!!cfg.account_id&&credentials.cf_write_token:ready),cfg.tunnel_id&&credentials.tunnel_token?'隧道和连接令牌已配置':'请先保存账户、域名和 API Token'],
    'tunnel-token-refresh':[!!cfg.tunnel_id&&!!cfg.account_id&&!!credentials.cf_write_token,'请先创建隧道并配置 API Token'],
    'connector-start':[!!cfg.tunnel_id&&credentials.tunnel_token&&!conn.running,'请先创建 Tunnel 并获取连接令牌；已运行时无需重复启动'],
    'connector-stop':[conn.running,'连接器当前未运行'],
    'cf-check':[!!cfg.tunnel_id&&(credentials.cf_read_token||credentials.cf_write_token),'请先创建 Tunnel 并配置 API Token'],
    'preview':[ready&&!!cfg.tunnel_id&&(!humanSites.length||(!!cfg.turnstile_sitekey&&credentials.turnstile_secret)),'请先创建 Tunnel；需要人类验证的网站还需配置 Turnstile'],
    'widget-create':[ready&&humanSites.length>0,'请先配置账户，并添加启用人类验证的网站']
  };
  for(const [id,[allowed,hint]] of Object.entries(rules)){
    const button=$('#'+id);button.disabled=!allowed||!!button.dataset.busy;button.title=allowed?'':hint;
  }
}

$('#read-token-remove').onclick=async e=>{
  try{await action(e.currentTarget,'credentials',null,{remove_cf_read_token:true});$('#credentials-form [name=cf_read_token]').value='';$('#credentials-feedback').textContent='只读令牌已移除。';}catch(err){$('#credentials-feedback').textContent=err.message;}
};
