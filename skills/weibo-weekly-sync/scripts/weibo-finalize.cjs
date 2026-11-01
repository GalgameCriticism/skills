// Publish the verified Weibo draft: handle the publish modal (31 | 公开 | 发布) and capture the public URL.
const path = require('node:path');
const fs = require('node:fs');
const { attach } = require('./browser-session.cjs');
const { checkSubmission, claimSubmission, weiboAccount } = require('./safety.cjs');

const args = { publish: false };
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a === '--publish') args.publish = true;
  else if (a.startsWith('--')) args[a.slice(2)] = process.argv[++i];
}
if (!/^\d+$/.test(args.draft || '')) throw new Error('--draft <numeric draft ID> is required');
const manifest = JSON.parse(fs.readFileSync(args.manifest, 'utf8'));
const outDir = path.dirname(args.manifest);
const receiptFile = path.join(outDir, 'weibo-receipt.json');
checkSubmission(receiptFile);
const log = o => console.log(JSON.stringify({ t: new Date().toISOString(), ...o }));
const sleep = ms => new Promise(r => setTimeout(r, ms));

const blocks = manifest.blocks.slice();
if (blocks.length && blocks[0].type !== 'image' && blocks[0].nodes.map(n => n.text).join('').trim() === manifest.source_title.trim()) blocks.shift();
const expectedText = blocks.filter(b => b.type !== 'image').map(b => b.nodes.map(n => n.text).join('')).join('').replace(/\s+/g, '');
const expectedImages = blocks.filter(b => b.type === 'image').length;

const stats = page => page.evaluate(() => {
  const root = document.querySelector('.ProseMirror');
  if (!root) return null;
  const figures = [...root.querySelectorAll('figure')];
  const srcs = figures.map(f => { const i = f.querySelector('img'); return i ? (i.currentSrc || i.src) : ''; });
  return {
    figures: figures.length,
    hosted: srcs.filter(u => /^https?:\/\//.test(u) && !/tc2\.service/.test(u)).length,
    placeholders: (root.innerText.match(/WBIMGPH_\d+/g) || []).length,
    text: root.innerText.replace(/WBIMGPH_\d+/g, '').replace(/\s+/g, ''),
    title: (document.querySelector('textarea[placeholder*="请输入标题"]') || {}).value || null,
  };
});

(async () => {
  const { ctx } = await attach(process.env.WEIBO_PROFILE, process.env.CHROME_DEBUG_PORT || 9333);
  const page = await ctx.newPage();
  await page.goto(`https://card.weibo.com/article/v5/editor#/draft/${args.draft}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(10000);

  const s = await stats(page);
  const textOk = s.text === expectedText;
  log({ step: 'fidelity', title: s.title, figures: s.figures, hosted: s.hosted, placeholders: s.placeholders, expectedImages, chars: s.text.length, expectedChars: expectedText.length, textOk });
  if (!args.publish) return;
  if (!textOk || s.placeholders !== 0 || s.hosted !== expectedImages || s.figures !== expectedImages) throw new Error('Fidelity gate failed; not publishing');

  await weiboAccount(page);
  claimSubmission(receiptFile);

  // The publish panel is a modal (visible either after 下一步 or restored from the previous session).
  let modal = page.locator('.n-modal, [role="dialog"]').filter({ hasText: '发布' }).first();
  if (!(await modal.count())) {
    await page.locator('button:has-text("下一步")').first().click({ timeout: 20000 });
    await sleep(5000);
    modal = page.locator('.n-modal, [role="dialog"]').filter({ hasText: '发布' }).first();
  }
  log({ step: 'modal_ready', present: await modal.count(), text: (await modal.count()) ? (await modal.innerText()).replace(/\n+/g, ' | ').slice(0, 200) : null });
  await page.screenshot({ path: path.join(outDir, 'weibo-publish-modal.png') });

  // The footer 发布 sits behind the modal mask; a DOM click bypasses hit-testing safely.
  const clicked = await page.evaluate(() => {
    const btn = [...document.querySelectorAll('button')].filter(b => b.textContent.trim() === '发布').pop();
    if (!btn) return false;
    btn.click();
    return true;
  });
  log({ step: 'publish_clicked', ok: clicked });
  await sleep(6000);

  if (!/ttarticle\/p\/show/.test(page.url())) {
    const confirm = await page.evaluate(() => {
      const btn = [...document.querySelectorAll('button')].filter(b => /^(确认|确定|继续发布)$/.test(b.textContent.trim())).pop();
      if (!btn) return false;
      btn.click();
      return true;
    });
    log({ step: 'confirm', ok: confirm });
  }

  for (let i = 0; i < 45; i++) {
    await sleep(2000);
    if (/ttarticle\/p\/show/.test(page.url())) break;
  }
  log({ step: 'after_publish', url: page.url() });
  await page.screenshot({ path: path.join(outDir, 'weibo-published.png') });
  fs.writeFileSync(path.join(outDir, 'weibo-receipt.json'), JSON.stringify({
    title: s.title, images: s.figures, hosted: s.hosted, chars: s.text.length, textOk,
    status: /ttarticle\/p\/show/.test(page.url()) ? 'published' : 'unknown',
    url: page.url(), at: new Date().toISOString(),
  }, null, 2));
  log({ step: 'browser_left_open' });
})().catch(e => { console.error('ERR', e && e.message); process.exit(1); });
