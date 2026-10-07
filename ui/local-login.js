'use strict';
window.localLoginUI=(()=>{
  const node=id=>document.getElementById(id);
  function requestFromHash(){if(window.LB_REMOTE)return '';return location.hash.match(/^#local-login=([A-Za-z0-9_-]{43})$/)?.[1]||'';}
  let approvalId=requestFromHash(),job=null,timer=null,generation=0,approvalCode='',starting=false;
  function stop(){generation++;clearTimeout(timer);timer=null;job=null;node('local-login-wait').hidden=true;node('local-login-options').hidden=true;node('local-login-start').setAttribute('aria-expanded','false');node('local-login-launch').disabled=false;node('local-login-start').disabled=false;node('local-login-browser').disabled=false;}
  function show(initialized){node('local-login-approval').hidden=true;node('local-login-start').hidden=window.LB_REMOTE||!initialized||!!approvalId;if(!initialized||approvalId)node('local-login-options').hidden=true;}
  async function poll(expected){
    if(!job||expected!==generation)return;
    try{
      const result=await api('local-login/poll',{request_id:job.request_id});
      if(expected!==generation)return;
      if(result.phase==='done'){csrf=result.csrf;node('auth-form').elements.password.value='';openShell();return;}
      if(result.phase==='denied')throw Error('本机授权已拒绝，可以重新发起。');
      if(Date.now()/1000>=job.expires_at)throw Error('本机授权已超时，请重新发起。');
      node('local-login-message').textContent=(job.browser_opened?'请在 '+job.browser_name+' 确认登录':job.launchFailed?'请复制授权地址到 '+job.browser_name+' 中打开':'请记住确认码，再点击下方按钮前往 '+job.browser_name)+'，剩余 '+Math.ceil(job.expires_at-Date.now()/1000)+' 秒。';
      timer=setTimeout(()=>poll(expected),1000);
    }catch(error){if(expected!==generation)return;stop();node('auth-error').textContent=error.message;}
  }
  node('local-login-start').onclick=async()=>{
    if(starting)return;
    if(!node('local-login-options').hidden){await cancelLogin();return;}
    starting=true;const expected=generation;
    node('local-login-start').disabled=true;node('auth-error').textContent='';
    try{
      const result=await api('local-login/browsers');
      if(expected!==generation)return;
      const select=node('local-login-browser');select.replaceChildren();
      for(const browser of result.browsers){const option=document.createElement('option');option.value=browser.id;option.textContent=browser.name;select.append(option);}
      let saved='default';try{saved=localStorage.getItem('lb-login-browser')||saved;}catch{}
      select.value=result.browsers.some(browser=>browser.id===saved)?saved:'default';
      node('local-login-options').hidden=false;node('local-login-start').setAttribute('aria-expanded','true');
    }catch(error){if(expected===generation)node('auth-error').textContent=error.message;}finally{starting=false;if(expected===generation)node('local-login-start').disabled=false;}
    if(expected===generation&&!node('local-login-options').hidden)await startConfirmation();
  };
  async function startConfirmation(){
    if(starting)return;
    stop();
    const expected=generation;
    starting=true;node('local-login-start').disabled=true;node('auth-error').textContent='';
    node('local-login-options').hidden=false;node('local-login-start').setAttribute('aria-expanded','true');
    node('local-login-browser').disabled=true;node('local-login-launch').disabled=true;
    try{
      const browser=node('local-login-browser').value;
      const result=await api('local-login/start',{browser});if(expected!==generation)return;job=result;
      try{localStorage.setItem('lb-login-browser',browser);}catch{}
      node('local-login-code').textContent=job.code;node('local-login-wait').hidden=false;
      node('local-login-message').textContent='请记住确认码，再前往 '+job.browser_name+' 确认登录。';
      node('local-login-launch').textContent='前往 '+job.browser_name+' 确认登录';
      node('local-login-launch').disabled=false;
      timer=setTimeout(()=>poll(expected),1000);
    }catch(error){if(expected===generation){stop();node('auth-error').textContent=error.message;}}finally{starting=false;if(expected===generation){node('local-login-start').disabled=false;node('local-login-browser').disabled=false;}}
  }
  node('local-login-browser').onchange=startConfirmation;
  node('local-login-launch').onclick=async()=>{
    if(!job)return;const current=job,expected=generation;node('local-login-launch').disabled=true;
    try{
      const result=await api('local-login/open',{request_id:current.request_id});
      if(expected!==generation)return;
      current.browser_opened=result.browser_opened;current.launchFailed=!result.browser_opened;
      node('local-login-message').textContent=result.browser_opened?'已请求打开 '+current.browser_name+'，请确认登录。':'未能打开 '+current.browser_name+'，请复制授权地址到该浏览器。';
      node('local-login-launch').textContent='重新打开 '+current.browser_name;
    }catch(error){if(expected===generation)node('auth-error').textContent=error.message;}
    finally{if(expected===generation)node('local-login-launch').disabled=false;}
  };
  node('local-login-copy').onclick=async()=>{try{if(job)await navigator.clipboard.writeText(job.approval_url);}catch{node('auth-error').textContent='复制失败，请重新发起授权。';}};
  async function cancelLogin(){
    const current=job;stop();if(!current)return;
    starting=true;node('local-login-start').disabled=true;
    try{await api('local-login/cancel',{request_id:current.request_id});}catch(error){node('auth-error').textContent=error.message;}finally{starting=false;node('local-login-start').disabled=false;}
  }
  node('local-login-cancel').onclick=cancelLogin;
  async function showApproval(){
    const expected=generation,requestId=approvalId;
    node('auth').hidden=true;node('shell').hidden=true;node('local-login-approval').hidden=false;
    node('local-approval-actions').hidden=true;
    try{
      const details=await api('local-login/request/'+requestId);
      if(expected!==generation||requestId!==approvalId)return;
      if(details.phase!=='pending')throw Error('此登录请求已经处理。');
      for(const id of ['local-approval-allow','local-approval-deny'])node(id).disabled=false;
      approvalCode=details.code;node('local-approval-code').textContent=details.code;
      node('local-approval-status').textContent='请在 '+new Date(details.expires_at*1000).toLocaleTimeString('zh-CN')+' 前确认。';
      node('local-approval-actions').hidden=false;
    }catch(error){if(expected===generation&&requestId===approvalId){node('local-approval-status').textContent=error.message;node('local-approval-return').hidden=false;}}
  }
  async function decide(allow){
    const expected=generation,requestId=approvalId;
    const buttons=[node('local-approval-allow'),node('local-approval-deny')];
    if(!requestId||buttons.some(button=>button.disabled))return;
    buttons.forEach(button=>button.disabled=true);
    try{
      await api('local-login/approve',{request_id:requestId,code:approvalCode,allow});
      if(expected!==generation||requestId!==approvalId)return;
      node('local-approval-actions').hidden=true;node('local-approval-return').hidden=false;
      node('local-approval-status').textContent=allow?'已允许登录，请回到发起请求的浏览器。此页面可以关闭。':'已拒绝登录，此页面可以关闭。';
    }catch(error){if(expected===generation&&requestId===approvalId)node('local-approval-status').textContent=error.message;}
    finally{if(expected===generation&&requestId===approvalId)buttons.forEach(button=>button.disabled=false);}
  }
  node('local-approval-allow').onclick=()=>decide(true);
  node('local-approval-deny').onclick=()=>decide(false);
  node('local-approval-return').onclick=()=>{approvalId='';history.replaceState(null,'',location.pathname);node('local-login-approval').hidden=true;openShell();};
  // Opening a confirmation URL may reuse a tab and only change its fragment.
  // The scripts are not reloaded in that case; refresh the session explicitly.
  window.addEventListener('hashchange',async()=>{
    const next=requestFromHash();if(next===approvalId)return;
    stop();approvalId=next;approvalCode='';node('local-approval-code').textContent='';
    node('local-approval-return').hidden=true;const expected=generation;
    try{
      const result=await api('bootstrap');if(expected!==generation)return;
      initialized=result.initialized;csrf=result.csrf;
      if(result.authenticated)openShell();else showAuth();
    }catch(error){if(expected===generation)node('auth-error').textContent=error.message;}
  });
  return {get approvalId(){return approvalId;},setRemote(remote){if(remote){stop();approvalId='';approvalCode='';}},show,stop,showApproval};
})();
