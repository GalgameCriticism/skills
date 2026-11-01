const path = require('node:path');
const { attach } = require('./browser-session.cjs');
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const { ctx } = await attach(process.env.WEIBO_PROFILE, process.env.CHROME_DEBUG_PORT || 9333);
  const page = ctx.pages()[0] || await ctx.newPage();
  await page.goto(process.env.URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(6000);
  const st = await page.evaluate(() => ({
    url: location.href,
    title: document.title,
    h1: (document.querySelector('h1') || {}).innerText || null,
    author: (document.querySelector('.W_autocut, .art_author, [class*="author"]') || {}).innerText || null,
    imgs: document.querySelectorAll('.WB_editor_iframe_new img, .art_content img, article img').length,
    textLen: ((document.querySelector('.art_content, article, .WB_editor_iframe_new') || {}).innerText || '').length,
  }));
  console.log(JSON.stringify(st));
  await page.screenshot({ path: path.join(__dirname, '..', 'gal265', 'weibo-public.png'), fullPage: false });
  // Leave the dedicated browser running for follow-up verification.
})().catch(e => { console.error('ERR', e && e.message); process.exit(1); });
