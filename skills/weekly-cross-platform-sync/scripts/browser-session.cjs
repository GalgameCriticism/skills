// Each skill includes this module so it remains independently installable.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { createRequire } = require('node:module');
const sleep = ms => new Promise(r => setTimeout(r, ms));

function privateProfile(directory) {
  if (!directory || !path.isAbsolute(directory)) throw new Error('An absolute dedicated Chrome profile is required');
  const resolved = fs.realpathSync(directory);
  const ordinary = [
    path.join(os.homedir(), 'Library/Application Support/Google/Chrome'),
    path.join(os.homedir(), '.config/google-chrome'),
    path.join(os.homedir(), 'Library/Application Support/Chromium'),
    path.join(os.homedir(), 'Library/Application Support/Microsoft Edge'),
    path.join(os.homedir(), '.config/chromium'),
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Google/Chrome/User Data'),
  ].filter(Boolean);
  if (!fs.statSync(resolved).isDirectory()) throw new Error('Chrome profile must be a directory');
  if (resolved === os.homedir() || /^(Default|Profile \d+|User Data|Chrome)$/i.test(path.basename(resolved)) ||
      ordinary.some(root => resolved.toLowerCase() === root.toLowerCase() || resolved.toLowerCase().startsWith(root.toLowerCase() + path.sep))) {
    throw new Error('Refusing an everyday Chrome profile; use a separate automation directory');
  }
  return resolved;
}

function playwright() {
  const modulePath = process.env.PLAYWRIGHT_MODULE_PATH;
  if (modulePath) return createRequire(path.join(modulePath, 'package.json'))('./index.js');
  try { return require('playwright'); } catch { return require('playwright-core'); }
}

async function connect(port) {
  try { return await playwright().chromium.connectOverCDP(`http://127.0.0.1:${port}`, { timeout: 5000 }); }
  catch { return null; }
}

async function verifyProfile(browser, profile) {
  const session = await browser.newBrowserCDPSession();
  try {
    // Requires an automation-enabled dedicated Chrome. Never infer identity from a port alone.
    const { arguments: args } = await session.send('Browser.getBrowserCommandLine');
    const profiles = args.filter(a => a.startsWith('--user-data-dir='));
    if (profiles.length !== 1 || fs.realpathSync(profiles[0].slice('--user-data-dir='.length)) !== profile ||
        args.includes('--no-sandbox')) throw new Error('Connected Chrome profile or sandbox does not match');
  } finally { await session.detach(); }
  const ctx = browser.contexts()[0];
  if (!ctx) throw new Error('Chrome has no usable context');
  return ctx;
}

async function attach(profileDir, port) {
  const profile = privateProfile(profileDir);
  const debugPort = Number(port || process.env.CHROME_DEBUG_PORT || 9333);
  if (!Number.isInteger(debugPort) || debugPort < 1024 || debugPort > 65535) throw new Error('Invalid debug port');
  let browser = await connect(debugPort);
  if (browser) return { browser, ctx: await verifyProfile(browser, profile), reused: true, port: debugPort };
  if (process.env.CHROME_ALLOW_LAUNCH !== '1') throw new Error('No matching Chrome; launch requires CHROME_ALLOW_LAUNCH=1 and task authorization');
  for (const name of ['SingletonLock', 'SingletonCookie', 'SingletonSocket', 'lockfile']) {
    try { fs.lstatSync(path.join(profile, name)); }
    catch (error) { if (error.code === 'ENOENT') continue; throw error; }
    throw new Error('Chrome profile is occupied; do not delete its locks');
  }
  const chrome = process.env.CHROME_PATH;
  if (!chrome || !path.isAbsolute(chrome) || !fs.statSync(chrome).isFile()) throw new Error('Set CHROME_PATH to the Chrome executable');
  const child = spawn(chrome, [
    `--user-data-dir=${profile}`, `--remote-debugging-port=${debugPort}`,
    '--remote-debugging-address=127.0.0.1', '--enable-automation',
    '--no-first-run', '--no-default-browser-check', 'about:blank',
  ], { detached: true, stdio: 'ignore' });
  let launchError;
  child.on('error', error => { launchError = error; });
  child.unref();
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    if (launchError) throw new Error('Chrome could not start');
    browser = await connect(debugPort);
    if (browser) return { browser, ctx: await verifyProfile(browser, profile), reused: false, port: debugPort };
    await sleep(600);
  }
  throw new Error('Chrome debug port not ready; inspect the dedicated session before retrying');
}

async function getPage(ctx, url) {
  const page = await ctx.newPage();
  if (url) await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  return page;
}
module.exports = { attach, connect, getPage, sleep, privateProfile, verifyProfile };
