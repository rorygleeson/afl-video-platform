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
  const mockStatuses = {scene1: 'NOT_REQUESTED', scene2: 'NOT_REQUESTED', scene3: 'NOT_REQUESTED'};
  const calls = [];
  const timers = new Map();
  let timerId = 0;
  const els = {};
  function el() {
    return {
      hidden: false, disabled: false, value: '', textContent: '', dataset: {},
      listeners: {},
      children: [],
      appendChild(child) { this.children.push(child); },
      querySelector(tag) { return this.children.find(c => c.tagName === tag) || null; },
      querySelectorAll(tag) { return this.children.filter(c => c.tagName === tag); },
      replaceChildren(...children) { this.children = [...children]; },
      addEventListener(name, fn) { this.listeners[name] = fn; },
    };
  }
  for (const name of ['selfie', 'preview', 'generate', 'testApi', 'refresh',
                      'generateScene1', 'resumeLast', 'status', 'pollStatus', 'key', 'team', 'scenario',
                      'testGoogle', 'googleStatus', 'firstName', 'lastName',
                      'videoPromptSection', 'videoPromptStatus', 'videoPromptText',
                      'videoPromptStatus1','videoPromptStatus2','videoPromptStatus3',
                      'videoPromptText1','videoPromptText2','videoPromptText3',
                      'videoPromptDetails1','videoPromptDetails2','videoPromptDetails3',
                      'videoScene1', 'videoScene2', 'videoScene3',
                      'videoTestSection','videoTestMessage','assembleFinal',
                      'generateVideo1','generateVideo2','generateVideo3',
                      'videoStatus1','videoStatus2','videoStatus3',
                      'videoPlayer1','videoPlayer2','videoPlayer3',
                      'videoPanel1','videoPanel2','videoPanel3']) {
    els[name] = el();
    // Keep the basic DOM simulation intact for the new reference image cards.
  }
  els.assembleFinal.disabled = true;
  els.assembleFinal.hidden = true; // Matches the HTML hidden attribute.
  els.key.value = 'test-api-key';
  els.team.value = 'geelong';
  els.firstName.value = 'Alex';
  els.lastName.value = 'Tonelli';
  els.scenario.value = 'afl-grand-final-winning-kick';
  els.selfie.files = [{name: 'selfie.png', type: 'image/png', size: 1200}];
  els.results = el();
  els.results.children = [];
  function imageCount() { return els.results.children.flatMap(c => c.children || []).filter(c => c.tagName === 'img').length; }
  const doc = {
    hidden: false,
    listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    getElementById(name) {
      if (els[name]) return els[name];
      for (const card of els.results.children) {
        if (card.id === name) return card;
        const child = (card.children || []).find(c => c.id === name);
        if (child) return child;
      }
      return null;
    },
    createElement(tag) { const node = el(); node.tagName = tag; return node; },
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
        scene1: {status: mockStatuses.scene1}, scene2: {status: mockStatuses.scene2},
        scene3: {status: mockStatuses.scene3},
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
  const storage = new Map();
  const sessionStorage = {
    getItem(k) { return storage.has(k) ? storage.get(k) : null; },
    setItem(k, v) { storage.set(k, String(v)); },
    removeItem(k) { storage.delete(k); },
  };
  const ctx = {
    document: doc,
    window: {AFL_CONFIG: {apiBaseUrl: 'https://api.example.com'}, confirm: () => true, sessionStorage},
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
  return {els, doc, calls, timers, flush, nextTimer, sessionStorage, getPolls: () => statusPolls, imageCount, mockStatuses};
}

test('auto polls every 5 seconds, adds each image once and stops when complete', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.generateScene1.hidden, false);
  assert.equal(h.els.generate.hidden, true, 'upload button hides once the job is created');
  assert.equal(h.els.refresh.hidden, true, 'manual refresh remains hidden; polling is automatic');
  await h.els.generateScene1.listeners.click();
  await h.flush();
  assert.equal(h.getPolls(), 1);
  assert.equal(h.imageCount(), 1);
  assert.equal(h.timers.size, 1);
  assert.equal(await h.nextTimer(), 5000);
  assert.equal(h.getPolls(), 2);
  assert.equal(h.imageCount(), 2);
  assert.equal(await h.nextTimer(), 5000);
  assert.equal(h.getPolls(), 3);
  assert.equal(h.imageCount(), 3);
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
  assert.equal(h.els.generate.hidden, false, 'changing details reveals new job upload action');
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
  assert.equal(h.els.videoPromptSection.hidden, true);
  await h.els.generateScene1.listeners.click();
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  assert.equal(h.els.videoPromptSection.hidden, false);
  await h.els.videoScene1.listeners.click();
  assert.match(h.els.videoPromptText.textContent, /Alex Tonelli/);
  assert.match(h.els.videoPromptText.textContent, /Geelong Cats/);
  assert.match(h.els.videoPromptText.textContent, /SCENE 1/);
  assert.match(h.els.videoPromptText1.textContent,/Alex Tonelli/);
  assert.equal(h.els.videoPromptDetails1.hidden,false,'prompt is available after preview');
  assert.equal(h.els.videoPromptDetails1.open,true,'first preview expands prompt');
  assert.equal(h.els.videoPromptText2.textContent,'', 'scene 2 prompt must not be displayed yet');
  assert.equal(h.els.videoScene2.hidden, true, 'scene 2 is locked before mock 1 completes');
  h.els.videoPromptDetails1.open = false; // User collapses long prompt.
  assert.equal(h.els.videoPromptDetails1.open,false);
  assert.equal(h.els.generateVideo1.hidden,false,'mock action remains visible when prompt is collapsed');
  assert.equal(h.calls.filter(c => c.url.includes('/video/scene') && c.url.endsWith('/preview')).length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/generate')).length, 1,
    'Only the separately confirmed reference-image generation request may have occurred');
  assert.equal(h.calls.filter(c => /\/videos\/scene[123]$/.test(c.url) && c.opts.method === 'POST').length, 0,
    'Previewing prompts must not start mock or paid video');
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
  await h.els.generateScene1.listeners.click();
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  await h.els.videoScene1.listeners.click();
  h.els.firstName.listeners.input();
  assert.equal(h.els.videoPromptSection.hidden, true);
  assert.equal(h.els.refresh.hidden, true);
});


test('three mock scene controls remain separate and never request paid video', async () => {
  const h=setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.videoTestSection.hidden,true, 'mock video section hidden until all reference images complete');
  assert.equal(h.els.videoPromptSection.hidden,true, 'free video previews also hidden during image generation');
  await h.els.generateScene1.listeners.click();
  await h.flush();
  assert.equal(h.els.videoTestSection.hidden,true, 'one completed image must not reveal video controls');
  await h.nextTimer();
  assert.equal(h.els.videoTestSection.hidden,true, 'two completed images must not reveal video controls');
  await h.nextTimer();
  assert.equal(h.els.videoTestSection.hidden,false);
  assert.equal(h.els.videoPromptSection.hidden,false);
  assert.equal(h.els.videoPanel1.hidden,false);
  assert.equal(h.els.videoPanel2.hidden,true);
  assert.equal(h.els.videoPanel3.hidden,true);
  assert.equal(h.els.generateVideo1.hidden,true, 'mock button hidden until prompt preview');
  await h.els.videoScene1.listeners.click();
  assert.equal(h.els.generateVideo1.hidden,false);
  await h.els.generateVideo1.listeners.click();
  await h.flush();
  const submitted=h.calls.filter(c=>/\/videos\/scene[123]$/.test(c.url)&&c.opts.method==='POST');
  assert.equal(submitted.length,1);
  assert.match(submitted[0].url,/videos\/scene1$/);
  assert.equal(h.els.assembleFinal.disabled,true);
  assert.equal(h.els.assembleFinal.hidden,true);
  h.mockStatuses.scene1 = 'MOCK_COMPLETED';
  // Poll the status endpoint and unlock scene 2 only after scene 1 mock completes.
  h.doc.listeners.visibilitychange();
  await h.flush();
  assert.equal(h.els.videoPanel2.hidden,false);
  assert.equal(h.els.videoPanel3.hidden,true);
  assert.match(h.els.videoStatus1.textContent,/No MP4 was generated/);
});

test('reference image progress stays above scene video controls', () => {
  const order = ['id="resumeLast"', 'id="jobProgressSection"', 'id="status"',
                 'id="results"', 'id="videoPromptSection"', 'id="videoTestSection"'];
  let previous = -1;
  for (const item of order) {
    const position = html.indexOf(item);
    assert.ok(position > previous, item + ' must appear after the preceding item');
    previous = position;
  }
});

test('video controls stay hidden if any reference scene is still generating', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.videoTestSection.hidden,true);
  await h.els.generateScene1.listeners.click();
  await h.flush();
  assert.equal(h.getPolls(),1);
  assert.equal(h.els.videoTestSection.hidden,true);
  assert.equal(h.els.videoPromptSection.hidden,true);
  await h.nextTimer();
  assert.equal(h.getPolls(),2);
  assert.equal(h.els.videoTestSection.hidden,true);
  assert.equal(h.els.videoPromptSection.hidden,true);
});
