// Compose a Weibo headline article (头条文章) directly in the v5 editor with Playwright:
// paste body HTML, replace image placeholders with real pasted images, then optionally publish.
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { attach } = require('./browser-session.cjs');
const { imageFile, checkSubmission, weiboAccount } = require('./safety.cjs');

const args = { publish: false };
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a === '--publish') args.publish = true;
  else if (a.startsWith('--')) args[a.slice(2)] = process.argv[++i];
}
if (!args.manifest) throw new Error('--manifest <manifest.json> is required');

const manifest = JSON.parse(fs.readFileSync(args.manifest, 'utf8'));
const payloadDir = path.dirname(args.manifest);
const optDir = args.optimized;
const receiptFile = path.join(payloadDir, 'weibo-receipt.json');
checkSubmission(receiptFile);
const account = process.env.WEIBO_ACCOUNT || 'Galgame批评';
const log = o => console.log(JSON.stringify({ t: new Date().toISOString(), ...(typeof o === 'string' ? { msg: o } : o) }));

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const marks = node => {
  let h = esc(node.text);
  for (const m of node.marks || []) {
    if (m === 'bold') h = `<strong>${h}</strong>`;
    if (m === 'italic') h = `<em>${h}</em>`;
    if (m === 'underline') h = `<u>${h}</u>`;
  }
  return h;
};

const blocks = manifest.blocks.slice();
if (blocks.length && blocks[0].type !== 'image' && blocks[0].nodes.map(n => n.text).join('').trim() === manifest.source_title.trim()) blocks.shift();

const title = (args.title || manifest.source_title).slice(0, 32);
const summary = (args.summary || '').slice(0, 44);

// Build HTML with image placeholders so each image can be pasted at its exact position.
let imgIndex = 0;
const imageFiles = [];
const htmlParts = [];
for (const b of blocks) {
  if (b.type === 'image') {
    imgIndex++;
    const optimized = optDir ? path.join(optDir, path.basename(b.path).replace(/\.(png|jpg|jpeg)$/i, '.jpg')) : null;
    const file = optimized && fs.existsSync(optimized) ? imageFile(optDir, path.basename(optimized)) : imageFile(payloadDir, b.path);
    imageFiles.push(file);
    htmlParts.push(`<p>WBIMGPH_${imgIndex}</p>`);
    continue;
  }
  const inner = b.nodes.map(marks).join('') || '<br>';
  const tag = b.type === 'heading' ? 'h2' : 'p';
  htmlParts.push(`<${tag}>${inner}</${tag}>`);
}
const html = htmlParts.join('');
const expectedImages = imageFiles.length;
const expectedText = blocks.filter(b => b.type !== 'image').map(b => b.nodes.map(n => n.text).join('')).join('').replace(/\s+/g, '');

const editorStats = page => page.evaluate(() => {
  const root = document.querySelector('.ProseMirror');
  const figures = [...root.querySelectorAll('figure')];
  const srcs = figures.map(f => {
    const img = f.querySelector('img');
    return img ? (img.currentSrc || img.src) : '';
  });
  const text = root.innerText.replace(/WBIMGPH_\d+/g, '').replace(/\s+/g, '');
  const placeholders = (root.innerText.match(/WBIMGPH_\d+/g) || []).length;
  return { figures: figures.length, srcs, text, placeholders };
});

(async () => {
  const { ctx, reused, port } = await attach(process.env.WEIBO_PROFILE);
  log({ step: 'browser', reused, port });
  const page = await ctx.newPage();
  try {
    if (!/card\.weibo\.com/.test(page.url())) {
      await page.goto('https://card.weibo.com/article/v3/editor', { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(7000);
    } else {
      await page.waitForTimeout(3000);
    }
    const writeBtn = page.getByText('写文章', { exact: true }).first();
    if (await writeBtn.count()) { await writeBtn.click().catch(() => {}); await page.waitForTimeout(5000); }

    await weiboAccount(page);

    const editor = page.locator('.ProseMirror').first();
    await editor.waitFor({ timeout: 30000 });
    await editor.click();
    await page.waitForTimeout(1000);

    // title
    const titleInput = page.locator('input[placeholder*="请输入标题"]').first();
    await titleInput.click();
    await titleInput.fill(title);
    log({ step: 'title_set', title, len: title.length });

    // summary (导语)
    if (summary) {
      const summaryBox = page.locator('[placeholder*="导语"]').first();
      if (await summaryBox.count()) {
        await summaryBox.click();
        await page.keyboard.type(summary);
        log({ step: 'summary_set', len: summary.length });
      }
    }

    // body HTML
    await editor.click();
    await page.keyboard.press('Control+End').catch(() => {});
    await page.evaluate(h => {
      const el = document.querySelector('.ProseMirror');
      el.focus();
      const dt = new DataTransfer();
      dt.setData('text/html', h);
      dt.setData('text/plain', h.replace(/<[^>]+>/g, ''));
      el.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
    }, html);
    await page.waitForTimeout(3000);
    let s = await editorStats(page);
    log({ step: 'body_pasted', placeholders: s.placeholders, textChars: s.text.length, expectedChars: expectedText.length });
    if (s.placeholders !== expectedImages) throw new Error(`Placeholder count ${s.placeholders} != expected ${expectedImages}`);

    // images one by one
    for (let n = 1; n <= expectedImages; n++) {
      const file = imageFiles[n - 1];
      const b64 = fs.readFileSync(file).toString('base64');
      const filename = path.basename(file);
      const ok = await page.evaluate(async ([data, name, idx]) => {
        const token = `WBIMGPH_${idx}`;
        const root = document.querySelector('.ProseMirror');
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node = null;
        while ((node = walker.nextNode())) {
          if (node.nodeValue.includes(token)) break;
        }
        if (!node) return false;
        const start = node.nodeValue.indexOf(token);
        const range = document.createRange();
        range.setStart(node, start);
        range.setEnd(node, start + token.length);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        const bin = atob(data);
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        const f = new File([arr], name, { type: 'image/jpeg' });
        const dt = new DataTransfer();
        dt.items.add(f);
        root.focus();
        root.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
        return true;
      }, [b64, filename, n]);
      if (!ok) throw new Error(`Placeholder ${n} not found in editor`);

      let uploaded = false;
      const deadline = Date.now() + 180000;
      while (Date.now() < deadline) {
        await page.waitForTimeout(2500);
        const st = await editorStats(page);
        const src = st.srcs[n - 1] || '';
        if (st.figures >= n && /^https?:\/\//.test(src) && !/tc2\.service/.test(src)) { uploaded = true; break; }
      }
      log({ step: 'image', index: n, total: expectedImages, uploaded });
      if (!uploaded) throw new Error(`Image ${n} did not finish uploading`);
    }

    s = await editorStats(page);
    const hosted = s.srcs.filter(u => /^https?:\/\//.test(u) && !/tc2\.service/.test(u)).length;
    const textOk = s.text === expectedText;
    log({ step: 'verify', figures: s.figures, hosted, placeholders: s.placeholders, textChars: s.text.length, expectedChars: expectedText.length, textOk });
    await page.screenshot({ path: path.join(payloadDir, 'weibo-composed.png'), fullPage: false });

    const state = { title, summary, images: s.figures, hosted, textChars: s.text.length, expectedImages, expectedChars: expectedText.length, textOk, url: page.url(), at: new Date().toISOString() };
    if (!args.publish) {
      fs.writeFileSync(receiptFile, JSON.stringify({ ...state, status: 'prepared' }, null, 2));
      log({ step: 'prepared' });
      return;
    }
    if (!textOk || hosted !== expectedImages || s.placeholders !== 0) throw new Error('Fidelity gate failed; not publishing');

    fs.writeFileSync(receiptFile, JSON.stringify({ ...state, status: 'prepared' }, null, 2));
    await page.getByRole('button', { name: '下一步', exact: true }).first().click();
    await page.waitForTimeout(4000);
    log({ step: 'next_step', url: page.url() });
    await page.screenshot({ path: path.join(payloadDir, 'weibo-settings.png') });
  } finally {
    // The Chrome window stays open on purpose: the next step (publish) re-attaches to it.
    log({ step: 'browser_left_open', url: page.url() });
  }
})().catch(e => { console.error('ERR', e && e.message); process.exit(1); });
