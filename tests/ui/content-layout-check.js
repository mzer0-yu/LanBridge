const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),fixture=require('./fixture').state;
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});try{
for(const width of [320,760,1440]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
 const state=structuredClone(fixture);
 state.sites[0].name='长网站名称'.repeat(20);
 state.sites[0].hostname='long-subdomain-name-for-layout.example.com';
 state.sites[0].origin='http://192.168.1.20:8088/'+ 'long-path/'.repeat(18);
 state.audit=[{id:1,at:Date.now()/1000,action:'site_saved',detail:{name:state.sites[0].name,hostname:state.sites[0].hostname}}];
 state.audit_storage={count:1,size_bytes:4096,limit_mb:10};
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://lb.preview/**',route=>{const p=new URL(route.request().url()).pathname;
 if(!p.startsWith('/api/'))return route.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});
 return route.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'fixture'}:p==='/api/state'?state:p==='/api/temporary-tokens'?{tokens:[]}: {}});
 });
 await page.goto('http://lb.preview/');
 for(const view of ['overview','sites','interfaces','audit','tokens','settings']){
  await page.locator(`nav [data-view=${view}]`).click();
  await page.locator(`#view-${view}`).waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${view} page overflow at ${width}`);
  const unexpected=await page.locator(`#view-${view}`).evaluate(el=>[...el.querySelectorAll('button,input,select,textarea')].filter(n=>n.getBoundingClientRect().width>0&&!n.matches('#temporary-token-value,.temporary-token-revealed')).filter(n=>!getComputedStyle(n).fontFamily.includes('Segoe UI')).map(n=>({tag:n.tagName,id:n.id,font:getComputedStyle(n).fontFamily})));
  assert.deepEqual(unexpected,[],`${view} inconsistent form fonts at ${width}`);
 }
 assert.deepEqual(errors,[]);await page.close();
}
console.log('PASS: six pages with long names/URLs at 320/760/1440px; form fonts and overflow');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
