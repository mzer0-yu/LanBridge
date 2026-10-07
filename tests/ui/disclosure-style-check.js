const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {state} = require('./fixture');

async function main() {
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  const artifacts = path.resolve(__dirname, '../../.test-artifacts/ui');
  fs.mkdirSync(artifacts, {recursive:true});
  try {
    for (const width of [1920, 1440, 760, 390, 320]) {
      const page = await browser.newPage({viewport:{width, height:1000}});
      const errors = [];
      page.on('pageerror', error=>errors.push(error.message));
      await page.route('http://lb.preview/**', route=>{
        const pathname = new URL(route.request().url()).pathname;
        if (!pathname.startsWith('/api/')) return route.fulfill({path:path.resolve(__dirname,'../../ui',pathname==='/'?'index.html':pathname.slice(1))});
        const data = pathname==='/api/bootstrap' ? {initialized:true,authenticated:true,csrf:'fixture'}
          : pathname==='/api/state' ? state : pathname==='/api/temporary-tokens' ? {tokens:[]} : {};
        return route.fulfill({json:data});
      });
      await page.goto('http://lb.preview/');
      await page.locator('#shell').waitFor();
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
      const custom = page.locator('.connector-software>details');
      await custom.locator('summary').click();
      assert(await custom.locator('.advanced-body').isVisible());
      await page.locator('.connector-software').screenshot({path:path.join(artifacts,`custom-path-${width}.png`)});
      await page.locator('#account-config-details>summary').click();
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
