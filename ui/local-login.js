'use strict';
window.localLoginUI=(()=>{
  const node=id=>document.getElementById(id);
  const match=location.hash.match(/^#local-login=([A-Za-z0-9_-]{43})$/);
  let approvalId=match?match[1]:'',job=null,timer=null,generation=0,approvalCode='',starting=false;
  function stop(){generation++;clearTimeout(timer);timer=null;job=null;node('local-login-wait').hidden=true;node('local-login-start').disabled=false;}
  function show(initialized){node('local-login-approval').hidden=true;node('local-login-start').hidden=!initialized||!!approvalId;}
  async function poll(expected){
    if(!job||expected!==generation)return;
    try{
      const result=await api('local-login/poll',{request_id:job.request_id});
      if(expected!==generation)return;
      if(result.phase==='done'){csrf=result.csrf;node('auth-form').elements.password.value='';openShell();return;}
      if(result.phase==='denied')throw Error('本机授权已拒绝，可以重新发起。');
      if(Date.now()/1000>=job.expires_at)throw Error('本机授权已超时，请重新发起。');
      node('local-login-message').textContent='请在 Chrome 确认登录，剩余 '+Math.ceil(job.expires_at-Date.now()/1000)+' 秒。';
      timer=setTimeout(()=>poll(expected),1000);
    }catch(error){if(expected!==generation)return;stop();node('auth-error').textContent=error.message;}
  }
  node('local-login-start').onclick=async()=>{
    if(starting||job)return;
    starting=true;node('local-login-start').disabled=true;node('auth-error').textContent='';
    try{
      job=await api('local-login/start',{});const expected=++generation;
      node('local-login-code').textContent=job.code;node('local-login-wait').hidden=false;
      node('local-login-message').textContent=job.browser_opened?'已打开外置浏览器，请确认登录。':'未能打开浏览器，请复制授权地址到 Chrome。';
      timer=setTimeout(()=>poll(expected),1000);
    }catch(error){stop();node('auth-error').textContent=error.message;}finally{starting=false;}
  };
  node('local-login-copy').onclick=async()=>{try{if(job)await navigator.clipboard.writeText(job.approval_url);}catch{node('auth-error').textContent='复制失败，请重新发起授权。';}};
  node('local-login-cancel').onclick=async()=>{
    if(!job)return;const current=job;stop();
    try{await api('local-login/cancel',{request_id:current.request_id});}catch(error){node('auth-error').textContent=error.message;}
  };
  async function showApproval(){
    node('auth').hidden=true;node('shell').hidden=true;node('local-login-approval').hidden=false;
    node('local-approval-actions').hidden=true;
    try{
      const details=await api('local-login/request/'+approvalId);
      if(details.phase!=='pending')throw Error('此登录请求已经处理。');
      approvalCode=details.code;node('local-approval-code').textContent=details.code;
      node('local-approval-status').textContent='请在 '+new Date(details.expires_at*1000).toLocaleTimeString('zh-CN')+' 前确认。';
      node('local-approval-actions').hidden=false;
    }catch(error){node('local-approval-status').textContent=error.message;node('local-approval-return').hidden=false;}
  }
  async function decide(allow){
    const buttons=[node('local-approval-allow'),node('local-approval-deny')];buttons.forEach(button=>button.disabled=true);
    try{
      await api('local-login/approve',{request_id:approvalId,code:approvalCode,allow});
      node('local-approval-actions').hidden=true;node('local-approval-return').hidden=false;
      node('local-approval-status').textContent=allow?'已允许登录，请回到发起请求的浏览器。此页面可以关闭。':'已拒绝登录，此页面可以关闭。';
    }catch(error){node('local-approval-status').textContent=error.message;}finally{buttons.forEach(button=>button.disabled=false);}
  }
  node('local-approval-allow').onclick=()=>decide(true);
  node('local-approval-deny').onclick=()=>decide(false);
  node('local-approval-return').onclick=()=>{approvalId='';history.replaceState(null,'',location.pathname);node('local-login-approval').hidden=true;openShell();};
  return {get approvalId(){return approvalId;},show,stop,showApproval};
})();
