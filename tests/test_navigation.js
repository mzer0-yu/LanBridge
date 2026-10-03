const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'ui/app.js'),'utf8');
function harness(state){
  const nodes={};
  const context={state,currentView:'overview',esc:value=>String(value).replaceAll('<','&lt;'),$:selector=>nodes[selector]||=( {})};
  vm.createContext(context);
  const readiness=source.slice(source.lastIndexOf('function connectorReadiness'),source.lastIndexOf('function renderConnectorReadiness'));
  vm.runInContext(readiness+source.slice(source.lastIndexOf('function navigationIssues')),context);
  return {context,nodes};
}
function ready(){return {settings:{tunnel_id:'t',turnstile_sitekey:'key'},connector:{installed:true,running:true},credentials:{tunnel_token:true,turnstile_secret:true},cloudflare_setup:{ready:true},cloudflare_permission_issues:[],sites:[],published_hosts:[],site_probes:{},token_management:{}};}
function marked(context){const issues=context.navigationIssues(context.state);return Object.keys(issues).filter(key=>key!=='overview'&&issues[key].length);}
test('missing account configuration belongs only to settings; empty unused features are not errors',()=>{
  const state=ready();state.settings.tunnel_id='';state.connector.running=false;state.credentials.tunnel_token=false;state.cloudflare_setup={ready:false,missing:['API Token']};
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['settings']);
});
test('permission failure does not spread to mapping, security or history tabs',()=>{
  const state=ready();state.cloudflare_permission_issues=[{detail:'Cloudflare API HTTP 403：创建 Tunnel失败'}];
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['settings']);
});
test('route changes and incomplete publication belong only to connector',()=>{
  const state=ready();state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:false}];state.publication_needs_review=true;
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['connector']);
});
test('source warning is tied to the checked origin; changed or disabled sites remove it',()=>{
  const state=ready();state.sites=[{id:'s',name:'LAN',enabled:true,hostname:'app.example.com',origin:'http://127.0.0.1:9300'}];state.published_hosts=['app.example.com'];state.site_probes={s:{reachable:false,origin:'http://127.0.0.1:9300'}};
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['sites']);
  state.sites[0].origin='http://127.0.0.1:9400';assert.deepEqual(Array.from(marked(context)),[]);
});
test('only enabled human-check sites require Turnstile credentials',()=>{
  const state=ready();state.sites=[{id:'s',enabled:true,hostname:'app.example.com',human_check:true}];state.published_hosts=['app.example.com'];state.credentials.turnstile_secret=false;
  const {context}=harness(state);assert.deepEqual(Array.from(marked(context)),['security']);state.credentials.turnstile_secret=true;assert.deepEqual(Array.from(marked(context)),[]);
});
test('connector badge resets after recovery and view changes; overview summary has no pending badge',()=>{
  const state=ready();state.publication_needs_review=true;
  const {context,nodes}=harness(state);context.renderNavigationIssues();assert.equal(nodes['#connector-nav-status'].textContent,'待处理');assert.equal(nodes['#overview-nav-status'].hidden,true);
  state.publication_needs_review=false;context.currentView='settings';context.renderNavigationIssues();assert.equal(nodes['#connector-nav-status'].textContent,'运行中');assert.equal(nodes['#connector-nav-status'].className,'nav-status success');assert.equal(nodes['#audit-nav-status'].hidden,true);
});
test('settings is the last list item without bottom anchoring',()=>{
  const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8'),css=fs.readFileSync(path.join(root,'ui/style.css'),'utf8');
  const nav=html.match(/<nav>(.*?)<\/nav>/s)[1];assert.equal([...nav.matchAll(/data-view="([^"]+)"/g)].at(-1)[1],'settings');assert(!css.includes('aside nav{flex:1}'));assert(!css.includes('button[data-view=settings]{margin-top:auto}'));
});
