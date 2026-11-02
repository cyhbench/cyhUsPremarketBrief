// 檢視器的真實瀏覽器測試：用 playwright-core 驅動本機 Brave（headless），不用假 DOM。
// 執行：NODE_PATH=/opt/agent-node-tools/lib/node_modules/@playwright/mcp/node_modules node tests/viewer_browser.js
// 站台以 route 攔截供應（index.html、VERSION 取自 repo，報告取自 tests/fixtures），可控制回應內容與延遲。
const fs = require('fs'), path = require('path'), assert = require('assert');
const { chromium } = require('playwright-core');
const ROOT = path.join(__dirname, '..');
const ORIGIN = 'http://viewer.test';
const read = (p) => fs.readFileSync(p, 'utf8');
const SAMPLE = read(path.join(__dirname, 'fixtures/sample.md'));   // 2026-11-02
const EDGE = read(path.join(__dirname, 'fixtures/edge.md'));       // 2026-11-03
const IDX = JSON.stringify({ schema_version: 1, reports: [{ date: '2026-11-03' }, { date: '2026-11-02' }] });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let passed = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); passed++; };
const eq = (a, b, msg) => { assert.deepStrictEqual(a, b, msg); passed++; };

function site(over) {  // over: path -> {status,body,delay}；未指定者走預設
  const d = {
    '/': { body: read(path.join(ROOT, 'index.html')), type: 'text/html' },
    '/VERSION': { body: '0.1.0\n' },
    '/reports/index.json': { body: IDX },
    '/reports/2026-11-02.md': { body: SAMPLE },
    '/reports/2026-11-03.md': { body: EDGE },
  };
  return Object.assign(d, over || {});
}
async function open(browser, opts, routes, hash) {
  const ctx = await browser.newContext({ viewport: { width: opts.w || 390, height: opts.h || 800 }, colorScheme: opts.scheme || 'light', deviceScaleFactor: 1 });
  await ctx.clock.setFixedTime(new Date(opts.now || '2026-11-04T15:00:00Z'));  // 美東今日固定，測試不隨實際日期變動
  const page = await ctx.newPage();
  const reqs = [];
  page.on('request', (r) => reqs.push(r.url()));
  page.on('pageerror', (e) => { throw e; });
  await page.route('**/*', async (route) => {
    const u = new URL(route.request().url());
    const e = routes[u.pathname];
    if (u.origin !== ORIGIN || !e) return route.fulfill({ status: 404, body: '' });
    if (e.delay) await wait(e.delay);
    if (e.abort) return route.abort();
    await route.fulfill({ status: e.status || 200, body: e.body || '', contentType: (e.type || 'text/plain') + '; charset=utf-8' });
  });
  await page.goto(ORIGIN + '/' + (hash || ''));
  return { page, ctx, reqs };
}
const settle = (page) => page.waitForFunction(() => document.getElementById('report').getAttribute('aria-busy') === 'false', null, { timeout: 5000 });
const text = (page, sel) => page.locator(sel).first().innerText();

(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/bin/brave-browser', headless: true, args: ['--no-sandbox'] });
  try {
    // 1. 四種寬度 × 淺色／深色：七節完整、整頁無水平溢出、來源儲存格文字都看得到
    const cells = [...SAMPLE.matchAll(/^\|(.+)\|$/gm)].filter((m) => !/^[\s|:\-]+$/.test(m[1]))
      .flatMap((m) => m[1].split('|').map((c) => c.trim().replace(/[`*]/g, ''))).filter(Boolean);
    for (const w of [320, 390, 768, 1280]) for (const scheme of ['light', 'dark']) {
      const { page, ctx } = await open(browser, { w, scheme }, site(), '#2026-11-02');
      await settle(page);
      const m = await page.evaluate(() => ({
        sw: document.documentElement.scrollWidth, iw: window.innerWidth,
        secs: [...document.querySelectorAll('[id^=sec-]:not([id$=-h])')].map((e) => e.id),
        src: !!document.querySelector('details.sources'),
        bg: getComputedStyle(document.body).backgroundColor,
        txt: document.getElementById('report').innerText }));
      ok(m.sw <= m.iw, `無水平溢出 ${w}/${scheme}: ${m.sw}>${m.iw}`);
      eq(m.secs, [1, 2, 3, 4, 5, 6, 7].map((n) => 'sec-' + n), `七節 ${w}/${scheme}`);
      ok(m.src, '資料來源區塊');
      eq(m.bg, scheme === 'dark' ? 'rgb(12, 20, 34)' : 'rgb(243, 246, 250)', `配色 ${scheme}`);
      const missing = cells.filter((c) => !m.txt.replace(/\s+/g, ' ').includes(c.replace(/\s+/g, ' ')));
      eq(missing, [], `儲存格文字都顯示 ${w}/${scheme}`);
      // 資料來源預設收合，展開後所有網址完整顯示
      eq(await page.locator('details.sources').evaluate((e) => e.open), false, '來源預設收合');
      await page.locator('details.sources summary').click();
      const srcTxt = await text(page, 'details.sources');
      ok(srcTxt.includes('symbols=.N225') && srcTxt.includes('day=oct31.2026'), '來源網址完整');
      await ctx.close();
    }

    // 2. 漲跌顏色 B：漲綠跌紅；零/無正負號/混合欄不著色；手機卡片與桌面表格都適用
    for (const w of [390, 1280]) {
      const { page, ctx } = await open(browser, { w }, site(), '#2026-11-02');
      await settle(page);
      const col = await page.evaluate(() => {
        const vis = (e) => e.offsetParent !== null;
        const out = {};
        for (const e of document.querySelectorAll('.up, .down, .flat')) if (vis(e)) out[e.textContent.trim()] = [e.className, getComputedStyle(e).color];
        return out;
      });
      eq(col['+2.24%'][0], 'up', '+2.24% 漲'); eq(col['-0.33%'][0], 'down', '-0.33% 跌');
      eq(col['+32.75'][0], 'up'); eq(col['-140.32'][0], 'down');
      eq(col['+2.24%'][1], 'rgb(19, 115, 75)', '漲=綠'); eq(col['-0.33%'][1], 'rgb(180, 35, 50)', '跌=紅');
      ok(!('101.78' in col) && !col['63,081.86'], '價格不著色');
      const cardMain = await page.evaluate(() => [...document.querySelectorAll('.card .main dd')].filter((e) => e.offsetParent).length);
      ok(w < 768 ? cardMain > 0 : cardMain === 0, `放大主值只在卡片 ${w}`);
      await ctx.close();
    }

    // 3. 超寬表：局部橫捲能到最後一欄、可鍵盤操作、提示只在溢出時顯示、整頁不溢出
    {
      const { page, ctx } = await open(browser, { w: 320 }, site(), '#2026-11-03');
      await settle(page);
      // 第 3 節有重複表頭 → 保底表格；用 CSS 讓它確實超寬以驗證捲動
      const info = await page.evaluate(() => {
        const sc = document.querySelector('#sec-3 .scroll');
        const t = sc.querySelector('table'); t.style.minWidth = '700px';
        window.dispatchEvent(new Event('resize'));
        return { cw: sc.clientWidth, sw: sc.scrollWidth, pw: document.documentElement.scrollWidth, iw: innerWidth, role: sc.getAttribute('role'), tab: sc.tabIndex };
      });
      ok(info.sw > info.cw, '容器確實超寬'); ok(info.pw <= info.iw, '整頁不溢出'); eq(info.role, 'region'); eq(info.tab, 0);
      ok(await page.locator('#sec-3 .hint').isVisible(), '溢出時顯示提示');
      await page.locator('#sec-3 .scroll').focus();
      for (let i = 0; i < 20; i++) await page.keyboard.press('ArrowRight');  // 只用方向鍵，不靠 End
      await wait(200);
      const end = await page.locator('#sec-3 .scroll').evaluate((e) => ({ l: e.scrollLeft, max: e.scrollWidth - e.clientWidth }));
      ok(end.l > 0 && Math.abs(end.l - end.max) <= 1, '鍵盤可捲到最後一欄 ' + JSON.stringify(end));
      const lastVisible = await page.evaluate(() => { const sc = document.querySelector('#sec-3 .scroll'); const th = sc.querySelector('th:last-child').getBoundingClientRect(); const r = sc.getBoundingClientRect(); return th.right <= r.right + 1; });
      ok(lastVisible, '最後一欄進入可視範圍');
      await ctx.close();
    }
    {  // 沒有溢出時不顯示提示（桌面寬度）
      const { page, ctx } = await open(browser, { w: 1280 }, site(), '#2026-11-03');
      await settle(page);
      ok(!(await page.locator('#sec-3 .hint').isVisible()), '未溢出不顯示提示');
      await ctx.close();
    }

    // 4. 惡意內容：不執行、無圖片／iframe、壞連結不可點、好連結安全屬性、圖片只剩替代文字
    {
      const { page, ctx, reqs } = await open(browser, { w: 390 }, site(), '#2026-11-03');
      await settle(page);
      const r = await page.evaluate(() => ({
        pwn: window.__pwn, imgs: document.querySelectorAll('#report img, iframe, svg, script:not(#marked):not(#app)').length,
        links: [...document.querySelectorAll('#report a')].map((a) => [a.getAttribute('href'), a.rel, a.target]),
        txt: document.getElementById('sec-1').innerText }));
      eq(r.pwn, undefined, '腳本未執行'); eq(r.imgs, 0, '無 img/iframe/svg/script');
      const body = r.links.filter((l) => l[0] && !l[0].startsWith('#'));
      eq(body, [['https://example.com/ok', 'noopener noreferrer', '_blank'], ['https://example.com/a', 'noopener noreferrer', '_blank']], '只剩合法 https 連結（內文一個、來源一個）');
      ok(r.txt.includes('<script>') && r.txt.includes('替代文字ALT') && r.txt.includes('壞連結') && r.txt.includes('帳密'), '惡意字樣顯示為文字、連結剩文字');
      ok(reqs.every((u) => new URL(u).origin === ORIGIN), '只有同站請求：' + reqs.filter((u) => new URL(u).origin !== ORIGIN));
      await ctx.close();
    }

    // 5. 頁尾：版本、產生時間（取自報告原始行）；切換日期不殘留
    {
      const { page, ctx, reqs } = await open(browser, { w: 390 }, site(), '#2026-11-02');
      await settle(page);
      eq(await text(page, '#f-ver'), '網站版本：0.1.0');
      eq(await text(page, '#f-gen'), '資料產生時間：美東 09:05 EST／台灣 11-02 22:05');
      ok((await text(page, '#report')).includes('美東 09:05 EST／台灣 11-02 22:05'), '導言也顯示');
      ok((await text(page, '#report')).includes('試跑報告'), '試跑標示可見');
      await page.selectOption('#dates', '2026-11-03');
      await page.waitForFunction(() => document.getElementById('f-gen').textContent.includes('11-03'));
      eq(await text(page, '#f-gen'), '資料產生時間：美東 09:03 EST／台灣 11-03 22:03');
      eq(await page.evaluate(() => location.hash), '#2026-11-03');
      await page.goBack();
      await page.waitForFunction(() => document.getElementById('f-gen').textContent.includes('11-02'));
      eq(await page.inputValue('#dates'), '2026-11-02', '上一頁回到 11-02');
      await page.goForward();
      await page.waitForFunction(() => document.getElementById('f-gen').textContent.includes('11-03'));
      // 節目錄：焦點移到節標題，網址日期片段不變、不新增歷史
      const hist = await page.evaluate(() => history.length);
      await page.locator('.toc a', { hasText: 'Mag 7' }).click();
      eq(await page.evaluate(() => document.activeElement.id), 'sec-6-h');
      eq(await page.evaluate(() => [location.hash, history.length]), ['#2026-11-03', hist]);
      ok(reqs.every((u) => new URL(u).origin === ORIGIN), '同站請求');
      await ctx.close();
    }
    for (const [name, r] of [['缺失', { status: 404 }], ['空白', { body: '  \n' }], ['500', { status: 500 }], ['網路失敗', { abort: true }]]) {
      const { page, ctx } = await open(browser, { w: 390 }, site({ '/VERSION': r }), '#2026-11-02');
      await settle(page);
      eq(await text(page, '#f-ver'), '網站版本：無法取得', 'VERSION ' + name);
      ok((await text(page, '#report')).includes('美股盤前快報 2026-11-02'), '版本失敗不影響正文');
      await ctx.close();
    }

    // 6. 交錯慢 A／快 B：畫面、狀態、頁尾皆屬 B
    {
      const { page, ctx } = await open(browser, { w: 390 }, site({ '/reports/2026-11-03.md': { body: EDGE, delay: 700 } }), '#2026-11-03');
      await wait(100);
      await page.selectOption('#dates', '2026-11-02');
      await page.waitForFunction(() => document.getElementById('f-gen').textContent.includes('11-02'));
      await wait(1000);
      ok((await text(page, '#report')).includes('2026-11-02'), '畫面為 B');
      eq(await text(page, '#f-gen'), '資料產生時間：美東 09:05 EST／台灣 11-02 22:05');
      ok(!(await text(page, '#report')).includes('惡意測試'), 'A 未覆蓋');
      await ctx.close();
    }

    // 7. 狀態畫面
    const states = [
      ['清單 404', { '/reports/index.json': { status: 404 } }, '尚無報告清單', true],
      ['清單 500', { '/reports/index.json': { status: 500 } }, '讀取報告清單失敗', true],
      ['清單壞格式', { '/reports/index.json': { body: '{"x":1}' } }, '報告清單格式不合法', true],
      ['清單 JSON 壞', { '/reports/index.json': { body: 'nope' } }, '報告清單格式不合法', true],
      ['清單空', { '/reports/index.json': { body: '{"schema_version":1,"reports":[]}' } }, '尚無報告', true],
      ['清單全為非法日期', { '/reports/index.json': { body: '{"schema_version":1,"reports":[{"date":"2026-02-30"},{"date":"../x"}]}' } }, '尚無報告', true],
      ['報告 404', { '/reports/2026-11-02.md': { status: 404 } }, '報告更新中', false],
      ['報告 500', { '/reports/2026-11-02.md': { status: 500 } }, '讀取報告失敗', false],
      ['報告空本文', { '/reports/2026-11-02.md': { body: '' } }, '讀取報告失敗', false],
      ['報告錯日期', { '/reports/2026-11-02.md': { body: SAMPLE.replace('2026-11-02', '2026-10-01') } }, '報告內容與日期不符', false],
    ];
    for (const [name, over, title, listDown] of states) {
      const { page, ctx } = await open(browser, { w: 390 }, site(over), '#2026-11-02');
      await settle(page);
      const t = await text(page, '#report');
      ok(t.includes(title), `${name}: 應顯示「${title}」，實際：${t.slice(0, 80)}`);
      eq(await page.locator('#dates').isDisabled(), listDown, `${name}: 日期選單`);
      eq(await text(page, '#f-gen'), '資料產生時間：—', name + ' 頁尾不殘留');
      if (!listDown) {  // 仍能換到另一天並成功顯示
        await page.selectOption('#dates', '2026-11-03');
        await page.waitForFunction(() => document.getElementById('report').innerText.includes('惡意測試'));
      }
      await ctx.close();
    }
    {  // 手動重試：先失敗、之後成功（正向對照）
      let n = 0;
      const { page, ctx } = await open(browser, { w: 390 }, site(), '#2026-11-02');
      await page.unroute('**/*');
      await page.route('**/*', (route) => {
        const u = new URL(route.request().url());
        const e = site()[u.pathname];
        if (u.pathname === '/reports/2026-11-02.md' && n++ === 0) return route.fulfill({ status: 500, body: '' });
        return e ? route.fulfill({ status: 200, body: e.body, contentType: (e.type || 'text/plain') + '; charset=utf-8' }) : route.fulfill({ status: 404, body: '' });
      });
      await page.reload(); await settle(page);
      ok((await text(page, '#report')).includes('讀取報告失敗'));
      await page.getByRole('button', { name: '重試此日期' }).click();
      await page.waitForFunction(() => document.getElementById('report').innerText.includes('重要美國市場頭條'));
      await ctx.close();
    }
    {  // 日期提示：不存在的日期、非日期片段、歷史/最新非今日
      let o = await open(browser, { w: 390 }, site(), '#2026-01-05'); await settle(o.page);
      ok((await text(o.page, '#banner')).includes('找不到指定日期，改顯示最新一份')); ok((await text(o.page, '#report')).includes('2026-11-03')); await o.ctx.close();
      o = await open(browser, { w: 390 }, site(), '#abc'); await settle(o.page);
      ok(!(await text(o.page, '#banner')).includes('找不到'), '非日期片段不提示'); await o.ctx.close();
      o = await open(browser, { w: 390 }, site(), '#2026-11-02'); await settle(o.page);
      ok((await text(o.page, '#banner')).includes('歷史快報')); await o.ctx.close();
      o = await open(browser, { w: 390 }, site(), '#2026-11-03'); await settle(o.page);
      ok((await text(o.page, '#banner')).includes('今日報告尚未發布或未產生')); await o.ctx.close();
      o = await open(browser, { w: 390, now: '2026-11-03T15:00:00Z' }, site(), '#2026-11-03'); await settle(o.page);
      eq(await text(o.page, '#banner'), '', '美東今日無提示'); ok((await text(o.page, '#report')).includes('盤前快照')); await o.ctx.close();
    }

    // 8. 無障礙：節目錄與按鈕 ≥44px、焦點外框可見、減少動態不需動畫
    {
      const { page, ctx } = await open(browser, { w: 390 }, site(), '#2026-11-02');
      await settle(page);
      const h = await page.evaluate(() => [...document.querySelectorAll('.toc a, button, select, summary')].filter((e) => e.offsetParent).map((e) => Math.round(e.getBoundingClientRect().height)));
      ok(h.length >= 10 && h.every((x) => x >= 44), '觸控目標 ≥44px：' + h);
      await page.keyboard.press('Tab'); await page.keyboard.press('Tab');
      const ol = await page.evaluate(() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, s.outlineWidth]; });
      eq(ol, ['solid', '3px'], '焦點外框');
      await ctx.close();
    }
    console.log(`viewer_browser OK (${passed} assertions)`);
  } finally { await browser.close(); }
})().catch((e) => { console.error(e); process.exit(1); });
