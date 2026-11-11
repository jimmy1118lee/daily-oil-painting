/* 本地截图辅助：node src/dop/prototypes/capture.cjs。无外部依赖，仅使用独立无界面 Chrome。 */
const fs = require('fs');
const path = require('path');
const {spawn} = require('child_process');
const {pathToFileURL} = require('url');

const chrome = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless', '--disable-gpu', '--no-first-run', '--disable-extensions',
  '--disable-background-networking', '--disable-component-update',
  '--remote-debugging-pipe', '--hide-scrollbars',
  `--user-data-dir=${path.join(__dirname, '_chrome-profile')}`,
], {stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'], windowsHide: true});
let sequence = 0;
let buffer = '';
const waiting = new Map();
chrome.stdio[4].on('data', chunk => {
  buffer += chunk.toString();
  let end;
  while ((end = buffer.indexOf('\0')) !== -1) {
    const message = JSON.parse(buffer.slice(0, end));
    buffer = buffer.slice(end + 1);
    const request = waiting.get(message.id);
    if (!request) continue;
    clearTimeout(request.timer);
    waiting.delete(message.id);
    if (message.error) request.reject(new Error(JSON.stringify(message.error)));
    else request.resolve(message.result);
  }
});
function command(method, params = {}, sessionId) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Chrome timeout: ${method}`)), 12000);
    waiting.set(id, {resolve, reject, timer});
    chrome.stdio[3].write(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}) + '\0');
  });
}
(async () => {
  try {
    const {targetId} = await command('Target.createTarget', {url: 'about:blank'});
    const {sessionId} = await command('Target.attachToTarget', {targetId, flatten: true});
    const run = (method, params = {}) => command(method, params, sessionId);
    const evaluate = async expression => {
      const result = await run('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    await run('Page.enable');
    await run('Emulation.setDeviceMetricsOverride', {width: 1536, height: 1030, deviceScaleFactor: 1, mobile: false});
    const shots = path.join(__dirname, 'screenshots');
    fs.mkdirSync(shots, {recursive: true});
    for (const variant of ['A', 'B', 'C']) {
      await run('Page.navigate', {url: pathToFileURL(path.join(__dirname, 'gui-prototype.html')).href + `?variant=${variant}&capture=1`});
      let ready = false;
      for (let attempt = 0; attempt < 30; attempt++) {
        ready = await evaluate(`document.readyState==='complete' && document.body.dataset.variant==='${variant}'`);
        if (ready) break;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      if (!ready) throw new Error(`Variant ${variant} did not load`);
      await evaluate('document.fonts.ready.then(()=>true)');
      const screenshot = await run('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
      fs.writeFileSync(path.join(shots, `prototype-${variant}.png`), Buffer.from(screenshot.data, 'base64'));
      const geometry = await evaluate(`({variant:document.body.dataset.variant,viewport:innerWidth,content:document.documentElement.scrollWidth,windowHeight:Math.round(document.querySelector('.window').getBoundingClientRect().height)})`);
      console.log(JSON.stringify(geometry));
    }
    await evaluate(`setVariant('A');document.querySelector('[data-action="change"]').click();document.querySelector('[data-action="favorite"]').click();document.querySelector('[data-action="settings"]').click();document.querySelector('.dialog [data-toggle="auto"]').click()`);
    console.log('Demo interaction state:', JSON.stringify(await evaluate('window.prototypeState')));
    await run('DOM.enable');
    await run('CSS.enable');
    const {root} = await run('DOM.getDocument');
    const {nodeId} = await run('DOM.querySelector', {nodeId: root.nodeId, selector: '.gallery-top h1'});
    const {fonts} = await run('CSS.getPlatformFontsForNode', {nodeId});
    console.log('CJK title font:', fonts.map(font => font.familyName).join(', '));
    await command('Browser.close');
  } catch (error) {
    console.error(error.message);
    chrome.kill();
    process.exitCode = 1;
  }
})();
