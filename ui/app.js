'use strict';
const $ = s => document.querySelector(s);
let csrf = '', initialized = false, state = null, currentView = 'overview', plan = null, toastTimer;
const titles = {interfaces:['选择适合你的调用方式。','在这里查看 CLI、API、MCP 与 SKILL 的用途、配置和示例。','调用方式'],overview:['公网访问，一处管理。','连接局域网服务，为每个入口设置合适的访问边界。','总览'],sites:['让网页走出局域网。','为每个服务绑定公网域名，完整转发网页与 API。','网站映射'],security:['每个入口，都有边界。','为不同网站组合人类验证、访问口令与访问范围。','访问策略'],connector:['连接，从这里开始。','创建Tunnel，预览路由并管理 Windows 连接器。','连接器'],audit:['每次变更，都可追溯。','核对管理员操作、发布结果与需要继续处理的变更。','操作审计'],settings:['账户与本机配置。','连接你的 Cloudflare 账户，凭据只在本机加密保存。','账户与配置']};
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(message, error=false){clearTimeout(toastTimer);const el=$('#toast');el.textContent=message;el.className=error?'error':'';el.hidden=false;toastTimer=setTimeout(()=>el.hidden=true,6500)}
async function api(path, data){const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)};const r=await fetch('/api/'+path,options);let body;try{body=await r.json()}catch{throw Error('服务器暂不可用')}if(!r.ok){if(r.status===401&&path!=='login')showAuth();throw Error(typeof body.detail==='string'?body.detail:'输入格式无效')}return body}
function showAuth(){csrf='';$('#auth').hidden=false;$('#shell').hidden=true;$('#auth-title').textContent=initialized?'欢迎回来。':'创建管理员账户。';$('#auth-desc').textContent=initialized?'登录管理员平台，管理公网入口与访问权限。':'首次使用，请设置本机管理员。密码至少 12 位。';$('#auth-submit').textContent=initialized?'登录管理台 →':'创建账户并登录 →';$('#auth-form [name=password]').minLength=initialized?1:12;$('#auth-form [name=password]').autocomplete=initialized?'current-password':'new-password'}
async function loadState(){if(!csrf)return;state=await api('state');render();$('#last-refresh').textContent='本机状态更新于 '+new Date().toLocaleTimeString('zh-CN');}
function openShell(){$('#auth').hidden=true;$('#shell').hidden=false;loadState().catch(e=>toast(e.message,true))}
function go(view){currentView=view;document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!=='view-'+view);document.querySelectorAll('nav button').forEach(el=>el.classList.toggle('active',el.dataset.view===view));const t=titles[view];$('#page-title').textContent=t[0];$('#page-desc').textContent=t[1];$('#breadcrumb').textContent='工作空间 / '+t[2];$('#add-site').hidden=!['overview','sites','security'].includes(view);if(view==='settings'&&state&&!$('#settings-form').dataset.dirty)fillSettings();if(state)renderSetup();}
function empty(){return '<div class="empty"><span class="empty-icon">↗</span><b>第一个公网入口，从这里开始。</b><p>先配置 Cloudflare 账户，再添加你想发布的局域网网页。</p><button class="text-button" data-goto="settings">配置账户 →</button></div>'}
function table(sites){if(!sites.length)return empty();return '<div class="table-wrap"><table><thead><tr><th>网站 / 公网域名</th><th>局域网源站</th><th>访问策略</th><th>本机状态</th><th>操作</th></tr></thead><tbody>'+sites.map(s=>`<tr><td><b>${esc(s.name)}</b><small><a href="https://${esc(s.hostname)}" target="_blank" rel="noreferrer">${esc(s.hostname)} ↗</a></small></td><td>${esc(s.origin)}</td><td>${s.human_check?'<span class="badge success">人类验证</span> ':''}${s.passcode_required?'<span class="badge warning">口令</span>':!s.human_check?'<span class="badge neutral">公开访问</span>':''}<small>${s.allowed_countries.length?esc(s.allowed_countries.join(' · ')):'国家不限'} · ${s.requests_per_minute}/分钟</small></td><td><span class="badge ${s.enabled?'success':'neutral'}">${s.enabled?'已启用':'已停用'}</span><small>路由发布见连接器页</small></td><td><button class="row-action" data-edit="${s.id}">编辑</button><button class="row-action" data-probe="${s.id}">探测</button></td></tr>`).join('')+'</tbody></table></div>'}
function render(){if(!state)return;renderSetup();const sites=state.sites,cfg=state.settings,conn=state.connector,cf=state.cloudflare;$('#nav-count').textContent=sites.length;$('#stat-sites').textContent=sites.length;$('#stat-enabled').textContent=sites.filter(s=>s.enabled).length;$('#stat-human').textContent=sites.filter(s=>s.enabled&&s.human_check).length;const fresh=cf&&(Date.now()/1000-cf.checked_at<150);$('#stat-edge').textContent=fresh?cf.connections:'—';$('#stat-edge-desc').textContent=cf?(fresh?'API 核验 · '+cf.edge_status:'上次核查已过期'):'尚未通过 API 核验';$('#flow-status').textContent=conn.running?'连接器已启动':'连接器未启动';$('#flow-status').className='badge '+(conn.running?'success':'neutral');$('#overview-sites').innerHTML=table(sites.slice(0,5));$('#sites-table').innerHTML=table(sites);$('#connector-status').textContent=conn.running?'运行中':conn.last_exit!=null?'已退出（'+conn.last_exit+'）':'未启动';$('#connector-status').className='badge '+(conn.running?'success':'neutral');$('#connector-path').textContent=cfg.cloudflared_path||(conn.installed?'已从 PATH 找到':'尚未找到 cloudflared.exe');$('#tunnel-id').textContent=cfg.tunnel_id||'尚未创建';$('#gateway-address').textContent='127.0.0.1:'+cfg.gateway_port;$('#edge-detail').textContent=cf?`边缘状态：${cf.edge_status} · 连接数：${cf.connections} · 核查时间：${new Date(cf.checked_at*1000).toLocaleString('zh-CN')}${fresh?'':'（已过期）'}`:'尚未核查远端状态。';$('#widget-status').textContent=cfg.turnstile_sitekey?'Site Key：'+cfg.turnstile_sitekey+' · '+(state.credentials.turnstile_secret?'服务端密钥已保存':'缺少服务端密钥'):'尚未配置；先添加网站，再创建 Widget。';$('#step-sites').textContent=sites.length?'✓':'2';$('#step-tunnel').textContent=cfg.tunnel_id?'✓':'3';$('#security-cards').innerHTML=sites.length?sites.map(s=>`<article class="panel"><div class="panel-heading"><div><h3>${esc(s.name)}</h3><small class="muted">${esc(s.hostname)}</small></div><button class="row-action" data-edit="${s.id}">编辑策略</button></div><div class="policy-list"><div><span>人类验证</span><b>${s.human_check?'Turnstile 服务端校验':'未启用'}</b></div><div><span>访问口令</span><b>${s.passcode_required?'已开启':'未启用'}</b></div><div><span>国家范围</span><b>${s.allowed_countries.length?esc(s.allowed_countries.join(', ')):'不限'}</b></div><div><span>IP 范围</span><b>${s.allowed_ips.length?esc(s.allowed_ips.join(', ')):'不限'}</b></div><div><span>请求速率</span><b>${s.requests_per_minute} 次 / 来源 / 分钟</b></div><div><span>验证会话</span><b>${s.session_minutes} 分钟</b></div></div></article>`).join(''):empty();const actionNames={admin_initialized:'初始化管理员',admin_login:'管理员登录',site_saved:'保存网站策略',settings_saved:'保存账户配置',credentials_updated:'更新加密凭据',tunnel_created:'创建Tunnel',turnstile_updated:'同步 Turnstile',publish_verified:'发布并核验成功',publish_incomplete:'发布未完成，需核对',connector_prepared:'检测 / 安装连接器',connector_started:'启动连接器',connector_stopped:'停止连接器',admin_password_changed:'修改管理员密码'};$('#audit-table').innerHTML=state.audit.length?'<div class="table-wrap"><table><thead><tr><th>时间</th><th>操作</th><th>资源摘要</th></tr></thead><tbody>'+state.audit.map(a=>`<tr><td>${new Date(a.at*1000).toLocaleString('zh-CN')}</td><td>${esc(actionNames[a.action]||a.action)}</td><td class="audit-detail">${esc(JSON.stringify(a.detail))}</td></tr>`).join('')+'</tbody></table></div>':'<div class="empty">暂时没有审计记录。</div>';$('#read-token-status').textContent=state.credentials.cf_read_token?'已保存':'未配置';$('#write-token-status').textContent=state.credentials.cf_write_token?'已保存':'未配置';$('#secret-status').textContent=state.credentials.turnstile_secret?'已保存':'未配置';if(currentView==='settings'&&!$('#settings-form').dataset.dirty)fillSettings();}
function fillSettings(){const form=$('#settings-form');for(const el of form.elements)if(el.name)el.value=state.settings[el.name]??'';form.dataset.dirty='';}
const setupFields = [
  {key:'account_id', label:'Account ID', selector:'#settings-form [name=account_id]', help:'填写 Cloudflare 账户 ID，并点击保存配置。'},
  {key:'zone_id', label:'Zone ID', selector:'#settings-form [name=zone_id]', help:'填写域名的区域 ID，并点击保存配置。'},
  {key:'zone_name', label:'Zone 名称', selector:'#settings-form [name=zone_name]', help:'填写已接入 Cloudflare 的域名，例如 example.com。'},
  {key:'cf_write_token', label:'写入 API Token', selector:'#credentials-form [name=cf_write_token]', help:'粘贴 Cloudflare API Token，再点击“加密保存凭据”。令牌不会回显。'}
];
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
  $('#token-template-status').textContent=url?'预填 DNS Edit、Zone Read、名称 LanBridge 和当前账户 / Zone。进入 Cloudflare 后还需添加账户 Tunnel Edit，以及自动管理验证所需的 Turnstile Edit；确认后创建，复制回来加密保存。':'请先填写并保存有效的 Account ID 和 Zone ID；保存后此入口自动启用。';
}
function pendingSetup(){return setupFields.filter(field=>!state?.cloudflare_setup || state.cloudflare_setup.missing.includes(field.label));}
function setupMessage(){return '还需完成：'+pendingSetup().map(field=>field.label).join('、');}
function locateSetup(key){
  const field=setupFields.find(item=>item.key===key)||pendingSetup()[0];
  if(!field)return;
  go('settings');
  const input=$(field.selector);
  input.scrollIntoView({behavior:'smooth',block:'center'});
  input.focus({preventScroll:true});
}
function renderSetup(){
  renderTokenTemplate();
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
  $('#setup-progress').textContent=ready?'必填配置已保存，可以添加第一个网站。':`准备添加网站 · ${setupFields.length-missing.length}/4 项已保存`;
  $('#setup-progress-detail').textContent=ready?'下一步：登记公网域名和局域网地址，再创建验证 Widget 和 Tunnel。云端权限与域名状态在发布时核验。':'填写后需要保存；只读 Token 和手动 Turnstile 密钥不影响新增网站。';
  $('#setup-progress-add').hidden=!ready;
  for(const field of setupFields){
    const input=$(field.selector),pending=missing.includes(field),label=input.closest('label'),hint=$('#hint-'+field.key);
    const unsaved=field.key==='cf_write_token'?!!input.value.trim():!!$('#settings-form').dataset.dirty&&input.value!==(state.settings[field.key]||'');
    label.classList.toggle('needs-setup',pending||unsaved);
    input.setAttribute('aria-required','true');
    input.setAttribute('aria-describedby','hint-'+field.key);
    hint.textContent=unsaved?'已填写，尚未保存。'+field.help:pending?field.help:'✓ 已保存'+(field.key==='cf_write_token'?'，留空保留当前令牌。':'。');
    hint.className='field-hint '+(pending||unsaved?'pending':'complete');
  }
  $('#credentials-form').classList.toggle('needs-attention',missing.some(field=>field.key==='cf_write_token'));
}
function invalidatePlan(){plan=null;$('#plan-content').hidden=true;$('#plan-empty').hidden=false;}
function editSite(id){if(!state)return;if(!id&&!state.cloudflare_setup?.ready){locateSetup();toast(setupMessage(),true);return;}const site=state.sites.find(s=>s.id===id);const form=$('#site-form');form.reset();form.elements.id.value='';form.elements.hostname.readOnly=false;$('#site-error').textContent='';$('#site-dialog-title').textContent=site?'编辑网站与策略':'添加网站';if(site){for(const el of form.elements){if(!el.name||el.name==='passcode')continue;const v=site[el.name];if(el.type==='checkbox')el.checked=!!v;else el.value=Array.isArray(v)?v.join(', '):v??'';}form.elements.hostname.readOnly=true;}$('#site-dialog').showModal();}
async function action(button,path,message,data={}){button.disabled=true;try{const result=await api(path,data);if(message)toast(message);await loadState();return result}catch(e){toast(e.message,true);throw e}finally{button.disabled=false}}
$('#auth-form').addEventListener('submit',async e=>{e.preventDefault();const button=$('#auth-submit');button.disabled=true;$('#auth-error').textContent='';try{const data=Object.fromEntries(new FormData(e.target));if(!initialized){await api('setup',data);initialized=true;}const result=await api('login',data);csrf=result.csrf;e.target.elements.password.value='';openShell();}catch(err){$('#auth-error').textContent=err.message}finally{button.disabled=false}});
document.addEventListener('click',async e=>{const el=e.target.closest('button');if(!el)return;if(el.dataset.copy){try{await navigator.clipboard.writeText($('#'+el.dataset.copy).textContent);toast('已复制，请按说明调整项目路径或账号')}catch{toast('复制失败，请手动选择示例内容复制',true)}}if(el.dataset.view)go(el.dataset.view);if(el.dataset.goto)go(el.dataset.goto);if(el.dataset.setup)locateSetup(el.dataset.setup);if(el.dataset.edit)editSite(el.dataset.edit);if(el.dataset.probe){try{const r=await action(el,'sites/'+el.dataset.probe+'/probe');toast(r.reachable?'源站可达 · HTTP '+r.http_status:'局域网源站暂不可达',!r.reachable)}catch{}}});
$('#add-site').onclick=async()=>{const button=$('#add-site');button.disabled=true;try{await loadState();editSite();}catch(e){toast('无法检查配置，请刷新后重试：'+e.message,true)}finally{if(state)renderSetup();}};$('#close-dialog').onclick=$('#cancel-site').onclick=()=>$('#site-dialog').close();$('#refresh').onclick=()=>loadState().catch(e=>toast(e.message,true));
$('#logout').onclick=async()=>{try{await api('logout',{});showAuth()}catch(e){toast(e.message,true)}};
$('#settings-form').oninput=e=>{e.currentTarget.dataset.dirty='true';$('#settings-feedback').textContent='有未保存的修改。';renderSetup();};
$('#credentials-form').oninput=()=>{$('#credentials-feedback').textContent='凭据尚未保存，请点击“加密保存凭据”。';renderSetup();};
$('#setup-progress-add').onclick=()=>$('#add-site').onclick();
$('#settings-form').onsubmit=async e=>{e.preventDefault();try{await action(e.submitter,'settings','配置已保存',Object.fromEntries(new FormData(e.target)));e.target.dataset.dirty='';invalidatePlan();fillSettings();renderSetup();$('#settings-feedback').textContent='配置已保存。';}catch(err){$('#settings-feedback').textContent=err.message}};
$('#credentials-form').onsubmit=async e=>{e.preventDefault();try{await action(e.submitter,'credentials','凭据已加密保存',Object.fromEntries(new FormData(e.target)));e.target.reset();invalidatePlan();renderSetup();$('#credentials-feedback').textContent=state.cloudflare_setup.ready?'凭据已加密保存，现在可以添加网站。':'凭据已加密保存。'+setupMessage();}catch(err){$('#credentials-feedback').textContent=err.message}};
$('#password-form').onsubmit=async e=>{e.preventDefault();try{await api('password',Object.fromEntries(new FormData(e.target)));e.target.reset();showAuth();toast('密码已修改，请重新登录')}catch(err){toast(err.message,true)}};
$('#site-form').onsubmit=async e=>{e.preventDefault();const f=e.target;const data=Object.fromEntries(new FormData(f));for(const k of ['enabled','human_check','passcode_required'])data[k]=f.elements[k].checked;for(const k of ['allowed_countries','allowed_ips'])data[k]=data[k].split(/[,，\s]+/).filter(Boolean);for(const k of ['requests_per_minute','session_minutes'])data[k]=Number(data[k]);e.submitter.disabled=true;try{await api('sites',data);f.elements.passcode.value='';$('#site-dialog').close();invalidatePlan();await loadState();toast('网站与本机访问策略已保存；路由变更请到连接器页发布')}catch(err){$('#site-error').textContent=err.message}finally{e.submitter.disabled=false}};
for(const [id,path,msg] of [['create-tunnel','cloudflare/create-tunnel','Tunnel 已就绪，连接令牌已加密保存'],['widget-create','cloudflare/turnstile','Turnstile 已同步；可继续预览并发布'],['cf-check','cloudflare/check','已获取 Cloudflare 边缘状态'],['connector-start','connector/start','已启动连接器，请通过 API 核查边缘状态'],['connector-stop','connector/stop','连接器已停止']])$('#'+id).onclick=async e=>{try{await action(e.currentTarget,path,msg)}catch{}};
$('#preview').onclick=async e=>{invalidatePlan();try{plan=await action(e.currentTarget,'cloudflare/preview');$('#plan-json').textContent=JSON.stringify(plan,null,2);$('#plan-empty').hidden=true;$('#plan-content').hidden=false;}catch{}};
$('#apply').onclick=async e=>{if(!plan)return;try{await action(e.currentTarget,'cloudflare/apply','路由与 DNS 写后核验通过，无需重启连接器',{revision:plan.revision});invalidatePlan()}catch{invalidatePlan()}};
api('bootstrap').then(r=>{initialized=r.initialized;csrf=r.csrf;if(r.authenticated)openShell();else showAuth()}).catch(e=>$('#auth-error').textContent=e.message);
setInterval(()=>{if(csrf)loadState().catch(()=>{})},15000);

$('#connector-ensure').onclick=async e=>{
 const button=e.currentTarget,feedback=$('#connector-install-feedback'),input=$('#settings-form [name=cloudflared_path]');
 button.disabled=true;feedback.textContent='正在检测；如需下载，请稍候（取决于网络速度）…';
 try{
   const result=await api('connector/ensure',{path:input.value.trim()});
   input.value=result.path;
   await loadState();
   feedback.textContent=({'download':'已从官方来源下载并校验','PATH':'已在系统 PATH 中找到','local':'已找到项目工具','configured':'指定程序已核验'}[result.source]||'已就绪')+' · '+result.version+'。路径已保存，其他未保存配置保持不变。';
 }catch(err){feedback.textContent=err.message+'。可再次点击重试。'}
 finally{button.disabled=false;}
};
