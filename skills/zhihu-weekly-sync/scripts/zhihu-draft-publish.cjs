// Open drafts from the creator draft list via their 编辑 buttons, pick the one with full fidelity, publish it.
const path = require('node:path');
const fs = require('node:fs');
const { attach } = require('./browser-session.cjs');
const { checkSubmission, claimSubmission, zhihuAccount } = require('./safety.cjs');

const args = { publish: false };
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a === '--publish') args.publish = true;
  else if (a.startsWith('--')) args[a.slice(2)] = process.argv[++i];
}
const manifest = JSON.parse(fs.readFileSync(args.manifest, 'utf8'));
const receiptFile = path.join(path.dirname(args.manifest), 'zhihu-receipt.json');
checkSubmission(receiptFile);
const blocks = manifest.blocks.slice();
if (blocks.length && blocks[0].type !== 'image' && blocks[0].nodes.map(n => n.text).join('').trim() === manifest.source_title.trim()) blocks.shift();
const expectedText = blocks.filter(b => b.type !== 'image').map(b => b.nodes.map(n => n.text).join('')).join('').replace(/\s+/g, '');
const expectedImages = blocks.filter(b => b.type === 'image').length;
const log = o => console.log(JSON.stringify({ t: new Date().toISOString(), ...o }));

const stats = page => page.evaluate(() => {
  const el = document.querySelector('.public-DraftEditor-content[contenteditable="true"]');
  if (!el) return null;
  const imgs = [...el.querySelectorAll('img')].map(i => i.currentSrc || i.src);
  const text = [...el.children[0].children].filter(b => !b.querySelector('img')).map(b => b.innerText.replace(/添加图片注释[^\n]*/g, '').trim()).join('\n').replace(/\s+/g, '');
  return { images: imgs.length, v2: imgs.filter(u => /\/v2-[0-9a-f]{32}/.test(u)).length, text };
});

(async () => {
  const { ctx, reused, port } = await attach(process.env.ZHIHU_PROFILE, process.env.ZHIHU_DEBUG_PORT || 9444);
  log({ step: 'browser', reused, port });
  const page = await ctx.newPage();
  try {
    await page.goto('https://www.zhihu.com/creator/manage/creation/draft?type=article', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(6000);
    const editButtons = page.getByText('编辑', { exact: true });
    const n = await editButtons.count();
    log({ step: 'list', drafts: n });
    for (let i = 0; i < n; i++) {
      await page.goto('https://www.zhihu.com/creator/manage/creation/draft?type=article', { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(5000);
      const before = ctx.pages().length;
      await page.getByText('编辑', { exact: true }).nth(i).click();
      await page.waitForTimeout(6000);
      let work = page;
      if (ctx.pages().length > before) {
        work = ctx.pages()[ctx.pages().length - 1];
        await work.waitForLoadState('domcontentloaded').catch(() => {});
        await work.waitForTimeout(6000);
      }
      const s = await stats(work);
      if (!s) { log({ step: 'no_editor', i, url: work.url() }); if (work !== page) await work.close().catch(() => {}); continue; }
      const textOk = s.text === expectedText;
      log({ step: 'check', i, images: s.images, v2: s.v2, chars: s.text.length, textOk, url: work.url() });
      if (s.images === expectedImages && s.v2 === expectedImages && textOk) {
        log({ step: 'candidate_ok', i });
        if (!args.publish) return;
        await zhihuAccount(work);
        claimSubmission(receiptFile);
        const respPromise = work.waitForResponse(r => /\/api\/v4\/content\/publish/.test(r.url()) && r.request().method() === 'POST', { timeout: 120000 });
        await work.getByRole('button', { name: '发布', exact: true }).click();
        await work.waitForTimeout(3000);
        const dlg = work.locator('.Modal button:has-text("发布")');
        if (await dlg.count()) await dlg.last().click().catch(() => {});
        const resp = await respPromise;
        const body = await resp.json().catch(() => ({}));
        const id = body && body.data && (body.data.id || body.data.article_id);
        log({ step: 'publish_response', status: resp.status(), id });
        await work.waitForTimeout(8000);
        log({ step: 'after_publish_url', url: work.url() });
        fs.writeFileSync(receiptFile, JSON.stringify({ title: manifest.source_title, images: s.images, textChars: s.text.length, status: id ? 'submitted' : 'unknown', responseStatus: resp.status(), articleId: id, afterUrl: work.url(), at: new Date().toISOString() }, null, 2));
        await work.screenshot({ path: path.join(path.dirname(args.manifest), 'zhihu-published.png') });
        return;
      }
    }
    throw new Error('No draft matched full fidelity');
  } finally {
    log({ step: 'browser_left_open', url: page.url() });
  }
})().catch(e => { console.error('ERR', e && e.message); process.exit(1); });
