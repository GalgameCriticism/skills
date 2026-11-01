// One-shot Weibo headline-article pipeline: launch the profile's Chrome once, compose, verify, publish.
// Everything runs in a single browser session, so the window is opened once per task and never restarted.
const path = require('node:path');
const fs = require('node:fs');
const { attach } = require('./browser-session.cjs');
const { imageFile, checkSubmission, claimSubmission, weiboAccount } = require('./safety.cjs');

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
const sleep = ms => new Promise(r => setTimeout(r, ms));

// The draft id in `#/history/<draftId>` is NOT the article id. The only reliable way to the
// public URL is the account timeline: the newest "发布了头条文章" card carries page_info.page_url.
const WEIBO_UID = process.env.WEIBO_UID || '7615845670';
async function findArticleUrl() {
  for (let i = 0; i < 10; i++) {
    try {
      const r = await fetch(`https://m.weibo.cn/api/container/getIndex?type=uid&value=${WEIBO_UID}&containerid=107603${WEIBO_UID}`, {
        headers: { 'user-agent': 'Mozilla/5.0', referer: `https://m.weibo.cn/u/${WEIBO_UID}` },
      });
      const j = await r.json();
      for (const card of (j && j.data && j.data.cards) || []) {
        const url = card && card.mblog && card.mblog.page_info && card.mblog.page_info.page_url;
        if (url && /ttarticle/.test(url)) return url;
      }
    } catch { /* timeline API is flaky right after publishing; retry */ }
    await sleep(15000);
  }
  return null;
}

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

const stats = page => page.evaluate(() => {
  const root = document.querySelector('.ProseMirror');
  if (!root) return null;
  const figures = [...root.querySelectorAll('figure')];
  const srcs = figures.map(f => { const i = f.querySelector('img'); return i ? (i.currentSrc || i.src) : ''; });
  return {
    figures: figures.length,
    srcs,
    hosted: srcs.filter(u => /^https?:\/\//.test(u) && !/tc2\.service/.test(u)).length,
    text: root.innerText.replace(/WBIMGPH_\d+/g, '').replace(/\s+/g, ''),
    placeholders: (root.innerText.match(/WBIMGPH_\d+/g) || []).length,
    title: (document.querySelector('textarea[placeholder*="请输入标题"]') || {}).value || null,
  };
});

// Real mouse click on the placeholder text: ProseMirror only honours its own selection,
// so the token must be selected with real input before the synthetic image paste.
const tokenRect = (page, n) => page.evaluate(idx => {
  const root = document.querySelector('.ProseMirror');
  const token = `WBIMGPH_${idx}`;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node = null;
  while ((node = walker.nextNode())) if (node.nodeValue.includes(token)) break;
  if (!node) return null;
  node.parentElement.scrollIntoView({ block: 'center' });
  const start = node.nodeValue.indexOf(token);
  const r = document.createRange();
  r.setStart(node, start);
  r.setEnd(node, start + 1);
  const b = r.getBoundingClientRect();
  return { x: Math.round(b.x + 2), y: Math.round(b.y + b.height / 2), len: token.length };
}, n);

(async () => {
  const { ctx, port, reused } = await attach(process.env.WEIBO_PROFILE, process.env.CHROME_DEBUG_PORT || 9333);
  log({ step: 'browser', reused, port });
  const page = await ctx.newPage();

  await page.goto('https://card.weibo.com/article/v3/editor', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(7000);
  const writeBtn = page.getByText('写文章', { exact: true }).first();
  if (await writeBtn.count()) { await writeBtn.click().catch(() => {}); await sleep(5000); }

  await weiboAccount(page);

  const editor = page.locator('.ProseMirror').first();
  await editor.waitFor({ timeout: 30000 });
  await editor.click();
  await sleep(800);

  const titleInput = page.locator('textarea[placeholder*="请输入标题"]').first();
  await titleInput.click();
  await titleInput.fill(title);
  log({ step: 'title_set', title, len: title.length });

  if (summary) {
    const summaryBox = page.locator('textarea[placeholder*="导语"]').first();
    if (await summaryBox.count()) {
      await summaryBox.click();
      await page.keyboard.type(summary);
      log({ step: 'summary_set', len: summary.length });
    }
  }

  await editor.click();
  await page.evaluate(h => {
    const el = document.querySelector('.ProseMirror');
    el.focus();
    const dt = new DataTransfer();
    dt.setData('text/html', h);
    dt.setData('text/plain', h.replace(/<[^>]+>/g, ''));
    el.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
  }, html);
  await sleep(3000);
  let s = await stats(page);
  log({ step: 'body_pasted', placeholders: s.placeholders, chars: s.text.length, expectedChars: expectedText.length });
  if (s.placeholders !== expectedImages) throw new Error(`Placeholder count ${s.placeholders} != ${expectedImages}`);

  for (let n = 1; n <= expectedImages; n++) {
    const file = imageFiles[n - 1];
    // Scrolling changes the layout, so locate the token twice and only trust the post-scroll rect.
    let rect = await tokenRect(page, n);
    if (!rect) throw new Error(`Placeholder ${n} not found`);
    await sleep(700);
    rect = await tokenRect(page, n);
    if (!rect) throw new Error(`Placeholder ${n} lost after scroll`);

    let selected = '';
    for (let attempt = 1; attempt <= 3; attempt++) {
      await page.mouse.click(rect.x, rect.y);
      await sleep(300);
      for (let k = 0; k < rect.len; k++) await page.keyboard.press('Shift+ArrowRight');
      await sleep(200);
      selected = await page.evaluate(() => window.getSelection().toString());
      if (selected === `WBIMGPH_${n}`) break;
      log({ step: 'reselect', index: n, attempt, selected });
      rect = await tokenRect(page, n);
      if (!rect) throw new Error(`Placeholder ${n} vanished while reselecting`);
    }
    if (selected !== `WBIMGPH_${n}`) throw new Error(`Could not select placeholder ${n} (got "${selected}")`);
    await page.keyboard.press('Backspace');
    await sleep(300);

    const b64 = fs.readFileSync(file).toString('base64');
    await page.evaluate(async ([data, name]) => {
      const root = document.querySelector('.ProseMirror');
      const bin = atob(data);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      const dt = new DataTransfer();
      dt.items.add(new File([arr], name, { type: 'image/jpeg' }));
      root.focus();
      root.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
    }, [b64, path.basename(file)]);

    let uploaded = false;
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      await sleep(2000);
      const st = await stats(page);
      const src = st.srcs[n - 1] || '';
      if (st.figures >= n && /^https?:\/\//.test(src) && !/tc2\.service/.test(src)) { uploaded = true; break; }
    }
    log({ step: 'image', index: n, total: expectedImages, uploaded });
    if (!uploaded) throw new Error(`Image ${n} did not finish uploading`);
  }

  s = await stats(page);
  const textOk = s.text === expectedText;
  log({ step: 'verify', figures: s.figures, hosted: s.hosted, placeholders: s.placeholders, chars: s.text.length, expectedChars: expectedText.length, textOk });
  await page.screenshot({ path: path.join(payloadDir, 'weibo-composed.png') });

  const state = { title, summary, images: s.figures, hosted: s.hosted, textChars: s.text.length, expectedImages, expectedChars: expectedText.length, textOk, url: page.url(), at: new Date().toISOString() };
  if (!args.publish) {
    fs.writeFileSync(receiptFile, JSON.stringify({ ...state, status: 'prepared' }, null, 2));
    log({ step: 'prepared' });
    log({ step: 'browser_left_open', url: page.url() });
    return;
  }
  if (!textOk || s.hosted !== expectedImages || s.placeholders !== 0) throw new Error('Fidelity gate failed; not publishing');

  await weiboAccount(page);
  claimSubmission(receiptFile);
  fs.writeFileSync(receiptFile, JSON.stringify({ ...state, status: 'submitting' }, null, 2));

  // 1. 下一步 must be a real locator click. A DOM click on the footer control logs ok:true and does nothing.
  await page.locator('button:has-text("下一步")').first().click({ timeout: 20000 });
  await sleep(5000);

  // 2. The publish settings are NOT inline in v5 — they open as a modal (`31 | 公开 | 发布`).
  const modal = page.locator('.n-modal, [role="dialog"]').filter({ hasText: '发布' }).first();
  log({ step: 'settings_modal', found: await modal.count(), url: page.url() });
  await page.screenshot({ path: path.join(payloadDir, 'weibo-settings.png') });

  if (args.cover) {
    const coverEntry = page.getByText('设置封面', { exact: false }).first();
    if (await coverEntry.count()) {
      await coverEntry.click().catch(() => {});
      await sleep(2000);
      const input = page.locator('input[type=file]').first();
      if (await input.count()) {
        await input.setInputFiles(args.cover);
        await sleep(8000);
        log({ step: 'cover_uploaded' });
      }
    }
  }

  // 3. `.n-modal-mask` has aria-hidden and swallows pointer events, so a Playwright click retries forever.
  //    A DOM click on the modal's own 发布 button bypasses hit-testing and is the verified path.
  const clicked = await page.evaluate(() => {
    const btn = [...document.querySelectorAll('button')].filter(b => b.textContent.trim() === '发布').pop();
    if (!btn) return false;
    btn.click();
    return true;
  });
  log({ step: 'publish_clicked', ok: clicked });
  if (!clicked) throw new Error('publish button missing after 下一步');

  // 4. Success is the SPA landing on #/history/<draftId> — it never redirects to a public article URL.
  let draftId = null;
  for (let i = 0; i < 45; i++) {
    await sleep(2000);
    const m = page.url().match(/#\/history\/(\d+)/);
    if (m) { draftId = m[1]; break; }
  }
  log({ step: 'after_publish', url: page.url(), draftId });
  await page.screenshot({ path: path.join(payloadDir, 'weibo-published.png') });

  // 5. Resolve the real article URL from the timeline.
  const publicUrl = draftId ? await findArticleUrl() : null;
  log({ step: 'public_url', url: publicUrl });

  fs.writeFileSync(receiptFile, JSON.stringify({
    ...state,
    status: draftId ? (publicUrl ? 'published' : 'published_url_unknown') : 'unknown',
    draftId,
    afterUrl: page.url(),
    publicUrl,
    at: new Date().toISOString(),
  }, null, 2));

  // 6. Leave the window open — closing it is what forced a relaunch on every later step.
  log({ step: 'browser_left_open', url: page.url() });
})().catch(e => { console.error('ERR', e && e.message); process.exit(1); });
