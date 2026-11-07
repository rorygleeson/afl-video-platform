/* Offline phone-page tests. No AWS, secrets or billable requests. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'index.html'), 'utf8');
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
new vm.Script(script, {filename: 'frontend/index.html'});

function setup() {
  let statusPolls = 0;
  const calls = [];
  const timers = new Map();
  let timerId = 0;
  const els = {};
  function el() {
    return {
      hidden: false, disabled: false, value: '', textContent: '', dataset: {},
      listeners: {},
      addEventListener(name, fn) { this.listeners[name] = fn; },
    };
  }
  for (const name of ['selfie', 'preview', 'generate', 'testApi', 'refresh',
                      'generateScene1', 'status', 'pollStatus', 'key', 'team', 'scenario']) {
    els[name] = el();
  }
  els.key.value = 'test-api-key';
  els.team.value = 'geelong';
  els.scenario.value = 'afl-grand-final-winning-kick';
  els.selfie.files = [{name: 'selfie.png', type: 'image/png', size: 1200}];
  els.results = el();
  els.results.children = [];
  els.results.replaceChildren = function() { this.children = []; };
  els.results.appendChild = function(child) { this.children.push(child); };
  els.results.querySelectorAll = function() { return this.children; };
  const doc = {
    hidden: false,
    listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    getElementById(name) { return els[name]; },
    createElement(tag) { assert.equal(tag, 'img'); return el(); },
  };
  const fetch = async (url, opts = {}) => {
    calls.push({url, opts});
    if (url.endsWith('/uploads')) return {
      ok: true, async json() { return {
        uploadKey: 'uploads/aaa.png',
        upload: {url: 'https://s3.example.com/upload', fields: {}},
      }; },
    };
    if (url === 'https://s3.example.com/upload') return {ok: true};
    if (url.endsWith('/jobs') && opts.method === 'POST') return {
      ok: true, async json() { return {job: {jobId: 'a'.repeat(32),
                                               status: 'AWAITING_IMAGE_PIPELINE'}}; },
    };
    if (url.endsWith('/generate')) return {
      ok: true, async json() { return {status: 'GENERATING_IMAGES'}; },
    };
    if (url.endsWith('/images')) return {
      ok: true, async json() { return {
        images: Array.from({length: Math.min(statusPolls, 3)},
          (_, i) => ({sceneId: 'scene' + (i + 1), url: 'https://replicate.example.com/img' + i})),
      }; },
    };
    if (/\/jobs\/a{32}$/.test(url)) {
      statusPolls++;
      const status = statusPolls >= 3 ? 'COMPLETED' : 'GENERATING_IMAGES';
      return {ok: true, async json() { return {
        job: {status, completedScenes: Math.min(statusPolls, 3), failedScenes: 0,
          scenes: {
            scene1: {status: 'COMPLETED'},
            scene2: {status: statusPolls >= 2 ? 'COMPLETED' : 'GENERATING'},
            scene3: {status: statusPolls >= 3 ? 'COMPLETED' : 'GENERATING'},
          }},
      }; }};
    }
    throw new Error('Unexpected URL ' + url);
  };
  const ctx = {
    document: doc,
    window: {AFL_CONFIG: {apiBaseUrl: 'https://api.example.com'}, confirm: () => true},
    fetch, Set, URL: {createObjectURL() { return 'blob:test'; }, revokeObjectURL() {}},
    FormData: class { append() {} },
    crypto: {randomUUID() { return 'test-idempotency-key'; }},
    setTimeout(fn, delay) {
      const id = ++timerId; timers.set(id, {fn, delay}); return id;
    },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(script, ctx);
  async function flush() {
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
  }
  async function nextTimer() {
    assert.equal(timers.size, 1, 'Exactly one status timer should be scheduled');
    const [id, scheduled] = timers.entries().next().value;
    timers.delete(id);
    scheduled.fn();
    await flush();
    return scheduled.delay;
  }
  return {els, doc, calls, timers, flush, nextTimer, getPolls: () => statusPolls};
}

test('auto polls every 5 seconds, adds each image once and stops when complete', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.generateScene1.hidden, false);
  await h.els.generateScene1.listeners.click();
  await h.flush();
  assert.equal(h.getPolls(), 1);
  assert.equal(h.els.results.children.length, 1);
  assert.equal(h.timers.size, 1);
  assert.equal(await h.nextTimer(), 5000);
  assert.equal(h.getPolls(), 2);
  assert.equal(h.els.results.children.length, 2);
  assert.equal(await h.nextTimer(), 5000);
  assert.equal(h.getPolls(), 3);
  assert.equal(h.els.results.children.length, 3);
  assert.equal(h.timers.size, 0, 'Completed job must stop polling');
  assert.equal(h.els.pollStatus.hidden, true);
  assert.equal(h.calls.filter(c => c.url.endsWith('/generate')).length, 1,
               'Polling cannot trigger new Replicate generation');
});

test('polling pauses while tab is hidden and resumes when visible', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush();
  assert.equal(h.getPolls(), 1);
  h.doc.hidden = true;
  h.doc.listeners.visibilitychange();
  assert.equal(h.timers.size, 0);
  assert.match(h.els.pollStatus.textContent, /paused/i);
  h.doc.hidden = false;
  h.doc.listeners.visibilitychange();
  await h.flush();
  assert.equal(h.getPolls(), 2);
  assert.equal(h.timers.size, 1);
});

test('changing team cancels pending polling and hides old paid job action', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush();
  assert.equal(h.timers.size, 1);
  h.els.team.listeners.change();
  assert.equal(h.timers.size, 0);
  assert.equal(h.els.generateScene1.hidden, true);
  assert.equal(h.els.refresh.hidden, true);
});
