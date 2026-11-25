// Records docs/demo/index.html to a video, frame by frame.
//
//   node tools/record_demo.cjs [out.mp4] [--fps 30] [--size 1280x720] [--frames 0,5.5,12]
//
// Needs Node, Playwright with Chromium (`npm i -g playwright` and
// `npx playwright install chromium`), ffmpeg, and the network for three.js.
// The page is opened with ?record: it has no clock of its own, and each
// frame is drawn for an exact time, so the video is smooth however slow the
// machine (or its software WebGL) is. With --frames it only writes PNG
// stills at those seconds, next to the output, for a quick look.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : dflt; };
const fps = Number(opt('--fps', 30));
const [width, height] = opt('--size', '1280x720').split('x').map(Number);
const stills = opt('--frames', null);
const out = path.resolve(args[0] || 'walka-demo.mp4');
const page_url = 'file://' + path.resolve(__dirname, '..', 'docs', 'demo', 'index.html') + '?record';

(async () => {
  const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  page.on('pageerror', e => console.error('page error:', e.message));
  page.on('console', m => { if (m.type() === 'error') console.error('console:', m.text()); });
  await page.goto(page_url);
  await page.waitForFunction(() => window.__demoReady === true, null, { timeout: 120000 });
  await page.evaluate(() => document.fonts.ready);
  const duration = await page.evaluate(() => window.__demo.duration);

  if (stills) {
    for (const t of stills.split(',').map(Number)) {
      await page.evaluate(tt => window.__demo.render(tt), t);
      const file = out.replace(/\.[^.]+$/, '') + `-${String(t).replace('.', '_')}.png`;
      await page.screenshot({ path: file });
      console.log('wrote', file);
    }
    await browser.close();
    return;
  }

  const ffmpeg = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out], { stdio: ['pipe', 'inherit', 'inherit'] });
  const frames = Math.round(duration * fps);
  const started = Date.now();
  for (let i = 0; i < frames; i++) {
    await page.evaluate(tt => window.__demo.render(tt), i / fps);
    const jpg = await page.screenshot({ type: 'jpeg', quality: 95 });
    if (!ffmpeg.stdin.write(jpg)) await new Promise(r => ffmpeg.stdin.once('drain', r));
    if (i % fps === 0) process.stdout.write(`\r${(i / fps).toFixed(0)}s / ${duration}s  (${((Date.now() - started) / 1000).toFixed(0)}s elapsed)`);
  }
  ffmpeg.stdin.end();
  await new Promise((res, rej) => ffmpeg.on('close', c => (c ? rej(new Error('ffmpeg exited ' + c)) : res())));
  await browser.close();
  console.log(`\nwrote ${out} (${frames} frames, ${fs.statSync(out).size >> 10} KB)`);
})().catch(e => { console.error(e); process.exit(1); });
