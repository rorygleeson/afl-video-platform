/* Offline phone-page tests. No AWS, secrets or billable requests. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'index.html'), 'utf8');
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
new vm.Script(script, {filename: 'frontend/index.html'});

function setup(options = {}) {
  let statusPolls = 0;
  const mockStatuses = {scene1: 'NOT_REQUESTED', scene2: 'NOT_REQUESTED', scene3: 'NOT_REQUESTED'};
  const calls = [];
  const downloads = [];
  const pendingVideoPosts = new Map();
  const timers = new Map();
  let timerId = 0;
  const els = {};
  function el() {
    return {
      hidden: false, disabled: false, value: '', textContent: '', dataset: {},
      listeners: {},
      children: [],
      appendChild(child) { this.children.push(child); },
      setAttribute(name, value) { (this.attributes ||= {})[name] = value; },
      getAttribute(name) { return this.attributes?.[name] ?? null; },
      querySelector(tag) { return this.children.find(c => c.tagName === tag) || null; },
      querySelectorAll(tag) { return this.children.filter(c => c.tagName === tag); },
      replaceChildren(...children) { this.children = [...children]; },
      addEventListener(name, fn) { this.listeners[name] = fn; },
    };
  }
  for (const name of ['selfie', 'preview', 'generate', 'testApi', 'refresh',
                      'generateScene1', 'status', 'pollStatus', 'key', 'apiConnectionStatus', 'team', 'scenario',
                      'jobIdPanel', 'currentJobIdText', 'copyJobId', 'copyJobStatus',
                      'testGoogle', 'googleStatus', 'apiTestResult', 'firstName', 'lastName',
                      'videoPromptSection', 'videoPromptStatus', 'videoPromptText',
                      'videoPromptStatus1','videoPromptStatus2','videoPromptStatus3',
                      'videoPromptText1','videoPromptText2','videoPromptText3',
                      'videoPromptDetails1','videoPromptDetails2','videoPromptDetails3',
                      'videoScene1', 'videoScene2', 'videoScene3',
                      'scene1Preflight', 'scene1PreflightStatus', 'scene1PaidStatus', 'generateAllVideos',
                      'scene2PaidStatus', 'scene3PaidStatus',
                      'videoTestSection','videoTestMessage','assembleFinal','assemblyPanel','assemblyStatus','assemblyPlayer','downloadFinal','nextStageMessage',
                      'generateVideo1','generateVideo2','generateVideo3',
                      'videoStatus1','videoStatus2','videoStatus3',
                      'videoPlayer1','videoPlayer2','videoPlayer3',
                      'videoPanel1','videoPanel2','videoPanel3',
                      'videoDebugDetails1','videoDebugDetails2','videoDebugDetails3',
                      'videoDebugText1','videoDebugText2','videoDebugText3',
                       'diagnoseScene1','diagnoseScene2','diagnoseScene3',
                       'diagnoseResult1','diagnoseResult2','diagnoseResult3']) {
    els[name] = el();
    // Keep the basic DOM simulation intact for the new reference image cards.
  }
  els.assembleFinal.disabled = true;
  els.assembleFinal.hidden = true; // Matches the HTML hidden attribute.
  els.generateAllVideos.hidden = true;
  for (const n of [1, 2, 3]) els['diagnoseScene' + n].hidden = true;
  for (const n of [1, 2, 3]) els['diagnoseScene' + n].hidden = true;
  for (const n of [1, 2, 3]) els['diagnoseScene' + n].hidden = true;
  els.jobIdPanel.hidden = true;
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
    createElement(tag) {
      const node = el(); node.tagName = tag;
      if (tag === 'a') {
        node.click = () => downloads.push({name: node.download, url: node.href});
        node.remove = () => {};
      }
      return node;
    },
    body: {appendChild() {}},
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
    if (url.endsWith('/video/scene1/preflight')) return {
      ok: true, async json() { return {
        status: 'READY', imageBytes: 18000, durationSeconds: 8,
        googleSubmissionMade: false, paidGenerationEnabled: false
      }; },
    };
    if (url.endsWith('/video/paid-google-auth-check')) return {
      ok: true, async json() { return {
        status: 'AUTHENTICATED', videoGenerationEnabled: false,
      }; },
    };
    if (url.endsWith('/scenarios')) {
      if (options.forbidGateway) return {
        ok:false, status:403, async json(){return {message:'Forbidden'};},
      };
      return {ok:true, status:200, async json(){return {scenarios:['afl-grand-final-winning-kick']};}};
    }
    if (url.endsWith('/uploads')) return {
      ok: true, async json() { return {
        uploadKey: 'uploads/aaa.png',
        upload: {url: 'https://s3.example.com/upload', fields: {}},
      }; },
    };
    if (url === 'https://s3.example.com/upload')
      return options.failSelfieUpload ? {ok:false, status:500} : {ok:true};
    if (url.endsWith('/jobs') && opts.method === 'POST') return {
      ok: !options.failJobCreation, status: options.failJobCreation ? 503 : 200,
      async json() { return options.failJobCreation ? {error:'Job creation unavailable'} : {job: {jobId: 'a'.repeat(32),
                                               status: 'AWAITING_IMAGE_PIPELINE'}}; },
    };
    if (url.endsWith('/generate')) return {
      ok: true, async json() { return {status: 'GENERATING_IMAGES'}; },
    };
    if (url.endsWith('/assembly')) return {
      ok: true, async json() { return {assembly: options.assembly || {}}; },
    };
    if (url === 'https://s3.example.com/final.mp4') return {
      ok: true, async blob() { return new Blob(['MP4 fixture'], {type: 'video/mp4'}); },
    };
    if (url.endsWith('/videos')) return {
      ok: true, async json() { return {
        videoGenerationMode: 'MOCK_ONLY',
        paidScenes: options.paidScenes || {},
        assembly: options.assembly || {},
        videos: {
          scene1: {status: mockStatuses.scene1},
          scene2: {status: mockStatuses.scene2},
          scene3: {status: mockStatuses.scene3},
          ...(options.realVideos || {}),
        },
      }; },
    };
    if (/\/real-videos\/scene[123]$/.test(url) && opts.method === 'POST') {
      const scene = url.match(/scene[123]$/)[0];
      if (options.deferPaidVideoResponses) {
        return new Promise(resolve => pendingVideoPosts.set(scene, resolve));
      }
      // Mimic DynamoDB reservation becoming visible in subsequent GET status.
      options.paidScenes = {...(options.paidScenes || {}), [scene]: false};
      options.realVideos = {...(options.realVideos || {}),
        [scene]: {mode: 'REAL', status: 'REQUESTED'}};
      return {ok: true, async json() { return {status: 'REAL_VIDEO_REQUESTED'}; }};
    }
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
    fetch, Set, Blob, URL: {createObjectURL() { return 'blob:test'; }, revokeObjectURL() {}},
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
  return {els, doc, calls, downloads, timers, pendingVideoPosts, flush, nextTimer, sessionStorage, getPolls: () => statusPolls, imageCount, mockStatuses};
}

test('auto polls every 5 seconds, adds each image once and stops when complete', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.generate.hidden, true, 'upload button hides once the job is created');
  assert.equal(h.els.refresh.hidden, true, 'manual refresh remains hidden; polling is automatic');
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
  assert.equal(h.timers.size, 1, 'Reference image polling ends; video status continues polling automatically');
  assert.equal(h.els.pollStatus.hidden, true);
  assert.equal(h.calls.filter(c => c.url.endsWith('/generate')).length, 1,
               'Polling cannot trigger new Replicate generation');
});

test('polling pauses while tab is hidden and resumes when visible', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
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
  await h.flush();
  assert.equal(h.timers.size, 1);
  h.els.team.listeners.change();
  assert.equal(h.els.generate.hidden, false, 'changing details reveals new job upload action');
  assert.equal(h.timers.size, 0);
  assert.equal(h.els.refresh.hidden, true);
});

test('Google Cloud auth-check button never invokes paid generation', async () => {
  const h = setup();
  await h.els.testGoogle.listeners.click();
  assert.equal(h.els.googleStatus.textContent, 'Working!');
  assert.match(h.els.googleStatus.className, /success/);
  assert.equal(h.els.testGoogle.disabled, false);
  assert.equal(h.calls.filter(c => c.url.endsWith('/video/paid-google-auth-check')).length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/generate')).length, 0);
});

test('connection diagnostics show local green success and red failure only', async () => {
  const h = setup();
  const priorStatus = h.els.status.textContent;
  await h.els.testApi.listeners.click();
  assert.equal(h.els.apiTestResult.textContent, 'Working!');
  assert.match(h.els.apiTestResult.className, /success/);
  assert.equal(h.els.status.textContent, priorStatus,
    'Diagnostic button must never overwrite selfie/upload status');
  assert.equal(h.els.googleStatus.textContent, '');
  await h.els.testGoogle.listeners.click();
  assert.equal(h.els.googleStatus.textContent, 'Working!');
  assert.match(h.els.googleStatus.className, /success/);

  const denied = setup({forbidGateway:true});
  const original = denied.els.status.textContent;
  await denied.els.testApi.listeners.click();
  assert.equal(denied.els.apiTestResult.textContent, 'Failed');
  assert.match(denied.els.apiTestResult.className, /failure/);
  assert.equal(denied.els.status.textContent, original);
  assert.equal(denied.els.googleStatus.textContent, '');
});

test('video controls appear after three reference images without prompt toggles', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.videoTestSection.hidden, true);
  await h.flush(); await h.nextTimer(); await h.nextTimer();
  assert.equal(h.els.videoTestSection.hidden, false);
  for (const n of [1, 2, 3]) {
    assert.equal(h.els['videoPanel'+n].hidden, false);
    assert.doesNotMatch(html, new RegExp('id="videoScene' + n + '"'));
    assert.doesNotMatch(html, new RegExp('id="videoPromptDetails' + n + '"'));
    assert.match(h.els['videoStatus'+n].textContent, /Real video not generated/);
  }
  assert.equal(h.calls.filter(c => c.url.includes('/video/scene') && c.url.endsWith('/preview')).length, 0);
  assert.doesNotMatch(html, /id="generateVideo[123]"/);
  assert.equal(h.els.assembleFinal.hidden, true);
  const request = h.calls.find(c => c.url.endsWith('/jobs') && c.opts.method === 'POST');
  assert.equal(JSON.parse(request.opts.body).firstName, 'Alex');
  assert.equal(JSON.parse(request.opts.body).lastName, 'Tonelli');
});

test('one click automatically submits exactly one image generation and never shows a second button', async () => {
  const h = setup();
  assert.doesNotMatch(html, /id="generateScene1"|Generate 3 reference images with Replicate/);
  await h.els.generate.listeners.click();
  assert.equal(h.calls.filter(c => c.url === 'https://s3.example.com/upload').length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/jobs') && c.opts.method === 'POST').length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/generate')).length, 1);
  assert.equal(h.els.generate.hidden, true);
  assert.equal(h.els.generate.disabled, true);
  assert.equal(h.getPolls(), 1);
});

test('rapid double click does not create another upload, job, or paid image request', async () => {
  const h = setup();
  const first = h.els.generate.listeners.click();
  const second = h.els.generate.listeners.click();
  await Promise.all([first, second]);
  await h.flush();
  assert.equal(h.calls.filter(c => c.url === 'https://s3.example.com/upload').length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/jobs') && c.opts.method === 'POST').length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/generate')).length, 1);
});

test('failed selfie upload stops before job creation or Replicate generation', async () => {
  const h = setup({failSelfieUpload:true});
  await h.els.generate.listeners.click();
  assert.equal(h.calls.filter(c => c.url === 'https://s3.example.com/upload').length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/jobs') && c.opts.method === 'POST').length, 0);
  assert.equal(h.calls.filter(c => c.url.endsWith('/generate')).length, 0);
  assert.match(h.els.status.textContent, /upload failed/i);
  assert.equal(h.els.generate.disabled, false);
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
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  h.els.firstName.listeners.input();
  assert.equal(h.els.refresh.hidden, true);
});


test('reference images gate video controls, existing mock records do not unlock paid controls', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.videoTestSection.hidden, true);
  await h.flush();
  assert.equal(h.els.videoTestSection.hidden, true);
  await h.nextTimer();
  assert.equal(h.els.videoTestSection.hidden, true);
  await h.nextTimer();
  assert.equal(h.els.videoTestSection.hidden, false);
  h.mockStatuses.scene1 = 'MOCK_COMPLETED';
  h.mockStatuses.scene2 = 'MOCK_COMPLETED';
  h.mockStatuses.scene3 = 'MOCK_COMPLETED';
  h.doc.listeners.visibilitychange();
  await h.flush();
  assert.equal(h.els.videoPanel2.hidden, false, 'Prompt for Scene 2 remains available');
  assert.equal(h.els.videoPanel3.hidden, false, 'Prompt for Scene 3 remains available');
  assert.equal(h.els.generateAllVideos.hidden, true, 'Mock result cannot authorize paid scene');
  assert.match(h.els.videoStatus1.textContent, /Real video not generated/);
  assert.equal(h.els.assembleFinal.hidden, true);
  assert.equal(h.calls.filter(c => /\/videos\/scene[123]$/.test(c.url) && c.opts.method === 'POST').length, 0);
});

test('reference image progress stays above scene prompt controls', () => {
  const order = ['id="generate"', 'id="jobProgressSection"', 'id="status"',
                 'id="results"', 'id="videoTestSection"'];
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
  await h.flush();
  assert.equal(h.getPolls(),1);
  assert.equal(h.els.videoTestSection.hidden,true);
  await h.nextTimer();
  assert.equal(h.getPolls(),2);
  assert.equal(h.els.videoTestSection.hidden,true);
});

test('Scene 1 preflight is read-only and makes no video POST', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  assert.equal(h.els.videoTestSection.hidden, false);
  await h.flush();
  assert.match(h.els.scene1PreflightStatus.textContent, /READY.*No charge/);
  assert.equal(h.calls.filter(c => c.url.endsWith('/video/scene1/preflight')).length, 1,
               'Automatic free preflight runs after all reference images complete');
  assert.equal(h.calls.filter(c => /\/videos\/scene1$/.test(c.url) && c.opts.method === 'POST').length, 0);
});

test('real Scene 1 paid button remains hidden and never starts without approval', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  await h.els.scene1Preflight.listeners.click();
  assert.equal(h.els.generateAllVideos.hidden, true);
  assert.equal(h.calls.filter(c => c.url.endsWith('/real-videos/scene1')).length, 0);
});

test('resume last job control is absent and a new job still starts normally', async () => {
  const h = setup();
  assert.doesNotMatch(html, /id="resumeLast"|Resume your last job/);
  assert.doesNotMatch(html, /id="jobIdPanel"|id="copyJobId"|id="currentJobIdText"/);
  await h.els.generate.listeners.click();
  assert.doesNotMatch(h.els.status.textContent, /Job:\\s*[0-9a-f]{32}/);
  await h.flush();
  assert.equal(h.calls.filter(c => c.url.endsWith('/jobs') && c.opts.method === 'POST').length, 1);
});

test('Scene 1 free readiness automatically runs once for a finished job', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.flush();
  assert.equal(h.calls.filter(c => c.url.endsWith('/video/scene1/preflight')).length, 0);
  await h.nextTimer();
  assert.equal(h.calls.filter(c => c.url.endsWith('/video/scene1/preflight')).length, 0);
  await h.nextTimer();
  assert.equal(h.els.videoTestSection.hidden, false);
  assert.equal(h.calls.filter(c => c.url.endsWith('/video/scene1/preflight')).length, 1);
  assert.match(h.els.scene1PreflightStatus.textContent, /READY/);
  await h.els.refresh.listeners.click();
  await h.flush();
  assert.equal(h.calls.filter(c => c.url.endsWith('/video/scene1/preflight')).length, 1,
               'Status polling and refresh never duplicate automatic checks');
  await h.els.scene1Preflight.listeners.click();
  assert.equal(h.calls.filter(c => c.url.endsWith('/video/scene1/preflight')).length, 2,
               'Optional manual recheck is explicitly initiated');
  assert.equal(h.calls.filter(c => c.url.endsWith('/real-videos/scene1')).length, 0,
               'Readiness never submits a paid MP4');
});

test('one batch button stays hidden until real videos are approved', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer();
  assert.equal(h.els.generateAllVideos.hidden, true);
  assert.ok(h.els.generateAllVideos.listeners.click);
  assert.doesNotMatch(html, /id="generateRealScene[123]"/);
  assert.equal(h.calls.filter(c => c.url.includes('/real-videos/scene')).length, 0);
});

test('one confirmed click submits three Google scenes concurrently, once each', async () => {
  const h = setup({paidScenes: {scene1:true,scene2:true,scene3:true}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.generateAllVideos.hidden, false);
  assert.match(h.els.generateAllVideos.textContent, /All 3 Videos/);
  const first = h.els.generateAllVideos.listeners.click();
  const second = h.els.generateAllVideos.listeners.click();
  await Promise.all([first, second]);
  await h.flush();
  const submitted = h.calls.filter(c => c.url.includes('/real-videos/scene'));
  assert.equal(submitted.length, 3);
  assert.deepEqual(submitted.map(c=>c.url.match(/scene[123]$/)[0]).sort(),
    ['scene1','scene2','scene3']);
  for (const c of submitted) {
    const scene = c.url.match(/scene[123]$/)[0];
    assert.equal(c.opts.method, 'POST');
    assert.deepEqual(JSON.parse(c.opts.body), {confirmPaidScene: scene});
    assert.ok(c.opts.headers['Idempotency-Key']);
  }
  for (const n of [1,2,3])
    assert.match(h.els['videoDebugText'+n].textContent,/API response: REAL_VIDEO_REQUESTED/);
  assert.equal(h.els.generateAllVideos.hidden,true);
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.calls.filter(c=>c.url.includes('/real-videos/scene')).length,3);
});

test('all three paid requests are simultaneously in flight; failure does not cancel or retry siblings', async () => {
  const h = setup({paidScenes:{scene1:true,scene2:true,scene3:true}, deferPaidVideoResponses:true});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  const batch = h.els.generateAllVideos.listeners.click();
  assert.equal(h.pendingVideoPosts.size, 3, 'three independent POSTs must begin before any response');
  assert.equal(h.els.generateAllVideos.hidden,true);
  await h.els.generateAllVideos.listeners.click();
  assert.equal(h.calls.filter(c=>c.url.includes('/real-videos/scene')).length,3,
    'double click cannot add paid requests while requests are in flight');
  h.pendingVideoPosts.get('scene2')({ok:false,status:503, async json(){return {error:'Provider unavailable'};}});
  h.pendingVideoPosts.get('scene3')({ok:true,status:202,async json(){return {status:'REAL_VIDEO_REQUESTED'};}});
  h.pendingVideoPosts.get('scene1')({ok:true,status:202,async json(){return {status:'REAL_VIDEO_REQUESTED'};}});
  await batch;
  await h.flush();
  assert.match(h.els.scene2PaidStatus.textContent,/Submission needs review/);
  assert.match(h.els.videoDebugText2.textContent,/will NOT be retried/);
  assert.match(h.els.videoDebugText1.textContent,/REAL_VIDEO_REQUESTED/);
  assert.match(h.els.videoDebugText3.textContent,/REAL_VIDEO_REQUESTED/);
  assert.equal(h.calls.filter(c=>c.url.includes('/real-videos/scene')).length,3);
});

test('paid batch outcome survives stale read-only status polling', async () => {
  const h = setup({paidScenes:{scene1:true,scene2:true,scene3:true}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  await h.els.generateAllVideos.listeners.click();
  await h.flush();
  await h.els.refresh.listeners.click(); await h.flush();
  assert.match(h.els.videoDebugText1.textContent,/API response: REAL_VIDEO_REQUESTED/);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/real-videos/scene1')).length,1);
});

test('read-only workflow review does not unlock another paid attempt', async () => {
  const h = setup({paidScenes:{scene1:false,scene2:false,scene3:false},
    realVideos:{scene1:{mode:'REAL',status:'REVIEW_REQUIRED',errorCode:'WORKFLOW_FAILED'}}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.match(h.els.scene1PaidStatus.textContent,/Generation needs review/);
  assert.equal(h.els.generateAllVideos.hidden,true);
  assert.equal(h.calls.filter(c=>c.url.includes('/real-videos/scene')).length,0);
});

test('batch only submits remaining eligible scenes, not completed MP4', async () => {
  const h = setup({paidScenes:{scene1:false,scene2:true,scene3:true},
    realVideos:{scene1:{mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene1.mp4'}}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.generateAllVideos.hidden,false);
  assert.match(h.els.generateAllVideos.textContent,/2 Remaining Videos/);
  await h.els.generateAllVideos.listeners.click();
  const submitted=h.calls.filter(c=>c.url.includes('/real-videos/scene'));
  assert.deepEqual(submitted.map(c=>c.url.match(/scene[123]$/)[0]).sort(),['scene2','scene3']);
});

test('uncertain scene stays read-only and is never automatically resubmitted', async () => {
  const h = setup({paidScenes:{scene1:false,scene2:false,scene3:false},
    realVideos:{scene1:{mode:'REAL',status:'SUBMISSION_UNKNOWN'}}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.generateAllVideos.hidden,true);
  assert.match(h.els.scene1PaidStatus.textContent,/do not retry/i);
  assert.match(h.els.videoDebugText1.textContent,/SUBMISSION_UNKNOWN/);
});

test('upload checks API access automatically and preserves any existing job on 403', async () => {
  const h = setup({forbidGateway:true});
  await h.els.generate.listeners.click();
  assert.match(h.els.status.textContent, /API Gateway refused access \(403\)/);
  assert.match(h.els.apiConnectionStatus.textContent, /API access denied/);
  assert.equal(h.calls.filter(c => c.url.endsWith('/scenarios')).length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/uploads')).length, 0,
    '403 must be caught before presigned upload is requested');
  assert.equal(h.calls.filter(c => c.url === 'https://s3.example.com/upload').length, 0);
  assert.equal(h.calls.filter(c => c.url.endsWith('/jobs') && c.opts.method==='POST').length,0);
  assert.equal(h.els.generate.disabled, false);
});

test('valid API key is checked before initiating signed selfie upload', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  const calls = h.calls.map(c => c.url);
  assert.ok(calls.indexOf('https://api.example.com/scenarios') <
            calls.indexOf('https://api.example.com/uploads'));
  assert.match(h.els.apiConnectionStatus.textContent, /API connected/);
});

test('there are no prompt toggle controls or preview API calls', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  for (const n of [1, 2, 3]) {
    assert.doesNotMatch(html, new RegExp('id="videoScene' + n + '"'));
    assert.doesNotMatch(html, new RegExp('id="videoPromptDetails' + n + '"'));
  }
  assert.equal(h.calls.filter(c => c.url.includes('/video/scene') && c.url.endsWith('/preview')).length, 0);
});

test('existing uncertain paid Scene 1 auto-opens debug and displays its status', async () => {
  const h = setup({paidScenes:{scene1:false,scene2:false,scene3:false},
    realVideos:{scene1:{mode:'REAL',status:'SUBMISSION_UNKNOWN'}}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.videoDebugDetails1.open, true);
  // One visible scene status only: hide the redundant player-status line.
  assert.equal(h.els.videoStatus1.hidden, true);
  assert.match(h.els.scene1PaidStatus.textContent,/submission/i);
  assert.match(h.els.videoDebugText1.textContent,/SUBMISSION_UNKNOWN/);
  assert.equal(h.els.generateAllVideos.hidden, true);
  assert.equal(h.calls.filter(c => /\/real-videos\/scene[123]$/.test(c.url)).length, 0);
});

test('per-scene debug shows status and live GET polling does not submit paid requests', async () => {
  const h = setup({paidScenes: {scene1:false,scene2:false,scene3:false},
    realVideos:{scene1:{mode:'REAL',status:'PROCESSING',workflowStatus:'RUNNING'}}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.match(h.els.videoDebugText1.textContent,/PROCESSING/);
  assert.match(h.els.videoDebugText1.textContent,/RUNNING/);
  const start = h.calls.filter(c => c.url.endsWith('/videos')).length;
  await h.nextTimer(); await h.flush();
  assert.ok(h.calls.filter(c => c.url.endsWith('/videos')).length > start);
  assert.equal(h.calls.filter(c => /\/real-videos\/scene[123]$/.test(c.url)).length,0);
});

test('four requested cosmetic changes keep progress and scene headings consistent', () => {
  assert.match(html, /Preparing your reference images…/);
  assert.doesNotMatch(html, /Preparing your videos…/);
  for (const [n, seconds] of [[1, 5], [2, 8], [3, 10]]) {
    assert.match(html, new RegExp('<strong>Scene ' + n + '[^<]*' + seconds + ' seconds<\\/strong>'));
  }
  assert.doesNotMatch(html, /Download Scene [123] MP4/);
  assert.doesNotMatch(html, /scene-download/);
  assert.match(html, /controlsList', 'nodownload'/);
  assert.match(html, /id="downloadFinal"/);
});

test('final download fetches existing MP4 only and saves a timestamped filename', async () => {
  const options = {
    paidScenes: {scene1: false, scene2: false, scene3: false},
    realVideos: Object.fromEntries([1, 2, 3].map(n => ['scene' + n,
      {mode: 'REAL', status: 'COMPLETED', url: 'https://s3.example.com/scene' + n + '.mp4'}])),
    assembly: {enabled: true, status: 'COMPLETED',
      url: 'https://s3.example.com/final.mp4',
      downloadUrl: 'https://s3.example.com/forced-afl-grand-final.mp4'},
  };
  const h = setup(options);
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.downloadFinal.hidden, false);
  await h.els.downloadFinal.listeners.click();
  await h.flush();
  assert.equal(h.downloads.length, 1);
  assert.match(h.downloads[0].name, /^afl-grand-final-\\d{14}\\.mp4$/);
  assert.equal(h.downloads[0].url, 'blob:test');
  assert.equal(h.calls.filter(c => c.url === 'https://s3.example.com/final.mp4').length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/assembly') && c.opts.method === 'POST').length, 0);
  assert.equal(h.calls.filter(c => /\\/real-videos\\/scene[123]$/.test(c.url) && c.opts.method === 'POST').length, 0);
  for (const n of [1, 2, 3]) {
    const player = h.els['videoPlayer' + n];
    assert.equal(player.children.length, 1, 'Only the scene player is shown');
    assert.equal(player.children[0].controls, true);
    assert.equal(player.children[0].getAttribute?.('controlsList'), 'nodownload');
  }
});
