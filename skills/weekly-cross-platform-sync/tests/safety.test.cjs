const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

for (const skill of ['weekly-cross-platform-sync', 'weibo-weekly-sync', 'zhihu-weekly-sync']) {
  const session = require(path.join(root, skill, 'scripts/browser-session.cjs'));
  test(`${skill}: refuse missing, everyday and aliased profiles`, () => {
    assert.throws(() => session.privateProfile());
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'profile-test-'));
    try {
      const daily = path.join(temp, 'Default'); fs.mkdirSync(daily);
      const alias = path.join(temp, 'alias'); fs.symlinkSync(daily, alias);
      assert.throws(() => session.privateProfile(daily));
      assert.throws(() => session.privateProfile(alias));
      assert.equal(session.privateProfile(temp), fs.realpathSync(temp));
    } finally { fs.rmSync(temp, { recursive: true }); }
  });
  test(`${skill}: refuse wrong CDP profile and disabled sandbox`, async () => {
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-test-'));
    try {
      for (const args of [[], ['--user-data-dir=' + temp, '--no-sandbox']]) {
        let detached = false;
        const browser = { newBrowserCDPSession: async () => ({ send: async () => ({ arguments: args }), detach: async () => { detached = true; } }) };
        await assert.rejects(session.verifyProfile(browser, temp));
        assert.equal(detached, true);
      }
    } finally { fs.rmSync(temp, { recursive: true }); }
  });
}
for (const skill of ['weibo-weekly-sync', 'zhihu-weekly-sync']) {
  const safety = require(path.join(root, skill, 'scripts/safety.cjs'));
  test(`${skill}: reject traversal, symlinks and disguised files`, () => {
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'images-test-'));
    const bundle = path.join(temp, 'bundle'); fs.mkdirSync(bundle);
    try {
      fs.writeFileSync(path.join(temp, 'outside.png'), Buffer.from('89504e470d0a1a0a00000000', 'hex'));
      fs.symlinkSync(path.join(temp, 'outside.png'), path.join(bundle, 'alias.png'));
      fs.writeFileSync(path.join(bundle, 'fake.png'), 'private data');
      for (const name of ['../outside.png', 'alias.png', 'fake.png']) assert.throws(() => safety.imageFile(bundle, name));
      const receipt = path.join(bundle, 'receipt.json');
      safety.claimSubmission(receipt);
      assert.throws(() => safety.claimSubmission(receipt));
      assert.throws(() => safety.checkSubmission(receipt));
    } finally { fs.rmSync(temp, { recursive: true }); }
  });
  test(`${skill}: wrong account stops publication`, async () => {
    const saved = process.env.ZHIHU_ACCOUNT_ID;
    process.env.ZHIHU_ACCOUNT_ID = 'expected';
    try { await assert.rejects(safety.zhihuAccount({ evaluate: async () => ({ id: 'other' }) })); }
    finally { if (saved === undefined) delete process.env.ZHIHU_ACCOUNT_ID; else process.env.ZHIHU_ACCOUNT_ID = saved; }
  });
}
