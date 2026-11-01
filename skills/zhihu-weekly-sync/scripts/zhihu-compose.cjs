// Compose the Zhihu article directly in the editor (DOCX import is blocked by the account's
// 直答 knowledge-base quota), verify text + image order, then optionally publish.
const path = require('node:path');
const fs = require('node:fs');
const { attach } = require('./browser-session.cjs');
const { imageFile, checkSubmission, claimSubmission, zhihuAccount } = require('./safety.cjs');

const args = { publish: false };
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a === '--publish') args.publish = true;
  else if (a.startsWith('--')) args[a.slice(2)] = process.argv[++i];
}
const manifest = JSON.parse(fs.readFileSync(args.manifest, 'utf8'));
const payloadDir = path.dirname(args.manifest);
const optDir = args.optimized || path.join(payloadDir, 'images-opt');
const receiptFile = path.join(payloadDir, 'zhihu-receipt.json');
checkSubmission(receiptFile);
const log = o => console.log(JSON.stringify({ t: new Date().toISOString(), ...(typeof o === 'string' ? { msg: o } : o) }));

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const marks = (node) => {
  let h = esc(node.text).replace(/\n/g, '<br>');
  for (const m of node.marks || []) {
    if (m === 'bold') h = `<strong>${h}</strong>`;
    if (m === 'italic') h = `<em>${h}</em>`;
    if (m === 'underline') h = `<u>${h}</u>`;
  }
  return h;
};

const blocks = manifest.blocks.slice();
// Drop the duplicated title block; the full title goes to the title field.
if (blocks.length && blocks[0].type !== 'image' && blocks[0].nodes.map(n => n.text).join('').trim() === manifest.source_title.trim()) blocks.shift();

const expectedText = blocks.filter(b => b.type !== 'image').map(b => b.nodes.map(n => n.text).join('')).join('').replace(/\s+/g, '');
const expectedImages = blocks.filter(b => b.type === 'image').length;
for (const b of blocks.filter(b => b.type === 'image')) imageFile(payloadDir, b.path);

const paste = (page, text, html) => page.evaluate(([t, h]) => {
  const el = document.querySelector('.public-DraftEditor-content[contenteditable="true"]');
  el.focus();
  const dt = new DataTransfer();
  dt.setData('text/html', h);
  dt.setData('text/plain', t);
  el.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
}, [text, html]);

const stats = page => page.evaluate(() => {
  const el = document.querySelector('.public-DraftEditor-content[contenteditable="true"]');
  const blocks = [...el.children[0].children];
  const imgs = [...el.querySelectorAll('img')].map(i => i.currentSrc || i.src);
  const text = blocks.filter(b => !b.querySelector('img'))
    .map(b => b.innerText.replace(/添加图片注释[^\n]*/g, '').trim())
    .join('\n').replace(/\s+/g, '');
  return { blockCount: blocks.length, images: imgs.length, imgs, text };
});

(async () => {
  const { ctx, reused, port } = await attach(process.env.ZHIHU_PROFILE, process.env.ZHIHU_DEBUG_PORT || 9444);
  log({ step: 'browser', reused, port });
  const page = await ctx.newPage();
  try {
    await page.goto('https://zhuanlan.zhihu.com/write', { waitUntil: 'domcontentloaded', timeout: 60000 });
    const editor = page.locator('.public-DraftEditor-content[contenteditable="true"]').first();
    await editor.waitFor({ timeout: 60000 });
    await page.waitForTimeout(2500);

    await zhihuAccount(page);
    const me = await page.evaluate(async () => {
      const r = await fetch('https://www.zhihu.com/api/v4/me', { credentials: 'include' });
      const j = await r.json();
      return { name: j.name, id: j.id, url_token: j.url_token };
    });
    log({ step: 'identity', me });


    // title
    const titleInput = page.locator('textarea[placeholder*="标题"]').first();
    await titleInput.click();
    await titleInput.fill(manifest.source_title);
    log({ step: 'title_set', title: manifest.source_title });

    await editor.click();
    await page.waitForTimeout(2500);

    const imgInput = page.locator('input[type=file][accept*="image"]').first();
    let imageIndex = 0;
    for (let i = 0; i < blocks.length; i++) {
      const b = blocks[i];
      if (b.type === 'image') {
        const optimized = path.join(optDir, path.basename(b.path).replace(/\.(png|jpg|jpeg)$/i, '.jpg'));
        const file = fs.existsSync(optimized) ? imageFile(optDir, path.basename(optimized)) : imageFile(payloadDir, b.path);
        let ok = false;
        for (let attempt = 1; attempt <= 1 && !ok; attempt++) {
          const before = await editor.locator('img').count();
          const alreadyThere = async () => {
            const imgs = await editor.locator('img').evaluateAll(els => els.map(e => e.currentSrc || e.src));
            return imgs.length > before && /\/v2-[0-9a-f]{32}/.test(imgs[imgs.length - 1]);
          };
          if (attempt > 1 && await alreadyThere()) { ok = true; break; }
          await page.locator('input[type=file][accept*="image"]').first().evaluate(el => { el.value = ''; }).catch(() => {});
          const target = attempt === 1 ? file : path.join(require('node:os').tmpdir(), `zhihu-retry-${imageIndex + 1}-${attempt}-${Date.now()}.jpg`);
          if (attempt > 1) fs.copyFileSync(file, target);
          await page.locator('input[type=file][accept*="image"]').first().setInputFiles(target);
          const deadline = Date.now() + 300000;
          while (Date.now() < deadline) {
            await page.waitForTimeout(1500);
            if (await alreadyThere()) { ok = true; break; }
          }
          if (!ok) {
            // Grace window: a slow upload may still land after the first timeout.
            const grace = Date.now() + 120000;
            while (Date.now() < grace) {
              await page.waitForTimeout(2000);
              if (await alreadyThere()) { ok = true; break; }
            }
          }
          if (!ok) log({ step: 'image_retry', index: imageIndex + 1, attempt });
        }
        imageIndex++;
        log({ step: 'image', index: imageIndex, total: expectedImages, ok });
        if (!ok) throw new Error(`Image ${imageIndex} did not finish uploading`);
        await page.waitForTimeout(600);
        continue;
      }
      const html = b.nodes.map(marks).join('') || '<br>';
      const plain = b.nodes.map(n => n.text).join('');
      await paste(page, plain, `<p>${html}</p>`);
      await page.waitForTimeout(500);
      if (b.type === 'heading') {
        const level = Math.min(3, Math.max(1, Number(b.level) || 1));
        await page.keyboard.press(`Meta+Alt+${level}`);
        await page.waitForTimeout(300);
      }
      await page.keyboard.press('Enter');
      await page.waitForTimeout(300);
    }

    const s = await stats(page);
    const zhimg = s.imgs.filter(u => /\/v2-[0-9a-f]{32}/.test(u)).length;
    log({ step: 'composed', images: s.images, zhimg, textChars: s.text.length, expectedImages, expectedChars: expectedText.length });
    await page.screenshot({ path: path.join(payloadDir, 'zhihu-composed.png') });

    // cover
    const coverInput = page.locator('.UploadPicture-input');
    if (await coverInput.count()) {
      await coverInput.setInputFiles(args.cover);
      await page.waitForTimeout(8000);
      log({ step: 'cover', uploaded: await page.locator('.UploadPicture img[src*="zhimg"]').count() > 0 });
    }

    const textOk = s.text === expectedText;
    log({ step: 'verify', textOk, imagesOk: s.images === expectedImages, zhimgOk: zhimg === expectedImages });
    const state = { manifest: args.manifest, title: manifest.source_title, images: s.images, zhimg, textChars: s.text.length, expectedImages, expectedChars: expectedText.length, textOk, url: page.url(), at: new Date().toISOString() };
    if (!args.publish) {
      fs.writeFileSync(receiptFile, JSON.stringify({ ...state, status: 'prepared' }, null, 2));
      log({ step: 'prepared' });
      return;
    }
    if (!textOk || s.images !== expectedImages || zhimg !== expectedImages) throw new Error('Fidelity gate failed; not publishing');
    await zhihuAccount(page);
    claimSubmission(receiptFile);
    fs.writeFileSync(receiptFile, JSON.stringify({ ...state, status: 'submitting' }, null, 2));
    const respPromise = page.waitForResponse(r => /\/api\/v4\/content\/publish/.test(r.url()) && r.request().method() === 'POST', { timeout: 120000 });
    await page.getByRole('button', { name: '发布', exact: true }).click();
    await page.waitForTimeout(3000);
    const dlg = page.locator('.Modal button:has-text("发布")');
    if (await dlg.count()) { await dlg.last().click().catch(() => {}); }
    const resp = await respPromise;
    const body = await resp.json().catch(() => ({}));
    const id = body && body.data && (body.data.id || body.data.article_id);
    log({ step: 'publish_response', status: resp.status(), id });
    await page.waitForTimeout(8000);
    log({ step: 'after_publish_url', url: page.url() });
    await page.screenshot({ path: path.join(payloadDir, 'zhihu-published.png') });
    fs.writeFileSync(receiptFile, JSON.stringify({ ...state, status: id ? 'submitted' : 'unknown', responseStatus: resp.status(), articleId: id, afterUrl: page.url() }, null, 2));
  } finally {
    // Chrome stays open; the publish/verify steps re-attach to the same window.
    log({ step: 'browser_left_open', url: page.url() });
  }
})().catch(e => { console.error('ERR', e && e.message); process.exit(1); });
