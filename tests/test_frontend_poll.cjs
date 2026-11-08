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
                      'generateScene1', 'resumeLast', 'status', 'pollStatus', 'key', 'apiConnectionStatus', 'team', 'scenario',
                      'jobIdPanel', 'currentJobIdText', 'copyJobId', 'copyJobStatus',
                      'testGoogle', 'googleStatus', 'firstName', 'lastName',
                      'videoPromptSection', 'videoPromptStatus', 'videoPromptText',
                      'videoPromptStatus1','videoPromptStatus2','videoPromptStatus3',
                      'videoPromptText1','videoPromptText2','videoPromptText3',
                      'videoPromptDetails1','videoPromptDetails2','videoPromptDetails3',
                      'videoScene1', 'videoScene2', 'videoScene3',
                      'scene1Preflight', 'scene1PreflightStatus', 'scene1PaidStatus', 'generateRealScene1',
                      'scene2PaidStatus', 'scene3PaidStatus', 'generateRealScene2', 'generateRealScene3',
                      'videoTestSection','videoTestMessage','assembleFinal','nextStageMessage',
                      'generateVideo1','generateVideo2','generateVideo3',
                      'videoStatus1','videoStatus2','videoStatus3',
                      'videoPlayer1','videoPlayer2','videoPlayer3',
                      'videoPanel1','videoPanel2','videoPanel3',
                      'videoDebugDetails1','videoDebugDetails2','videoDebugDetails3',
                      'videoDebugText1','videoDebugText2','videoDebugText3']) {
    els[name] = el();
    // Keep the basic DOM simulation intact for the new reference image cards.
  }
  els.assembleFinal.disabled = true;
  els.assembleFinal.hidden = true; // Matches the HTML hidden attribute.
  for (const n of [1, 2, 3]) els['generateRealScene' + n].hidden = true;
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
    if (url.endsWith('/video/scene1/preflight')) return {
      ok: true, async json() { return {
        status: 'READY', imageBytes: 18000, durationSeconds: 8,
        googleSubmissionMade: false, paidGenerationEnabled: false
      }; },
    };
    if (url.endsWith('/video/auth-check')) return {
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
    if (url === 'https://s3.example.com/upload') return {ok: true};
    if (url.endsWith('/jobs') && opts.method === 'POST') return {
      ok: true, async json() { return {job: {jobId: 'a'.repeat(32),
                                               status: 'AWAITING_IMAGE_PIPELINE'}}; },
    };
    if (url.endsWith('/generate')) return {
      ok: true, async json() { return {status: 'GENERATING_IMAGES'}; },
    };
    if (url.endsWith('/videos')) return {
      ok: true, async json() { return {
        videoGenerationMode: 'MOCK_ONLY',
        paidScenes: options.paidScenes || {},
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
      // Mimic DynamoDB reservation becoming visible in subsequent GET status.
      options.paidScenes = {scene1: false, scene2: false, scene3: false};
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
  assert.equal(h.timers.size, 1, 'Reference image polling ends; video status continues polling automatically');
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

test('test buttons and three collapsible prompts appear after reference images finish', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.videoPromptSection.hidden, true);
  await h.els.generateScene1.listeners.click();
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  assert.equal(h.els.videoPromptSection.hidden, false);
  assert.equal(h.els.videoTestSection.hidden, false);
  assert.equal(h.els.videoPanel1.hidden, false);
  assert.equal(h.els.videoPanel2.hidden, false);
  assert.equal(h.els.videoPanel3.hidden, false);
  assert.match(html, /id="testApi" type="button">Test x-api-key/);
  assert.match(html, /id="testGoogle" type="button">Test Google connectivity/);
  assert.match(html, /id="scene1Preflight"[^>]*hidden/);
  for (const n of [1, 2, 3]) {
    assert.match(html, new RegExp('id="videoScene' + n + '" type="button"'));
    await h.els['videoScene'+n].listeners.click();
    assert.match(h.els['videoPromptText'+n].textContent, /Alex Tonelli/);
    assert.match(h.els['videoPromptText'+n].textContent, /Geelong Cats/);
    assert.equal(h.els['videoPromptDetails'+n].open, true);
    h.els['videoPromptDetails'+n].open = false;
    assert.equal(h.els['videoPromptDetails'+n].open, false);
    assert.match(h.els['videoStatus'+n].textContent, /Real video not generated/);
  }
  assert.equal(h.calls.filter(c => c.url.includes('/video/scene') && c.url.endsWith('/preview')).length, 3);
  assert.equal(h.calls.filter(c => /\/videos\/scene[123]$/.test(c.url) && c.opts.method==='POST').length, 0);
  assert.doesNotMatch(html, /id="generateVideo[123]"/, 'No mock submission buttons in HTML');
  assert.doesNotMatch(script, /method: 'POST'[\s\S]*\/videos\/scene/, 'No mock video submit handlers');
  assert.equal(h.els.assembleFinal.hidden, true);
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


test('reference images gate all video prompt sections, existing mock records do not unlock paid controls', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.videoTestSection.hidden, true);
  await h.els.generateScene1.listeners.click();
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
  assert.equal(h.els.generateRealScene2.hidden, true, 'Mock result cannot authorize paid scene');
  assert.match(h.els.videoStatus1.textContent, /Real video not generated/);
  assert.equal(h.els.assembleFinal.hidden, true);
  assert.equal(h.calls.filter(c => /\/videos\/scene[123]$/.test(c.url) && c.opts.method === 'POST').length, 0);
});

test('reference image progress stays above scene prompt controls', () => {
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

test('Scene 1 preflight is read-only and makes no video POST', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
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
  await h.els.generateScene1.listeners.click();
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  await h.els.scene1Preflight.listeners.click();
  assert.equal(h.els.generateRealScene1.hidden, true);
  assert.equal(h.calls.filter(c => c.url.endsWith('/real-videos/scene1')).length, 0);
});

test('job ID remains visible after creation and can be recovered when resuming', async () => {
  const h = setup();
  assert.equal(h.els.jobIdPanel.hidden, true);
  await h.els.generate.listeners.click();
  assert.equal(h.els.jobIdPanel.hidden, false);
  assert.equal(h.els.currentJobIdText.textContent, 'a'.repeat(32));
  assert.equal(h.sessionStorage.getItem('aflLastJobId'), 'a'.repeat(32));
  await h.els.generateScene1.listeners.click();
  await h.flush();
  assert.equal(h.els.currentJobIdText.textContent, 'a'.repeat(32));
  assert.equal(h.els.jobIdPanel.hidden, false);
});

test('changing selections hides stale job ID', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  assert.equal(h.els.jobIdPanel.hidden, false);
  h.els.team.listeners.change();
  assert.equal(h.els.jobIdPanel.hidden, true);
  assert.equal(h.els.currentJobIdText.textContent, '');
});

test('Scene 1 free readiness automatically runs once for a finished job', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
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

test('all three real video buttons are independently wired but never call paid endpoints while locked', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  for (const n of [1,2,3]) {
    assert.equal(h.els['generateRealScene' + n].hidden, true);
    assert.ok(h.els['generateRealScene' + n].listeners.click, 'Scene ' + n + ' must be wired');
    assert.match(h.els['scene' + n + 'PaidStatus'].textContent, /locked/);
  }
  assert.equal(h.calls.filter(c => /\/real-videos\/scene[123]$/.test(c.url)).length, 0);
});

test('approved Scene 1 click submits only Scene 1 once with exact consent', async () => {
  const h = setup({paidScenes: {scene1: true, scene2: true, scene3: true}});
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  await h.flush();
  assert.equal(h.els.generateRealScene1.hidden, false);
  assert.equal(h.els.generateRealScene2.hidden, false);
  assert.equal(h.els.generateRealScene3.hidden, false);
  await h.els.generateRealScene1.listeners.click();
  await h.flush();
  assert.equal(h.els.generateRealScene1.hidden, true);
  // Hide other paid buttons immediately until backend reports global slot state.
  assert.equal(h.els.generateRealScene2.hidden, true);
  assert.equal(h.els.generateRealScene3.hidden, true);
  const submitted = h.calls.filter(c => /\/real-videos\/scene[123]$/.test(c.url));
  assert.equal(submitted.length, 1);
  assert.match(submitted[0].url, /\/real-videos\/scene1$/);
  assert.equal(submitted[0].opts.method, 'POST');
  assert.deepEqual(JSON.parse(submitted[0].opts.body), {confirmPaidScene: 'scene1'});
  await h.els.generateRealScene1.listeners.click();
  assert.equal(h.calls.filter(c => /\/real-videos\/scene[123]$/.test(c.url)).length, 1,
    'Second click must never resubmit Scene 1');
});

test('read-only workflow review status does not re-open a paid button', async () => {
  const h = setup({paidScenes: {scene1: false, scene2: false, scene3: false},
    realVideos: {scene1: {mode:'REAL', status:'REVIEW_REQUIRED', errorCode:'WORKFLOW_FAILED'}}});
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush();
  await h.nextTimer();
  await h.nextTimer();
  assert.match(h.els.scene1PaidStatus.textContent, /Generation needs review/);
  assert.equal(h.els.generateRealScene1.hidden, true);
  assert.equal(h.calls.filter(c => /\/real-videos\/scene[123]$/.test(c.url)).length, 0);
});

test('all three prompts and independent paid actions are available after reference images', async () => {
  const h = setup({paidScenes: {scene1: false, scene2: true, scene3: true}});
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush();
  await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.videoPanel2.hidden, false);
  assert.equal(h.els.videoPanel3.hidden, false);
  // Mock completion must never count as a real MP4 or paid authorization.
  h.mockStatuses.scene1 = 'MOCK_COMPLETED';
  await h.els.refresh.listeners.click(); await h.flush();
  assert.equal(h.els.videoPanel2.hidden, false);
  const options = {mode:'REAL', status:'COMPLETED', url:'https://example.invalid/video1.mp4'};
  // Fetch override is handled by setup options, so use a new independent job simulation.
  const h2 = setup({paidScenes: {scene1:false, scene2:true, scene3:true},
    realVideos:{scene1:options}});
  await h2.els.generate.listeners.click();
  await h2.els.generateScene1.listeners.click();
  await h2.flush(); await h2.nextTimer(); await h2.nextTimer(); await h2.flush();
  assert.equal(h2.els.videoPanel1.hidden, false);
  assert.equal(h2.els.videoPanel2.hidden, false);
  assert.equal(h2.els.generateRealScene2.hidden, false);
  assert.equal(h2.els.videoPanel3.hidden, false);
  const h3 = setup({paidScenes: {scene1:false,scene2:false,scene3:true},
    realVideos:{
      scene1:{mode:'REAL',status:'COMPLETED',url:'https://example.invalid/video1.mp4'},
      scene2:{mode:'REAL',status:'COMPLETED',url:'https://example.invalid/video2.mp4'},
    }});
  await h3.els.generate.listeners.click();
  await h3.els.generateScene1.listeners.click();
  await h3.flush(); await h3.nextTimer(); await h3.nextTimer(); await h3.flush();
  assert.equal(h3.els.videoPanel3.hidden, false);
  assert.equal(h3.els.generateRealScene3.hidden, false);
  assert.equal(h3.calls.filter(c => /\/real-videos\/scene[123]$/.test(c.url)).length,0);
});

test('uncertain Scene 1 keeps all three prompts visible but locks paid retries', async () => {
  const h = setup({paidScenes:{scene1:false,scene2:false,scene3:false},
    realVideos:{scene1:{mode:'REAL',status:'SUBMISSION_UNKNOWN'}}});
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.equal(h.els.generateRealScene1.hidden, true);
  assert.equal(h.els.videoPanel2.hidden, false);
  assert.equal(h.els.videoPanel3.hidden, false);
  assert.equal(h.els.generateRealScene2.hidden, true);
  assert.equal(h.els.generateRealScene3.hidden, true);
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
  assert.equal(h.els.jobIdPanel.hidden, false);
});

test('prompt preview toggles closed and open without repeating API calls', async () => {
  const h = setup();
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  await h.els.videoScene2.listeners.click();
  assert.equal(h.els.videoPromptDetails2.open, true);
  assert.equal(h.els.videoPromptDetails2.hidden, false);
  await h.els.videoScene2.listeners.click();
  assert.equal(h.els.videoPromptDetails2.open, false);
  await h.els.videoScene2.listeners.click();
  assert.equal(h.els.videoPromptDetails2.open, true);
  assert.equal(h.calls.filter(c => c.url.endsWith('/video/scene2/preview')).length,1);
});

test('per-scene debug shows status and live GET polling does not submit paid requests', async () => {
  const h = setup({paidScenes: {scene1:false,scene2:false,scene3:false},
    realVideos:{scene1:{mode:'REAL',status:'PROCESSING',workflowStatus:'RUNNING'}}});
  await h.els.generate.listeners.click();
  await h.els.generateScene1.listeners.click();
  await h.flush(); await h.nextTimer(); await h.nextTimer(); await h.flush();
  assert.match(h.els.videoDebugText1.textContent,/PROCESSING/);
  assert.match(h.els.videoDebugText1.textContent,/RUNNING/);
  const start = h.calls.filter(c => c.url.endsWith('/videos')).length;
  await h.nextTimer(); await h.flush();
  assert.ok(h.calls.filter(c => c.url.endsWith('/videos')).length > start);
  assert.equal(h.calls.filter(c => /\/real-videos\/scene[123]$/.test(c.url)).length,0);
});
