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
                      'generateScene1', 'status', 'pollStatus', 'key', 'team', 'scenario',
                      'testGoogle', 'googleStatus', 'firstName', 'lastName',
                      'videoPromptSection', 'videoPromptStatus', 'videoPromptText',
                      'videoScene1', 'videoScene2', 'videoScene3',
                      'videoTestSection','videoTestMessage','assembleFinal',
                      'generateVideo1','generateVideo2','generateVideo3',
                      'videoStatus1','videoStatus2','videoStatus3',
                      'videoPlayer1','videoPlayer2','videoPlayer3']) {
    els[name] = el();
    els[name].replaceChildren = function() {};
  }
  els.key.value = 'test-api-key';
  els.team.value = 'geelong';
  els.firstName.value = 'Alex';
  els.lastName.value = 'Tonelli';
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
    if (url.includes('/video/scene') && url.endsWith('/preview')) return {
      ok: true, async json() { return {
        sceneId: url.match(/scene[123]/)[0], playerName: 'Alex Tonelli',
        teamName: 'Geelong Cats', durationSeconds: 8,
        prompt: 'Alex Tonelli plays for Geelong Cats; 3D video prompt',
        videoGenerationEnabled: false,
      }; },
    };
    if (url.endsWith('/video/auth-check')) return {
      ok: true, async json() { return {
        status: 'AUTHENTICATED', videoGenerationEnabled: false,
      }; },
    };
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
    if (url.endsWith('/videos')) return {
      ok: true, async json() { return {videoGenerationMode: 'MOCK_ONLY', videos: {
        scene1: {status: 'NOT_REQUESTED'}, scene2: {status: 'NOT_REQUESTED'},
        scene3: {status: 'NOT_REQUESTED'},
      }}; },
    };
    if (/\/videos\/scene[123]$/.test(url) && opts.method === 'POST') return {
      ok: true, async json() { return {status: 'MOCK_REQUESTED'}; },
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

test('Google Cloud auth-check button never invokes paid generation', async () => {
  const h = setup();
  await h.els.testGoogle.listeners.click();
  assert.match(h.els.googleStatus.textContent, /connected successfully/i);
  assert.equal(h.els.testGoogle.disabled, false);
  assert.equal(h.calls.filter(c => c.url.endsWith('/video/auth-check')).length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/generate')).length, 0);
});

test('separate free video preview buttons substitute name/team without paid API calls', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.videoPromptSection.hidden, false);
  for (const n of [1,2,3]) {
    await h.els['videoScene' + n].listeners.click();
    assert.match(h.els.videoPromptText.textContent, /Alex Tonelli/);
    assert.match(h.els.videoPromptText.textContent, /Geelong Cats/);
  }
  assert.equal(h.calls.filter(c => c.url.includes('/video/scene') && c.url.endsWith('/preview')).length, 3);
  assert.equal(h.calls.filter(c => c.url.endsWith('/generate')).length, 0);
  const request = h.calls.find(c => c.url.endsWith('/jobs') && c.opts.method === 'POST');
  assert.equal(JSON.parse(request.opts.body).firstName, 'Alex');
  assert.equal(JSON.parse(request.opts.body).lastName, 'Tonelli');
});

test('first and last names are mandatory before S3 upload', async () => {
  const h = setup();
  h.els.lastName.value = '';
  await h.els.generate.listeners.click();
  assert.match(h.els.status.textContent, /both first name and surname/);
  assert.equal(h.calls.length, 0);
});

test('editing names invalidates existing job and previews', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.els.videoScene1.listeners.click();
  h.els.firstName.listeners.input();
  assert.equal(h.els.videoPromptSection.hidden, true);
  assert.equal(h.els.refresh.hidden, true);
});


test('three mock scene controls remain separate and never request paid video', async () => {
  const h=setup();
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush();
  assert.equal(h.els.videoTestSection.hidden,false);
  assert.equal(h.els.generateVideo1.disabled,false);
  assert.equal(h.els.generateVideo2.disabled,true);
  await h.els.generateVideo1.listeners.click();
  await h.flush();
  const submitted=h.calls.filter(c=>/\/videos\/scene[123]$/.test(c.url)&&c.opts.method==='POST');
  assert.equal(submitted.length,1);
  assert.match(submitted[0].url,/videos\/scene1$/);
  assert.equal(h.els.assembleFinal.disabled,true);
});
