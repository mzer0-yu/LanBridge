const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {state: fixture} = require('./fixture');

async function main() {
  const artifacts = path.resolve(__dirname, '../../.test-artifacts/ui');
  fs.mkdirSync(artifacts, {recursive:true});
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    for (const width of [1440, 900, 760, 390]) {
      const page = await browser.newPage({viewport:{width, height:900}});
      const errors = [];
      const state = structuredClone(fixture);
      const hostile = '<img src=x onerror="window.__injected=true">';
      state.sites[0].name = hostile;
      state.audit = [{id:1, at:Date.now()/1000, action:'site_saved', detail:{name:hostile}}];
      state.audit_storage = {count:1, size_bytes:4096, limit_mb:10};
      page.on('pageerror', error => errors.push(error.message));
      await page.route('http://lb.preview/**', route => {
        const pathname = new URL(route.request().url()).pathname;
        if (!pathname.startsWith('/api/')) {
          return route.fulfill({path:path.resolve(__dirname, '../../ui', pathname==='/'?'index.html':pathname.slice(1))});
        }
        const result = pathname==='/api/bootstrap'
          ? {initialized:true, authenticated:true, csrf:'test'}
          : pathname==='/api/state' ? state
          : pathname==='/api/temporary-tokens'
            ? {tokens:[{id:'test', name:hostile, permissions:['sites'], expires:Date.now()/1000+3600, revealable:true}]}
            : {};
        return route.fulfill({json:result});
      });
      await page.goto('http://lb.preview/');
      await page.locator('#shell').waitFor();
      for (const view of ['overview', 'sites', 'interfaces', 'audit', 'tokens', 'settings']) {
        await page.locator(`nav [data-view=${view}]`).click();
        await page.locator(`#view-${view}`).waitFor();
        if (view==='tokens') await page.locator('.temporary-token-item').waitFor();
        assert.equal(await page.locator('nav [data-view].active').count(), 1);
        assert.equal(await page.locator('nav [data-view].active').getAttribute('data-view'), view);
        await page.evaluate(() => Promise.all([...document.querySelectorAll('nav [data-view]')].flatMap(el => el.getAnimations()).map(animation => animation.finished)));
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth), false, `${view} overflow at ${width}`);
        assert.equal(await page.evaluate(()=>Boolean(window.__injected)), false, `${view} executed untrusted text`);
        assert.equal(await page.locator(`#view-${view} img`).count(), 0);
        await page.screenshot({path:path.join(artifacts, `review-${view}-${width}.png`)});
      }
      await page.locator('nav [data-view=sites]').click();
      assert((await page.locator('#sites-table').textContent()).includes(hostile));
      await page.locator('#sites-table [data-edit]').click();
      assert.equal(await page.locator('#site-form [name=name]').inputValue(), hostile);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS: all six pages, desktop/mobile overflow, escaped site/token/log text');
  } finally {
    await browser.close();
  }
}
main().catch(error => {console.error(error); process.exit(1);});
