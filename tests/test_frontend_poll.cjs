/* Offline phone-page tests. No AWS, secrets or billable requests. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'styles.css'), 'utf8');
assert.match(html, /<script src="\.\/app\.js"><\/script>/);
assert.match(html, /<link rel="stylesheet" href="\.\/styles\.css">/);
new vm.Script(script, {filename: 'frontend/app.js'});

function setup(options = {}) {
  let statusPolls = 0;
  let s3UploadAttempts = 0;
  const mockStatuses = {scene1: 'NOT_REQUESTED', scene2: 'NOT_REQUESTED', scene3: 'NOT_REQUESTED'};
  const calls = [];
  const downloads = [];
  const pendingVideoPosts = new Map();
  const timers = new Map();
  const intervals = new Map();
  let timerId = 0;
  const els = {};
  function el() {
    return {
      hidden: false, disabled: false, value: '', textContent: '', dataset: {}, style: {},
      listeners: {},
      classList: { values: new Set(), add(name) { this.values.add(name); },
        remove(name) { this.values.delete(name); },
        toggle(name, force) { const enabled = force === undefined ? !this.values.has(name) : !!force;
          if (enabled) this.values.add(name); else this.values.delete(name); return enabled; },
        contains(name) { return this.values.has(name); } },
      children: [],
      appendChild(child) { this.children.push(child); },
      setAttribute(name, value) { (this.attributes ||= {})[name] = value; },
      getAttribute(name) { return this.attributes?.[name] ?? null; },
      removeAttribute(name) { if (this.attributes) delete this.attributes[name]; if (name === 'src') this.src = ''; },
      querySelector(tag) { return this.children.map(c => c.tagName === tag ? c : c.querySelector?.(tag)).find(Boolean) || null; },
      querySelectorAll(tag) { return this.children.filter(c => c.tagName === tag); },
      replaceChildren(...children) { this.children = [...children]; },
      addEventListener(name, fn) { this.listeners[name] = fn; },
      play() { this.playCalls = (this.playCalls || 0) + 1; this.paused = false; return Promise.resolve(); },
      pause() { this.pauseCalls = (this.pauseCalls || 0) + 1; this.paused = true; this.listeners.pause?.(); },
    };
  }
  for (const name of ['selfie', 'preview', 'removeSelfie', 'generate', 'testApi', 'refresh',
                      'generateScene1', 'status', 'pollStatus', 'key', 'apiConnectionStatus', 'team', 'scenario',
                      'jobIdPanel', 'currentJobIdText', 'copyJobId', 'copyJobStatus',
                      'referenceImagesHeading',
                      'imageDiagnosticsText', 'developerDiagnostics', 'overallJobStatus',
                      'testGoogle', 'googleStatus', 'apiTestResult', 'firstName', 'lastName', 'personalMessage', 'personalMessageCount',
                      'videoPromptSection', 'videoPromptStatus', 'videoPromptText',
                      'videoPromptStatus1','videoPromptStatus2','videoPromptStatus3',
                      'videoPromptText1','videoPromptText2','videoPromptText3',
                      'videoPromptDetails1','videoPromptDetails2','videoPromptDetails3',
                      'videoScene1', 'videoScene2', 'videoScene3',
                      'scene1Preflight', 'scene1PreflightStatus', 'scene1PaidStatus', 'generateAllVideos',
                      'scene2PaidStatus', 'scene3PaidStatus',
                      'videoTestSection','videoTestMessage','assembleFinal','assemblyPanel','assemblyStatus','assemblyPlayer','downloadFinal','nextStageMessage',
                      'assemblyProgress','assemblyPercent','assemblyProgressBar','assemblyProgressFill','assemblyProgressNote',
                      'generateVideo1','generateVideo2','generateVideo3',
                      'videoStatus1','videoStatus2','videoStatus3',
                      'videoPlayer1','videoPlayer2','videoPlayer3',
                      'videoPanel1','videoPanel2','videoPanel3',
                      'scene1Progress','scene1Percent','scene1ProgressFill','scene1ProgressBar','scene1ProgressNote',
                      'scene2Progress','scene2Percent','scene2ProgressFill','scene2ProgressBar','scene2ProgressNote',
                      'scene3Progress','scene3Percent','scene3ProgressFill','scene3ProgressBar','scene3ProgressNote',
                      'videoDebugDetails1','videoDebugDetails2','videoDebugDetails3',
                      'videoDebugText1','videoDebugText2','videoDebugText3',
                       'diagnoseScene1','diagnoseScene2','diagnoseScene3',
                       'diagnoseResult1','diagnoseResult2','diagnoseResult3',
                       'retryVideoScene1','retryVideoScene2','retryVideoScene3',
                       'retryVideoResult1','retryVideoResult2','retryVideoResult3']) {
    els[name] = el();
    // Keep the basic DOM simulation intact for the new reference image cards.
  }
  for (const n of [1,2,3]) els['scene'+n+'Progress'].hidden = true;
  els.assemblyProgress.hidden = true;
  els.assembleFinal.disabled = true;
  els.assembleFinal.hidden = true; // Matches the HTML hidden attribute.
  els.generateAllVideos.hidden = true;
  for (const n of [1, 2, 3]) els['diagnoseScene' + n].hidden = true;
  for (const n of [1, 2, 3]) els['diagnoseScene' + n].hidden = true;
  for (const n of [1, 2, 3]) els['diagnoseScene' + n].hidden = true;
  els.referenceImagesHeading.hidden = true;
  els.jobIdPanel.hidden = true;
  els.key.value = 'test-api-key';
  els.team.value = 'geelong';
  els.firstName.value = 'Alex';
  els.lastName.value = 'Tonelli';
  els.scenario.value = 'afl-grand-final-winning-kick';
  els.selfie.files = [{name: 'selfie.png', type: 'image/png', size: 1200}];
  els.removeSelfie.hidden = true;
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
    if (url === 'https://s3.example.com/upload') {
      s3UploadAttempts++;
      if (s3UploadAttempts <= (options.networkS3Failures || 0))
        throw new TypeError('Failed to fetch');
      return options.failSelfieUpload ? {ok:false, status:500} : {ok:true};
    }
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
        sequence: options.sequence || {},
        assembly: options.assembly || {},
        videos: {
          scene1: {status: mockStatuses.scene1},
          scene2: {status: mockStatuses.scene2},
          scene3: {status: mockStatuses.scene3},
          ...(options.realVideos || {}),
        },
      }; },
    };
    if (url.endsWith('/real-videos/sequence') && opts.method === 'POST') {
      if (options.deferPaidVideoResponses) {
        return new Promise(resolve => pendingVideoPosts.set('sequence', resolve));
      }
      options.sequence = {status:'APPROVED',mode:'AWS_EVENT_DRIVEN'};
      const prior = options.realVideos?.scene1;
      if (!prior) {
        options.paidScenes = {...(options.paidScenes || {}),scene1:false};
        options.realVideos = {...(options.realVideos || {}),
          scene1:{mode:'REAL',status:'REQUESTED'}};
      }
      return {ok:true,async json(){return {status:'SEQUENCE_APPROVED',
        sequence:{status:'APPROVED',mode:'AWS_EVENT_DRIVEN'}};}};
    }
    if (/\/real-videos\/scene[123]\/retry$/.test(url) && opts.method === 'POST') {
      const scene = url.match(/scene[123]/)[0];
      options.realVideos = {...(options.realVideos || {}),
        [scene]: {mode: 'REAL', status: 'REQUESTED', retryAvailable: false}};
      return {ok: true, async json() { return {status: 'REAL_VIDEO_RETRY_REQUESTED', attemptNumber: 2}; }};
    }
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
          (_, i) => ({sceneId: 'scene' + (i + 1), url: 'https://images.example.com/img' + i})),
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
            ...(options.imageScenes || {}),
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
    fetch, TypeError, Set, Blob, URL: {createObjectURL() { return 'blob:test'; }, revokeObjectURL() {}},
    FormData: class { append() {} },
    crypto: {randomUUID() { return 'test-idempotency-key'; }},
    setTimeout(fn, delay) {
      const id = ++timerId; timers.set(id, {fn, delay}); return id;
    },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn, delay) { const id = ++timerId; intervals.set(id, {fn, delay}); return id; },
    clearInterval(id) { intervals.delete(id); },
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
  return {els, doc, calls, downloads, timers, intervals, pendingVideoPosts, flush, nextTimer, sessionStorage, getPolls: () => statusPolls, imageCount, mockStatuses, getS3Attempts: () => s3UploadAttempts};
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
               'Polling cannot trigger new Google generation');
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
    assert.equal(h.els['videoPanel'+n].hidden, n !== 1,
      'Scene 2/3 panels remain hidden until earlier real MP4 completes');
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
  assert.doesNotMatch(html, /id="generateScene1"|Generate 3 reference images with Google/);
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

test('remove selfie clears file and preview, then permits reselecting without API calls', async () => {
  const h = setup();
  h.els.selfie.listeners.change();
  assert.equal(h.els.preview.hidden, false);
  assert.equal(h.els.removeSelfie.hidden, false);
  assert.equal(h.els.preview.src, 'blob:test');
  h.els.removeSelfie.listeners.click();
  assert.equal(h.els.selfie.value, '');
  assert.equal(h.els.preview.hidden, true);
  assert.equal(h.els.removeSelfie.hidden, true);
  assert.equal(h.els.preview.src, '');
  assert.equal(h.calls.length, 0, 'Clearing a photo never calls AWS');
  h.els.selfie.files = [{name: 'correct-selfie.jpg', type: 'image/jpeg', size: 2400}];
  h.els.selfie.listeners.change();
  assert.equal(h.els.preview.hidden, false);
  assert.equal(h.els.removeSelfie.hidden, false);
  assert.equal(h.els.preview.src, 'blob:test');
});

test('failed selfie upload stops before job creation or Google generation', async () => {
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
  assert.equal(h.els.videoPanel2.hidden, true,
    'Mock video does not unlock Scene 2; only a completed real MP4 does');
  assert.equal(h.els.videoPanel3.hidden, true,
    'Mock video does not unlock Scene 3; only a completed real MP4 does');
  assert.equal(h.els.generateAllVideos.hidden, true, 'Mock result cannot authorize paid scene');
  assert.match(h.els.videoStatus1.textContent, /Real video not generated/);
  assert.equal(h.els.assembleFinal.hidden, true);
  assert.equal(h.calls.filter(c => /\/videos\/scene[123]$/.test(c.url) && c.opts.method === 'POST').length, 0);
});

test('reference image progress appears beneath scene cards and before video controls', () => {
  const order = ['id="generate"', 'id="jobProgressSection"', 'id="results"',
                 'id="status"', 'id="pollStatus"', 'id="videoTestSection"'];
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

test('video status polling never starts paid generation without backend approval', async () => {
  const h=setup({paidScenes:{scene1:false,scene2:false,scene3:false}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.generateAllVideos.hidden,true);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/real-videos/sequence')).length,0);
});
test('three completed images stay read-only while AWS scenes unlock panels and stitching', async () => {
  const options={paidScenes:{scene1:true,scene2:true,scene3:true},realVideos:{}};
  const h=setup(options);
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  const approvals=()=>h.calls.filter(c=>c.url.endsWith('/real-videos/sequence') && c.opts.method==='POST');
  assert.equal(approvals().length,0, 'Backend owns video sequence submission');
  const jobPosts=h.calls.filter(c=>c.url.endsWith('/jobs') && c.opts.method==='POST');
  assert.equal(JSON.parse(jobPosts[0].opts.body).confirmPaidSequence,true,
    'Consent is recorded at initial job creation');
  assert.equal(h.els.generateAllVideos.hidden,true);
  assert.equal(h.els.videoPanel2.hidden,true);
  options.realVideos.scene1={mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene1.mp4'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.videoPanel2.hidden,false);
  assert.equal(h.els.videoPanel3.hidden,true);
  options.realVideos.scene2={mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene2.mp4'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.videoPanel3.hidden,false);
  options.realVideos.scene3={mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene3.mp4'};
  options.assembly={enabled:true,status:'READY'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.assembleFinal.hidden,true);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/assembly') && c.opts.method==='POST').length,0,
    'Only AWS may start stitching after Scene 3');
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.calls.filter(c=>c.url.endsWith('/assembly') && c.opts.method==='POST').length,0);
  assert.equal(approvals().length,0);
  assert.equal(h.calls.filter(c=>/\/real-videos\/scene[123]$/.test(c.url) && c.opts.method==='POST').length,0);
});
test('Scene 2 and Scene 3 video panels unlock only after previous MP4 completion', async () => {
  const options = {paidScenes:{scene1:true,scene2:true,scene3:true},realVideos:{}};
  const h = setup(options);
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.videoPanel1.hidden,false);
  assert.equal(h.els.videoPanel2.hidden,true);
  assert.equal(h.els.videoPanel3.hidden,true);
  options.realVideos.scene1 = {mode:'REAL',status:'FAILED',errorCode:'GOOGLE_FAILED',retryAvailable:true};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.videoPanel2.hidden,true, 'Scene 1 failure must not reveal Scene 2');
  assert.equal(h.els.retryVideoScene1.hidden,false, 'Manual retry stays accessible');
  options.realVideos.scene1 = {mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene1.mp4'};
  options.paidScenes.scene1 = false;
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.videoPanel2.hidden,false);
  assert.equal(h.els.videoPanel3.hidden,true);
  options.realVideos.scene2 = {mode:'REAL',status:'FAILED',errorCode:'GOOGLE_FAILED',retryAvailable:true};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.videoPanel3.hidden,true,'Scene 2 failure must not reveal Scene 3');
  assert.equal(h.els.retryVideoScene2.hidden,false);
  options.realVideos.scene2 = {mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene2.mp4'};
  options.paidScenes.scene2 = false;
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.videoPanel3.hidden,false);
  assert.equal(h.els.assembleFinal.hidden,true);
});

test('failed Scene 2 blocks Scene 3 while keeping manual retry and successful videos', async () => {
  const options={paidScenes:{scene1:true,scene2:true,scene3:true},realVideos:{}};
  const h=setup(options);
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  await h.flush(); // sequence is now started by AWS, not browser polling
  options.realVideos.scene1={mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene1.mp4'};
  options.realVideos.scene2={mode:'REAL',status:'FAILED',retryAvailable:true,
    providerFinalStatus:'failed',providerErrorCode:'too_many_requests'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.videoPanel2.hidden,false);
  assert.equal(h.els.videoPanel3.hidden,true);
  assert.equal(h.els.retryVideoScene2.hidden,false);
  assert.ok(h.els.videoPlayer1.querySelector('video'),'Completed scene stays playable');
  const count=()=>h.calls.filter(c=>c.url.endsWith('/real-videos/sequence')).length;
  assert.equal(count(),0);
  await h.els.retryVideoScene2.listeners.click(); await h.flush();
  assert.equal(h.calls.filter(c=>c.url.endsWith('/real-videos/scene2/retry')).length,1);
  assert.equal(h.els.videoPanel3.hidden,true);
  options.realVideos.scene2={mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene2.mp4'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.videoPanel3.hidden,false);
  assert.equal(count(),0,'No browser-side sequence approvals');
  assert.equal(h.calls.filter(c=>/\/real-videos\/scene3$/.test(c.url) && c.opts.method==='POST').length,0,
    'Scene 3 is submitted by AWS, not this browser');
});

test('background app pauses read-only polling but AWS completion becomes visible on return', async () => {
  const options={paidScenes:{scene1:true,scene2:true,scene3:true},realVideos:{}};
  const h=setup(options);
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  await h.flush(); // sequence auto-started when all reference images completed
  h.doc.hidden=true;
  h.doc.listeners.visibilitychange();
  options.realVideos.scene1={mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene1.mp4'};
  options.realVideos.scene2={mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene2.mp4'};
  options.realVideos.scene3={mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene3.mp4'};
  await h.flush();
  const approvals=h.calls.filter(c=>c.url.endsWith('/real-videos/sequence'));
  assert.equal(approvals.length,0);
  assert.equal(h.els.videoPanel3.hidden,true,'Background page did not repaint');
  h.doc.hidden=false;
  h.doc.listeners.visibilitychange();
  await h.flush();
  assert.equal(h.els.videoPanel3.hidden,false,'On return page displays AWS-completed scenes');
  assert.equal(h.calls.filter(c=>c.url.endsWith('/real-videos/sequence')).length,0);
  assert.equal(h.calls.filter(c=>/\/real-videos\/scene[123]$/.test(c.url) && c.opts.method==='POST').length,0);
});


test('already approved sequence never triggers automatic paid resubmission', async () => {
  const h=setup({paidScenes:{scene1:false,scene2:true,scene3:true},
    sequence:{status:'APPROVED',mode:'AWS_EVENT_DRIVEN'},
    realVideos:{scene1:{mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene1.mp4'}}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.generateAllVideos.hidden,true);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/real-videos/sequence')).length,0);
});
test('browser never attempts paid approval even when backend reports uncertain status', async () => {
  const h=setup({paidScenes:{scene1:true,scene2:true,scene3:true},
    realVideos:{},deferPaidVideoResponses:true});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.pendingVideoPosts.size,0);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/real-videos/sequence')).length,0);
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.calls.filter(c=>c.url.endsWith('/real-videos/sequence')).length,0);
  assert.equal(h.els.generateAllVideos.hidden,true);
});
test('stale video polling cannot initiate an AWS-owned paid sequence', async () => {
  const h=setup({paidScenes:{scene1:true,scene2:true,scene3:true}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.calls.filter(c=>c.url.endsWith('/real-videos/sequence')).length,0);
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

test('completed MP4 does not trigger fresh automatic sequence approval', async () => {
  const h=setup({paidScenes:{scene1:false,scene2:true,scene3:true},
    realVideos:{scene1:{mode:'REAL',status:'COMPLETED',url:'https://example.invalid/scene1.mp4'}}});
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.generateAllVideos.hidden,true);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/real-videos/sequence')).length,0);
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
  assert.doesNotMatch(html, /Preparing your reference images…|Scene 1 is available below\./);
  assert.match(script, /'Reference images: ' \+ completed \+ '\/3 completed'/);
  assert.doesNotMatch(html, /Preparing your videos…/);
  for (const [n, seconds] of [[1, 4], [2, 8], [3, 5]]) {
    assert.match(html, new RegExp('<strong>Scene ' + n + '[^<]*' + seconds + ' seconds</strong>'));
  }
  assert.doesNotMatch(html, /Download Scene [123] MP4/);
  assert.doesNotMatch(html, /scene-download/);
  assert.match(script, /video\.muted = true/);
  assert.doesNotMatch(script, /controlsList', 'nodownload'/);
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
  assert.match(h.downloads[0].name, /^afl-grand-final-\d{14}\.mp4$/);
  assert.equal(h.downloads[0].url, 'blob:test');
  assert.equal(h.calls.filter(c => c.url === 'https://s3.example.com/final.mp4').length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/assembly') && c.opts.method === 'POST').length, 0);
  assert.equal(h.calls.filter(c => /\/real-videos\/scene[123]$/.test(c.url) && c.opts.method === 'POST').length, 0);
  for (const n of [1, 2, 3]) {
    const player = h.els['videoPlayer' + n];
    assert.equal(player.children.length, 1, 'Only the scene player is shown');
    const wrapper = player.children[0];
    const preview = wrapper.querySelector('video');
    const button = wrapper.querySelector('button');
    assert.ok(preview && button);
    assert.equal(preview.controls, false, 'Scene previews have no native controls');
    assert.equal(preview.muted, true, 'Scene previews must always be silent');
    assert.equal(preview.defaultMuted, true);
    assert.equal(preview.autoplay, false, 'Explicit autoplay runs only on first completion');
    assert.equal(preview.loop, false, 'No continuous looping');
    assert.equal(preview.playsInline, true);
    assert.equal(preview.getAttribute('muted'), '');
    assert.equal(preview.getAttribute('autoplay'), null);
    assert.equal(preview.getAttribute('loop'), null);
    assert.equal(preview.getAttribute('playsinline'), '');
    assert.equal(preview.getAttribute('controls'), null);
    assert.equal(preview.playCalls, 1, 'Autoplays exactly once on first completion');
    assert.equal(typeof button.listeners.click, 'function', 'Every scene has one replay action');
    preview.listeners.ended();
    assert.equal(button.hidden, false, 'Single Play button appears after clip ends');
    await button.listeners.click();
    assert.equal(preview.muted, true);
    await h.flush();
    assert.equal(preview.playCalls, 2, 'Replay is user initiated');
  }
});

test('reference image cards show clean status, green completion and animated generating cog', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.flush();
  function progressFor(n) {
    const card = h.els.results.children.find(c => c.id === 'imageCard' + n);
    assert.ok(card, 'Scene ' + n + ' reference card exists');
    return card.querySelector('p');
  }
  const initial = progressFor(1);
  assert.equal(initial.textContent, 'Completed');
  assert.equal(initial.className, 'reference-progress completed');
  const generating = progressFor(2);
  assert.equal(generating.textContent, 'Generating…');
  assert.equal(generating.className, 'reference-progress generating');
  assert.doesNotMatch(generating.textContent, /Reference image:/);
  await h.nextTimer();
  assert.equal(progressFor(2).textContent, 'Completed');
  assert.equal(progressFor(2).className, 'reference-progress completed');
  assert.equal(progressFor(3).textContent, 'Generating…');
  await h.nextTimer();
  assert.equal(progressFor(3).textContent, 'Completed');
  assert.equal(progressFor(3).className, 'reference-progress completed');
  assert.match(css, /reference-progress\.generating::before,\.video-state\.generating::before,#status\.upload-retrying::before\{content:"⚙"/);
  assert.match(css, /@keyframes reference-gear-spin/);
  assert.match(css, /prefers-reduced-motion:reduce/);
});

test('individual failed video retry preserves other completed MP4 state', async () => {
  const h = setup({
    realVideos: {
      scene1: {mode:'REAL', status:'FAILED', retryAvailable:true, attemptNumber:1},
      scene2: {mode:'REAL', status:'COMPLETED', url:'https://s3.example.com/scene2.mp4'},
      scene3: {mode:'REAL', status:'COMPLETED', url:'https://s3.example.com/scene3.mp4'},
    },
  });
  await h.els.generate.listeners.click();
  await h.flush();
  // Job must reach completed reference image state before video section becomes visible.
  for (let i=0;i<3 && h.els.videoTestSection.hidden;i++) {
    if (h.timers.size) await h.nextTimer();
  }
  await h.flush();
  assert.equal(h.els.retryVideoScene1.hidden, false);
  assert.equal(h.els.retryVideoScene2.hidden, true);
  assert.equal(h.els.retryVideoScene3.hidden, true);
  await h.els.retryVideoScene1.listeners.click();
  await h.flush();
  const retry = h.calls.filter(c => /\/real-videos\/scene1\/retry$/.test(c.url) && c.opts.method === 'POST');
  assert.equal(retry.length, 1);
  assert.equal(JSON.parse(retry[0].opts.body).confirmRetryScene, 'scene1');
  assert.equal(h.calls.filter(c => /\/generate$/.test(c.url)).length, 1,
     'retry must not regenerate the already completed reference images');
  assert.equal(h.calls.filter(c => /\/real-videos\/scene[23]\/retry$/.test(c.url)).length, 0);
});

test('Google failure details live only in bottom developer diagnostics', () => {
  const diagnostics = html.indexOf('id="developerDiagnosticsSection"');
  const main = html.indexOf('id="videoTestSection"');
  assert.ok(diagnostics > main);
  for (const n of [1, 2, 3]) {
    const mainCard = html.indexOf('id="videoPanel' + n + '"');
    const retryButton = html.indexOf('id="retryVideoScene' + n + '"');
    const diagnosticButton = html.indexOf('id="diagnoseScene' + n + '"');
    const diagnosticOutput = html.indexOf('id="diagnoseResult' + n + '"');
    const retryOutput = html.indexOf('id="retryVideoResult' + n + '"');
    assert.ok(mainCard > main && mainCard < diagnostics);
    assert.ok(retryButton > mainCard && retryButton < diagnostics);
    assert.ok(diagnosticButton > diagnostics);
    assert.ok(diagnosticOutput > diagnostics);
    assert.ok(retryOutput > diagnostics);
  }
});

test('completed retried scene clears stale transient retry message', async () => {
  const h = setup({
    realVideos: {
      scene1: {mode:'REAL', status:'COMPLETED', attemptNumber:2,
               url:'https://s3.example.com/video1.mp4'},
      scene2: {mode:'REAL', status:'COMPLETED', url:'https://s3.example.com/video2.mp4'},
      scene3: {mode:'REAL', status:'COMPLETED', url:'https://s3.example.com/video3.mp4'},
    }
  });
  await h.els.generate.listeners.click();
  await h.flush();
  for (let i=0; i<3 && h.els.videoTestSection.hidden; i++) {
    if (h.timers.size) await h.nextTimer();
  }
  await h.flush();
  // Seed stale text on an existing job, not before the new-job reset.
  h.els.retryVideoResult1.textContent = 'Scene 1 retry requested — checking progress';
  h.els.diagnoseResult1.textContent = 'Historical Google failure details';
  h.doc.listeners.visibilitychange();
  await h.flush();
  assert.equal(h.els.scene1PaidStatus.textContent, 'MP4 status: completed');
  assert.equal(h.els.retryVideoResult1.textContent, '');
  assert.equal(h.els.diagnoseResult1.textContent, 'Historical Google failure details',
               'historical details remain in developer diagnostics');
  assert.equal(h.els.retryVideoScene1.hidden, true);
});

test('two independent failures show separate retry controls and explain a blocked third', async () => {
  const h = setup({
    realVideos: {
      scene1: {mode:'REAL',status:'FAILED',retryAvailable:true,attemptNumber:1},
      scene2: {mode:'REAL',status:'FAILED',retryAvailable:true,attemptNumber:1},
      scene3: {mode:'REAL',status:'REVIEW_REQUIRED',retryAvailable:false,
               retryBlockedReason:'WORKFLOW_REVIEW_REQUIRED',failureCode:'WORKER_TIMEOUT'},
    },
  });
  await h.els.generate.listeners.click();
  await h.flush();
  for (let i=0;i<3 && h.els.videoTestSection.hidden;i++) {
    if (h.timers.size) await h.nextTimer();
  }
  await h.flush();
  assert.equal(h.els.retryVideoScene1.hidden, false);
  assert.equal(h.els.retryVideoScene2.hidden, false);
  assert.equal(h.els.retryVideoScene3.hidden, true);
  assert.match(h.els.scene3PaidStatus.textContent, /needs review/i);
});


test('image, video and stitching spinner styles use the same green rotating cog', () => {
  assert.match(css, /\.reference-progress\.generating::before,\.video-state\.generating::before,#status\.upload-retrying::before\{/);
  assert.match(css, /color:#16733b;animation:reference-gear-spin/);
  assert.match(script, /paidStatus\.classList\.toggle\('generating'/);
  assert.match(script, /assemblyStatus\.classList\.toggle\('generating'/);
  assert.doesNotMatch(script, /submitFinalAssembly\(/, 'Browser cannot start assembly from status polling');
});

test('image retry waiting switches to green-cog Generating when attempt resumes', async () => {
  const imageScenes = {scene1: {status: 'RETRY_WAIT', retryCount: 1, maxRetries: 7}};
  const h = setup({imageScenes});
  await h.els.generate.listeners.click();
  await h.flush();
  const progress = () => h.els.results.children.find(c => c.id === 'imageCard1').querySelector('p');
  assert.match(progress().textContent, /Google busy — retrying automatically \(1\/7\)/);
  assert.equal(progress().className, 'reference-progress generating');
  imageScenes.scene1.status = 'GENERATING';
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(progress().textContent, 'Generating…');
  assert.equal(progress().className, 'reference-progress generating');
  imageScenes.scene1.status = 'COMPLETED';
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(progress().textContent, 'Completed');
  assert.equal(progress().className, 'reference-progress completed');
});

test('transient S3 network error retries with fresh presign and then creates exactly one job', async () => {
  const h=setup({networkS3Failures:1});
  const submitted=h.els.generate.listeners.click();
  await h.flush();
  assert.equal(h.els.status.classList.contains('upload-retrying'),true);
  assert.match(h.els.status.textContent,/Upload interrupted — retrying \(1\/2\)/);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/uploads')).length,1);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/jobs') && c.opts.method==='POST').length,0);
  assert.equal(await h.nextTimer(),700);
  await submitted;
  assert.equal(h.getS3Attempts(),2);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/uploads')).length,2);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/jobs') && c.opts.method==='POST').length,1);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/generate') && c.opts.method==='POST').length,1);
  assert.equal(h.els.status.classList.contains('upload-retrying'),false);
});
test('persistent S3 network error gives up after two retries without creating a job', async () => {
  const h=setup({networkS3Failures:3});
  const submitted=h.els.generate.listeners.click();
  await h.flush();
  assert.equal(await h.nextTimer(),700);
  assert.match(h.els.status.textContent,/Upload interrupted — retrying \(2\/2\)/);
  assert.equal(await h.nextTimer(),1400);
  await submitted;
  assert.equal(h.getS3Attempts(),3);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/uploads')).length,3);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/jobs') && c.opts.method==='POST').length,0);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/generate')).length,0);
  assert.equal(h.els.status.classList.contains('upload-retrying'),false);
  assert.match(h.els.status.textContent,/Reference image generation was not started/);
});
test('observed S3 HTTP error is not automatically retried', async () => {
  const h=setup({failSelfieUpload:true});
  await h.els.generate.listeners.click();
  assert.equal(h.getS3Attempts(),1);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/uploads')).length,1);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/jobs') && c.opts.method==='POST').length,0);
  assert.equal(h.els.status.classList.contains('upload-retrying'),false);
});


test('video section uses simple user-friendly generation heading without AWS explanation', () => {
  assert.match(html, /<h2 style="margin-bottom:6px">Generating Video<\/h2>/);
  assert.doesNotMatch(html, /id="realVideoGate"/);
  assert.doesNotMatch(script, /AWS is generating scenes in sequence/);
  assert.doesNotMatch(script, /Video status updated — see each scene above/);
  assert.match(script, /videoTestMessage\.textContent = 'Video status unavailable:/);
});


test('final stitched player retains native sound and playback controls', async () => {
  const h=setup({
    realVideos:Object.fromEntries([1,2,3].map(n=>['scene'+n,
      {mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene'+n+'.mp4'}])),
    assembly:{enabled:true,status:'COMPLETED',url:'https://s3.example.com/final.mp4'}
  });
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  const final=h.els.assemblyPlayer.querySelector('video');
  assert.ok(final);
  assert.equal(final.controls,true);
  assert.notEqual(final.muted,true);
  assert.notEqual(final.loop,true);
});

test('only one of three scene previews plays; new autoplay pauses previous preview', async () => {
  const opts={realVideos:{scene1:{mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene1.mp4'}}};
  const h=setup(opts);
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  const one=h.els.videoPlayer1.querySelector('video');
  const onePlay=h.els.videoPlayer1.querySelector('button');
  assert.equal(one.playCalls,1);
  opts.realVideos.scene2={mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene2.mp4'};
  await h.els.refresh.listeners.click(); await h.flush();
  const two=h.els.videoPlayer2.querySelector('video');
  assert.equal(two.playCalls,1);
  assert.equal(one.pauseCalls,1);
  assert.equal(onePlay.hidden,false);
  opts.realVideos.scene3={mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene3.mp4'};
  await h.els.refresh.listeners.click(); await h.flush();
  const three=h.els.videoPlayer3.querySelector('video');
  assert.equal(three.playCalls,1);
  assert.equal(two.pauseCalls,1);
  assert.equal(one.playCalls,1,'Polling must never restart Scene 1');
  await onePlay.listeners.click();
  await h.flush();
  assert.equal(three.pauseCalls,1,'Manual replay pauses Scene 3');
  assert.equal(one.playCalls,2);
  assert.equal(one.muted,true);
  assert.equal(one.controls,false);
  assert.match(css,/\.scene-preview-play/);
});

test('retry countdown tracks backend nextRetryAt every second without more Google calls', async () => {
  const now = Math.floor(Date.now()/1000);
  const scene = {status:'RETRY_WAIT',retryCount:2,maxRetries:7,nextRetryAt:now+22};
  const h=setup({imageScenes:{scene1:scene}});
  await h.els.generate.listeners.click();
  await h.flush();
  const progress=()=>h.els.results.children.find(c=>c.id==='imageCard1').querySelector('p');
  assert.match(progress().textContent,/Retrying in 2[123] seconds \(2\/7\)/);
  assert.equal(progress().className,'reference-progress generating');
  assert.equal(h.intervals.size,1);
  assert.equal([...h.intervals.values()][0].delay,1000);
  scene.nextRetryAt=now+5;
  [...h.intervals.values()][0].fn();
  assert.match(progress().textContent,/Retrying in [456] seconds \(2\/7\)/);
  scene.nextRetryAt=now-1;
  [...h.intervals.values()][0].fn();
  assert.equal(progress().textContent,'Retrying shortly… (2/7)');
  assert.equal(h.calls.filter(c=>c.url.endsWith('/generate')).length,1);
  scene.status='GENERATING';
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(progress().textContent,'Generating…');
  assert.equal(h.intervals.size,0,'Countdown timer cleared after retry starts');
});

test('final video shows green generating cog until completed and prominent green download', async () => {
  assert.doesNotMatch(html, /Stitching starts automatically when all three videos are complete/);
  assert.match(html, /id="downloadFinal"[^>]*class="download-final-button"[^>]*>↓ Download Now<\/button>/);
  assert.match(css, /#downloadFinal\.download-final-button\{[^}]*background:#16733b/);
  const opts = {
    realVideos: Object.fromEntries([1,2,3].map(n=>['scene'+n,
      {mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene'+n+'.mp4'}])),
    assembly:{enabled:true,status:'QUEUED'}
  };
  const h=setup(opts);
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.assemblyStatus.textContent, 'Generating final video…');
  assert.equal(h.els.assemblyStatus.classList.contains('generating'), true);
  assert.equal(h.els.downloadFinal.hidden,true);
  opts.assembly={enabled:true,status:'PROCESSING'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.assemblyStatus.textContent,'Generating final video…');
  assert.equal(h.els.assemblyStatus.classList.contains('generating'),true);
  opts.assembly={enabled:true,status:'COMPLETED',url:'https://s3.example.com/final.mp4'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.assemblyStatus.textContent,'Your final video is ready!');
  assert.equal(h.els.assemblyStatus.classList.contains('generating'),false);
  assert.equal(h.els.downloadFinal.hidden,false);
  assert.equal(h.els.assemblyPlayer.querySelector('video').controls,true);
});

test('per-scene estimated progress follows AWS timestamps, never claims Google completion', async () => {
  const started=new Date(Date.now()-32000).toISOString();
  const opts={realVideos:{
    scene1:{mode:'REAL',status:'PROCESSING',submissionStartedAt:started},
    scene2:{mode:'REAL',status:'PROCESSING',submissionStartedAt:started},
    scene3:{mode:'REAL',status:'PROCESSING',submissionStartedAt:started},
  }};
  const h=setup(opts);
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  // Panels unlock sequentially; Scene 1 can show progress before video completion.
  assert.equal(h.els.scene1Progress.hidden,false);
  const first=Number(h.els.scene1Percent.textContent.replace('%',''));
  assert.ok(first>0 && first<95);
  assert.equal(h.els.scene1ProgressBar.getAttribute('aria-valuenow'),String(first));
  assert.equal(h.els.scene2Progress.hidden,true);
  assert.equal(h.els.scene3Progress.hidden,true);
  assert.match(h.els.scene1ProgressNote.textContent,/elapsed · based on previous videos/);
  assert.equal([...h.intervals.values()].filter(x=>x.delay===1000).length,1);
  opts.realVideos.scene1={mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene1.mp4',
    submissionStartedAt:started,completedAt:new Date().toISOString()};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.scene1Progress.hidden,true);
  assert.equal(h.els.scene2Progress.hidden,false);
  const second=Number(h.els.scene2Percent.textContent.replace('%',''));
  assert.ok(second>0 && second<first,'8-second Scene 2 uses slower historical average');
  opts.realVideos.scene2={mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene2.mp4'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.scene3Progress.hidden,false);
  const third=Number(h.els.scene3Percent.textContent.replace('%',''));
  assert.ok(third>second,'Scene 3 uses faster historical average');
  opts.realVideos.scene3={mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene3.mp4'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.scene3Progress.hidden,true);
  assert.equal([...h.intervals.values()].filter(x=>x.delay===1000).length,0);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/generate')).length,1);
  assert.match(css,/\.scene-estimate-track/);
});

test('long-running progress caps at 95 percent; missing timestamp stays at zero', async () => {
  const opts={realVideos:{scene1:{mode:'REAL',status:'PROCESSING',
    submittedAt:new Date(Date.now()-3600000).toISOString()}}};
  const h=setup(opts);
  await h.els.generate.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.scene1Percent.textContent,'95%');
  assert.match(h.els.scene1ProgressNote.textContent,/Taking longer than usual/);
  opts.realVideos.scene1={mode:'REAL',status:'REQUESTED'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.scene1Percent.textContent,'0%');
  assert.match(h.els.scene1ProgressNote.textContent,/Waiting for generation/);
  opts.realVideos.scene1={mode:'REAL',status:'REVIEW_REQUIRED'};
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.scene1Progress.hidden,true);
});

test('reference image divider and heading only appear after confirmed upload and job', async () => {
  assert.match(html, /id="referenceImagesHeading"[^>]*hidden/);
  assert.match(html, /Generating 3 Reference Images/);
  assert.match(css, /\.reference-images-heading\{border-top:1px solid/);
  const h=setup();
  assert.equal(h.els.referenceImagesHeading.hidden,true);
  await h.els.generate.listeners.click();
  await h.flush();
  assert.equal(h.els.referenceImagesHeading.hidden,false);
  h.els.team.value='carlton';
  h.els.team.listeners.change();
  assert.equal(h.els.referenceImagesHeading.hidden,true);
});

test('failed selfie upload never shows the reference image heading', async () => {
  const h=setup({failSelfieUpload:true});
  await h.els.generate.listeners.click();
  await h.flush();
  assert.equal(h.els.referenceImagesHeading.hidden,true);
});

test('assembly estimate shows while stitching, uses one timer and clears on completion', async () => {
  assert.match(html, /id="assemblyProgress"[^>]*hidden/);
  const opts = {
    realVideos:Object.fromEntries([1,2,3].map(n=>['scene'+n,
      {mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene'+n+'.mp4'}])),
    assembly:{enabled:true,status:'QUEUED'}
  };
  const h=setup(opts);
  await h.els.generate.listeners.click();await h.flush();await h.nextTimer();await h.nextTimer();await h.flush();
  assert.equal(h.els.assemblyProgress.hidden,false);
  assert.equal(h.els.assemblyStatus.classList.contains('generating'),true);
  assert.match(h.els.assemblyProgressNote.textContent,/10s baseline/);
  assert.equal(h.els.assemblyPercent.textContent,'0%');
  assert.equal([...h.intervals.values()].filter(t=>t.delay===1000).length,1);
  // Status polling does not reset the original start time.
  await h.els.refresh.listeners.click();await h.flush();
  assert.equal([...h.intervals.values()].filter(t=>t.delay===1000).length,1);
  opts.assembly={enabled:true,status:'COMPLETED',url:'https://s3.example.com/final.mp4'};
  await h.els.refresh.listeners.click();await h.flush();
  assert.equal(h.els.assemblyProgress.hidden,true);
  assert.equal([...h.intervals.values()].filter(t=>t.delay===1000).length,0);
  assert.equal(h.els.downloadFinal.hidden,false);
  assert.equal(h.els.assemblyStatus.textContent,'Your final video is ready!');
  assert.equal(h.calls.filter(c=>c.url.endsWith('/assembly') && c.opts.method==='POST').length,0);
});

test('assembly estimate hides on review and reset, without influencing AWS submission', async () => {
  const opts = {
    realVideos:Object.fromEntries([1,2,3].map(n=>['scene'+n,
      {mode:'REAL',status:'COMPLETED',url:'https://s3.example.com/scene'+n+'.mp4'}])),
    assembly:{enabled:true,status:'PROCESSING'}
  };
  const h=setup(opts);
  await h.els.generate.listeners.click();await h.flush();await h.nextTimer();await h.nextTimer();await h.flush();
  assert.equal(h.els.assemblyProgress.hidden,false);
  opts.assembly={enabled:true,status:'REVIEW_REQUIRED',errorCode:'TEST_FAILURE'};
  await h.els.refresh.listeners.click();await h.flush();
  assert.equal(h.els.assemblyProgress.hidden,true);
  assert.equal([...h.intervals.values()].filter(t=>t.delay===1000).length,0);
  h.els.team.listeners.change();
  assert.equal(h.els.assemblyProgress.hidden,true);
});

test('optional greeting is sent to job creation and displayed character count updates', async () => {
  assert.match(html,/id="personalMessage"[^>]*maxlength="80"/);
  assert.match(html,/Happy Birthday Dad, from Mary/);
  const h=setup();
  h.els.personalMessage.value='Happy Birthday Dad, from Mary';
  h.els.personalMessage.listeners.input();
  assert.equal(String(h.els.personalMessageCount.textContent), '29');
  await h.els.generate.listeners.click();await h.flush();
  const post=h.calls.find(c=>c.url.endsWith('/jobs') && c.opts.method==='POST');
  assert.equal(JSON.parse(post.opts.body).personalMessage,'Happy Birthday Dad, from Mary');
});
test('blank optional greeting is permitted without changed generation flow', async () => {
  const h=setup();
  await h.els.generate.listeners.click();await h.flush();
  const post=h.calls.find(c=>c.url.endsWith('/jobs') && c.opts.method==='POST');
  assert.equal(JSON.parse(post.opts.body).personalMessage,'');
});

test('failed reference image renders safely recorded Google diagnostic reasons', async () => {
  const opts={imageScenes: {scene1: {
    status:'FAILED', errorCode:'GOOGLE_IMAGE_SAFETY_BLOCK',
    providerFinishReason:'IMAGE_SAFETY', providerBlockReason:'UNKNOWN',
    providerBlockedCategories:['HARM_CATEGORY_DANGEROUS_CONTENT'],
    providerCandidateCount:1,
  }}};
  const h=setup(opts);
  await h.els.generate.listeners.click();
  await h.flush();
  await h.els.refresh.listeners.click();
  await h.flush();
  assert.match(h.els.imageDiagnosticsText.textContent,/IMAGE_SAFETY/);
  assert.match(h.els.imageDiagnosticsText.textContent,/HARM_CATEGORY_DANGEROUS_CONTENT/);
  assert.equal(h.calls.filter(c => c.url.includes('/diagnostics') && c.url.includes('/images')).length,0);
});
