// Re-shoot the README screenshots from a built showcase page.
//
//   halflife showcase --out docs/showcase.html --workers 4
//   npm install playwright        # once; or use a global install
//   node scripts/shoot_showcase.js docs/showcase.html docs/images
//
// Writes survival, trace, matrix and judge PNGs in light and dark variants.
// Set CHROMIUM_PATH to use a specific browser binary instead of Playwright's own.
const { chromium } = require('playwright');
const path = require('path');
if (process.argv.length < 4) {
  console.error('usage: node scripts/shoot_showcase.js <showcase.html> <out-dir>');
  process.exit(2);
}
const page_url = 'file://' + path.resolve(process.argv[2]);
const out = process.argv[3];
(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: { width: 1180, height: 900 }, deviceScaleFactor: 1.5, colorScheme: scheme });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    await page.goto(page_url, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    const sfx = scheme === 'dark' ? '-dark' : '';

    // 1. Header + survival curves, with the hover tooltip showing at cycle 20.
    const svg = await page.$('#surv-svg');
    const b = await svg.boundingBox();
    await page.mouse.move(b.x + (46 + 656 * 20 / 30) * b.width / 720, b.y + b.height * 0.5);
    const top = await page.$eval('header', e => e.getBoundingClientRect().top + scrollY);
    const bottom = await page.$eval('#curves', e => e.getBoundingClientRect().bottom + scrollY);
    await page.screenshot({ path: `${out}/survival${sfx}.png`, clip: { x: 0, y: top - 8, width: 1180, height: bottom - top + 24 }, fullPage: true });
    await page.mouse.move(0, 0);

    // 2. Trace at cycle 1: the note was just relabelled from untrusted to system.
    await page.$eval('#cycle', e => { e.value = 1; e.dispatchEvent(new Event('input', { bubbles: true })); });
    await (await page.$('#trace')).screenshot({ path: `${out}/trace${sfx}.png` });

    // 3. Matrix (default metric: attack half-life).
    await (await page.$('#matrix')).screenshot({ path: `${out}/matrix${sfx}.png` });

    // 4. Judge lab with a noisy judge (20% / 30%).
    await page.click('#ctl-fpr button:nth-child(3)');
    await page.click('#ctl-fnr button:nth-child(4)');
    await (await page.$('#judge')).screenshot({ path: `${out}/judge${sfx}.png` });

    if (errors.length) console.log(scheme, 'ERRORS', errors);
    await ctx.close();
  }
  await browser.close();
})();
