// 檢視器的日期切換測試：node tests/viewer_race.js（以 stub 的 DOM／fetch 執行 index.html 內嵌 script，不需瀏覽器）
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];

function run(responses) {  // responses: path -> {status,text,delay}
  const els = {};
  const el = (id) => els[id] || (els[id] = {
    id, textContent: '', innerHTML: '', disabled: false, value: '', children: [], handlers: {},
    classList: { toggle() {} }, style: {}, appendChild(c) { this.children.push(c); if (!this.value) this.value = c.value; },
    addEventListener(t, f) { this.handlers[t] = f; } });
  const ctx = {
    document: { getElementById: el, createElement: () => ({}), title: '' },
    location: { hash: '' }, URL, Intl, Date, Array, Set, JSON, Promise, setTimeout, String, isNaN,
    marked: { Renderer: function () {}, setOptions() {}, parse: (t) => 'HTML:' + t },
    fetch: (p) => new Promise((res) => setTimeout(() => {
      const r = responses[p] || { status: 404, text: '' };
      res({ ok: r.status === 200, status: r.status, text: async () => r.text });
    }, (responses[p] || {}).delay || 0)),
  };
  vm.runInNewContext(script, ctx);
  return { el, ctx };
}
const idx = JSON.stringify({ schema_version: 1, reports: [{ date: '2026-11-03' }, { date: '2026-11-02' }] });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // 1. 慢的舊請求不得覆蓋後選的日期
  let { el } = run({ 'reports/index.json': { status: 200, text: idx },
    'reports/2026-11-03.md': { status: 200, text: '# 2026-11-03 A', delay: 150 },
    'reports/2026-11-02.md': { status: 200, text: '# 2026-11-02 B', delay: 10 } });
  await wait(20);
  el('dates').value = '2026-11-02'; el('dates').handlers.change();
  await wait(300);
  assert.strictEqual(el('report').innerHTML, 'HTML:# 2026-11-02 B');

  // 2. 單份報告 404 不得關掉選單，可改看其他日期
  ({ el } = run({ 'reports/index.json': { status: 200, text: idx },
    'reports/2026-11-02.md': { status: 200, text: '# 2026-11-02 B' } }));
  await wait(30);
  assert.strictEqual(el('report').textContent, '報告更新中，請稍後重試。');
  assert.strictEqual(el('dates').disabled, false);
  el('dates').value = '2026-11-02'; el('dates').handlers.change();
  await wait(30);
  assert.strictEqual(el('report').innerHTML, 'HTML:# 2026-11-02 B');

  // 3. 清單失敗才關閉選單
  ({ el } = run({}));
  await wait(30);
  assert.strictEqual(el('dates').disabled, true);
  console.log('viewer OK');
})().catch((e) => { console.error(e); process.exit(1); });
