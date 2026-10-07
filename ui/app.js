'use strict';
// Service credentials are masked text, not browser login-password fields.
for(const field of document.querySelectorAll('textarea.secret-input')){
  field.addEventListener('input',()=>{
    const singleLine=field.value.replace(/[\r\n]/g,'');
    if(field.value!==singleLine)field.value=singleLine;
  });
  field.addEventListener('keydown',event=>{
    if(event.key==='Enter'&&!event.isComposing){
      event.preventDefault();
      if(!field.disabled&&!field.readOnly&&field.form){
        const submitter=[...field.form.elements].find(control=>control.type==='submit');
        if(!submitter?.disabled)field.form.requestSubmit(submitter);
      }
    }
  });
}

const $ = s => document.querySelector(s);
let remoteAccess = false, publicClientEnabled = true;
let csrf = '', initialized = false, state = null, currentView = 'overview', plan = null, stateSequence=0, appliedStateSequence=0;
const formLocks=new WeakMap();
const revealedTemporaryTokens=new Map();
const sitePauseErrors=new Map(),sitePausePending=new Set();
function lockForm(form){const saved=[...form.elements].map(el=>[el,el.disabled]);formLocks.set(form,saved);for(const [el] of saved)el.disabled=true;}
function unlockForm(form){for(const [el,disabled] of formLocks.get(form)||[])el.disabled=disabled;formLocks.delete(form);}
const titles = {tokens:['临时授权','','临时授权'],interfaces:['调用方式','','调用方式'],overview:['总览','','总览'],sites:['网站转发','','网站转发'],audit:['操作日志','','操作日志'],settings:['账户与配置','','账户与配置']};
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(message, error=false, anchor=null){
  const el=$('#operation-feedback'),source=anchor||document.activeElement;
  const container=$('#shell').hidden?$('.auth-card'):source?.closest('dialog[open] form,.panel')||$('.view:not([hidden])')||$('main');
  container.append(el);el.textContent=message;el.className=error?'error':'';el.hidden=false;
}
const copyFeedbackTimers=new WeakMap();
function markCopied(button){
  if(!button)return;
  const previous=copyFeedbackTimers.get(button);if(previous)clearTimeout(previous.timer);
  const label=previous?.label||button.textContent;button.textContent='已复制';button.classList.add('copy-success');
  const timer=setTimeout(()=>{button.textContent=label;button.classList.remove('copy-success');copyFeedbackTimers.delete(button);},2000);
  copyFeedbackTimers.set(button,{label,timer});
}

async function api(path, data){
  const requestSession=csrf;
  const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)};
  if(data===undefined)options.signal=AbortSignal.timeout(15000);
  let r,body;
  try{
    r=await fetch('/api/'+path,options);
    try{body=await r.json();}catch(error){if(error.name==='TimeoutError')throw error;throw Error('服务器暂不可用');}
  }catch(error){if(error.name==='TimeoutError')throw Error(path==='state'?'状态读取超时，请重试':'读取超时，请重试');throw error;}
  if(!r.ok){
    if(remoteAccess&&body.verification_required){location.reload();throw Error('请重新完成入口访问验证');}
    if(r.status===401&&!['login','token-login'].includes(path)&&requestSession===csrf)showAuth();
    throw Error(typeof body.detail==='string'?body.detail:'输入格式无效');
  }
  return body;
}

function setLoginMethod(method){
  const token=initialized&&method==='token';
  $('#password-login').hidden=token;$('#temporary-login').hidden=!token;
  $('#auth-error').textContent='';$('#temporary-login-feedback').textContent='';
  for(const button of document.querySelectorAll('[data-login-method]')){
    const selected=(button.dataset.loginMethod==='token')===token;
    button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;
  }
}
for(const button of document.querySelectorAll('[data-login-method]')){
  button.onclick=()=>setLoginMethod(button.dataset.loginMethod);
  button.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const method=e.key==='Home'?'admin':e.key==='End'?'token':button.dataset.loginMethod==='admin'?'token':'admin';setLoginMethod(method);$('#login-'+method+'-tab').focus();};
}
function showAuth(){$('#operation-feedback').hidden=true;revealedTemporaryTokens.clear();$('#temporary-token-list').innerHTML='';clearTemporaryToken();$('#login-methods').hidden=!initialized;setLoginMethod('admin');$('#temporary-login-form').reset();for(const kind of ['api-token','authority-token']){clearTokenDiscovery(kind);$('#'+kind+'-discovery-feedback').textContent='';}sitePauseErrors.clear();sitePausePending.clear();cloudCheck={pending:false,error:'',tunnel:''};window.localLoginUI?.show(initialized);csrf='';state=null;stateSequence++;appliedStateSequence=stateSequence;invalidatePlan();$('#credentials-form').reset();$('#token-manager-form').reset();$('#site-form [name=passcode]').value='';replacingWriteToken=false;$('#verification-credentials-form').reset();$('#verification-credentials-form').dataset.dirty='';$('#verification-feedback').textContent='';$('#auth').hidden=false;$('#shell').hidden=true;$('#auth-title').textContent=initialized?'欢迎回来':'创建管理员账户';$('#auth-desc').textContent=window.localLoginUI?.approvalId?'请先登录，随后会显示确认码和允许登录按钮。':initialized?'':'首次使用，请设置本机管理员。密码至少 12 位。';$('#auth-desc').hidden=!$('#auth-desc').textContent;$('#auth-submit').textContent=initialized?'登录管理台 →':'创建账户并登录 →';$('#auth-form [name=password]').minLength=initialized?1:12;$('#auth-form [name=password]').autocomplete=initialized?'current-password':'new-password'}
async function loadState(){if(!csrf)return;const session=csrf,sequence=++stateSequence;try{const next=await api('state');if(session!==csrf||sequence<appliedStateSequence)return;if(currentView==='audit'&&state?.audit?.length>100&&next.audit_storage?.oldest_id){const fresh=new Set(next.audit.map(row=>row.id));next.audit.push(...state.audit.filter(row=>row.id>=next.audit_storage.oldest_id&&!fresh.has(row.id)));next.audit.sort((a,b)=>b.id-a.id);}state=next;appliedStateSequence=sequence;$('#state-refresh-warning').hidden=true;render();$('#last-refresh').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN');}catch(err){if(session===csrf&&sequence>=appliedStateSequence)$('#state-refresh-warning').hidden=false;throw err;}}
let refreshFeedbackTimer;
async function refreshState(){
  const button=$('#refresh'),icon=button.querySelector('span'),status=$('#refresh-status'),session=csrf;
  if(button.disabled||!session)return;
  clearTimeout(refreshFeedbackTimer);
  const setFeedback=(mode,label,symbol)=>{
    button.dataset.refreshState=mode;icon.textContent=symbol;
    button.title=label;button.setAttribute('aria-label',label);status.textContent=label;
  };
  button.disabled=true;button.setAttribute('aria-busy','true');
  setFeedback('loading','正在刷新状态','↻');
  // Keep fast local responses visible without delaying the request itself.
  const visibleLoading=new Promise(resolve=>setTimeout(resolve,450));
  try{
    try{await loadState();}finally{await visibleLoading;}
    if(session!==csrf)return;
    setFeedback('success','状态已刷新','✓');
  }catch(error){
    if(session!==csrf)return;
    setFeedback('error','刷新失败，点击重试','!');
    const warning=$('#state-refresh-warning');
    warning.textContent='状态刷新失败，当前显示上次结果。'+String(error.message).replace(/[。.!！?？\s]+$/,'')+'。请重试。';warning.hidden=false;
  }finally{
    button.disabled=false;button.removeAttribute('aria-busy');
    if(session!==csrf){setFeedback('idle','刷新状态','↻');}
    else if(button.dataset.refreshState==='success')refreshFeedbackTimer=setTimeout(()=>setFeedback('idle','刷新状态','↻'),2000);
  }
}
function openShell(){window.localLoginUI?.stop();if(window.localLoginUI?.approvalId)return window.localLoginUI.showApproval();$('#auth').hidden=true;$('#shell').hidden=false;loadState().catch(e=>toast(e.message,true))}
function go(view){if(state?.access_scope==='sites'&&!temporaryViews().includes(view))view=temporaryViews().includes('sites')?'sites':'settings';currentView=view;document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!=='view-'+view);document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view));const t=titles[view];$('#page-title').textContent=t[0];$('#page-desc').textContent=t[1];$('#page-desc').hidden=!t[1];$('#add-site').hidden=view!=='sites';if(view==='settings'&&state&&!$('#settings-form').dataset.dirty)fillSettings();if(view==='tokens'&&state)loadTemporaryTokens().catch(err=>$('#temporary-token-feedback').textContent=err.message);if(state){renderSetup();renderNavigationIssues();}}
function empty(){const ready=!!state?.cloudflare_setup?.ready;return '<div class="empty"><span class="empty-icon">↗</span><b>第一个公网入口，从这里开始。</b><p>'+ (ready?'账户配置已就绪，添加你想发布的局域网网页。':'先配置 Cloudflare 账户，再添加你想发布的局域网网页。')+'</p>'+(ready?'<button class="text-button" data-add-site="true">添加第一个网站 →</button>':'<button class="text-button" data-goto="settings">配置账户 →</button>')+'</div>'}
function renderAgentGuide(){
  const base='http://127.0.0.1:'+(state?.settings.admin_port||8890);
  const apiOnly=$('#agent-mode').value==='api';
  const entries=(state?.sites||[]).filter(s=>s.target==='lanbridge'&&s.enabled&&!s.paused&&(state.published_hosts||[]).includes(s.hostname));
  const entry=$('#agent-public-entry'),previous=entry.value;
  const addresses=[{url:base,label:base+'（仅运行 LanBridge 的电脑可访问）'},...entries.map(s=>({url:'https://'+s.hostname,label:'https://'+s.hostname}))];
  entry.innerHTML=addresses.map(s=>`<option value="${esc(s.url)}">${esc(s.label)}</option>`).join('');
  if(addresses.some(s=>s.url===previous))entry.value=previous;
  else entry.value=base;
  $('#agent-public-entry-label').hidden=!apiOnly;
  $('#agent-skill-details').hidden=apiOnly;
  $('#agent-skill-path').textContent=state?.agent_skill_path?'请先阅读此 SKILL 文件，再按我的要求操作 LanBridge：\n'+state.agent_skill_path:'请在本机管理台获取 SKILL 路径。';
  $('#copy-agent-skill').disabled=!state?.agent_skill_path;
  $('#copy-agent-guide').disabled=false;
  const publicEndpoint=entry.value.startsWith('https://');
  $('#agent-mode-note').textContent=apiOnly?(publicEndpoint?'通过公网 API 接入，需要入口验证和管理员登录。':'Agent 与 LanBridge 须在同一台电脑上运行，无需读取项目文件。'):'可读取项目文件，使用 CLI 或本机 MCP。';
  if(apiOnly){
    const publicBase=entry.value;
    $('#agent-guide').textContent=publicBase?[
      '请仅通过 LanBridge HTTP API 完成随后提供的任务，不读取或修改项目文件，不使用 CLI 或 stdio MCP。这个接入方式适用于本机或远程 Agent，实际可达范围由所选管理地址决定。',
      '管理台：'+publicBase+'/admin；转发列表：'+publicBase+'/client；API 基址：'+publicBase+'/api/。'+(publicEndpoint?'通过 HTTPS 同一公网域名请求。':'此回环地址仅在运行 LanBridge 的电脑上可达，远程 Agent 不能使用它。'),
      '认证方式：优先使用用户签发的临时管理 Token，请求头 Authorization: Bearer <token>，按 Token 的 permissions 管理网站转发或 Cloudflare 账户与域名。到期或撤销后停止并请用户重新授权。Token 不放入 URL，不输出凭据。'+(publicEndpoint?'公网 Token 接入不需要入口验证 Cookie，但仍受 HTTPS、入口暂停、IP/国家与限流策略约束。管理员密码登录则须先在同一浏览器完成入口验证。':'管理员密码登录也可 POST /api/login，JSON 为 username、password，Origin 为 '+publicBase+'。')+'未获得凭据时请用户授权，不读取服务器凭据文件。',
      'GET /api/bootstrap 核对 authenticated 与 scope，再 GET /api/state 获取 sites、settings、connector、published_hosts。写入使用 POST JSON 和 Content-Type: application/json。临时管理 Token 请求使用 Bearer，无需 Cookie 或 CSRF。密码登录需保留会话 Cookie 和 csrf，写请求携带 X-CSRF-Token: bootstrap.csrf 与同源 Origin '+publicBase+'。不要导出 Cookie、密码、Token 或 csrf 到对话。',
      '仅管理员或已获 account 权限的临时管理 Token 可修改域名接入：state.settings.zones 是已接入域名列表。POST /api/cloudflare/zones + {} 读取同一账户的可用域名；POST /api/zones + {"zone_id":"32位区域ID","zone_name":"example.com"} 核验并接入，不要通过 /api/settings 替换 zones。',
      '新增或编辑网站：POST /api/sites。编辑时从 state.sites 取完整现有对象，保留 id 和未要求改变的字段；不能直接改已有 hostname。普通网站示例：{"name":"示例网站","hostname":"app.example.com","origin":"http://192.168.1.20:8080","target":"website","enabled":true,"paused":false,"protocols":["http","websocket"],"human_check":true,"passcode_required":false,"allowed_countries":["CN","HK","JP","US"],"allowed_ips":[],"requests_per_minute":180,"session_minutes":60}。域名需属于 settings.zones 中的域名，可填写对应 zone_id；省略时按域名自动匹配。origin 是运行 LanBridge 的电脑访问的源站地址；回环地址指服务器自身，不是你的电脑。',
      'POST /api/sites/{id}/pause + {"paused":true} 暂停；{"paused":false} 恢复。停用网站通过 POST /api/sites 保存 enabled:false。不要误停用或暂停正在使用的 LanBridge 公网入口，否则会失去远程访问。',
      '保存自动准备人类验证并发布所需路由；检查响应 publication.status：failed 表示已保存但发布未完成。发布失败时检查原因，再 POST /api/cloudflare/preview + {}，向 POST /api/cloudflare/apply 提交 {"revision":"预览返回的 revision"}；保留其他路由，不覆盖冲突 DNS。',
      'POST /api/sites/{id}/probe + {} 检查源站，随后 GET /api/state 核对结果；源站可达和路由已发布不等于实际公网访问已通过，应在授权会话验证目标网站。遇到 401 或 verification_required 请用户重新登录或完成入口验证；不要无限重试。',
      (publicEndpoint?'公网接口不支持初始化管理员、本机浏览器授权、Cloudflare 浏览器授权、修改管理员密码或关闭平台。':'')+'管理员会话具有完整权限；临时管理 Token 的 sites 权限允许网站管理、发布路由、检查连接和启停连接器；account 权限允许 Cloudflare 账户与域名配置。所有临时管理 Token 均不能修改管理员密码、网关端口或签发临时管理 Token。仅执行用户授权的操作，不输出凭据；完成后报告实际变更、发布状态和验证结果。',
      '我的具体任务：请等待我补充。'
    ].join('\n\n'):'尚无可用的 LanBridge 公网入口。';
    return;
  }
  $('#agent-guide').textContent=[
    '请使用 LanBridge 帮我完成随后提供的任务。先检查现有状态，再按我的要求修改网站转发。',
    '管理台：'+base+'/admin；转发列表：'+base+'/client。这些本机地址仅适用于运行 LanBridge 的电脑。',
    '先定位 LanBridge 项目目录，阅读 skills/lanbridge/SKILL.md 和 README.md；运行 .venv/Scripts/python.exe run.py capabilities 查看当前能力。找不到目录时请向我询问。',
    '优先使用已配置的 LanBridge MCP。尚未配置时，按照项目 README 接入 MCP 或使用已认证的本机 API；GET /api/state 读取当前状态。管理员凭据需经安全环境或隐藏输入提供，不能由这段说明自动授予。',
    '管理台运行时，通过 API / MCP 修改；CLI 修改需要先停止平台，避免并发写入。不要为操作直接编辑 data 或数据库。',
    'API / MCP 保存网站会自动准备人类验证、发布所需路由并核验；检查返回的 publication 结果，failed 表示网站已保存但发布未完成，不能宣称成功。CLI 保存仍需显式发布。',
    '暂停或恢复使用 POST /api/sites/{id}/pause，发送 {"paused":true} 或 {"paused":false}；无需重新发布。',
    '发布失败时先检查原因；使用 POST /api/cloudflare/preview 取得最新计划，再向 POST /api/cloudflare/apply 提交返回的 revision。保留其他路由，不覆盖冲突 DNS。',
    '不输出或复制密码、令牌、Cookie、密钥；保留现有访问验证与策略，除非我的具体任务明确要求修改。完成后报告实际修改和验证结果。',
    '我的具体任务：请等待我补充。'
  ].join('\n\n');
}
for(const selector of ['#agent-mode','#agent-public-entry'])$(selector).onchange=()=>{$('#agent-copy-status').hidden=true;renderAgentGuide();};
$('#copy-agent-skill').onclick=async()=>{
  const path=state?.agent_skill_path;if(!path)return;
  const text=$('#agent-skill-path').textContent;
  const feedback=$('#agent-skill-feedback');feedback.hidden=false;
  try{await navigator.clipboard.writeText(text);feedback.textContent='已复制路径和阅读指令，可粘贴给本机 Agent。';}
  catch{feedback.textContent='无法访问剪贴板，请复制上方说明。';const range=document.createRange();range.selectNodeContents($('#agent-skill-path'));const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);}
};
$('#copy-agent-guide').onclick=async()=>{
  renderAgentGuide();const status=$('#agent-copy-status');status.hidden=false;
  if($('#copy-agent-guide').disabled)return;
  try{await navigator.clipboard.writeText($('#agent-guide').textContent);status.textContent='已复制，可粘贴给 Agent。';}
  catch{status.textContent='无法访问剪贴板，请复制下方操作说明。';$('#agent-guide-details').open=true;const range=document.createRange();range.selectNodeContents($('#agent-guide'));const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);}
};
function publicationStatus(site,current){
  const published=(current.published_hosts||[]).includes(site.hostname);
  if(!site.enabled)return {label:published?'待停用':'已停用',kind:published?'warning':'neutral'};
  if(site.paused)return {label:'已暂停',kind:'paused'};
  if(!published)return {label:'待发布',kind:'warning'};
  if(current.publication_needs_review)return {label:'待核验',kind:'warning'};
  return {label:'已发布',kind:'success'};
}
function pauseNotice(sites){const paused=sites.filter(s=>s.enabled&&s.paused).length;const forwarding=sites.filter(s=>s.enabled&&!s.paused&&(state.published_hosts||[]).includes(s.hostname)).length;return sites.length?`<div class="forwarding-summary" role="status"><span>转发中 <strong>${forwarding}</strong> 个</span><span class="summary-paused">已暂停 <strong>${paused}</strong> 个</span></div>`:'';}
function table(sites){if(!sites.length)return empty();return '<div class="table-wrap"><table class="sites-grid-table"><thead><tr><th>网站 / 公网域名</th><th>转发目标</th><th>访问策略</th><th>发布状态</th><th>操作</th></tr></thead><tbody>'+sites.map(s=>`<tr class="${s.enabled&&s.paused?'site-paused':''}"><td data-label="网站"><b>${esc(s.name)}</b><small><a href="https://${esc(s.hostname)}" target="_blank" rel="noreferrer">${esc(s.hostname)} ↗</a></small></td><td data-label="转发目标">${esc(s.origin)}<small>${(s.protocols||['http','websocket']).map(p=>p==='http'?'HTTP / HTTPS':'WebSocket / WSS').join(' · ')}</small></td><td data-label="访问策略">${s.human_check?'<span class="badge '+(state.settings.turnstile_sitekey&&state.credentials.turnstile_secret?'success':'warning')+'">人类验证'+(state.settings.turnstile_sitekey&&state.credentials.turnstile_secret?'':'待配置')+'</span> ':''}${s.passcode_required?'<span class="badge warning">口令</span>':!s.human_check?'<span class="badge neutral">公开访问</span>':''}<small>${s.allowed_countries.length?esc(s.allowed_countries.join(' · ')):'国家不限'} · ${s.requests_per_minute}/分钟</small></td><td data-label="发布状态"><span class="badge ${publicationStatus(s,state).kind}">${publicationStatus(s,state).label}</span></td><td data-label="操作"><button class="row-action" data-edit="${s.id}">编辑</button><button class="row-action" data-probe="${s.id}">探测</button>${s.enabled?`<button class="row-action ${s.paused?'resume-action':''}" data-pause-site="${s.id}" ${sitePausePending.has(s.id)?'disabled aria-busy="true"':''} title="${s.paused?'恢复该网站的转发':'立即暂停该网站的转发'}">${sitePausePending.has(s.id)?'正在处理…':s.paused?'恢复转发':'暂停转发'}</button>`:''}${sitePauseErrors.has(s.id)?`<p class="site-action-error" role="status">${esc(sitePauseErrors.get(s.id))}</p>`:''}</td></tr>`).join('')+'</tbody></table></div>'}
function publicationHint(current){
  if(current.publication_error)return '自动发布未完成：'+current.publication_error;
  if(current.publication_needs_review)return '上次发布未完成或核验失败，需要核对路由。';
  if(!Array.isArray(current.published_hosts))return '';
  const desired=current.sites.filter(site=>site.enabled).map(site=>site.hostname).sort();
  const published=[...current.published_hosts].sort();
  return JSON.stringify(desired)!==JSON.stringify(published)?'网站路由有待发布的变更':'';
}
function renderPublicationHint(){const message=publicationHint(state);$('#sites-publication-hint').hidden=!message;$('#sites-publication-message').textContent=message;}
let cloudCheck={pending:false,error:'',tunnel:''};
function cloudConnectionLabel(status){return {healthy:'已连接',degraded:'已连接，但连接不稳定',down:'未连接',inactive:'尚未建立连接'}[status]||'连接状态未知';}
function checkTimeAgo(timestamp){
  const seconds=Math.max(0,Math.floor(Date.now()/1000-timestamp));
  if(seconds<60)return '刚刚';
  if(seconds<3600)return Math.floor(seconds/60)+' 分钟前';
  if(seconds<86400)return Math.floor(seconds/3600)+' 小时前';
  return Math.floor(seconds/86400)+' 天前';
}
function renderCloudConnection(){
  renderTemporaryAccess();
  renderGatewayPort();
  renderAdminPort();
  const cf=state.cloudflare,cfg=state.settings,same=cf&&cf.tunnel_id===cfg.tunnel_id;
  const fresh=same&&Date.now()/1000>=cf.checked_at&&Date.now()/1000-cf.checked_at<150;
  if(cloudCheck.tunnel!==cfg.tunnel_id)cloudCheck={pending:false,error:'',tunnel:cfg.tunnel_id};
  const label=same?cloudConnectionLabel(cf.edge_status):'未检查';
  const checked=same?checkTimeAgo(cf.checked_at)+'（'+new Date(cf.checked_at*1000).toLocaleString('zh-CN')+'）':'';
  const badge=$('#edge-status');let detail='';
  const currentLabel=cloudCheck.pending?'正在检查':cloudCheck.error?'检查失败':!same||!fresh?'尚未确认':label;
  badge.textContent=currentLabel;
  badge.hidden=!!(same&&!fresh&&!cloudCheck.pending&&!cloudCheck.error);
  badge.className='badge '+(cloudCheck.error?'warning':cloudCheck.pending||!same||!fresh?'neutral':cf.edge_status==='healthy'?'success':'warning');
  const history=same?'上次检查：'+label+' · '+checked:'';
  if(cloudCheck.error)detail=cloudCheck.error+(same?'；'+history:'');
  else if(cloudCheck.pending)detail=history||'正在查询 Cloudflare 连接状态…';
  else if(same)detail=fresh?'隧道连接：'+cf.connections+' 条 · 检查于 '+checked:history;
  else detail='尚未检查连接';
  $('#edge-detail').textContent=detail;
  $('#edge-detail').className='muted'+(cloudCheck.error?' cloud-check-error':'');
  $('#stat-edge').textContent=cloudCheck.pending?'正在检查':cloudCheck.error?'检查失败':same?label:currentLabel;
  $('#stat-edge').hidden=badge.hidden;
  $('#stat-edge').className=badge.className;
  $('#stat-edge-desc').textContent=cloudCheck.error?cloudCheck.error:cloudCheck.pending?'正在检查连接…':same?(fresh?'检查于 '+checked:history):'点击检查连接，获取当前状态。';
}
function auditResourceSummary(record){
  const detail=record.detail;
  if(!detail||typeof detail!=='object'||!Object.keys(detail).length)return '<span class="audit-no-detail">—</span>';
  const labels={id:record.action.startsWith('temporary_token_')?'令牌 ID':'对象 ID',name:'名称',hostname:'域名',zone_name:'域名',zone_id:'域名 ID',account_id:'账户 ID',site_id:'网站 ID',permissions:'权限',limit_mb:'日志上限',port:'端口',pid:'进程',enabled:'启用',version:'版本',hostnames:'域名',automatic:'自动发布',reconcile_required:'需核对',source:'来源',running_restored:'恢复运行'};
  const values=Object.entries(detail).slice(0,3).map(([key,value])=>{
    let text=typeof value==='boolean'?(value?'是':'否'):Array.isArray(value)?value.map(v=>v==='sites'?'网站转发':v==='account'?'账户与域名':String(v)).join('、'):typeof value==='object'?JSON.stringify(value):String(value??'—');
    if(key==='limit_mb')text+=' MB';
    if((key==='id'||key.endsWith('_id'))&&text.length>16)text=text.slice(0,12)+'…';
    return '<span class="audit-resource-field"><span>'+esc(labels[key]||key)+'</span><b>'+esc(text)+'</b></span>';
  });
  return '<div class="audit-resource-summary">'+values.join('')+'</div>';
}
function renderAudit(){
const actionNames={connector_auto_start_changed:'修改启动时自动连接',public_client_changed:'修改公网转发列表',temporary_token_created:'创建临时管理令牌',temporary_token_revoked:'撤销临时管理令牌',temporary_token_viewed:'查看临时管理令牌',temporary_token_login:'临时令牌登录',audit_limit_changed:'修改日志大小上限',gateway_port_scheduled:'修改网关端口',connector_updated:'更新连接器',zone_added:'接入域名',zone_removed:'移除域名',admin_initialized:'初始化管理员',admin_login:'管理员登录',site_saved:'保存网站策略',site_paused:'暂停网站转发',site_resumed:'恢复网站转发',settings_saved:'保存账户配置',admin_local_login:'本机授权登录',local_login_approved:'允许本机授权登录',local_login_denied:'拒绝本机授权登录',credentials_updated:'更新凭据',api_token_connected:'通过 API Token 接入',browser_oauth_connected:'浏览器授权连接 Cloudflare',platform_shutdown_requested:'退出 LanBridge',platform_restart_requested:'重启 LanBridge',admin_port_scheduled:'修改管理台端口',business_token_created:'创建写入令牌',business_token_updated:'更新写入令牌',read_token_created:'创建只读令牌',read_token_updated:'更新只读令牌',tunnel_created:'创建隧道',turnstile_updated:'同步 Turnstile',publish_verified:'发布并核验成功',publish_incomplete:'发布未完成，需核对',connector_prepared:'检测 / 安装连接器',connector_started:'启动连接器',connector_stopped:'停止连接器',admin_password_changed:'修改管理员密码'};const opened=new Set([...document.querySelectorAll('#audit-table details[open]')].map(el=>el.dataset.auditId));
$('#audit-table').innerHTML=state.audit.length?'<div class="table-wrap"><table><thead><tr><th scope="col">时间</th><th scope="col">操作</th><th scope="col">对象与详情</th></tr></thead><tbody>'+state.audit.map(a=>{
  const date=new Date(a.at*1000),hasDetails=a.detail&&Object.keys(a.detail).length;
  return `<tr><td class="audit-time"><time datetime="${date.toISOString()}"><strong>${esc(date.toLocaleTimeString('zh-CN',{hour12:false}))}</strong><span>${esc(date.toLocaleDateString('zh-CN'))}</span></time></td><td class="audit-action">${esc(actionNames[a.action]||a.action)}</td><td class="audit-resource"><div class="audit-resource-body">${auditResourceSummary(a)}${hasDetails?`<details class="audit-raw-details" data-audit-id="${esc(a.id)}" ${opened.has(String(a.id))?'open':''}><summary>原始详情</summary><pre>${esc(JSON.stringify(a.detail,null,2))}</pre></details>`:''}</div></td></tr>`;
}).join('')+'</tbody></table></div>':'<div class="empty">暂无操作日志。</div>';
  const storage=state.audit_storage;
  if(storage){
    $('#audit-storage-summary').textContent='共 '+storage.count+' 条 · 已用 '+(storage.size_bytes/1024/1024).toFixed(2)+' MB / 上限 '+storage.limit_mb+' MB';
    if(!$('#audit-storage-form').dataset.dirty)$('#audit-storage-form [name=limit_mb]').value=storage.limit_mb;
  }
  $('#audit-load-more').hidden=!state.audit.length||(storage?state.audit.length>=storage.count:state.audit.length<100);
}
$('#connection-details-toggle').onclick=()=>{
  const panel=$('#connection-details'),button=$('#connection-details-toggle');
  panel.hidden=!panel.hidden;button.setAttribute('aria-expanded',String(!panel.hidden));
};
$('#audit-export-copy').onclick=async()=>{
  const button=$('#audit-export-copy');
  try{await navigator.clipboard.writeText(new URL('/api/audit/export',location.origin).href);markCopied(button);}
  catch{toast('无法复制，请使用当前管理地址后的 /api/audit/export。',true,button);}
};
$('#audit-settings-toggle').onclick=()=>{
  const panel=$('#audit-retention-settings'),button=$('#audit-settings-toggle');
  panel.hidden=!panel.hidden;button.setAttribute('aria-expanded',String(!panel.hidden));
};
$('#audit-storage-form [name=limit_mb]').oninput=()=>$('#audit-storage-form').dataset.dirty='true';
async function saveLocalSetting(form,feedback,path,data,message,inlineSuccess=false){
  const session=csrf,button=inlineSuccess?form.querySelector('button[type=submit]'):null;lockForm(form);feedback.textContent='';
  if(button){button.textContent='保存中';button.classList.remove('save-success');}
  try{
    const result=await api(path,data);if(session!==csrf)return;
    form.dataset.dirty='';
    const saved=typeof message==='function'?message(result):message;
    feedback.className='form-feedback small';
    if(button){button.textContent='已保存';button.classList.add('save-success');}
    else feedback.textContent=saved;
    try{await loadState();}
    catch(err){if(session===csrf){feedback.className='small';feedback.textContent=saved+' 页面刷新失败，请点击顶部刷新重试。';}}
  }catch(err){if(session===csrf){feedback.className='form-feedback small error';feedback.textContent=err.message;if(button)button.textContent='保存';}}
  finally{unlockForm(form);}
}
$('#audit-storage-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget;
  await saveLocalSetting(form,$('#audit-storage-feedback'),'audit/settings',{limit_mb:Number(form.elements.limit_mb.value)},'日志大小上限已保存');
};
$('#audit-load-more').onclick=async()=>{
  if(!state?.audit?.length)return;
  const button=$('#audit-load-more'),session=csrf,sequence=appliedStateSequence;button.disabled=true;
  try{
    const result=await api('audit?before='+state.audit.at(-1).id);
    if(session!==csrf||!state)return;
    const oldest=Math.max(state.audit_storage?.oldest_id||0,result.storage?.oldest_id||0);
    const records=new Map([...state.audit,...result.records].filter(row=>row.id>=oldest).map(row=>[row.id,row]));
    state.audit=[...records.values()].sort((a,b)=>b.id-a.id);
    if(sequence===appliedStateSequence)state.audit_storage=result.storage;
    renderAudit();if(!result.records.length)button.hidden=true;
  }catch(err){if(session===csrf){$('#audit-storage-feedback').className='small error';$('#audit-storage-feedback').textContent=err.message;}}finally{button.disabled=false;}
};

function clearTemporaryToken(){
  $('#temporary-token-value').value='';$('#temporary-token-result').hidden=true;
}
function temporaryPermissions(){return state?.access_permissions||['sites'];}
function temporaryViews(){return ['overview',...(temporaryPermissions().includes('sites')?['sites']:[]),...(temporaryPermissions().includes('account')?['settings']:[])];}
function temporaryPermissionText(permissions){return (permissions||['sites']).map(p=>p==='account'?'Cloudflare 账户与域名':'网站转发').join('、');}
function renderTemporaryAccess(){
  const limited=state?.access_scope==='sites',account=!limited||temporaryPermissions().includes('account'),sites=!limited||temporaryPermissions().includes('sites');
  for(const view of ['sites','settings','interfaces','audit'])$('[data-view='+view+']').hidden=limited&&!temporaryViews().includes(view);
  document.querySelectorAll('[data-goto=settings],[data-setup],[data-browser-authorize],[data-token-manager]').forEach(el=>{el.hidden=!account;});
  $('.public-access-panel').hidden=limited;
  $('.temporary-tokens-panel').hidden=limited;$('.temporary-token-list-panel').hidden=limited;
  $('.local-security-panel').hidden=limited;
  $('.local-security-panel').classList.toggle('unavailable',remoteAccess);
  const oldRuntime=state.admin_port_supported!==true;
  $('#local-security-note').hidden=!remoteAccess&&!oldRuntime;
  $('#local-security-note').textContent=remoteAccess?'仅支持本机修改':'当前运行的是旧版本，请先退出 LanBridge，再通过启动器重新启动一次。';
  document.querySelectorAll('[data-local-setting]').forEach(button=>{button.disabled=remoteAccess||(button.dataset.localSetting==='admin'&&oldRuntime);button.title=remoteAccess?'仅支持本机修改':button.dataset.localSetting==='admin'&&oldRuntime?'请先重新启动以加载新功能':'';});
  if(remoteAccess)setLocalSecurity('');
  $('#human-verification-panel').hidden=limited;
  $('#connector-version').closest('.panel').hidden=limited;$('.settings-security').hidden=limited;$('.account-settings-layout').classList.toggle('temporary-account-layout',limited);
  $('nav [data-view=tokens]').hidden=limited;
  if(currentView==='tokens'&&!limited)loadTemporaryTokens().catch(err=>$('#temporary-token-feedback').textContent=err.message);
  if(currentView==='tokens'&&limited)go(temporaryPermissions().includes('sites')?'sites':'settings');
  $('#shutdown').hidden=limited||remoteAccess;
  $('#restart').disabled=limited||remoteAccess||state.restart_supported!==true;
  $('#restart').title=remoteAccess?'仅支持本机重启':state.restart_supported!==true?'当前运行方式不支持网页重启，请使用启动器':'';
  $('#add-site').hidden=!sites||currentView!=='sites';$('#overview-add-site').hidden=!sites;
  document.querySelectorAll('[data-goto=sites]').forEach(el=>{el.hidden=!sites;});
  $('#temporary-access-notice').hidden=!limited;
  if(limited){$('#temporary-access-notice').textContent='临时管理 · '+temporaryPermissionText(temporaryPermissions())+' · 到期时间：'+new Date(state.access_expires*1000).toLocaleString('zh-CN');if(!temporaryViews().includes(currentView))go(sites?'sites':'settings');}
}
let temporaryTokenSequence=0;
async function loadTemporaryTokens(){
  const sequence=++temporaryTokenSequence;
  const session=csrf,result=await api('temporary-tokens'),now=Date.now()/1000;if(session!==csrf||state?.access_scope==='sites')return;
  if(sequence!==temporaryTokenSequence)return;
  const tokens=result.tokens.filter(token=>!token.revoked&&token.expires>now);
  for(const id of revealedTemporaryTokens.keys())if(!tokens.some(token=>token.id===id))revealedTemporaryTokens.delete(id);
  $('#temporary-token-count').textContent=tokens.length+' 个';
  $('#temporary-token-list').innerHTML=tokens.length?tokens.map(token=>`<div class="temporary-token-item"><div class="temporary-token-info"><b>${esc(token.name)}</b><small>${esc(temporaryPermissionText(token.permissions))} · 到期：${esc(new Date(token.expires*1000).toLocaleString('zh-CN'))}</small>${token.last_login?`<small>上次登录：${esc(checkTimeAgo(token.last_login))}（${esc(new Date(token.last_login*1000).toLocaleString('zh-CN'))}）</small>`:''}${token.last_access?`<small>上次访问：${esc(checkTimeAgo(token.last_access))}（${esc(new Date(token.last_access*1000).toLocaleString('zh-CN'))}）</small>`:!token.last_login?'<small>暂无登录或访问记录</small>':''}${!token.revealable?'<small>旧版本未保存原值</small>':''}${revealedTemporaryTokens.has(token.id)?`<input class="temporary-token-revealed" type="text" readonly spellcheck="false" title="点击复制 Token" aria-label="${esc(token.name)}的完整 Token" value="${esc(revealedTemporaryTokens.get(token.id))}">`:''}</div><div class="temporary-token-actions">${token.revealable?`<button class="text-button" type="button" data-view-temporary="${esc(token.id)}">${revealedTemporaryTokens.has(token.id)?'隐藏':'查看 Token'}</button><button class="text-button" type="button" data-copy-temporary="${esc(token.id)}">复制</button>`:''}<button class="text-button" type="button" data-revoke-temporary="${esc(token.id)}">撤销</button></div></div>`).join(''):'<div class="temporary-token-empty"><p>暂无有效的临时管理 Token</p><small>创建后可在这里查看使用记录或撤销。</small></div>';
}
const temporaryHoursInput=$('#temporary-token-form [name=hours]');
function updateTemporaryHourPresets(){document.querySelectorAll('[data-token-hours]').forEach(button=>button.setAttribute('aria-pressed',String(Number(temporaryHoursInput.value)===Number(button.dataset.tokenHours))));}
temporaryHoursInput.addEventListener('input',updateTemporaryHourPresets);
document.querySelectorAll('[data-token-hours]').forEach(button=>button.onclick=()=>{temporaryHoursInput.value=button.dataset.tokenHours;updateTemporaryHourPresets();});
$('#temporary-token-form').onsubmit=async e=>{
  e.preventDefault();const session=csrf,form=e.currentTarget,feedback=$('#temporary-token-feedback');feedback.classList.remove('error');clearTemporaryToken();lockForm(form);
  try{const result=await api('temporary-tokens',{name:form.elements.name.value,hours:Number(form.elements.hours.value),permissions:Array.from(form.querySelectorAll('[name=permissions]:checked'),el=>el.value)});if(session!==csrf)return;$('#temporary-token-value').value=result.token;$('#temporary-token-result').hidden=false;feedback.textContent='已创建：'+result.name;await loadTemporaryTokens();}
  catch(err){if(session===csrf){feedback.classList.add('error');feedback.textContent=err.message;}}finally{unlockForm(form);}
};
async function copyVisibleTemporaryToken(input){if(!input.value)return;input.select();const button=input.closest('.temporary-token-item')?.querySelector('[data-copy-temporary]')||$('#temporary-token-copy');try{await navigator.clipboard.writeText(input.value);markCopied(button);}catch{toast('复制失败，已选中 Token，可手动复制',true,input);}}
$('#temporary-token-value').onclick=()=>copyVisibleTemporaryToken($('#temporary-token-value'));
$('#temporary-token-copy').onclick=()=>copyVisibleTemporaryToken($('#temporary-token-value'));
$('#temporary-token-dismiss').onclick=clearTemporaryToken;
$('#temporary-token-list').onclick=async e=>{
  const input=e.target.closest('.temporary-token-revealed');if(input){await copyVisibleTemporaryToken(input);return;}
  const button=e.target.closest('[data-revoke-temporary],[data-view-temporary],[data-copy-temporary]');if(!button||button.disabled)return;
  const session=csrf,id=button.dataset.revokeTemporary||button.dataset.viewTemporary||button.dataset.copyTemporary;button.disabled=true;
  try{
    if(button.dataset.revokeTemporary){await api('temporary-tokens/'+id+'/revoke',{});if(session!==csrf)return;revealedTemporaryTokens.delete(id);clearTemporaryToken();await loadTemporaryTokens();$('#temporary-token-feedback').textContent='';}
    else if(button.dataset.viewTemporary&&revealedTemporaryTokens.has(id)){revealedTemporaryTokens.delete(id);await loadTemporaryTokens();}
    else{const result=await api('temporary-tokens/'+id+'/reveal',{});if(session!==csrf)return;if(button.dataset.copyTemporary){await navigator.clipboard.writeText(result.token);markCopied(button);}else{revealedTemporaryTokens.set(id,result.token);await loadTemporaryTokens();}}
  }catch(err){toast(err.message,true,button);}finally{button.disabled=false;}
};
$('#temporary-login-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget;lockForm(form);
  try{const result=await api('token-login',{token:form.elements.token.value.trim()});form.reset();csrf=result.csrf;go((result.permissions||['sites']).includes('sites')?'sites':'settings');openShell();}
  catch(err){$('#temporary-login-feedback').textContent=err.message;}finally{unlockForm(form);}
};
function renderGatewayPort(){
  const form=$('#gateway-port-form');if(!form||!state)return;
  const pending=state.pending_gateway_port;
  const gateway=state.gateway;
  const unavailable=gateway&&gateway.running===false;
  $('#gateway-runtime-status').hidden=!unavailable;
  $('#gateway-runtime-status').textContent=unavailable?gateway.error+' 转发暂不可用，管理台可正常使用。':'';
  $('#gateway-retry').hidden=!unavailable;
  $('#gateway-port-note').textContent=unavailable?'修改端口并保存可立即重试启动；恢复后到网站转发同步云端路由。':'下次启动时生效。重启后到网站转发同步云端路由。';
  $('#gateway-port-current').textContent=state.settings.gateway_port+(pending?' · 下次启动使用：'+pending:'');
  if(!form.dataset.dirty)form.elements.port.value=pending||(unavailable?gateway.port:null)||state.settings.gateway_port;
}
function renderAdminPort(){
  if(!state)return;const form=$('#admin-port-form'),pending=state.pending_admin_port;
  $('#admin-port-current').textContent=state.settings.admin_port+(pending?' · 下次启动：'+pending:'');
  if(!form.dataset.dirty)form.elements.port.value=pending||state.settings.admin_port;
  $('#restart-warning').hidden=!state.restart_warning;$('#restart-warning').textContent=state.restart_warning||'';
}
$('#admin-port-form').oninput=e=>{e.currentTarget.dataset.dirty='true';};
$('#admin-port-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget;
  await saveLocalSetting(form,$('#admin-port-feedback'),'admin-port',{port:Number(form.elements.port.value)},result=>result.restart_required?'已保存，下次启动生效。可点击“重启 LanBridge”立即应用。':'已取消待生效的修改，继续使用当前管理端口。');
};
function renderPublicClientSetting(){
  if(typeof state?.public_client_enabled==='boolean')publicClientEnabled=state.public_client_enabled;
  const form=$('#public-client-form');
  if(!form.dataset.dirty)form.elements.enabled.checked=publicClientEnabled;
  document.querySelectorAll('a[href="/client"]').forEach(link=>link.hidden=remoteAccess&&!publicClientEnabled);
}
$('#public-client-form').onchange=e=>{const form=e.currentTarget;form.dataset.dirty='true';const button=form.querySelector('button[type=submit]');button.textContent='保存';button.classList.remove('save-success');$('#public-client-feedback').textContent='';};
$('#public-client-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget;
  await saveLocalSetting(form,$('#public-client-feedback'),'public-client',{enabled:form.elements.enabled.checked},result=>result.enabled?'公网转发列表已启用':'公网转发列表已关闭',true);
};
function renderConnectorAutoStart(){
  const form=$('#connector-auto-start-form'),unsupported=state.connector_auto_start_supported!==true;
  if(!form.dataset.dirty)form.elements.enabled.checked=state.connector_auto_start!==false;
  form.elements.enabled.disabled=remoteAccess||unsupported;
  form.querySelector('button[type=submit]').disabled=remoteAccess||unsupported;
  form.title=remoteAccess?'仅支持本机修改':unsupported?'重新启动 LanBridge 后可设置':'';
  const warning=$('#connector-startup-warning');warning.hidden=!state.connector_startup_warning;warning.textContent=state.connector_startup_warning||'';
}
$('#connector-auto-start-form').onchange=e=>{
  e.currentTarget.dataset.dirty='true';const button=e.currentTarget.querySelector('button[type=submit]');button.textContent='保存';button.classList.remove('save-success');$('#connector-auto-start-feedback').textContent='';
};
$('#connector-auto-start-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget;
  await saveLocalSetting(form,$('#connector-auto-start-feedback'),'connector-auto-start',{enabled:form.elements.enabled.checked},'启动时自动连接设置已保存',true);
};
let localSecuritySelection='';
function setLocalSecurity(selection){
  if(remoteAccess||state?.access_scope==='sites')selection='';
  localSecuritySelection=selection;
  $('#local-security-editor').hidden=!selection;
  $('#local-security-close').hidden=!selection;
  $('#gateway-port-editor').hidden=selection!=='gateway';
  $('#admin-port-editor').hidden=selection!=='admin';
  $('#password-details').hidden=selection!=='password';
  document.querySelectorAll('[data-local-setting]').forEach(button=>{
    const selected=button.dataset.localSetting===selection;
    button.setAttribute('aria-expanded',String(selected));
    button.classList.toggle('selected',selected);
  });
}
document.querySelectorAll('[data-local-setting]').forEach(button=>button.onclick=()=>setLocalSecurity(button.dataset.localSetting===localSecuritySelection?'':button.dataset.localSetting));
$('#local-security-close').onclick=()=>setLocalSecurity('');
$('#gateway-retry').onclick=async e=>{const button=e.currentTarget,session=csrf;button.disabled=true;let saved=false;try{await api('gateway-retry',{});saved=true;if(session!==csrf)return;await loadState();if(session===csrf)$('#gateway-port-feedback').textContent='转发网关已启动';}catch(err){if(session===csrf)$('#gateway-port-feedback').textContent=saved?'网关已启动，但页面刷新失败，请手动刷新。':err.message;}finally{button.disabled=false;}};
$('#gateway-port-form').oninput=e=>{e.currentTarget.dataset.dirty='true';};
$('#gateway-port-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget;
  await saveLocalSetting(form,$('#gateway-port-feedback'),'gateway-port',{port:Number(form.elements.port.value)},result=>result.recovered?'网关已启动。请到网站转发同步云端路由。':result.restart_required?'已保存。重新启动 LanBridge 后生效，再到网站转发同步云端路由。':'已取消待生效的端口修改，继续使用当前端口。');
};
function overviewSites(current){
 if(!current.sites.length)return '<div class="overview-empty">暂无网站。完成接入后，可添加要转发的局域网网页。</div>';
 const priority=s=>!s.enabled?3:s.paused?1:publicationStatus(s,current).kind==='warning'?0:2;
 const sites=[...current.sites].sort((a,b)=>priority(a)-priority(b)).slice(0,6);
 const rows=sites.map(site=>{
  const publication=publicationStatus(site,current),label=!site.enabled?'已停用':site.paused?'已暂停':publication.label;
  const kind=!site.enabled||site.paused?'neutral':publication.kind;
  const origin=site.target==='lanbridge'?'http://127.0.0.1:'+current.settings.admin_port:site.origin;
  return `<div class="overview-site"><b class="overview-site-name">${esc(site.name)}</b><a href="https://${esc(site.hostname)}" target="_blank" rel="noopener noreferrer">${esc(site.hostname)}</a><span class="overview-site-origin">源站 ${esc(origin)}</span><span class="badge overview-site-status ${kind}">${esc(label)}</span></div>`;
 }).join('');
 return '<div class="overview-site-list">'+rows+'</div>'+(current.sites.length>6?'<p class="overview-list-note small muted">显示 6 个网站，共 '+current.sites.length+' 个；待处理和已暂停的网站优先。</p>':'');
}
function render(){if(!state)return;renderPublicClientSetting();renderConnectorAutoStart();renderAgentGuide();renderPublicationHint();renderSetup();renderPermissionIssues();renderTokenManager();renderConnectorReadiness();renderNavigationIssues();renderActionAvailability();renderConnectorMaintenance();const sites=state.sites,cfg=state.settings,conn=state.connector;$('#nav-count').textContent=sites.length;$('#stat-sites').textContent=sites.length;$('#stat-enabled').textContent=sites.filter(s=>s.enabled&&!s.paused).length;$('#stat-paused').textContent=sites.filter(s=>s.enabled&&s.paused).length;renderCloudConnection();$('#flow-status').textContent=conn.running?'运行中':conn.last_exit!=null&&conn.last_exit!==0?'异常退出':'未启动';$('#overview-connector-detail').hidden=false;$('#overview-connector-detail').textContent=state.gateway&&state.gateway.running===false?state.gateway.error+' 请在账户与配置 → 本机与安全中恢复网关。':conn.running?'本机网关：127.0.0.1:'+cfg.gateway_port:conn.installed?'可在网站转发中启动连接器。':'尚未安装 Cloudflared';$('#flow-status').className='badge '+(conn.running?'success':'neutral');$('#overview-sites').innerHTML=overviewSites(state);$('#sites-table').innerHTML=pauseNotice(sites)+table(sites);$('#connector-status').textContent=conn.running?'运行中':conn.last_exit!=null&&conn.last_exit!==0?'异常退出（'+conn.last_exit+'）':'已停止';$('#connector-status').className='badge '+(conn.running?'success':'neutral');$('#connector-path').textContent=cfg.cloudflared_path||(conn.installed?'已从 PATH 找到':'尚未找到 cloudflared.exe');$('#tunnel-id').textContent=cfg.tunnel_id||'尚未创建';$('#gateway-address').textContent='127.0.0.1:'+cfg.gateway_port;renderHumanVerification(state);if(!$('#verification-credentials-form').dataset.dirty)$('#verification-credentials-form [name=turnstile_sitekey]').value=cfg.turnstile_sitekey||'';renderAudit();$('#read-token-status').textContent=state.credentials.cf_read_token?'已保存':'未配置';$('#read-token-remove').hidden=!state.credentials.cf_read_token;$('#secret-status').textContent=state.credentials.turnstile_secret?'已保存':'未配置';if(currentView==='settings'&&!$('#settings-form').dataset.dirty)fillSettings();renderTemporaryAccess();}
function fillSettings(){const form=$('#settings-form');for(const el of form.elements)if(el.name)el.value=state.settings[el.name]??'';form.dataset.dirty='';renderConnectorMaintenance();}
const setupFields = [
  {key:'account_id', label:'Account ID', selector:'#settings-form [name=account_id]', help:'填写 Cloudflare 账户 ID，并点击保存配置。'},
  {key:'zone_id', label:'Zone ID', selector:'#settings-form [name=zone_id]', help:'填写域名的区域 ID，并点击保存配置。'},
  {key:'zone_name', label:'Zone 名称', selector:'#settings-form [name=zone_name]', help:'填写已接入 Cloudflare 的域名，例如 example.com。'},
  {key:'cf_write_token', label:'写入 API Token', selector:'#credentials-form [name=cf_write_token]', help:'需具备 Tunnel 编辑、DNS 编辑和 Zone 读取权限，再读取账户与域名。'}
];
let replacingWriteToken=false;
function renderHumanVerification(current){
  const hosts=current.sites.filter(site=>site.enabled&&site.human_check).map(site=>site.hostname);
  const configured=!!current.settings.turnstile_sitekey&&!!current.credentials.turnstile_secret;
  const status=$('#widget-status');
  status.textContent=configured?'已配置':current.settings.turnstile_sitekey?'缺少密钥':'未配置';
  status.className='badge '+(configured?'success':'neutral');
  $('#widget-scope').textContent=hosts.length?'启用网站：'+hosts.join('、'):'暂无启用人类验证的网站';
  $('#widget-create').textContent='自动配置人类验证';
  $('#widget-create').hidden=configured;
  $('#verification-advanced summary').textContent=configured?'更换 Turnstile 密钥':'使用已有 Turnstile 密钥';
  $('#widget-note').textContent=configured?'配置已完成，无需再填写密钥。':hosts.length?'保存启用人类验证的网站时会自动配置；授权需具备 Turnstile 编辑权限。':'添加并保存启用人类验证的网站后，会自动配置；授权需具备 Turnstile 编辑权限。';
}
function renderCredentialsGuide(){
  const saved=!!state.credentials.cf_write_token;
  const oauth=state.token_management?.managed?.kind==='oauth';
  $('#write-token-saved h3').textContent=oauth?'正在使用浏览器授权':'写入 API Token 已保存';
  $('#write-token-replace').textContent=oauth?'改用已有 API Token':'更换写入 API Token';
  $('#write-token-saved p').hidden=true;
  $('#write-token-saved').hidden=!saved||replacingWriteToken||!$('#account-manual-path').hidden;
  $('#write-token-editor').hidden=saved&&!replacingWriteToken&&$('#account-manual-path').hidden;
  $('#write-token-cancel').hidden=!saved;
  const pendingToken=!!$('#credentials-form [name=cf_write_token]').value.trim();
  $('#write-token-status').textContent=pendingToken?'尚未保存':'';
  $('#write-token-status').hidden=!pendingToken;
  $('#token-wizard-title').textContent=saved?'更换写入 API Token':'写入 API Token';
}
function revealWriteToken(){
  renderSetup();
  const input=$('#credentials-form [name=cf_write_token]');
  revealControl(input);input.focus();
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
  if(field.key==='cf_write_token'){replacingWriteToken=!!state.credentials.cf_write_token;revealWriteToken();}
  const input=$(field.selector);
  revealControl(input);
  input.scrollIntoView({behavior:'smooth',block:'center'});
  input.focus({preventScroll:true});
}
function setupProgress(current){
  const count=current.sites.length,title=count?'已登记 '+count+' 个网站':'必填配置已保存';
  if(!count)return {title,detail:'添加第一个网站，填写公网域名和局域网地址。',label:'添加网站 →',next:'add'};
  const enabled=current.sites.filter(site=>site.enabled),cfg=current.settings;
  if(!enabled.length)return {title,detail:'现有网站均未启用，可在网站转发中调整。',label:'管理网站',next:'sites'};
  if(!cfg.tunnel_id)return {title,detail:current.tunnel_pending?'上次隧道创建结果需要核对，请恢复原请求。':'网站已登记，下一步创建隧道。',label:current.tunnel_pending?'核对并恢复隧道 →':'创建隧道 →',next:'sites'};
  if(!current.credentials.tunnel_token)return {title,detail:'隧道已创建，但本机缺少令牌，请在账户与配置中重新获取。',label:'获取隧道令牌 →',next:'settings'};
  if(enabled.some(site=>site.human_check)&&(!cfg.turnstile_sitekey||!current.credentials.turnstile_secret))return {title,detail:'现有网站启用了人类验证，请先完成 Turnstile 密钥配置。',label:'自动配置人类验证 →',next:'verification'};
  const desired=enabled.map(site=>site.hostname).sort(),published=[...(current.published_hosts||[])].sort();
  if(current.publication_needs_review||JSON.stringify(desired)!==JSON.stringify(published))return {title,detail:'网站发布尚未完成，请在网站转发中重试。',label:'查看网站发布',next:'sites'};
  const connector=connectorReadiness(current);
  if(connector.attention)return {title,detail:connector.detail,label:'完成连接器配置 →',next:'sites'};
  return {title,detail:'网站映射已发布',label:'管理网站',next:'sites',complete:true};
}
function renderSetup(){
  renderTokenTemplate();
  renderCredentialsGuide();
  const missing=pendingSetup(),ready=!!state?.cloudflare_setup?.ready,button=$('#add-site');
  button.disabled=!ready;
  button.innerHTML=ready?'<span aria-hidden="true">+</span><span>添加网站</span>':'添加网站（请先完成配置）';
  const overviewAdd=$('#overview-add-site');overviewAdd.disabled=!ready;overviewAdd.title=ready?'添加网站':setupMessage();
  button.title=ready?'添加网站':setupMessage();
  $('#setup-required').hidden=ready||currentView==='overview';
  $('#setup-required-message').textContent=missing.length===1?'只差一步：保存'+missing[0].label+'，即可添加网站。':`已完成 ${setupFields.length-missing.length}/4 项，完成下面的必填配置后即可添加网站。`;
  $('#setup-required-list').innerHTML=setupFields.map(field=>{
    const pending=missing.includes(field);
    return `<li class="${pending?'pending':'complete'}">${pending?`<button type="button" data-setup="${field.key}"><span>○ 待配置</span> ${esc(field.label)} →</button>`:`<span>✓ 已保存</span> ${esc(field.label)}`}</li>`;
  }).join('');
  $('#setup-next').textContent=missing[0]?'去配置'+missing[0].label+' →':'配置已完成';
  $('#setup-next').dataset.setup=missing[0]?.key||'';
  const progress=ready?setupProgress(state):null;
  $('#overview-next-step').hidden=!!progress?.complete;
  $('#setup-progress').textContent=ready?(progress.next==='add'?'添加第一个网站':'需要完成的配置'):'完成 Cloudflare 接入';
  $('#setup-progress-detail').textContent=ready?progress.detail:'选择浏览器授权或写入 API Token 接入，完成后即可添加网站。';
  const nextButton=$('#setup-progress-add');
  nextButton.hidden=false;
  nextButton.textContent=(progress?.label||'前往账户与配置').replace(/ →$/,'');
  nextButton.dataset.next=progress?.next||'settings';
  for(const field of setupFields){
    const input=$(field.selector),pending=missing.includes(field),label=input.closest('label'),hint=$('#hint-'+field.key);
    const unsaved=field.key==='cf_write_token'?!!input.value.trim():!!$('#settings-form').dataset.dirty&&input.value!==(state.settings[field.key]||'');
    label.classList.toggle('needs-setup',pending||unsaved);
    input.setAttribute('aria-required','true');
    input.setAttribute('aria-describedby','hint-'+field.key);
    hint.textContent=unsaved?'尚未保存。'+field.help:pending?field.help:field.key==='cf_write_token'?(replacingWriteToken?'保存新令牌后才会替换当前接入方式。':'已保存，留空保留当前令牌。'):'';
    if(field.key==='cf_write_token'&&replacingWriteToken&&!input.value.trim())hint.textContent='确认接入后才会替换当前授权。';
    hint.className='field-hint '+(pending||unsaved?'pending':'complete');
  }
  $('#credentials-form').classList.toggle('needs-attention',missing.some(field=>field.key==='cf_write_token'));
}
function invalidatePlan(){plan=null;$('#plan-content').hidden=true;}
function revealControl(control){
  if(control?.closest?.('#account-manual-path'))setAccountAccess('manual');
  if(control?.closest?.('#account-browser-path'))setAccountAccess('browser');
  for(let parent=control?.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
}
function updateSiteControls(){
  const form=$('#site-form');
  const own=form.elements.target?.value==='lanbridge';
  if(form.elements.target){
    $('#site-origin-field').hidden=own;$('#site-origin-hint').hidden=own;$('#site-lanbridge-hint').hidden=!own;
    form.elements.origin.required=!own;form.elements.origin.disabled=own;
    $('#site-protocol-options').hidden=own;
    if(own){form.elements.protocol_http.checked=true;form.elements.protocol_websocket.checked=false;}
  }
  $('#site-passcode-field').hidden=!form.elements.passcode_required.checked;
  $('#policy-save-hint').hidden=!form.elements.id.value||!(form.elements.human_check.checked||form.elements.passcode_required.checked);
}
function selectedProtocols(form){return ['http','websocket'].filter(key=>form.elements['protocol_'+key].checked);}
function editSite(id){if(!state)return;if(!id&&!state.cloudflare_setup?.ready){locateSetup();toast(setupMessage(),true);return;}const site=state.sites.find(s=>s.id===id);const form=$('#site-form');form.reset();form.elements.zone_id.innerHTML=configuredZones(state).map(z=>`<option value="${esc(z.zone_id)}">${esc(z.zone_name)}</option>`).join('');form.elements.id.value='';form.elements.hostname.readOnly=false;$('#site-error').textContent='';$('#site-dialog-title').textContent=site?'编辑网站与策略':'添加网站';$('#policy-save-hint').hidden=!site;if(site){for(const el of form.elements){if(!el.name||el.name==='passcode'||el.name.startsWith('protocol_'))continue;const v=site[el.name];if(el.type==='checkbox')el.checked=!!v;else el.value=Array.isArray(v)?v.join(', '):v??'';}form.elements.hostname.readOnly=true;}form.elements.zone_id.value=site?.zone_id||configuredZones(state).filter(z=>site?.hostname.endsWith('.'+z.zone_name)).sort((a,b)=>b.zone_name.length-a.zone_name.length)[0]?.zone_id||state.settings.zone_id;form.elements.zone_id.disabled=!!site;form.elements.hostname.placeholder='app.'+(configuredZones(state).find(z=>z.zone_id===form.elements.zone_id.value)?.zone_name||'example.com');form.elements.target.value=site?.target||'website';const protocols=site?.protocols||['http','websocket'];for(const key of ['http','websocket'])form.elements['protocol_'+key].checked=protocols.includes(key);updateSiteControls();$('#site-dialog').showModal();$('.site-dialog-body').scrollTop=0;}
async function setSitePaused(id){
  const site=state?.sites.find(row=>row.id===id),session=csrf;
  if(!site||sitePausePending.has(id))return;
  sitePausePending.add(id);sitePauseErrors.delete(id);render();
  try{
    const result=await api('sites/'+id+'/pause',{paused:!site.paused});
    if(session!==csrf||!state)return;
    if(result.id===id)state.sites=state.sites.map(row=>row.id===id?result:row);
    invalidatePlan();render();
    try{await loadState();}catch{if(session===csrf&&state)sitePauseErrors.set(id,'操作已完成，状态刷新失败；请刷新页面确认。');}
  }catch(err){if(session===csrf&&state){sitePauseErrors.set(id,err.message);await loadState().catch(()=>{});}}
  finally{sitePausePending.delete(id);if(session===csrf&&state)render();}
}
async function action(button,path,message,data={}){if(button.dataset.busy)throw Error('操作正在进行，请稍候');const session=csrf;button.dataset.busy='true';button.disabled=true;try{const result=await api(path,data);if(session!==csrf)throw Error('登录状态已变化，请在当前会话核对操作结果');if(state){for(const key of ['settings','credentials','cloudflare_setup','cloudflare_permission_issues'])if(result[key])state[key]=result[key];}if(message)toast(message);try{await loadState()}catch{if(session===csrf){if(state)render();toast('操作已完成，但状态刷新失败。请刷新状态，勿重复提交。',true)}}if(session!==csrf)throw Error('登录状态已变化，请在当前会话核对操作结果');return result}catch(e){if(session===csrf){toast(e.message,true);await loadState().catch(()=>{});}throw e}finally{button.dataset.busy='';button.disabled=false;if(state)renderActionAvailability();}}
$('#auth-form').addEventListener('submit',async e=>{e.preventDefault();const button=$('#auth-submit');button.disabled=true;$('#auth-error').textContent='';try{const data=Object.fromEntries(new FormData(e.target));if(!initialized){await api('setup',data);initialized=true;}const result=await api('login',data);csrf=result.csrf;e.target.elements.password.value='';openShell();}catch(err){$('#auth-error').textContent=err.message}finally{button.disabled=false}});
document.addEventListener('click',async e=>{const el=e.target.closest('button');if(!el)return;if(el.dataset.copy){try{await navigator.clipboard.writeText($('#'+el.dataset.copy).textContent);markCopied(el)}catch{toast('复制失败，请手动选择示例内容复制',true)}}if(el.dataset.addSite){el.disabled=true;try{await $('#add-site').onclick()}finally{el.disabled=false}}if(el.dataset.view)go(el.dataset.view);if(el.dataset.goto){go(el.dataset.goto);if(el.dataset.section){const section=document.getElementById(el.dataset.section);if(section&&!section.closest('[hidden]')){section.focus({preventScroll:true});section.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}}}if(el.dataset.setup)locateSetup(el.dataset.setup);if(el.dataset.tokenManager)locateTokenManager();if(el.dataset.browserAuthorize)await startBrowserAuthorization(el);if(el.dataset.edit)editSite(el.dataset.edit);if(el.dataset.pauseSite)await setSitePaused(el.dataset.pauseSite);if(el.dataset.probe){try{const r=await action(el,'sites/'+el.dataset.probe+'/probe');toast(r.reachable?'源站可达 · HTTP '+r.http_status:'局域网源站暂不可达',!r.reachable)}catch{}}});
$('#add-site').onclick=async()=>{const button=$('#add-site');button.disabled=true;try{await loadState();editSite();}catch(e){toast('无法检查配置，请刷新后重试：'+e.message,true)}finally{if(state)renderSetup();}};$('#close-dialog').onclick=$('#cancel-site').onclick=()=>$('#site-dialog').close();$('#refresh').onclick=refreshState;
$('#logout').onclick=async()=>{try{await api('logout',{});showAuth()}catch(e){toast(e.message,true)}};
$('#shutdown').onclick=()=>{$('#shutdown-error').textContent='';$('#shutdown-dialog').showModal();};
$('#cancel-shutdown').onclick=()=>$('#shutdown-dialog').close();
$('#confirm-shutdown').onclick=async e=>{const button=e.currentTarget;button.disabled=true;$('#cancel-shutdown').disabled=true;try{await api('shutdown',{});$('#shutdown-dialog').close();showAuth();$('#auth').hidden=true;$('#platform-stopped').hidden=false;}catch(err){$('#shutdown-error').textContent='退出请求未确认：'+err.message+'。请检查平台是否仍在运行；也可在启动终端按 Ctrl+C。';}finally{button.disabled=false;$('#cancel-shutdown').disabled=false;}};
function updateSettingsDirty(){
  if(!state)return;
  const form=$('#settings-form'),feedback=$('#settings-feedback');
  const changed=[...form.elements].some(el=>el.name&&el.value!==String(state.settings[el.name]??''));
  form.dataset.dirty=changed?'true':'';
  if(changed)feedback.textContent='有未保存的修改';
  else if(feedback.textContent==='有未保存的修改')feedback.textContent='';
  renderSetup();
}
$('#settings-form').oninput=$('#settings-form').onchange=updateSettingsDirty;
$('#cloudflared-path').oninput=$('#cloudflared-path').onchange=()=>{if(state&&$('#cloudflared-path').value!==(state.settings.cloudflared_path||'')){connectorUpdate=null;renderConnectorMaintenance();}updateSettingsDirty();};
const tokenDiscoveries={};
function clearTokenDiscovery(kind){delete tokenDiscoveries[kind];$('#'+kind+'-zone-choice').hidden=true;$('#'+kind+'-manual').hidden=true;}
async function discoverTokenAccess(kind){
  const session=csrf;
  const input=kind==='api-token'?$('#credentials-form [name=cf_write_token]'):$('#token-manager-form [name=authority]');
  const feedback=$('#'+kind+'-discovery-feedback'),button=$('#'+kind+'-discover'),token=input.value.trim();
  if(button.dataset.busy)return;
  if(!token){clearTokenDiscovery(kind);feedback.textContent=kind==='api-token'?'请先粘贴写入 API Token':'请先粘贴管理授权令牌';input.focus();return;}
  clearTokenDiscovery(kind);feedback.textContent='正在读取账户与域名…';button.dataset.busy='true';button.disabled=true;
  try{
    const result=await api('cloudflare/token-discover',{token});
    if(session!==csrf||input.value.trim()!==token||!state)return;
    tokenDiscoveries[kind]=result;
    $('#'+kind+'-zone-select').innerHTML=result.zones.map((z,i)=>'<option value="'+i+'">'+esc(z.zone_name+' · '+(z.account_name||z.account_id))+'</option>').join('');
    $('#'+kind+'-zone-choice').hidden=false;feedback.textContent='请选择账户与默认域名，ID 将自动填写。';
  }catch(err){if(session===csrf){feedback.textContent=err.message;$('#'+kind+'-manual').hidden=false;}}
  finally{button.dataset.busy='';button.disabled=false;}
}
async function connectTokenAccess(kind){
  const session=csrf;
  const result=tokenDiscoveries[kind],choice=result?.zones[Number($('#'+kind+'-zone-select').value)];
  const input=kind==='api-token'?$('#credentials-form [name=cf_write_token]'):$('#token-manager-form [name=authority]');
  const feedback=$('#'+kind+'-discovery-feedback'),button=$('#'+kind+'-connect');
  if(!choice||button.dataset.busy)return;
  if($('#settings-form').dataset.dirty){feedback.textContent='账户配置有未保存的修改，请先保存配置，再重新读取。';return;}
  const form=input.form;lockForm(form);button.dataset.busy='true';feedback.textContent='正在核验并保存…';
  try{
    await api('cloudflare/token-connect',{token:input.value.trim(),revision:result.revision,zone_id:choice.zone_id,account_id:choice.account_id,save_token:kind==='api-token',cf_read_token:kind==='api-token'?form.elements.cf_read_token.value.trim():''});
    if(session!==csrf)return;
    if(kind==='api-token'){form.reset();replacingWriteToken=false;$('#credentials-feedback').textContent='API Token、账户与域名已保存';}
    $('#settings-form').dataset.dirty='';clearTokenDiscovery(kind);invalidatePlan();
    await loadState();if(session!==csrf||!state)return;fillSettings();feedback.textContent=kind==='api-token'?'API Token、账户与域名已保存':'账户与域名已保存，可创建 API Token。';
  }catch(err){if(session===csrf)feedback.textContent=err.message;}
  finally{button.dataset.busy='';unlockForm(form);if(state){renderSetup();renderTokenManager();}}
}
for(const kind of ['api-token','authority-token']){
  $('#'+kind+'-discover').onclick=()=>discoverTokenAccess(kind);
  $('#'+kind+'-connect').onclick=()=>connectTokenAccess(kind);
  const input=kind==='api-token'?$('#credentials-form [name=cf_write_token]'):$('#token-manager-form [name=authority]');
  input.addEventListener('input',()=>{clearTokenDiscovery(kind);$('#'+kind+'-discovery-feedback').textContent='';});
}
$('#credentials-form').oninput=e=>{$('#credentials-feedback').textContent=e.target.name==='cf_read_token'&&e.target.value.trim()?'只读令牌尚未保存':'';renderSetup();};
$('#setup-progress-add').onclick=()=>{const next=$('#setup-progress-add').dataset.next;if(next==='add')return $('#add-site').onclick();if(next==='verification'){go('settings');$('#human-verification-panel').scrollIntoView({behavior:'smooth',block:'center'});return $('#widget-create').onclick({currentTarget:$('#widget-create')});}if(next)go(next);};
$('#settings-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,data=Object.fromEntries(new FormData(form));lockForm(form);try{await action(e.submitter,'settings','配置已保存',data);form.dataset.dirty='';invalidatePlan();await loadState().catch(()=>{});if(state)fillSettings();$('#settings-feedback').textContent='配置已保存';}catch(err){$('#settings-feedback').textContent=err.message}finally{unlockForm(form);if(state)renderSetup();}};
$('#credentials-form').onsubmit=async e=>{e.preventDefault();const values=Object.fromEntries(new FormData(e.target));if(String(values.cf_write_token||'').trim()&&!$('#manual-account-config').open){await discoverTokenAccess('api-token');return;}if(!Object.values(values).some(value=>String(value).trim())||(replacingWriteToken&&!String(values.cf_write_token||'').trim())){$('#credentials-feedback').textContent='请先粘贴要保存的新令牌；无需更换时点击取消更换。';if(!state.credentials.cf_write_token||replacingWriteToken)revealWriteToken();return;}lockForm(e.target);try{await action(e.submitter,'credentials','凭据已保存',values);e.target.reset();replacingWriteToken=false;invalidatePlan();renderSetup();$('#credentials-feedback').textContent=state.cloudflare_setup.ready?'凭据已保存，配置已就绪。':'凭据已保存。'+setupMessage();}catch(err){$('#credentials-feedback').textContent=err.message}finally{unlockForm(e.target);if(state)renderSetup();}};
function updateVerificationDirty(){
  if(!state)return;
  const form=$('#verification-credentials-form'),feedback=$('#verification-feedback');
  const changed=form.elements.turnstile_sitekey.value.trim()!==(state.settings.turnstile_sitekey||'').trim()||Boolean(form.elements.turnstile_secret.value.trim());
  form.dataset.dirty=changed?'true':'';
  if(changed)feedback.textContent='有未保存的修改';
  else if(feedback.textContent==='有未保存的修改')feedback.textContent='';
}
$('#verification-credentials-form').oninput=updateVerificationDirty;
$('#verification-credentials-form').onchange=updateVerificationDirty;
$('#verification-credentials-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget,feedback=$('#verification-feedback');
  const sitekey=form.elements.turnstile_sitekey.value.trim(),secret=form.elements.turnstile_secret.value.trim();
  if(!sitekey){feedback.textContent='请填写 Site Key';return;}
  if((!state.credentials.turnstile_secret||sitekey!==state.settings.turnstile_sitekey)&&!secret){feedback.textContent='请同时填写与 Site Key 对应的 Secret Key';return;}
  if(secret&&(secret.length<10||secret.length>4096)){feedback.textContent='Secret Key 长度无效';return;}
  lockForm(form);let secretSaved=false,configSaved=false;
  try{
    if(secret){await api('credentials',{turnstile_secret:secret});secretSaved=true;form.elements.turnstile_secret.value='';}
    await api('settings',{...state.settings,turnstile_sitekey:sitekey});configSaved=true;
    form.dataset.dirty='';invalidatePlan();await loadState();feedback.textContent='人类验证配置已保存';
  }catch(err){feedback.textContent=(configSaved?'配置已保存，但状态刷新失败，请刷新页面确认。':secretSaved?'Secret Key 已保存，Site Key 未确认保存，请重试。':'')+err.message;await loadState().catch(()=>{});}
  finally{unlockForm(form);}
};
$('#password-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,feedback=$('#password-feedback');if(form.dataset.busy)return;const values=Object.fromEntries(new FormData(form));feedback.textContent='';lockForm(form);try{await api('password',values);form.reset();setLocalSecurity('');showAuth();toast('密码已修改，请重新登录')}catch(err){setLocalSecurity('password');feedback.textContent=err.message}finally{unlockForm(form)}};
$('#site-form').onchange=updateSiteControls;
$('#site-form').onsubmit=async e=>{e.preventDefault();const f=e.target;const data=Object.fromEntries(new FormData(f));data.protocols=selectedProtocols(f);delete data.protocol_http;delete data.protocol_websocket;if(!data.protocols.length){$('#site-error').textContent='请至少选择一种转发协议';return;}for(const k of ['enabled','human_check','passcode_required'])data[k]=f.elements[k].checked;for(const k of ['allowed_countries','allowed_ips'])data[k]=data[k].split(/[,，\s]+/).filter(Boolean);for(const k of ['requests_per_minute','session_minutes'])data[k]=Number(data[k]);lockForm(f);try{const result=await action(e.submitter,'sites',null,data);toast(result.publication?.status==='failed'?'网站已保存，自动发布失败；请查看发布提示':result.publication?.status==='published'?'网站已保存并发布，云端核验通过':'网站配置已生效',result.publication?.status==='failed');f.elements.passcode.value='';$('#site-dialog').close();invalidatePlan()}catch(err){$('#site-error').textContent=err.message}finally{unlockForm(f);}};
for(const [id,path,msg] of [['connector-start','connector/start','连接器已启动'],['connector-stop','connector/stop','连接器已停止']])$('#'+id).onclick=async e=>{try{await action(e.currentTarget,path,msg)}catch{}};
$('#overview-cf-check').onclick=$('#cf-check').onclick=async e=>{
  const button=e.currentTarget;if(button.dataset.busy||cloudCheck.pending)return;
  const session=csrf,tunnel=state.settings.tunnel_id;
  cloudCheck={pending:true,error:'',tunnel};for(const id of ['cf-check','overview-cf-check'])$('#'+id).dataset.busy='true';renderActionAvailability();renderCloudConnection();
  try{const result=await api('cloudflare/check',{});if(session!==csrf||!state||state.settings.tunnel_id!==tunnel)return;state.cloudflare=result;cloudCheck.pending=false;renderCloudConnection();await loadState().catch(()=>{});}
  catch(err){if(session===csrf&&state&&state.settings.tunnel_id===tunnel){cloudCheck={pending:false,error:err.message,tunnel};renderCloudConnection();}}
  finally{for(const id of ['cf-check','overview-cf-check'])$('#'+id).dataset.busy='';if(state)renderActionAvailability();}
};
$('#widget-create').onclick=async e=>{
  try{
    const result=await action(e.currentTarget,'cloudflare/turnstile-auto',null,{});
    if(['preparing','authorizing','creating'].includes(result.phase)){go('settings');$('#browser-authorize').scrollIntoView({behavior:'smooth',block:'center'});}
    else toast('人类验证已自动配置，可继续预览并发布网站');
  }catch{}
};
function renderRoutePlan(value){
  const before=value.before?.ingress||[],after=value.after?.ingress||[];
  const previous=new Map(before.filter(rule=>rule.hostname).map(rule=>[rule.hostname,rule]));
  const next=new Map(after.filter(rule=>rule.hostname).map(rule=>[rule.hostname,rule]));
  const rows=[];
  for(const [host,rule] of next){const old=previous.get(host);if(!old||JSON.stringify(old)!==JSON.stringify(rule))rows.push([old?'更新路由':'新增路由',host]);}
  for(const host of previous.keys())if(!next.has(host))rows.push(['移除路由',host]);
  for(const record of value.dns||[])if(record.create)rows.push(['新增 DNS',record.hostname]);
  $('#plan-summary').innerHTML=rows.length?'<ul class="route-change-list">'+rows.map(([label,host])=>`<li><span>${esc(label)}</span><b>${esc(host)}</b></li>`).join('')+'</ul>':'<p class="small muted">云端路由与当前网站配置一致，无需发布。</p>';
  $('#plan-json').textContent=JSON.stringify(value,null,2);
  $('#apply').textContent='发布变更并核验';
  $('#apply').hidden=!value.routes_changed&&!(value.dns||[]).some(record=>record.create);
  $('#plan-content').hidden=false;
}
let publicationRetryPending=false;
$('#publication-retry').onclick=async e=>{
  if(publicationRetryPending)return;
  const button=e.currentTarget,feedback=$('#publish-feedback');
  publicationRetryPending=true;button.dataset.busy='true';button.disabled=true;invalidatePlan();
  feedback.hidden=false;feedback.textContent='正在检查并重新发布…';
  try{const checked=await api('cloudflare/preview',{});await api('cloudflare/apply',{revision:checked.revision});feedback.textContent='网站路由已发布并通过核验';}
  catch(err){feedback.textContent='发布未完成：'+err.message;}
  finally{publicationRetryPending=false;delete button.dataset.busy;await loadState().catch(()=>{});if(state)renderActionAvailability();}
};
$('#plan-cancel').onclick=()=>invalidatePlan();
$('#preview').onclick=async e=>{
  const button=e.currentTarget;
  if(publicationRetryPending||button.dataset.busy)return;
  publicationRetryPending=true;invalidatePlan();const feedback=$('#publish-feedback');
  feedback.hidden=false;feedback.textContent='正在检查 Cloudflare 路由与 DNS…';
  try{
    plan=await action(button,'cloudflare/preview');
    if(!plan.routes_changed&&!(plan.dns||[]).some(record=>record.create)){
      // This unchanged snapshot is read back and recorded without cloud writes.
      await action(button,'cloudflare/apply',null,{revision:plan.revision});
      invalidatePlan();feedback.textContent='检查完成：云端路由与当前网站配置一致，无需发布。';
    }else{renderRoutePlan(plan);feedback.hidden=true;}
  }catch(err){invalidatePlan();feedback.textContent=err.message;}
  finally{publicationRetryPending=false;if(state)renderActionAvailability();}
};
$('#apply').onclick=async e=>{if(!plan||publicationRetryPending)return;const feedback=$('#publish-feedback');feedback.hidden=false;feedback.textContent='正在发布并核验…';try{await action(e.currentTarget,'cloudflare/apply',null,{revision:plan.revision});invalidatePlan();feedback.textContent='网站路由已发布并通过核验';}catch(err){invalidatePlan();feedback.textContent=err.message;}};
api('bootstrap').then(r=>{initialized=r.initialized;csrf=r.csrf;remoteAccess=r.remote===true;publicClientEnabled=r.public_client_enabled!==false;renderPublicClientSetting();window.LB_REMOTE=remoteAccess;window.localLoginUI?.setRemote?.(remoteAccess);document.body.classList.toggle('remote-management',remoteAccess);if(remoteAccess)document.querySelectorAll('.remote-only').forEach(el=>el.hidden=false);if(r.authenticated)openShell();else showAuth()}).catch(e=>$('#auth-error').textContent=e.message).finally(()=>document.body.classList.remove('auth-pending'));
let automaticStateRefresh=null;
function pollState(){
  if(!csrf||document.hidden||automaticStateRefresh)return;
  automaticStateRefresh=loadState().catch(()=>{}).finally(()=>{automaticStateRefresh=null;});
}
setInterval(pollState,15000);

let connectorUpdate=null,connectorUpdateBusy=false;
function renderConnectorMaintenance(){
 if(!state)return;
 const installed=state.connector.installed,current=connectorUpdate?.current||state.connector.version||'',input=$('#cloudflared-path');
 const custom=input.value.trim()!==(state.settings.cloudflared_path||'');
 const newer=installed&&connectorUpdate?.available&&!custom;
 const versionNumber=current.match(/cloudflared version (\d{4}\.\d+\.\d+)/i)?.[1];
 $('#connector-version').textContent=(installed?(versionNumber?'当前版本 '+versionNumber:'已安装 · 版本暂不可用'):'尚未安装')+(connectorUpdate?' · 最新版本 '+connectorUpdate.latest:'');
 $('#connector-ensure').hidden=installed||custom||remoteAccess;
 $('#connector-check-update').hidden=!installed||custom||(newer&&!remoteAccess);
 $('#connector-update').hidden=!newer||remoteAccess;
 $('#connector-update').textContent='更新到 '+(connectorUpdate?.latest||'');
 $('#connector-update-note').hidden=!newer||remoteAccess;
 $('#connector-use-path').hidden=remoteAccess;
 $('#connector-use-path').disabled=connectorUpdateBusy||!input.value.trim();
 for(const id of ['connector-ensure','connector-check-update','connector-update'])$('#'+id).disabled=connectorUpdateBusy;
}
async function prepareConnector(custom){
 if(connectorUpdateBusy||remoteAccess)return;
 const input=$('#cloudflared-path'),feedback=$('#connector-update-feedback');
 if(custom&&!input.value.trim())return;
 connectorUpdateBusy=true;renderConnectorMaintenance();
 feedback.textContent=custom?'正在验证指定程序…':'正在安装 Cloudflared…';
 try{
  const result=await api('connector/ensure',{path:custom?input.value.trim():''});
  input.value=result.path;connectorUpdate=null;
  await loadState();
  feedback.textContent=(custom?'指定程序已验证并使用':'Cloudflared 已安装')+' '+result.version;
 }catch(err){feedback.textContent=err.message;}
 finally{connectorUpdateBusy=false;renderConnectorMaintenance();}
}
$('#connector-ensure').onclick=()=>prepareConnector(false);
$('#connector-use-path').onclick=()=>prepareConnector(true);
$('#connector-check-update').onclick=async()=>{
 if(connectorUpdateBusy)return;
 connectorUpdateBusy=true;connectorUpdate=null;renderConnectorMaintenance();
 const feedback=$('#connector-update-feedback');feedback.textContent='正在检查官方版本…';
 try{connectorUpdate=await api('connector/check-update',{});feedback.textContent=connectorUpdate.available?(remoteAccess?'有新版本，请在本机管理台更新。':'有新版本可用'):'当前已是最新版本';}
 catch(err){feedback.textContent=err.message;}
 finally{connectorUpdateBusy=false;renderConnectorMaintenance();}
};
$('#connector-update').onclick=async()=>{
 if(connectorUpdateBusy||!connectorUpdate||remoteAccess)return;
 connectorUpdateBusy=true;renderConnectorMaintenance();
 const feedback=$('#connector-update-feedback');feedback.textContent='正在下载并校验；完成后将切换连接器…';
 try{const result=await api('connector/update',{version:connectorUpdate.latest});$('#cloudflared-path').value=result.path;connectorUpdate=null;await loadState();feedback.textContent='更新完成'+(result.running_restored?'，连接器已恢复运行。':'。');}
 catch(err){feedback.textContent=err.message+'。可检查状态后重试。';}
 finally{connectorUpdateBusy=false;renderConnectorMaintenance();}
};

$('#write-token-replace').onclick=()=>{replacingWriteToken=true;revealWriteToken();};
$('#write-token-cancel').onclick=()=>{setAccountAccess('browser');const input=$('#credentials-form [name=cf_write_token]');input.value='';replacingWriteToken=false;$('#credentials-feedback').textContent='已取消更换，继续使用原令牌。';renderSetup();};


function locateTunnelToken(){
  go('settings');const details=$('#tunnel-maintenance');details.open=true;details.scrollIntoView({behavior:'smooth',block:'center'});$('#tunnel-token-refresh').focus({preventScroll:true});
}
$('#tunnel-token-refresh').onclick=async e=>{
  const feedback=$('#tunnel-token-feedback');feedback.className='form-feedback';feedback.textContent='正在从 Cloudflare 获取隧道令牌…';
  try{await action(e.currentTarget,'cloudflare/create-tunnel');feedback.textContent=state.connector.running?'令牌已保存。请到网站转发停止并重新启动连接器，新令牌才会生效。':'令牌已保存，可到网站转发启动连接器。';}
  catch(err){feedback.className='form-feedback error';feedback.textContent=err.message;await loadState().catch(()=>{});}
};
$('#create-tunnel').onclick=async e=>{
  if(state.settings.tunnel_id)return locateTunnelToken();
  const feedback=$('#tunnel-feedback'), help=$('#tunnel-permission-help');
  feedback.hidden=false;feedback.className='form-feedback';feedback.textContent=state.settings.tunnel_id?'正在获取连接令牌…':state.tunnel_pending?'正在核对并恢复上次创建结果…':'正在创建隧道…';help.hidden=true;
  try{await action(e.currentTarget,'cloudflare/create-tunnel');feedback.textContent='连接令牌已保存，可启动连接器。';}
  catch(err){feedback.className='form-feedback error';feedback.textContent=err.message;help.hidden=!err.message.includes('HTTP 403');await loadState().catch(()=>{});}
};

function renderPermissionIssues(){
  const issues=state.cloudflare_permission_issues||[];
  $('#cloudflare-permission-alert').hidden=!issues.length;
  $('#cloudflare-permission-details').innerHTML=issues.map(issue=>'<p>'+ (issue.status==='needs_recheck'?'凭据已更新，需重试原操作验证。上次记录：':'上次操作失败记录：')+esc(issue.detail)+'<br><small>上次失败：'+new Date(issue.checked_at*1000).toLocaleString('zh-CN')+'</small></p>').join('');
  $('#credentials-form [name=cf_write_token]').closest('label').classList.toggle('permission-field-error',issues.some(issue=>issue.credential==='cf_write_token'&&issue.status!=='needs_recheck'));
  $('#read-token-save').form.elements.cf_read_token.closest('label').classList.toggle('permission-field-error',issues.some(issue=>issue.credential==='cf_read_token'&&issue.status!=='needs_recheck'));
}

function locateTokenManager(){
  go('settings');
  const form=$('#token-manager-form'),feedback=$('#token-manager-feedback');
  revealControl(form.elements.authority);

  const readOnly=(state?.cloudflare_permission_issues||[]).length>0&&(state.cloudflare_permission_issues||[]).every(issue=>issue.credential==='cf_read_token');
  const target=readOnly?'只读':'写入';
  $('#token-operation').value=readOnly?'token-repair-read':'token-repair-write';renderTokenOperation();
  feedback.className='form-feedback';
  feedback.textContent=(state?.token_management?.authority_saved?'授权令牌已保存，输入框可留空。':'请在“授权令牌”输入框粘贴 API Tokens Write 授权令牌。')+'可创建新的'+target+'令牌，或修复当前'+target+'令牌权限。新建会替换本机凭据；修复保留令牌值。';
  form.elements.authority.focus({preventScroll:true});
  form.elements.authority.scrollIntoView({behavior:'smooth',block:'center'});
}

async function startBrowserAuthorization(button){
  browserAuthInteraction=true;
  go('settings');
  $('#account-config-details').open=true;
  $('#browser-authorize').scrollIntoView({behavior:'smooth',block:'center'});
  try{await action(button,'cloudflare/browser-authorize',null,{browser:$('#cloudflare-browser').value});}
  catch(err){$('#browser-auth-status').textContent=err.message;$('#browser-auth-status').className='form-feedback error';}
}

async function loadCloudflareBrowsers(){
  try{
    const result=await api('local-login/browsers');
    const select=$('#cloudflare-browser');
    select.innerHTML=result.browsers.map(browser=>'<option value="'+esc(browser.id)+'">'+esc(browser.name)+'</option>').join('');
    let saved='default';try{saved=localStorage.getItem('lb-cloudflare-browser')||'default';}catch{}
    select.value=result.browsers.some(browser=>browser.id===saved)?saved:'default';
  }catch{}
}
$('#cloudflare-browser').onchange=()=>{try{localStorage.setItem('lb-cloudflare-browser',$('#cloudflare-browser').value);}catch{}};
loadCloudflareBrowsers();
let browserAuthPoll;
let browserAuthInteraction=false;
function browserAuthMessage(job){
  const messages={idle:'',preparing:'正在准备浏览器授权…',creating:'正在核验权限并完成配置…',cancelling:'正在取消授权…',cancelled:'本次授权已取消，已有配置保持不变。',done:'本次授权已完成，配置已保存。'};
  if(job.phase!=='authorizing')return messages[job.phase]??job.message;
  const message='请在浏览器中确认授权。';
  if(!Number.isFinite(job.updated_at))return message;
  const seconds=Math.max(0,Math.ceil(120-(Date.now()/1000-job.updated_at)));
  return message+(seconds>0?' 剩余约 '+seconds+' 秒。':' 即将超时，可重新打开授权页或取消。');
}
function configuredZones(current){const cfg=current?.settings||{};return cfg.zones?.length?cfg.zones:cfg.zone_id&&cfg.zone_name?[{zone_id:cfg.zone_id,zone_name:cfg.zone_name}]:[];}
function accountConfiguration(current){
 const cfg=current.settings||{},credentials=current.credentials||{},zones=configuredZones(current);
 const missing=[];if(!cfg.account_id)missing.push('Account ID');if(!zones.length)missing.push('域名');if(!credentials.cf_write_token)missing.push('管理凭据');
 const ready=missing.length===0,issues=(current.cloudflare_permission_issues||[]).filter(issue=>issue.credential!=='cf_read_token');
 const hasAny=!!(cfg.account_id||cfg.zone_id||cfg.zone_name||credentials.cf_write_token);
 return {ready,label:!ready?(hasAny?'配置不完整':'未配置'):issues.length?'凭据待检查':current.token_management?.managed?.kind==='oauth'?'已连接':'已配置',
  detail:!ready?'缺少：'+missing.join('、'):'已接入 '+zones.length+' 个域名'+(issues.length?' · 请检查管理凭据权限':''),attention:!ready||issues.length>0};
}
function setAccountAccess(mode){
 const manual=mode==='manual';
 $('#account-browser-path').hidden=manual;
 $('#account-manual-path').hidden=!manual;
 $('#account-use-browser').ariaPressed=String(!manual);
 $('#account-use-manual').ariaPressed=String(manual);
}
function renderAccountConfiguration(active){
 const info=accountConfiguration(state),details=$('#account-config-details');
 $('#account-config-status').textContent=info.label;
 $('#account-config-status').className='badge '+(info.attention?'neutral':'success');
 $('#account-config-summary').textContent=info.detail;
 const zones=configuredZones(state);
 $('#configured-zones').innerHTML=zones.map(z=>`<div class="configured-zone"><span>${esc(z.zone_name)}</span>${z.zone_id===state.settings.zone_id?'<span class="small muted">默认域名</span>':`<button type="button" class="text-button" data-remove-zone="${esc(z.zone_id)}">移除</button>`}</div>`).join('');
 $('#zones-discover').disabled=!state.settings.account_id||!state.credentials.cf_write_token||!!$('#zones-discover').dataset.busy;
 $('#zones-discover').hidden=!zones.length||!state.settings.account_id||!state.credentials.cf_write_token;
 if($('#zones-discover').hidden)$('#zone-management').hidden=true;
 $('#account-config-toggle').textContent=info.ready?'修改接入方式':'完成接入';
 const key=String(info.ready);
 if(details.dataset.ready!==key&&!$('#settings-form').dataset.dirty){details.open=!info.ready;details.dataset.ready=key;}
 const phase=state.browser_auth?.phase||'idle';
 const completed=['preparing','authorizing','choosing_zone','creating','cancelling'].includes(details.dataset.authPhase)&&phase==='done';
 if(completed&&info.ready&&!$('#settings-form').dataset.dirty)details.open=false;
 details.dataset.authPhase=phase;
 if(active){details.open=true;setAccountAccess('browser');}
}
function renderBrowserAuth(){
  const job=state.browser_auth||{phase:"idle",message:"在浏览器授权后自动接入 Cloudflare。"},active=["preparing","authorizing","choosing_zone","creating","cancelling"].includes(job.phase);
  renderAccountConfiguration(active);
  $("#browser-auth-status").textContent=job.phase==='cancelled'&&!browserAuthInteraction?'':browserAuthMessage(job);
  $("#browser-auth-status").className="form-feedback"+(job.phase==="error"?" error":"");
  for(const button of document.querySelectorAll('#browser-authorize, [data-browser-authorize]')){
    button.disabled=active||!!button.dataset.busy||!!state.token_management?.pending;
    button.title=active?'浏览器授权正在进行':state.token_management?.pending?'先前创建结果未知，请先核对':'授权后自动获取账户与域名，接入并刷新凭据';
  }
  const chooser=$('#browser-zone-choice'),select=$('#browser-zone-select'),previous=select.value;
  chooser.hidden=job.phase!=='choosing_zone';
  if(job.phase==='choosing_zone'){
    select.innerHTML=(job.zones||[]).map(z=>`<option value="${esc(z.zone_id)}">${esc(z.zone_name)}${z.account_name?' · '+esc(z.account_name):' · '+esc(z.account_id)}</option>`).join('');
    if((job.zones||[]).some(z=>z.zone_id===previous))select.value=previous;
  }
  const controls=[$('#browser-authorize-restart'),$('#browser-authorize-cancel')];
  const recoveryBusy=controls.some(button=>!!button.dataset.busy);
  $('#browser-authorize-restart').hidden=job.phase!=='authorizing'&&job.phase!=='cancelling';
  $('#browser-authorize-cancel').hidden=!['authorizing','choosing_zone','cancelling'].includes(job.phase);
  for(const button of controls)button.disabled=!['authorizing','choosing_zone'].includes(job.phase)||recoveryBusy;
  if(active&&!browserAuthPoll)browserAuthPoll=setInterval(()=>{if(state)pollState();else{clearInterval(browserAuthPoll);browserAuthPoll=null;}},2000);
  if(!active&&browserAuthPoll){clearInterval(browserAuthPoll);browserAuthPoll=null;}
}
$('#account-use-browser').onclick=()=>setAccountAccess('browser');
$('#account-use-manual').onclick=()=>{replacingWriteToken=!!state?.credentials.cf_write_token;setAccountAccess('manual');renderSetup();};
$("#browser-authorize").onclick=e=>startBrowserAuthorization(e.currentTarget);
$('#browser-zone-confirm').onclick=async e=>{
 const button=e.currentTarget;if(button.dataset.busy)return;
 try{await action(button,'cloudflare/browser-authorize-select-zone',null,{zone_id:$('#browser-zone-select').value});}
 catch(err){$('#browser-auth-status').textContent=err.message;$('#browser-auth-status').className='form-feedback error';}
};
async function recoverBrowserAuthorization(button,operation){
  browserAuthInteraction=true;
  if(button.dataset.busy||[$('#browser-authorize-restart'),$('#browser-authorize-cancel')].some(control=>!!control.dataset.busy))return;
  try{await action(button,'cloudflare/browser-authorize-'+operation,null,operation==='restart'?{browser:$('#cloudflare-browser').value}:{});}
  catch(err){$('#browser-auth-status').textContent=err.message;$('#browser-auth-status').className='form-feedback error';}
}
$('#browser-authorize-restart').onclick=e=>recoverBrowserAuthorization(e.currentTarget,'restart');
$('#browser-authorize-cancel').onclick=e=>recoverBrowserAuthorization(e.currentTarget,'cancel');
function renderTokenOperation(){
  const selected=$('#token-operation').value;
  for(const id of ['token-manager-submit','token-repair-write','token-create-read','token-repair-read'])$('#'+id).hidden=id!==selected;
  const repair=selected.includes('repair'),read=selected.includes('read');
  $('#token-existing-read').hidden=selected!=='read-existing';
  $('#token-provision-fields').hidden=!selected||selected==='read-existing';
  $('#token-operation-description').textContent=!selected?'':selected==='read-existing'?'保存你在 Cloudflare 创建的只读令牌，仅用于查询状态。':repair?'补齐 LanBridge 所需权限，保留原令牌值。':'创建并切换到新令牌，旧令牌保留在 Cloudflare。';
  $('#token-manager-form [name=human_check]').closest('label').hidden=read;
  $('#read-manager-status').hidden=!read;
}
$('#token-operation').onchange=()=>{renderTokenOperation();$('#token-manager-feedback').textContent='';};
function renderTokenManager(){
  renderBrowserAuth();renderTokenOperation();
  const management=state.token_management||{},cfg=state.settings;
  const ready=!!tokenTemplateURL(cfg)&&!!cfg.zone_name;
  $('#token-manager-submit').disabled=!ready||!!management.pending||!!$('#token-manager-form').dataset.busy||!!$('#token-manager-submit').dataset.busy;
  if(management.managed&&!$('#token-manager-form').dataset.dirty)$('#token-manager-form [name=human_check]').checked=management.managed.human_check;
  $('#token-manager-submit').textContent='创建新写入令牌';
  const busy=!!$('#token-manager-form').dataset.busy;
  $('#token-create-read').disabled=!ready||busy||!!management.pending_read;
  $('#token-create-read').textContent='创建新只读令牌';
  $('#token-repair-read').disabled=!ready||busy||!state.credentials.cf_read_token;
  $('#token-repair-write').disabled=!ready||busy||!state.credentials.cf_write_token||['account','oauth'].includes(management.managed?.kind);
  const unavailable=!ready?'请先保存 Account ID、Zone ID 和 Zone 名称':busy?'操作正在进行，请稍候':'';
  $('#token-manager-submit').title=unavailable||(management.pending?'先核对上次创建结果，避免重复创建':'创建新的写入令牌并切换本机凭据，旧令牌保留');
  $('#token-create-read').title=unavailable||(management.pending_read?'先核对上次创建结果，避免重复创建':'创建新的只读令牌并切换本机只读凭据，旧令牌保留');
  $('#token-repair-read').title=unavailable||(!state.credentials.cf_read_token?'尚未配置只读令牌':'为当前只读令牌补齐权限，保留令牌值');
  $('#token-repair-write').title=unavailable||(['account','oauth'].includes(management.managed?.kind)?'浏览器授权请重新授权；账户令牌请在 Cloudflare 管理':!state.credentials.cf_write_token?'尚未配置写入令牌':'为当前写入令牌补齐权限，保留令牌值');
  $('#token-manager-forget').disabled=busy;
  $('#read-manager-status').textContent=management.pending_read?'先前创建结果未知，请在 Cloudflare 核对 '+management.pending_read.name+'；平台不会重复创建。':management.managed_read?'只读令牌已配置':state.credentials.cf_read_token?'只读令牌已保存，可选择修复当前权限。':'未配置只读令牌';
  $('#token-manager-forget').hidden=!management.authority_saved;
  $('#token-manager-form [name=authority]').placeholder=management.authority_saved?'授权令牌已保存；留空使用已保存授权':'粘贴授权令牌，仅用于令牌管理';
  $('#authority-token-discovery').hidden=ready;
  $('#token-manager-status').className='small muted';
  $('#token-manager-status').textContent=management.pending?'上次创建结果尚未确认，请先在 Cloudflare 核对，避免重复创建。':!ready?'先读取账户与域名，选择本次操作的范围。':'操作范围：'+cfg.zone_name;

}
$('#token-manager-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget,feedback=$('#token-manager-feedback');
  if(form.dataset.busy)return;
  if(!$('#token-operation').value||$('#token-operation').value==='read-existing')return;
  const authority=form.elements.authority.value.trim();
  if(!authority&&!state.token_management?.authority_saved){feedback.textContent='请先粘贴 API Tokens Write 授权令牌';form.elements.authority.focus();return;}
  const button=$('#'+$('#token-operation').value),target=button.dataset.target||'write',repair_existing=button.dataset.repair==='true';
  if(button.disabled){feedback.textContent=button.title||'当前操作不可用，请检查账户配置与令牌状态。';return;}
  const data={authority,remember:form.elements.remember.checked,human_check:form.elements.human_check.checked,target,repair_existing,force_new:button.dataset.new==='true'};
  form.dataset.busy='true';lockForm(form);renderTokenManager();feedback.className='form-feedback';feedback.textContent='正在核对并配置'+(target==='read'?'只读':'写入')+'令牌，请稍候…';
  try{const result=await action(button,'cloudflare/provision-token',null,data);form.elements.authority.value='';form.dataset.dirty='';replacingWriteToken=false;invalidatePlan();if(state){renderSetup();renderTokenManager();}const name=target==='read'?'只读令牌':'写入令牌';feedback.textContent=result.action==='created'?name+'已创建并保存':name+'权限已更新，请重试失败的操作。';}
  catch(err){form.elements.authority.value='';feedback.className='form-feedback error';feedback.textContent=err.message;await loadState().catch(()=>{});}
  finally{form.dataset.busy='';unlockForm(form);if(state)renderTokenManager();}
};
$('#token-manager-forget').onclick=async e=>{try{await action(e.currentTarget,'cloudflare/forget-token-authority');$('#token-manager-feedback').textContent='本机授权令牌已移除，业务令牌仍可使用。';}catch(err){$('#token-manager-feedback').textContent=err.message;}};

$('#token-manager-form').oninput=e=>{e.currentTarget.dataset.dirty='true';};

function connectorReadiness(current){
  const cfg=current.settings,conn=current.connector,reasons=[];
  if(conn.running)return {label:'运行中',kind:'success',detail:'连接器运行中',attention:false};

  if(!conn.installed)reasons.push('尚未找到 cloudflared，可在账户与配置中自动检测或下载');
  if(!cfg.tunnel_id&&current.cloudflare_setup?.ready)reasons.push(current.tunnel_pending?'上次隧道创建结果未知，请核对并恢复，勿重复创建':'尚未创建隧道');
  else if(cfg.tunnel_id&&!current.credentials.tunnel_token)reasons.push('缺少隧道令牌，请前往账户与配置重新获取');
  if(reasons.length)return {label:!conn.installed?'待安装连接器':!cfg.tunnel_id?(current.tunnel_pending?'待恢复隧道':'待创建隧道'):'待获取连接令牌',kind:'warning',detail:reasons.join('；')+'。',attention:true};
  if(!current.cloudflare_setup?.ready)return {label:'待配置',kind:'neutral',detail:'请先完成账户与配置，再创建隧道。',attention:true};
  return {label:'未启动',kind:'neutral',detail:'在网站转发中启动连接器。',attention:true};
}
function renderConnectorReadiness(){
  const readiness=connectorReadiness(state),ready=!!state.cloudflare_setup?.ready;
  const noSites=ready&&!state.sites.length;
  const needsVerification=ready&&state.sites.some(site=>site.enabled&&site.human_check)&&(!state.settings.turnstile_sitekey||!state.credentials.turnstile_secret);
  const preparation=noSites?'add':needsVerification?'verification':'';
  $('#connector-readiness').hidden=!readiness.attention&&!preparation;
  $('#connector-readiness strong').textContent=noSites?'下一步：添加网站':needsVerification?'下一步：配置人类验证':readiness.kind==='warning'?'连接器需要处理':!ready?'先完成账户配置':'连接器尚未启动';
  $('#connector-readiness-detail').textContent=noSites?'账户已配置，添加要转发的网站。':needsVerification?'已有网站启用了人类验证，请完成验证配置后发布。':readiness.detail;
  $('#connector-readiness [data-goto=settings]').hidden=!!preparation||(ready&&!!state.connector.installed);
  $('#forwarding-next').hidden=!preparation;
  $('#forwarding-next').dataset.next=preparation;
  $('#forwarding-next').textContent=noSites?'添加网站':'配置人类验证';
}
$('#forwarding-next').onclick=()=>{
  if($('#forwarding-next').dataset.next==='add')return $('#add-site').onclick();
  go('settings');$('#human-verification-panel').scrollIntoView({behavior:'smooth',block:'center'});
};

function navigationIssues(current){
  const issues={overview:[],sites:[],audit:[],settings:[],interfaces:[]};
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
  if(readiness.kind==='warning')issues.sites.push(readiness.detail);
  const humanSites=enabled.filter(site=>site.human_check&&(current.published_hosts||[]).includes(site.hostname));
  if(humanSites.length&&(!cfg.turnstile_sitekey||!current.credentials.turnstile_secret))issues.settings.push('人类验证缺少配置或服务端密钥，请自动配置人类验证');
  for(const site of enabled){
    const probe=current.site_probes?.[site.id];
    if(probe?.reachable===false&&probe.origin===site.origin)issues.sites.push(site.name+'：上次源站检查不可达，请检查局域网地址并重新检查');
  }
  if(cfg.tunnel_id&&Array.isArray(current.published_hosts)){
    const desired=enabled.map(site=>site.hostname).sort(),published=[...current.published_hosts].sort();
    if(JSON.stringify(desired)!==JSON.stringify(published)){
      issues.sites.push('网站有待发布变更，请重试发布');
    }
  }
  if(current.publication_needs_review){
    issues.sites.push('上次发布未完成或核验失败，请检查并重试');
  }
  const edge=current.cloudflare;
  if(current.connector.running&&edge&&Date.now()/1000>=edge.checked_at&&Date.now()/1000-edge.checked_at<150&&edge.tunnel_id===cfg.tunnel_id&&edge.edge_status!=='healthy')issues.sites.push('上次云端检查：'+cloudConnectionLabel(edge.edge_status)+'，请检查连接并重新检查');
  issues.overview=[...new Set(['settings','sites','audit'].flatMap(view=>issues[view]))];
  return issues;
}
function renderNavigationIssues(){
  const issues=navigationIssues(state);
  for(const [view,reasons] of Object.entries(issues)){
    const button=$('[data-view='+view+']'),badge=$('#'+view+'-nav-status');
      const recheck=view==='settings'&&!reasons.length&&(state.cloudflare_permission_issues||[]).some(issue=>issue.status==='needs_recheck');
      const permissions=view==='settings'&&state.cloudflare_setup?.ready&&!state.token_management?.error&&!state.token_management?.pending&&!state.token_management?.pending_read&&(state.cloudflare_permission_issues||[]).length>0;
      badge.textContent=permissions?'权限待核验':recheck?'待核验':'待处理';badge.className='nav-status '+(recheck?'neutral':'warning');
      badge.hidden=view==='overview'||(!reasons.length&&!recheck);
      button.title=reasons.length&&view!=='overview'?[...new Set(reasons)].join('；'):recheck?'凭据已更新，请重试原操作核验权限':'';
      if(view==='sites'&&!reasons.length){const readiness=connectorReadiness(state);if(readiness.attention&&state.cloudflare_setup?.ready){badge.hidden=false;badge.textContent=readiness.label;badge.className='nav-status '+readiness.kind;button.title=readiness.detail;}}
  }
  const reasons=[...new Set(issues[currentView]||[])].filter(reason=>(currentView!=='sites'||reason!==connectorReadiness(state).detail)&&(currentView!=='overview'||!reason.startsWith('账户配置未完成：')));
  $('#view-issues').hidden=!reasons.length;
  $('#view-issues-list').innerHTML=reasons.map(reason=>'<li>'+esc(reason)+'</li>').join('');
}

function renderActionAvailability(){
  const cfg=state.settings,credentials=state.credentials,conn=state.connector;
  const humanSites=state.sites.filter(site=>site.enabled&&site.human_check);
  const ready=!!state.cloudflare_setup?.ready;
  $('#create-tunnel').textContent=cfg.tunnel_id?'获取隧道令牌 →':state.tunnel_pending?'核对并恢复隧道':'创建隧道';
  $('#create-tunnel').hidden=!!(cfg.tunnel_id&&credentials.tunnel_token);
  $('#tunnel-maintenance').hidden=!cfg.tunnel_id;
  $('#tunnel-token-state').textContent=credentials.tunnel_token?'已保存':'未保存';
  $('#connector-start').textContent='启动连接器';
  $('#connector-start').hidden=!!conn.running;
  $('#connector-stop').hidden=!conn.running;
  const rules={
    'create-tunnel':[cfg.tunnel_id?!credentials.tunnel_token:ready,cfg.tunnel_id&&credentials.tunnel_token?'隧道和连接令牌已配置':'请先完成 Cloudflare 账户接入'],
    'tunnel-token-refresh':[!!cfg.tunnel_id&&!!cfg.account_id&&!!credentials.cf_write_token,'请先创建隧道并配置 API Token'],
    'connector-start':[!!cfg.tunnel_id&&credentials.tunnel_token&&!conn.running&&state.gateway?.running!==false,'请先创建 Tunnel 并获取连接令牌；已运行时无需重复启动'],
    'connector-stop':[conn.running,'连接器当前未运行'],
    'cf-check':[!!cfg.tunnel_id&&(credentials.cf_read_token||credentials.cf_write_token),'请先创建 Tunnel 并配置 API Token'],
    'overview-cf-check':[!!cfg.tunnel_id&&(credentials.cf_read_token||credentials.cf_write_token),'请先完成 Cloudflare 接入并创建隧道'],
    'publication-retry':[ready&&!!cfg.tunnel_id&&(!humanSites.length||(!!cfg.turnstile_sitekey&&credentials.turnstile_secret)),'请先完成账户、隧道和人类验证配置'],
    'preview':[ready&&!!cfg.tunnel_id&&(!humanSites.length||(!!cfg.turnstile_sitekey&&credentials.turnstile_secret)),'请先创建 Tunnel；需要人类验证的网站还需配置 Turnstile'],
    'widget-create':[ready&&humanSites.length>0,'请先配置账户，并添加启用人类验证的网站']
  };
  for(const [id,[allowed,hint]] of Object.entries(rules)){
    const button=$('#'+id);button.disabled=!allowed||!!button.dataset.busy||(['preview','publication-retry'].includes(id)&&publicationRetryPending);button.title=allowed?'':hint;
  }
}

$('#read-token-save').onclick=async e=>{
 const input=e.currentTarget.form.elements.cf_read_token,feedback=$('#read-token-feedback');
 if(!input.value.trim()){feedback.textContent='请先粘贴只读 API Token';input.focus();return;}
 try{await action(e.currentTarget,'credentials',null,{cf_read_token:input.value.trim()});input.value='';feedback.textContent='只读令牌已保存';}
 catch(err){feedback.textContent=err.message;}
};
$('#read-token-remove').onclick=async e=>{
  try{await action(e.currentTarget,'credentials',null,{remove_cf_read_token:true});$('#read-token-save').form.elements.cf_read_token.value='';$('#read-token-feedback').textContent='只读令牌已移除';}catch(err){$('#credentials-feedback').textContent=err.message;}
};

$('#site-zone').onchange=()=>{const z=configuredZones(state).find(z=>z.zone_id===$('#site-zone').value);$('#site-form').elements.hostname.placeholder='app.'+(z?.zone_name||'example.com');};
let zoneChoices=[];
$('#zones-discover').onclick=async e=>{
 const feedback=$('#zones-feedback');$('#zone-management').hidden=false;$('#zone-choice-row').hidden=true;feedback.textContent='正在读取可添加的域名…';
 try{const result=await action(e.currentTarget,'cloudflare/zones',null,{});zoneChoices=result.zones.filter(z=>!configuredZones(state).some(saved=>saved.zone_id===z.zone_id));$('#zone-choice').innerHTML=zoneChoices.map(z=>`<option value="${esc(z.zone_id)}">${esc(z.zone_name)}</option>`).join('');$('#zone-choice-row').hidden=!zoneChoices.length;feedback.textContent=zoneChoices.length?'':'账户中没有其他可添加的域名。也可以手动输入域名与 Zone ID。';}
 catch(err){feedback.textContent=err.message;}
};
$('#zone-add-cancel').onclick=()=>{$('#zone-management').hidden=true;$('#zones-feedback').textContent='';};
async function attachZone(button,zone){
 const feedback=$('#zones-feedback');feedback.textContent='正在核验域名所属账户及状态…';
 try{await action(button,'zones',null,zone);$('#zone-choice-row').hidden=true;$('#zone-add-name').value='';$('#zone-add-id').value='';invalidatePlan();$('#zone-management').hidden=true;feedback.textContent=zone.zone_name+' 已接入。';}
 catch(err){feedback.textContent=err.message;}
}
$('#zone-add').onclick=e=>{const zone=zoneChoices.find(z=>z.zone_id===$('#zone-choice').value);if(zone)attachZone(e.currentTarget,zone);};
$('#zone-add-manual').onclick=e=>attachZone(e.currentTarget,{zone_name:$('#zone-add-name').value.trim(),zone_id:$('#zone-add-id').value.trim()});

$('#configured-zones').onclick=async e=>{const button=e.target.closest('[data-remove-zone]');if(!button)return;try{await action(button,'zones/'+button.dataset.removeZone+'/remove',null,{});invalidatePlan();$('#zones-feedback').textContent='域名已从 LanBridge 移除，Cloudflare DNS 未改动。';}catch(err){$('#zones-feedback').textContent=err.message;$('#account-config-details').open=true;}};

$('#restart').onclick=()=>{if(remoteAccess||state?.access_scope==='sites')return;$('#restart-error').textContent='';$('#restart-address').textContent='重启后管理地址：http://127.0.0.1:'+(state.pending_admin_port||state.settings.admin_port)+'/admin';$('#restart-dialog').showModal();};
$('#cancel-restart').onclick=()=>$('#restart-dialog').close();
$('#confirm-restart').onclick=async e=>{
  const button=e.currentTarget,session=csrf;button.disabled=true;$('#cancel-restart').disabled=true;
  try{await api('restart',{});if(session!==csrf)return;$('#restart-dialog').close();showAuth();$('#auth').hidden=true;$('#platform-restarting').hidden=false;}
  catch(err){if(session===csrf)$('#restart-error').textContent='重启请求未确认：'+err.message+'。请重新检测运行状态。';}
  finally{button.disabled=false;$('#cancel-restart').disabled=false;}
};
