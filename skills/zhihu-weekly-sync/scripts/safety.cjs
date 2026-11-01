// Created by Miyin
const fs = require('node:fs');
const path = require('node:path');

function imageFile(root, value) {
  if (typeof value !== 'string' || path.isAbsolute(value) || value.split(/[\\/]/).includes('..')) throw new Error('Image must stay inside its source bundle');
  const directory = fs.realpathSync(root);
  const file = fs.realpathSync(path.resolve(directory, value));
  if (!file.startsWith(directory + path.sep) || !fs.statSync(file).isFile() || !/\.(png|jpe?g|gif|webp)$/i.test(file)) throw new Error('Invalid image path');
  const fd = fs.openSync(file, 'r');
  const header = Buffer.alloc(12);
  try { fs.readSync(fd, header, 0, 12, 0); } finally { fs.closeSync(fd); }
  if (!(header.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex')) ||
        header.subarray(0, 3).equals(Buffer.from('ffd8ff', 'hex')) ||
        /^GIF8[79]a/.test(header.toString('ascii')) ||
        (header.subarray(0, 4).toString() === 'RIFF' && header.subarray(8, 12).toString() === 'WEBP'))) throw new Error('File is not a supported image');
  return file;
}

function checkSubmission(receiptFile) {
  try { if (fs.lstatSync(receiptFile).isSymbolicLink()) throw new Error('Receipt cannot be a symlink'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (fs.existsSync(receiptFile + '.attempt.json')) throw new Error('Earlier submission exists; verify its outcome before retrying');
  if (fs.existsSync(receiptFile)) {
    const receipt = JSON.parse(fs.readFileSync(receiptFile, 'utf8'));
    if (receipt.status !== 'prepared') throw new Error('Existing receipt requires verification; not reposting');
  }
}
function claimSubmission(receiptFile) {
  checkSubmission(receiptFile);
  fs.writeFileSync(receiptFile + '.attempt.json', JSON.stringify({ status: 'submitting', at: new Date().toISOString() }), { flag: 'wx', mode: 0o600 });
}
async function zhihuAccount(page) {
  const expected = process.env.ZHIHU_ACCOUNT_ID;
  if (!expected) throw new Error('Set ZHIHU_ACCOUNT_ID to the target account ID');
  const me = await page.evaluate(async () => {
    const r = await fetch('https://www.zhihu.com/api/v4/me', { credentials: 'include' });
    if (!r.ok) throw new Error('Login check failed');
    return r.json();
  });
  if (String(me.id) !== expected) throw new Error('Unexpected Zhihu account');
}
async function weiboAccount(page) {
  const expected = process.env.WEIBO_ACCOUNT;
  if (!expected) throw new Error('Set WEIBO_ACCOUNT to the target nickname');
  // Read the editor account control, never arbitrary body text.
  const names = await page.locator('.woo-avatar-name, .gn_name, .user-name, .user_name, .nickname').allTextContents();
  if (!names.some(name => name.trim() === expected)) throw new Error('Cannot establish the target Weibo account; stop before writing');
}
module.exports = { imageFile, checkSubmission, claimSubmission, zhihuAccount, weiboAccount };
