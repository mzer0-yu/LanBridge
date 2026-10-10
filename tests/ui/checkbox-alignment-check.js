const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),{chromium}=require('playwright');
const {state}=require('./fixture');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'}),artifacts=path.resolve(__dirname,'../../.test-artifacts/ui');fs.mkdirSync(artifacts,{recursive:true});
 try{for(const width of [1440,760,390,320]){
  const page=await browser.newPage({viewport:{width,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;return p.startsWith('/api/')?r.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'fixture'}:p==='/api/temporary-tokens'?{tokens:[]}:state}):r.fulfill({path:path.resolve(__dirname,'../../ui',p==='/'?'index.html':p.slice(1))});});
  await page.goto('http://lb.preview/');await page.locator('#shell').waitFor();
  const rows=await page.evaluate(()=>{
   const checks=[...document.querySelectorAll('label>input[type=checkbox]')];
   for(const input of checks){for(let el=input.parentElement;el&&el!==document.body;el=el.parentElement){el.hidden=false;if(el.tagName==='DETAILS')el.open=true;if(el.tagName==='DIALOG'){el.setAttribute('open','');el.style.position='relative';}}}
   return checks.map(input=>{const label=input.parentElement,walk=document.createTreeWalker(label,NodeFilter.SHOW_TEXT);let text;while(text=walk.nextNode())if(text.textContent.trim())break;const range=document.createRange();range.selectNodeContents(text);const t=range.getClientRects()[0],c=input.getBoundingClientRect();return {name:label.textContent.trim(),offset:Math.abs(c.y+c.height/2-t.y-t.height/2),width:c.width,height:c.height,overflow:label.scrollWidth>label.clientWidth+1};});
  });
  assert(rows.length>=15);for(const row of rows){assert(row.offset<=2,`${width}px ${row.name}: center offset ${row.offset}`);assert.equal(row.width,13);assert.equal(row.height,13);assert(!row.overflow,`${width}px ${row.name}: overflow`);}
  await page.evaluate(()=>{const el=document.querySelector('#pause-dialog');el.style.position='';el.removeAttribute('open');el.showModal();document.querySelector('#pause-host').textContent='layout.example.com';});
  await page.locator('#pause-dialog').screenshot({path:path.join(artifacts,`checkbox-alignment-${width}.png`)});assert.deepEqual(errors,[]);await page.close();console.log(`PASS: ${rows.length} checkbox labels align with their first text line at ${width}px`);
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
