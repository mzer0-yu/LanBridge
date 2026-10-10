const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {state} = require('./fixture');

async function main() {
  const browser = await chromium.launch({headless:true, channel:'msedge', ignoreDefaultArgs:['--hide-scrollbars']});
  const artifacts = path.resolve(__dirname, '../../.test-artifacts/ui');
  fs.mkdirSync(artifacts, {recursive:true});
  try {
    for (const width of [1920, 1440, 1100, 900, 760, 390, 320]) {
      const page = await browser.newPage({viewport:{width, height:1000}});
      const errors = [];
      page.on('pageerror', error=>errors.push(error.message));
      await page.route('http://lb.preview/**', route=>{
        const pathname = new URL(route.request().url()).pathname;
        if (!pathname.startsWith('/api/')) return route.fulfill({path:path.resolve(__dirname,'../../ui',pathname==='/'?'index.html':pathname.slice(1))});
        const data = pathname==='/api/bootstrap' ? {initialized:true,authenticated:true,csrf:'fixture'}
          : pathname==='/api/state' ? {...state,audit:[{id:1,at:1760000000,action:'site_saved',detail:{name:'布局检查网站',hostname:'layout.example.com',note:'长内容'.repeat(80)}}]} : pathname==='/api/temporary-tokens' ? {tokens:[]} : {};
        return route.fulfill({json:data});
      });
      await page.goto('http://lb.preview/');
      await page.locator('#shell').waitFor();
      // Classic Windows scrollbars must not resize cards when content gets taller.
      if(width===1440){
        await page.locator('nav [data-view=settings]').click();
        await page.setViewportSize({width,height:700});
        await page.evaluate(()=>{
          for(const el of document.querySelectorAll('details'))el.open=false;
          document.querySelector('#domain-onboarding').hidden=true;
          document.querySelector('footer').hidden=true;
        });
        const positions=async()=>page.locator('.settings-column>.panel').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width}}));
        const before=await positions();
        assert(!(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight)),'Collapsed page should fit viewport');
        await page.locator('#account-config-details').evaluate(el=>el.open=true);
        const after=await positions();
        assert(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight),'Expanded page should require scrolling');
        for(let i=0;i<before.length;i++){
          assert(Math.abs(before[i].x-after[i].x)<.1,'Card horizontal position changed');
          assert(Math.abs(before[i].width-after[i].width)<.1,'Card width changed');
          if(i>=2)assert(Math.abs(before[i].y-after[i].y)<.1,'Unrelated right column moved');
        }
        assert(after[1].y>before[1].y,'Connector should move down with expanded content');
        await page.screenshot({path:path.join(artifacts,'settings-scrollbar-stable-1440.png'),fullPage:true});
        await page.evaluate(()=>{document.querySelector('#account-config-details').open=false;document.querySelector('#domain-onboarding').hidden=false;document.querySelector('footer').hidden=false;});
        await page.setViewportSize({width,height:1000});
      }
      // Render hidden branches in isolation, preserving their original state.
      // Titles must not move or change size when their own body is opened.
      const geometry = await page.locator('details').evaluateAll(details=>details.map(detail=>{
        const ancestors=[];
        for(let el=detail;el&&el!==document.body;el=el.parentElement){
          ancestors.push({el,hidden:el.hidden,open:el.open});
          el.hidden=false;
          if(el.tagName==='DETAILS'&&el!==detail)el.open=true;
        }
        const dialog=detail.closest('dialog');
        const dialogWasOpen=dialog?.open;
        if(dialog&&!dialogWasOpen)dialog.showModal();
        const summary=detail.querySelector(':scope>summary');
        const measure=()=>{
          const rect=summary.getBoundingClientRect(),parent=detail.parentElement.getBoundingClientRect();
          const style=getComputedStyle(summary);
          return {x:rect.x-parent.x,y:rect.y-parent.y,width:rect.width,height:rect.height,
            font:style.font,padding:style.padding,margin:style.margin,
            containerWidth:detail.getBoundingClientRect().width,
            overflow:document.documentElement.scrollWidth>innerWidth};
        };
        detail.open=false;const before=measure();
        detail.open=true;const after=measure();
        if(dialog&&!dialogWasOpen)dialog.close();
        for(const {el,hidden,open} of ancestors){el.hidden=hidden;if(el.tagName==='DETAILS')el.open=open;}
        return {label:detail.id||summary.textContent.trim(),before,after};
      }));
      for(const {label,before,after} of geometry){
        assert(before.width>0&&before.height>0,`${label}: title not rendered at ${width}px`);
        for(const key of ['x','y','width','height','containerWidth'])assert(Math.abs(before[key]-after[key])<.1,`${label}: ${key} changed ${before[key]} → ${after[key]} at ${width}px`);
        for(const key of ['font','padding','margin'])assert.equal(after[key],before[key],`${label}: ${key} changed at ${width}px`);
        assert.equal(after.overflow,false,`${label}: expanded page overflows at ${width}px`);
      }
      console.log(`PASS: ${geometry.length} disclosure titles retain geometry and spacing at ${width}px`);
      const toggles=await page.locator('[aria-expanded][aria-controls]').evaluateAll(buttons=>buttons.map(button=>{
        const target=document.getElementById(button.getAttribute('aria-controls'));
        const saved=new Map();
        for(const node of [button,target])for(let el=node;el&&el!==document.body;el=el.parentElement){
          if(!saved.has(el))saved.set(el,{hidden:el.hidden,open:el.open});
          if(el!==target)el.hidden=false;
          if(el.tagName==='DETAILS')el.open=true;
        }
        const measure=()=>{
          const r=button.getBoundingClientRect(),p=button.parentElement.getBoundingClientRect();
          return {x:r.x-p.x,y:r.y-p.y,width:r.width,height:r.height};
        };
        const expanded=button.getAttribute('aria-expanded');
        target.hidden=true;button.setAttribute('aria-expanded','false');const before=measure();
        target.hidden=false;button.setAttribute('aria-expanded','true');const after=measure();
        const overflow=document.documentElement.scrollWidth>innerWidth;
        button.setAttribute('aria-expanded',expanded);
        for(const [el,{hidden,open}] of saved){el.hidden=hidden;if(el.tagName==='DETAILS')el.open=open;}
        return {label:button.id||button.textContent.trim(),before,after,overflow};
      }));
      for(const {label,before,after,overflow} of toggles){
        assert(before.width>0,`${label}: expand control not rendered at ${width}px`);
        for(const key of ['x','y','width','height'])assert(Math.abs(before[key]-after[key])<.1,`${label}: ${key} shifted at ${width}px`);
        assert.equal(overflow,false,`${label}: expanded content overflows at ${width}px`);
      }
      console.log(`PASS: ${toggles.length} button disclosures retain layout at ${width}px`);
      // Check every static disclosure, including nested/initially hidden branches.
      const checked = await page.locator('details').evaluateAll(details=>details.map(detail=>{
        const summary = detail.querySelector(':scope>summary');
        const states = [false,true].map(open=>{
          detail.open=open;
          const style=getComputedStyle(summary);
          return {open,top:style.borderTopWidth,bottom:style.borderBottomWidth};
        });
        detail.open=false;
        return {label:summary.textContent.trim(),states};
      }));
      for (const detail of checked) for (const state of detail.states) {
        assert.equal(state.top,'0px',`${detail.label} top border, open=${state.open}`);
        assert.equal(state.bottom,'0px',`${detail.label} bottom border, open=${state.open}`);
      }
      await page.locator('nav [data-view=settings]').click();
      // Sibling card gaps must stay equal with any combination of expanded bodies.
      for(let mask=0;mask<4;mask++){
        const gaps=await page.evaluate(mask=>{
          const cards=['#domain-onboarding','#account-config-details'].map(s=>document.querySelector(s));
          cards.forEach((el,i)=>el.open=!!(mask&(1<<i)));
          return cards.slice(1).map((el,i)=>el.getBoundingClientRect().top-cards[i].getBoundingClientRect().bottom);
        },mask);
        for(const gap of gaps)assert(Math.abs(gap-12)<1,`Sibling disclosure gap ${gap}px at ${width}px, state ${mask}`);
      }
      await page.evaluate(()=>{for(const el of document.querySelectorAll('#cloudflare-account-panel>details'))el.open=false});
      await page.locator('#cloudflare-account-panel').screenshot({path:path.join(artifacts,`account-collapsed-spacing-${width}.png`)});
      const custom = page.locator('.connector-software>details');
      await custom.locator('summary').click();
      assert(await custom.locator('.advanced-body').isVisible());
      await page.locator('.connector-software').screenshot({path:path.join(artifacts,`custom-path-${width}.png`)});
      await page.locator('#account-config-details>summary').click();
      assert.equal(await page.locator('#account-config-toggle').textContent(),'Cloudflare 授权与连接');
      assert.equal(await page.locator('#account-config-details #tunnel-maintenance').count(),1);
      assert.equal(await page.locator('#tunnel-maintenance').evaluate(el=>el.open),false);
      await page.locator('#tunnel-maintenance>summary').click();
      assert(await page.locator('#tunnel-token-refresh').isVisible());
      const maintenanceGap=await page.locator('#tunnel-maintenance').evaluate(el=>{
        let previous=el.previousElementSibling;
        while(previous&&previous.getBoundingClientRect().height===0)previous=previous.previousElementSibling;
        return el.getBoundingClientRect().top-previous.getBoundingClientRect().bottom;
      });
      assert(Math.abs(maintenanceGap-12)<1,`Nested maintenance gap ${maintenanceGap}px at ${width}px`);
      await page.locator('#cloudflare-account-panel').screenshot({path:path.join(artifacts,`cloudflare-connection-${width}.png`)});
      await page.locator('#tunnel-maintenance>summary').click();
      await page.locator('#account-config-details').evaluate(el=>el.open=false);
      await page.evaluate(()=>locateTunnelToken());
      assert(await page.locator('#tunnel-token-refresh').isVisible());
      assert.equal(await page.locator('#account-config-details').evaluate(el=>el.open),true);
      await page.locator('#tunnel-maintenance>summary').click();
      await page.locator('#account-use-manual').click();
      await page.locator('#manual-account-config>summary').click();
      await page.locator('#advanced-token-management>summary').click();
      await page.locator('#verification-advanced>summary').click();
      // Measure the rendered gap, including margins collapsing through form wrappers.
      const spacing = await page.evaluate(()=>[
        ['.connector-software>details', '.advanced-body>label', 12],
        ['#manual-account-config', '.manual-account-fields>div>label', 8],
        ['#advanced-token-management', '.advanced-body>label', 8],
        ['#verification-advanced', '.advanced-body>p', 20],
      ].map(([selector,first,max])=>{
        const detail=document.querySelector(selector), field=detail.querySelector(first);
        return {selector,max,gap:field.getBoundingClientRect().top-detail.querySelector('summary').getBoundingClientRect().bottom};
      }));
      for (const {selector,max,gap} of spacing) assert(gap>=0&&gap<=max,`${selector}: leading gap ${gap}px at ${width}px`);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(artifacts,`settings-expanded-${width}.png`),fullPage:true});
      const lines = await page.locator('#view-settings').evaluate(root=>Array.from(root.querySelectorAll('*')).filter(el=>{
        const style=getComputedStyle(el),rect=el.getBoundingClientRect();
        return rect.width>0&&rect.height>0&&[style.borderTopWidth,style.borderBottomWidth].some(w=>parseFloat(w)>0)&&parseFloat(style.borderLeftWidth)===0&&parseFloat(style.borderRightWidth)===0;
      }).map(el=>({tag:el.tagName,id:el.id,class:el.className})));
      console.log(JSON.stringify({width,disclosures:checked.length,remainingHorizontalSeparators:lines}));
      assert.deepEqual(errors,[]);
      await page.close();
    }
    console.log('PASS: all disclosure headings, collapsed/expanded states, desktop/mobile nested settings');
  } finally {
    await browser.close();
  }
}
main().catch(error=>{console.error(error);process.exit(1);});
