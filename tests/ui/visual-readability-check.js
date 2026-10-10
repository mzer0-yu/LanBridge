const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright'),fixture=require('./fixture').state;
const root=path.resolve(__dirname,'../..');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});const reports=[];try{
for(const width of [390,1440]){
const page=await browser.newPage({viewport:{width,height:1000}}),state=structuredClone(fixture);
state.visitor_protection={verified:2,memory_hits:62,restricted:0,sites:{[state.sites[0].id]:{verified:2,memory_hits:62,page_views:24,average_hourly_views:1,unique_ips:1}},unattributed:{}};
await page.route('http://lb.preview/**',r=>{const p=new URL(r.request().url()).pathname;if(!p.startsWith('/api/'))return r.fulfill({path:path.join(root,'ui',p==='/'?'index.html':p.slice(1))});return r.fulfill({json:p==='/api/bootstrap'?{initialized:true,authenticated:true,csrf:'fixture'}:p==='/api/temporary-tokens'?{tokens:[]}:state});});
await page.goto('http://lb.preview/');await page.locator('#shell').waitFor();
for(const view of ['overview','sites','interfaces','audit','tokens','settings','site-dialog']){
if(view==='site-dialog'){await page.locator('nav [data-view=sites]').click();await page.locator('#add-site').click();await page.locator('#site-dialog').waitFor();}
else{await page.locator(`nav [data-view=${view}]`).click();await page.locator(`#view-${view}`).waitFor();}
if(view==='overview')await page.locator('#site-protection-details summary').click();
const failures=await page.evaluate(()=>{
 const rgb=s=>{const v=s.match(/[\d.]+/g)?.map(Number);return v?.length>=3?[...v.slice(0,3),v[3]??1]:null;};
 const blend=(a,b)=>a.slice(0,3).map((v,i)=>v*a[3]+b[i]*(1-a[3]));
 const lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
 const out=[],seen=new Set(),walk=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
 const check=(el,text,pseudo=null)=>{
 if(!el.getClientRects().length||el.closest('script,style,[hidden],[aria-disabled=true],button:disabled,input:disabled,textarea:disabled,fieldset:disabled'))return;
 const cs=getComputedStyle(el,pseudo);if(cs.visibility!=='visible')return;
 const layers=[];let excluded=false;
 for(let ancestor=el;ancestor;ancestor=ancestor.parentElement){const st=getComputedStyle(ancestor);if(Number(st.opacity)<1||st.backgroundImage!=='none'){excluded=true;break;}const color=rgb(st.backgroundColor);if(color)layers.push(color);}
 if(excluded)return;let background=[255,255,255];for(const layer of layers.reverse())background=blend(layer,background);
 const foreground=rgb(cs.color);if(!foreground)return;foreground[3]*=Number(cs.opacity);
 const light=lum(blend(foreground,background)),dark=lum(background),ratio=(Math.max(light,dark)+.05)/(Math.min(light,dark)+.05);
 const size=parseFloat(cs.fontSize),weight=parseFloat(cs.fontWeight)||400,minimum=size>=24||(size>=18.667&&weight>=700)?3:4.5;
 if(ratio+.001<minimum)out.push({tag:el.tagName,id:el.id,classes:el.className,text:text.slice(0,35),pseudo,color:cs.color,background,ratio:Number(ratio.toFixed(2)),minimum});
 };
 while(walk.nextNode()){
 const node=walk.currentNode,el=node.parentElement;if(!node.textContent.trim()||!el||seen.has(el))continue;
 seen.add(el);check(el,node.textContent.trim());
 }
 for(const el of document.querySelectorAll('input[placeholder],textarea[placeholder]')){
 if(!el.value&&el.placeholder)check(el,el.placeholder,'::placeholder');
 }
 return out;
});reports.push({width,view,failures});
if(view==='site-dialog'){await page.screenshot({path:path.join(root,`.test-artifacts/ui/readability-site-dialog-${width}.png`)});await page.locator('#cancel-site').click();}
}
await page.close();
}
fs.mkdirSync(path.join(root,'.test-artifacts'),{recursive:true});fs.writeFileSync(path.join(root,'.test-artifacts/visual-readability.json'),JSON.stringify(reports,null,2));
const failures=reports.flatMap(r=>r.failures.map(f=>({width:r.width,view:r.view,...f})));
console.log(JSON.stringify({scenes:reports.length,failures},null,2));assert.equal(failures.length,0,'visible enabled text contrast');
console.log('PASS: visible text readability on six pages and site dialog with placeholders, desktop/mobile; gradients and disabled controls excluded');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
