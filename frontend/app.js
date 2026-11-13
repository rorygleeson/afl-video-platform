const input = document.getElementById('selfie');
  const preview = document.getElementById('preview');
  const generate = document.getElementById('generate');
  const testApi = document.getElementById('testApi');
  const apiTestResult = document.getElementById('apiTestResult');
  const apiConnectionStatus = document.getElementById('apiConnectionStatus');
  const testGoogle = document.getElementById('testGoogle');
  const googleStatus = document.getElementById('googleStatus');
  const refresh = document.getElementById('refresh');
  const status = document.getElementById('status');
  const results = document.getElementById('results');
  const pollStatus = document.getElementById('pollStatus');
  const videoTestSection = document.getElementById('videoTestSection');
  const generateAllVideos = document.getElementById('generateAllVideos');
  const videoTestMessage = document.getElementById('videoTestMessage');
  const apiBaseUrl = window.AFL_CONFIG?.apiBaseUrl;
  let previewUrl, currentJobId, currentPhase = 'Initialising';
  let uploadInProgress = false;
  let uploadSubmissionUncertain = false;
  const POLL_INTERVAL_MS = 5000;
  const POLL_ERROR_BACKOFF_MS = 15000;
  const TERMINAL_STATUSES = new Set(['COMPLETED', 'PARTIAL_SUCCESS', 'FAILED']);
  let pollTimer = null;
  let pollGeneration = 0;
  let refreshInFlight = false;
  let pollingEnabled = false;
  let pollErrors = 0;
  let videoTimer = null;
  let videoBusy = false;
  let videoRefreshRequested = false;
  let realVideoTracking = false;
  const videoSubmittedScenes = new Set();
  const videoRetryInFlight = new Set();
  let videoBatchSubmitting = false;
  let availablePaidScenes = [];
  let sequentialBatchJobId = null;
  let lastVideoSnapshot = null;
  const videoSubmissionLog = new Map();
  let lastImageScenes = {};
  let preflightCheckedJobId = null;
  const SCENE_LABELS = ['Run-up', 'Winning kick', 'Celebration'];

  function clearPollTimer() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function showPollingMessage(message) {
    pollStatus.hidden = !message;
    pollStatus.textContent = message;
  }

  function stopVideoPoll() {
    if (videoTimer !== null) {
      clearTimeout(videoTimer);
      videoTimer = null;
    }
  }

  function resetVideoUi() {
    stopVideoPoll();
    videoRefreshRequested = false;
    realVideoTracking = false;
    videoSubmittedScenes.clear();
    videoRetryInFlight.clear();
    for (const n of [1, 2, 3]) {
      document.getElementById('retryVideoScene' + n).hidden = true;
      document.getElementById('retryVideoScene' + n).disabled = false;
      document.getElementById('retryVideoResult' + n).textContent = '';
    }
    videoBatchSubmitting = false;
    availablePaidScenes = [];
    sequentialBatchJobId = null;
    lastVideoSnapshot = null;
    generateAllVideos.hidden = true;
    generateAllVideos.disabled = true;
    generateAllVideos.textContent = 'Generate All 3 Videos (paid)';
    for (const n of [1, 2, 3]) {
      const button = document.getElementById('diagnoseScene' + n);
      button.hidden = true;
      button.disabled = false;
      document.getElementById('diagnoseResult' + n).textContent = '';
    }
    videoSubmissionLog.clear();
    preflightCheckedJobId = null;
    lastImageScenes = {};
    videoTestSection.hidden = true;
    videoTestMessage.textContent = '';
    document.getElementById('scene1PreflightStatus').textContent = 'Scene 1 check runs automatically when reference images are ready.';
    document.getElementById('scene1PaidStatus').textContent = 'MP4 generation: locked (not charged)';
    for (const n of [1, 2, 3]) {
      document.getElementById('scene' + n + 'PaidStatus').textContent = 'MP4 generation: locked (not charged)';
    }
    for (const n of [1, 2, 3]) {
      document.getElementById('videoStatus' + n).textContent = 'Real video not generated.';
      document.getElementById('videoPlayer' + n).replaceChildren();
    }
    document.getElementById('assembleFinal').disabled = true;
    document.getElementById('assembleFinal').hidden = true;
    document.getElementById('assemblyPanel').hidden = true;
    document.getElementById('assemblyStatus').textContent = '';
    document.getElementById('assemblyPlayer').replaceChildren();
    document.getElementById('downloadFinal').hidden = true;
  }

  // A single explicit confirmation authorises the three original paid scenes,
  // one at a time. The browser NEVER starts another paid scene until the
  // preceding MP4 is confirmed complete. Manual scene retries remain separate.
  // Closing/reloading the tab clears this consent; resuming requires a click.
  async function submitNextSequentialScene(jobId, snapshot) {
    if (jobId !== currentJobId || sequentialBatchJobId !== jobId ||
        videoBatchSubmitting || document.hidden) return;
    const videos = snapshot.videos || {};
    const approvals = snapshot.paidScenes || {};
    let next = null;
    for (const n of [1, 2, 3]) {
      const sceneId = 'scene' + n;
      const current = videos[sceneId] || {};
      // A failed, in-progress or unknown submission blocks the whole sequence.
      // A manually retried scene can resume progression only after COMPLETED.
      if (current.mode === 'REAL' || approvals[sceneId] !== true) {
        if (current.mode === 'REAL' && current.status === 'COMPLETED' &&
            current.url && !current.mock) continue;
        return;
      }
      if (videoSubmittedScenes.has(sceneId)) return;
      next = n;
      break;
    }
    if (next === null) return;
    const sceneId = 'scene' + next;
    // Lock before any await, including ambiguous network errors.
    videoBatchSubmitting = true;
    videoSubmittedScenes.add(sceneId);
    generateAllVideos.hidden = true;
    generateAllVideos.disabled = true;
    const submission = {
      status: 'Submitting paid request for Scene ' + next + '…',
      events: ['Sequential paid generation: Scene ' + next + ' of 3',
               'POST /jobs/' + jobId + '/real-videos/' + sceneId + ' started']
    };
    videoSubmissionLog.set(sceneId, submission);
    document.getElementById('scene' + next + 'PaidStatus').textContent = submission.status;
    document.getElementById('videoDebugText' + next).textContent = submission.events.join('\\n');
    try {
      const result = await api('/jobs/' + jobId + '/real-videos/' + sceneId, {
        method: 'POST',
        headers: {'Idempotency-Key': crypto.randomUUID()},
        body: JSON.stringify({confirmPaidScene: sceneId}),
      });
      submission.status = 'Paid request accepted (' + result.status + '). Checking AWS status…';
      submission.events.push('API response: ' + (result.status || 'ACCEPTED') +
                             ' at ' + new Date().toLocaleTimeString());
    } catch (error) {
      // Even a failed HTTP response may be ambiguous after a paid POST.
      // Never automatically resubmit; require an explicit status review.
      submission.status = 'Submission needs review: ' + error.message + ' Do not retry until checked.';
      submission.events.push('API error: ' + error.message);
      submission.events.push('Result may be uncertain; paid request will NOT be retried.');
      sequentialBatchJobId = null;
    } finally {
      videoBatchSubmitting = false;
      if (jobId === currentJobId) {
        document.getElementById('scene' + next + 'PaidStatus').textContent = submission.status;
        document.getElementById('videoDebugText' + next).textContent = submission.events.join('\\n');
        void refreshVideos();
      }
    }
  }

  async function refreshVideos() {
    if (!currentJobId || document.hidden || videoTestSection.hidden) return;
    if (videoBusy) {
      // A paid POST can complete while an older status GET is still running.
      // Remember to fetch fresh state instead of silently dropping that poll.
      videoRefreshRequested = true;
      return;
    }
    videoBusy = true;
    const jobId = currentJobId;
    try {
      // Read-only: never call POST /videos/sceneN, which remains a mock-only API.
      const data = await api('/jobs/' + jobId + '/videos');
      if (jobId !== currentJobId) return;
      const approvals = data.paidScenes || {};
      lastVideoSnapshot = data;
      availablePaidScenes = [1, 2, 3].filter(n => {
        const id = 'scene' + n;
        return approvals[id] === true && !videoSubmittedScenes.has(id) &&
          data.videos?.[id]?.mode !== 'REAL';
      });
      // The first ungenerated scene can start only after all predecessors
      // completed. A failed/in-flight/unknown scene never unlocks the next.
      const nextScene = availablePaidScenes[0];
      const priorReady = nextScene && [1, 2, 3].slice(0, nextScene - 1).every(n => {
        const prior = data.videos?.['scene' + n] || {};
        return prior.mode === 'REAL' && prior.status === 'COMPLETED' &&
          !!prior.url && !prior.mock;
      });
      const canStart = !!priorReady && !sequentialBatchJobId && !videoBatchSubmitting;
      generateAllVideos.hidden = !canStart;
      generateAllVideos.disabled = !canStart;
      generateAllVideos.textContent = availablePaidScenes.length === 3
        ? 'Generate All 3 Videos (paid, sequential)'
        : 'Generate ' + availablePaidScenes.length + ' Remaining Video' +
          (availablePaidScenes.length === 1 ? '' : 's') + ' (paid, sequential)';
      for (const n of [1, 2, 3]) {
        document.getElementById('videoPanel' + n).hidden = false;
      }
      for (const n of [1, 2, 3]) {
        const id = 'scene' + n;
        const real = data.videos?.[id] || {};
        const authorised = approvals[id] === true;
        const pendingAttempt = videoSubmissionLog.get(id);
        const retryButton = document.getElementById('retryVideoScene' + n);
        const mayRetry = real.retryAvailable === true && !videoRetryInFlight.has(id);
        retryButton.hidden = !mayRetry;
        retryButton.disabled = videoRetryInFlight.has(id);
        // The user-facing scene card displays only the current MP4 state.
        // Completed retries clear transient text; historical diagnostic detail
        // remains confined to the developer section below.
        if (real.status === 'COMPLETED') {
          document.getElementById('retryVideoResult' + n).textContent = '';
        }
        document.getElementById('diagnoseScene' + n).hidden =
          real.status !== 'FAILED' && real.status !== 'REVIEW_REQUIRED';
        document.getElementById('scene' + n + 'PaidStatus').textContent =
          real.mode !== 'REAL' && pendingAttempt
            ? pendingAttempt.status
            : real.mode === 'REAL'
            ? (real.status === 'REVIEW_REQUIRED'
                ? 'Generation needs review' +
                  (real.failureCode ? ' (' + real.failureCode.replaceAll('_', ' ') + ')' : '') +
                  '. Google may have charged; do not retry.'
                : real.status === 'SUBMISSION_UNKNOWN'
                  ? 'Checking original Google submission. Please do not retry or start another scene.'
                  : real.status === 'FAILED' && !mayRetry
                    ? 'Generation failed — Retry unavailable (' +
                      ({
                        MAX_ATTEMPTS_REACHED: '10 attempts reached',
                        ASSEMBLY_STARTED: 'assembly already started',
                        GOOGLE_FAILURE_NOT_CONFIRMED: 'Google failure unconfirmed',
                        WORKFLOW_REVIEW_REQUIRED: 'workflow needs review',
                      }[real.retryBlockedReason] || 'needs review') +
                      '). See Developer Diagnostics.'
                    : 'MP4 status: ' + real.status.replaceAll('_',' ').toLowerCase())
            : authorised ? 'Ready to generate — paid Google API' : 'MP4 generation: locked (not charged)';
        const lines = [
          'Job: ' + jobId,
          'Scene: ' + id,
          'Mode: ' + (real.mode || 'NOT_REQUESTED'),
          'Status: ' + (real.status || 'NOT_REQUESTED'),
          'Retry available: ' + (real.retryAvailable === true ? 'yes' : 'no'),
          'Retry blocked: ' + (real.retryBlockedReason || '—'),
          'Paid attempt: ' + (real.attemptNumber || 1) + ' of ' + (real.maxAttempts || 10),
          'Workflow: ' + (real.workflowStatus || '—'),
          'Google interaction: ' + (real.interactionId || '—'),
          'Request: ' + (real.requestId || '—'),
          'Error: ' + (real.failureCode || real.errorCode || '—'),
          'Submitted to Google (UTC): ' + (real.submittedAt || '—'),
          'Google terminal status: ' + (real.providerFinalStatus || '—'),
          'Original Google error: ' + (real.providerErrorCode || '—'),
          'Original error category: ' + (real.providerErrorCategory || '—'),
          'Original failure observed (UTC): ' + (real.providerFailureObservedAt || '—'),
          'Completed (UTC): ' + (real.completedAt || '—'),
          'Last checked: ' + new Date().toLocaleTimeString(),
          'Status checks are read-only. No paid retries.',
        ];
        if (pendingAttempt) {
          lines.push('— Paid submission attempt —');
          lines.push(...pendingAttempt.events);
          if (real.mode !== 'REAL') lines.push('Waiting for backend confirmation. No automatic retry.');
        }
        document.getElementById('videoDebugText' + n).textContent = lines.join('\n');
        const debugDetails = document.getElementById('videoDebugDetails' + n);
        // Automatically show diagnostics for submissions that have been
        // started or need reconciliation, including when resuming a job.
        // Never auto-close a panel the user explicitly expanded.
        if ((pendingAttempt && real.mode !== 'REAL') ||
            (real.mode === 'REAL' && real.status !== 'COMPLETED' &&
            real.status !== 'FAILED' && real.status !== 'NOT_REQUESTED')) {
          debugDetails.open = true;
        }
      }
      for (const n of [1, 2, 3]) {
        const item = data.videos?.['scene' + n] || {};
        const player = document.getElementById('videoPlayer' + n);
        const state = document.getElementById('videoStatus' + n);
        // The paid-status element already shows this scene's state; keep this
        // secondary status for internal updates but never display it twice.
        state.hidden = true;
        if (item.status === 'COMPLETED' && item.url && !item.mock) {
          state.textContent = 'Real MP4 completed.';
          // Polling regenerates short-lived S3 URLs. Do not replace a playing
          // video every five seconds while another scene is generating.
          if (!player.querySelector('video')) {
            const video = document.createElement('video');
            video.controls = true;
            video.setAttribute('controlsList', 'nodownload');
            video.preload = 'metadata';
            video.src = item.url;
            player.replaceChildren(video);
            player.dataset.url = item.url;
          }

        } else {
          // Never present mock JSON as a real MP4, but show genuine progress.
          state.textContent = item.mode === 'REAL'
            ? 'Real video: ' + item.status.replaceAll('_', ' ').toLowerCase() + '.'
            : 'Real video not generated.';
          player.replaceChildren();
          delete player.dataset.url;
        }
      }
      const realStates = [1, 2, 3].map(n => data.videos?.['scene' + n] || {});
      const assembly = data.assembly || {};
      const readyForAssembly = realStates.every(st =>
        st.mode === 'REAL' && st.status === 'COMPLETED' && !!st.url && !st.mock);
      const assemblyPanel = document.getElementById('assemblyPanel');
      const assembleButton = document.getElementById('assembleFinal');
      const assemblyStatus = document.getElementById('assemblyStatus');
      const finalPlayer = document.getElementById('assemblyPlayer');
      const finalDownload = document.getElementById('downloadFinal');
      assemblyPanel.hidden = !(readyForAssembly && assembly.enabled);
      assembleButton.hidden = assembly.status !== 'READY' || !readyForAssembly;
      assembleButton.disabled = assembly.status !== 'READY';
      const display = {QUEUED:'Assembly queued', PROCESSING:'Stitching the three scenes…',
        COMPLETED:'Final video ready', FAILED:'Assembly failed', REVIEW_REQUIRED:'Assembly needs review'};
      assemblyStatus.textContent = display[assembly.status] || '';
      if (assembly.status === 'COMPLETED' && assembly.url) {
        if (!finalPlayer.querySelector('video')) {
          const movie = document.createElement('video');
          movie.controls = true;
          movie.preload = 'metadata';
          movie.src = assembly.url;
          finalPlayer.replaceChildren(movie);
        }
        finalDownload.hidden = false;
      } else {
        finalPlayer.replaceChildren();
        finalDownload.hidden = true;
      }
      if (assembly.errorCode && ['FAILED','REVIEW_REQUIRED'].includes(assembly.status)) {
        assemblyStatus.textContent += ' (' + assembly.errorCode.replaceAll('_', ' ') + ')';
      }
      realVideoTracking = realStates.some(st => st.mode === 'REAL' &&
        !['COMPLETED', 'FAILED', 'CANCELLED', 'REVIEW_REQUIRED'].includes(st.status));
      // Continue only a previously user-confirmed sequential paid batch.
      // No automatic paid retries or unapproved scene generation.
      if (sequentialBatchJobId === jobId) {
        void submitNextSequentialScene(jobId, data);
      }
      // Poll current job status, including manual scene retries and diagnostics.
      stopVideoPoll();
      if (!document.hidden && jobId === currentJobId && !videoTestSection.hidden) {
        videoTimer = setTimeout(() => {
          videoTimer = null;
          void refreshVideos();
        }, POLL_INTERVAL_MS);
      }
      if (realStates.some(st => st.mode === 'REAL' &&
          ['COMPLETED', 'FAILED', 'REVIEW_REQUIRED'].includes(st.status))) {
        videoTestMessage.textContent = 'Video status updated — see each scene above.';
      }
    } catch (error) {
      if (jobId === currentJobId) {
        videoTestMessage.textContent = 'Video status unavailable: ' + error.message +
          '. Retrying status only; no paid request will be repeated.';
        // Status GET is safe. Never retry a paid POST.
        stopVideoPoll();
        if (!document.hidden) {
          videoTimer = setTimeout(() => {
            videoTimer = null;
            void refreshVideos();
          }, POLL_ERROR_BACKOFF_MS);
        }
      }
    } finally {
      videoBusy = false;
      if (videoRefreshRequested) {
        videoRefreshRequested = false;
        if (jobId === currentJobId) void refreshVideos();
      }
    }
  }

  function stopPolling() {
    pollingEnabled = false;
    pollGeneration++;
    clearPollTimer();
    showPollingMessage('');
  }

  function schedulePoll(delay = POLL_INTERVAL_MS) {
    clearPollTimer();
    if (!pollingEnabled || !currentJobId) return;
    if (document.hidden) {
      showPollingMessage('Auto-refresh paused while this tab is hidden.');
      return;
    }
    showPollingMessage(pollErrors
      ? 'Connection interrupted. Retrying job status automatically…'
      : 'Automatically checking job status every 5 seconds.');
    const generation = pollGeneration;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      if (pollingEnabled && generation === pollGeneration) {
        void refreshJob();
      }
    }, delay);
  }

  function startPolling() {
    clearPollTimer();
    pollingEnabled = true;
    pollErrors = 0;
    pollGeneration++;
    if (!document.hidden && !refreshInFlight) {
      void refreshJob();
    } else {
      schedulePoll();
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden && videoTimer !== null) {
      clearTimeout(videoTimer);
      videoTimer = null;
    }
    if (!document.hidden && currentJobId) void refreshVideos();
    if (!pollingEnabled) return;
    if (document.hidden) {
      clearPollTimer();
      showPollingMessage('Auto-refresh paused while this tab is hidden.');
    } else if (!refreshInFlight) {
      clearPollTimer();
      void refreshJob();
    }
  });

  status.textContent = apiBaseUrl ? '' : 'Backend configuration missing. Contact the developer.';
  generate.disabled = !apiBaseUrl;

  function setDiagnosticResult(element, working) {
    element.textContent = working ? 'Working!' : 'Failed';
    element.className = 'diagnostic-result ' + (working ? 'success' : 'failure');
  }

  testGoogle.addEventListener('click', async () => {
    testGoogle.disabled = true;
    googleStatus.textContent = '';
    googleStatus.className = 'diagnostic-result';
    try {
      const result = await api('/video/paid-google-auth-check');
      setDiagnosticResult(googleStatus, result.status === 'AUTHENTICATED');
    } catch (error) {
      setDiagnosticResult(googleStatus, false);
    } finally {
      testGoogle.disabled = false;
    }
  });

  function invalidateJobSelection() {
    if (uploadInProgress) return;
    if (uploadSubmissionUncertain) {
      uploadSubmissionUncertain = false;
      generate.disabled = !apiBaseUrl;
    }
    if (currentJobId) {
      stopPolling();
      currentJobId = null;
      generate.hidden = false;
      generate.disabled = !apiBaseUrl;
      refresh.hidden = true;
        resetVideoUi();
        results.replaceChildren();
      showStatus('Selection changed. Upload selfie and create a new job before generating.');
    }
  }

  document.getElementById('team').addEventListener('change', invalidateJobSelection);
  document.getElementById('scenario').addEventListener('change', invalidateJobSelection);
  document.getElementById('firstName').addEventListener('input', invalidateJobSelection);
  document.getElementById('lastName').addEventListener('input', invalidateJobSelection);

  input.addEventListener('change', () => {
    invalidateJobSelection();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const file = input.files[0];
    preview.hidden = !file;
    if (file) {
      previewUrl = URL.createObjectURL(file);
      preview.src = previewUrl;
    }
  });

  async function api(path, options = {}) {
    const key = document.getElementById('key').value.trim();
    if (!key) throw new Error('Enter your API key first.');
    const response = await fetch(apiBaseUrl + path, {
      ...options,
      headers: {
        'x-api-key': key,
        ...(options.body ? {'Content-Type': 'application/json'} : {}),
        ...(options.headers || {}),
      },
    });
    let data;
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) {
      // API Gateway returns {"message":"Forbidden"} for a missing/invalid
      // usage-plan key, and the generic text previously hid the real cause.
      if (response.status === 401 || response.status === 403) {
        throw new Error('API Gateway refused access (' + response.status +
          '). Check the x-api-key above. If it is correct, the API Gateway key or usage-plan configuration needs repair. No upload or paid generation was started.');
      }
      throw new Error(data.errorCode || data.error || data.message ||
        'API error ' + response.status);
    }
    return data;
  }

  function showStatus(message) {
    status.textContent = message;
  }

  function setPhase(phase) {
    currentPhase = phase;
    showStatus(phase + '…');
  }

  function describeError(error, phase) {
    // Browsers intentionally obscure many CORS failures as TypeError: Failed to fetch.
    // Preserve the failing stage so API requests can be distinguished from S3 uploads.
    const networkError = error instanceof TypeError;
    const detail = networkError
      ? 'Browser could not complete the network request (possibly CORS, DNS or connectivity).'
      : error.message;
    return 'Failed during: ' + phase + '\n' + detail;
  }

  // No test button is needed: validate access automatically on each upload,
  // before making any changes to an existing job or uploading the selfie.
  document.getElementById('key').addEventListener('input', () => {
    apiConnectionStatus.textContent = 'API access will be checked when you upload.';
  });

  testApi.addEventListener('click', async () => {
    testApi.disabled = true;
    apiTestResult.textContent = '';
    apiTestResult.className = 'diagnostic-result';
    try {
      await api('/scenarios');
      setDiagnosticResult(apiTestResult, true);
    } catch (error) {
      setDiagnosticResult(apiTestResult, false);
    } finally {
      testApi.disabled = false;
    }
  });

  async function refreshJob() {
    if (!currentJobId || refreshInFlight) return;
    if (document.hidden && pollingEnabled) {
      schedulePoll();
      return;
    }
    const requestedJobId = currentJobId;
    const generation = pollGeneration;
    refreshInFlight = true;
    clearPollTimer();
    refresh.disabled = true;
    try {
      const [record, sceneResults] = await Promise.all([
        api('/jobs/' + requestedJobId),
        api('/jobs/' + requestedJobId + '/images'),
      ]);
      if (requestedJobId !== currentJobId || generation !== pollGeneration) return;
      pollErrors = 0;
      const scenes = record.job.scenes || {};
      lastImageScenes = scenes;
      // Only show video controls after all three reference images are complete.
      const referenceImagesReady = ['scene1', 'scene2', 'scene3']
        .every(id => scenes[id]?.status === 'COMPLETED' && (sceneResults.images || []).some(image => image.sceneId === id && image.url));
      videoTestSection.hidden = !referenceImagesReady;
      if (!referenceImagesReady) {
        stopVideoPoll();
      }
      const completed = record.job.completedScenes ?? sceneResults.images.length;
      const failed = record.job.failedScenes ?? 0;
      showStatus('Reference images: ' + completed + '/3 completed' +
        (failed ? '; ' + failed + ' failed' : '') + '.');
      // Image cards keep their own status and retain thumbnails during polling.
      // Never reuse the image-job COMPLETED value to describe video generation.
      for (const n of [1,2,3]) {
        const id = 'scene' + n;
        let card = document.getElementById('imageCard' + n);
        if (!card) {
          card = document.createElement('div');
          card.id = 'imageCard' + n;
          card.className = 'reference-card';
          const heading = document.createElement('strong');
          heading.textContent = 'Scene ' + n + ' — ' + SCENE_LABELS[n-1];
          card.appendChild(heading);
          const progress = document.createElement('p');
          progress.id = 'imageState' + n;
          card.appendChild(progress);
          results.appendChild(card);
        }
        const progress = card.querySelector('p');
        const imageStatus = scenes[id]?.status;
        const retrying = imageStatus === 'RETRY_WAIT' ||
          (['SUBMITTING', 'GENERATING'].includes(imageStatus) && Number(scenes[id]?.retryCount) > 0);
        progress.className = 'reference-progress' +
          (imageStatus === 'COMPLETED' ? ' completed' :
           (imageStatus === 'GENERATING' || retrying) ? ' generating' : '');
        progress.textContent =
          imageStatus === 'COMPLETED' ? 'Completed' :
          imageStatus === 'FAILED' ? 'Failed — ' + (scenes[id].errorCode || 'retry required') :
          retrying ? 'Google busy — retrying automatically (' +
            Number(scenes[id]?.retryCount || 1) + '/' + Number(scenes[id]?.maxRetries || 7) + ')' :
          imageStatus === 'GENERATING' || imageStatus === 'SUBMITTING' ? 'Generating…' : 'Waiting';
        const image = (sceneResults.images || []).find(item => item.sceneId === id && item.url);
        if (image && !card.querySelector('img')) {
          const img = document.createElement('img');
          img.src = image.url;
          img.dataset.sceneId = id;
          img.alt = 'Reference image for scene ' + n;
          card.appendChild(img);
        }
      }
      if (referenceImagesReady) {
        void refreshVideos();
        // Run once per visible job. A manual recheck remains available if inputs change.
        if (preflightCheckedJobId !== requestedJobId) {
          preflightCheckedJobId = requestedJobId;
          void runScene1Preflight(requestedJobId);
        }
      }
      if (TERMINAL_STATUSES.has(record.job.status)) {
        stopPolling();
      } else if (record.job.status === 'GENERATING_IMAGES' && !pollingEnabled) {
        startPolling();
      }
    } catch (error) {
      if (requestedJobId !== currentJobId || generation !== pollGeneration) return;
      pollErrors++;
      if (pollingEnabled) {
        showPollingMessage('Status request failed (' + pollErrors +
          '). Retrying automatically; no new image generation will be started.');
      } else {
        showStatus('Could not refresh job: ' + error.message);
      }
    } finally {
      refreshInFlight = false;
      refresh.disabled = false;
      if (pollingEnabled && requestedJobId === currentJobId) {
        schedulePoll(pollErrors ? POLL_ERROR_BACKOFF_MS : POLL_INTERVAL_MS);
      }
    }
  }
  refresh.addEventListener('click', () => {
    void refreshJob();
    void refreshVideos();
  });


  generateAllVideos.addEventListener('click', async () => {
    if (!currentJobId || videoTestSection.hidden || generateAllVideos.hidden ||
        generateAllVideos.disabled || videoBatchSubmitting || sequentialBatchJobId) return;
    const jobId = currentJobId;
    const count = availablePaidScenes.length;
    if (!count || !lastVideoSnapshot) return;
    if (!window.confirm('Generate ' + count + ' paid Google video' +
        (count === 1 ? '' : 's') + ' sequentially? Scene 2 starts ONLY after ' +
        'Scene 1 completes, and Scene 3 ONLY after Scene 2 completes. ' +
        'Each scene is billed separately. Failed scenes require manual retry. ' +
        'Keep this tab open until all three scenes finish.')) return;
    sequentialBatchJobId = jobId;
    generateAllVideos.hidden = true;
    generateAllVideos.disabled = true;
    // One paid POST per confirmed, eligible scene. Status polling unlocks the
    // next scene only after its predecessor has a completed real MP4.
    await submitNextSequentialScene(jobId, lastVideoSnapshot);
  });

  // A retry is explicitly confirmed, charged separately and applies to only
  // one terminal failed video. Existing reference images and successful MP4s stay.
  for (const n of [1, 2, 3]) {
    const sceneId = 'scene' + n;
    const button = document.getElementById('retryVideoScene' + n);
    button.addEventListener('click', async () => {
      if (!currentJobId || button.hidden || button.disabled ||
          videoRetryInFlight.has(sceneId)) return;
      if (!window.confirm('Retry Scene ' + n + ' video only? This starts one NEW paid ' +
          'Google generation. Existing reference images and other video scenes will remain unchanged.')) return;
      const jobId = currentJobId;
      const output = document.getElementById('retryVideoResult' + n);
      videoRetryInFlight.add(sceneId);
      button.disabled = true;
      button.hidden = true;
      output.textContent = 'Submitting one paid retry for Scene ' + n + '…';
      try {
        const result = await api('/jobs/' + jobId + '/real-videos/' + sceneId + '/retry', {
          method: 'POST',
          headers: {'Idempotency-Key': crypto.randomUUID()},
          body: JSON.stringify({confirmRetryScene: sceneId}),
        });
        if (jobId === currentJobId) {
          output.textContent = 'Scene ' + n + ' retry requested (' +
            (result.status || 'ACCEPTED') + '). Checking progress…';
        }
      } catch (error) {
        if (jobId === currentJobId) {
          output.textContent = 'Retry status uncertain: ' + error.message +
            '. Check the existing scene before attempting again.';
        }
      } finally {
        videoRetryInFlight.delete(sceneId);
        if (jobId === currentJobId) void refreshVideos();
      }
    });
  }

  for (const n of [1, 2, 3]) {
    const button = document.getElementById('diagnoseScene' + n);
    button.addEventListener('click', async () => {
      if (!currentJobId || button.hidden || button.disabled) return;
      const requestedJob = currentJobId;
      const output = document.getElementById('diagnoseResult' + n);
      button.disabled = true;
      output.textContent = 'Checking existing Google interaction (read-only)…';
      try {
        const data = await api('/jobs/' + requestedJob + '/real-videos/scene' + n +
                               '/diagnostics');
        if (currentJobId !== requestedJob) return;
        const detail = data.diagnostic || {};
        const fmt = value => String(value || 'unknown').replaceAll('_', ' ').toLowerCase();
        const recorded = 'Recorded: ' + fmt(detail.recordedErrorCode);
        const evidence = [
          detail.interactionId ? 'Google interaction: ' + detail.interactionId : '',
          detail.submittedAt ? 'Submitted (UTC): ' + detail.submittedAt : '',
          detail.providerFinalStatus ? 'Google terminal status: ' + detail.providerFinalStatus : '',
          detail.providerFailureObservedAt ? 'Failure observed (UTC): ' + detail.providerFailureObservedAt : '',
        ].filter(Boolean).join('. ');
        if (detail.status === 'RECORDED_PROVIDER_FAILURE') {
          output.textContent = 'Original Google failure recorded when it occurred. ' +
            'Provider reason: ' + fmt(detail.errorCategory) +
            (detail.errorCode ? ' (' + detail.errorCode + ')' : '') +
            '. ' + recorded + '. ' + evidence +
            '. No additional Google lookup or paid request was made.';
        } else if (detail.status === 'READ_ONLY_DIAGNOSTIC') {
          output.textContent = 'Google status: ' + fmt(detail.providerStatus) +
            '. Provider reason: ' + fmt(detail.errorCategory) +
            (detail.errorCode ? ' (' + detail.errorCode + ')' : '') +
            '. ' + recorded + '.' +
            (detail.reason === 'GOOGLE_DID_NOT_SUPPLY_A_CLASSIFIABLE_REASON'
              ? ' Google supplied no classifiable reason.' : '') +
            '. ' + evidence + ' Read-only check; no new paid generation.';
        } else {
          const category = detail.errorCategory && detail.errorCategory !== 'UNSPECIFIED'
            ? ' Stored provider reason: ' + fmt(detail.errorCategory) + '.' : '';
          output.textContent = recorded + '.' + category +
            ' Google lookup unavailable: ' + fmt(detail.reason) +
            (detail.errorCode ? ' (' + detail.errorCode + ')' : '') +
            '. This does not mean the original request failed for the same reason. ' +
            evidence + ' No new paid generation.';
        }
      } catch (error) {
        if (currentJobId === requestedJob)
          output.textContent = 'Read-only diagnostic unavailable. No video was restarted.';
      } finally {
        button.disabled = false;
      }
    });
  }

  document.getElementById('assembleFinal').addEventListener('click', async () => {
    const button = document.getElementById('assembleFinal');
    if (!currentJobId || button.hidden || button.disabled) return;
    const jobId = currentJobId;
    button.disabled = true;
    button.hidden = true;
    document.getElementById('assemblyStatus').textContent = 'Requesting final video assembly…';
    try {
      await api('/jobs/' + jobId + '/assembly', {method:'POST'});
      if (jobId === currentJobId) void refreshVideos();
    } catch (error) {
      if (jobId === currentJobId) {
        document.getElementById('assemblyStatus').textContent =
          'Assembly request uncertain: ' + error.message + '. Checking existing job; no automatic retry.';
        void refreshVideos();
      }
    }
  });

  document.getElementById('downloadFinal').addEventListener('click', async () => {
    const button = document.getElementById('downloadFinal');
    const jobId = currentJobId;
    if (!jobId || button.hidden) return;
    button.disabled = true;
    try {
      const result = await api('/jobs/' + jobId + '/assembly');
      if (jobId !== currentJobId) return;
      if (result.assembly?.status !== 'COMPLETED' || !result.assembly.url)
        throw new Error('Final MP4 is not ready.');
      // S3's attachment-signed URL fixes the filename to afl-grand-final.mp4.
      // Download through the existing CORS-enabled read URL instead, so the
      // browser can apply a fresh local filename without changing S3 or the API.
      const fileResponse = await fetch(result.assembly.url);
      if (!fileResponse.ok) throw new Error('Unable to retrieve the final MP4.');
      if (jobId !== currentJobId) return;
      const blob = await fileResponse.blob();
      if (jobId !== currentJobId) return;
      const now = new Date();
      const stamp = [now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, '0'),
        String(now.getDate()).padStart(2, '0'),
        String(now.getHours()).padStart(2, '0'),
        String(now.getMinutes()).padStart(2, '0'),
        String(now.getSeconds()).padStart(2, '0')].join('');
      const objectUrl = URL.createObjectURL(blob);
      try {
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = 'afl-grand-final-' + stamp + '.mp4';
        link.style.display = 'none';
        document.body.appendChild(link);
        try {
          link.click();
        } finally {
          link.remove();
        }
      } finally {
        // Let the browser start saving before releasing the Blob URL.
        setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
      }
    } catch (error) {
      if (jobId === currentJobId)
        document.getElementById('assemblyStatus').textContent = 'Download failed: ' + error.message;
    } finally {
      button.disabled = false;
    }
  });

  async function runScene1Preflight(jobId) {
    if (!jobId || videoTestSection.hidden || jobId !== currentJobId) return;
    const button = document.getElementById('scene1Preflight');
    const status = document.getElementById('scene1PreflightStatus');
    button.disabled = true;
    status.textContent = 'Checking existing image and prompt in AWS — no video generation…';
    try {
      const result = await api('/jobs/' + jobId + '/video/scene1/preflight');
      if (jobId !== currentJobId) return;
      if (result.status !== 'READY' || result.googleSubmissionMade !== false) {
        throw new Error('Readiness check did not complete safely');
      }
      status.textContent = '✓ READY — Image (' +
        (result.imageBytes / 1048576).toFixed(2) +
        ' MB) and video prompt checked. No charge.';
      button.textContent = 'Recheck readiness (free)';
    } catch (error) {
      if (jobId === currentJobId) status.textContent = 'Preflight failed: ' + error.message;
    } finally {
      button.disabled = false;
    }
  }

  document.getElementById('scene1Preflight').addEventListener('click', () => {
    if (currentJobId) void runScene1Preflight(currentJobId);
  });

  generate.addEventListener('click', async () => {
    // Single-flight guard protects against rapid double taps, including
    // programmatic event dispatch while the button is disabled.
    if (uploadInProgress || uploadSubmissionUncertain || currentJobId) return;
    const file = input.files[0];
    if (!apiBaseUrl) return;
    if (!document.getElementById('key').value.trim()) {
      showStatus('Enter your API key first.');
      return;
    }
    if (!file) { showStatus('Choose a selfie first.'); return; }
    const firstName = document.getElementById('firstName').value.trim();
    const lastName = document.getElementById('lastName').value.trim();
    if (!firstName || !lastName) {
      showStatus('Enter both first name and surname before creating a job.'); return;
    }
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) ||
        file.size < 1 || file.size > 10 * 1024 * 1024) {
      showStatus('Choose a JPEG, PNG or WebP image up to 10 MB.');
      return;
    }
    uploadInProgress = true;
    generate.disabled = true;
    const formFields = ['key', 'scenario', 'team', 'firstName', 'lastName', 'selfie']
      .map(id => document.getElementById(id));
    for (const field of formFields) field.disabled = true;
    let jobCreated = false;
    let jobCreateRequestSent = false;
    try {
      // No S3 upload or paid generation before authentication succeeds.
      setPhase('Connecting to API Gateway');
      apiConnectionStatus.textContent = 'Checking API access…';
      await api('/scenarios');
      apiConnectionStatus.textContent = 'API connected.';
      setPhase('Requesting upload form from API Gateway');
      const signed = await api('/uploads', {
        method: 'POST',
        body: JSON.stringify({contentType: file.type, sizeBytes: file.size}),
      });
      const form = new FormData();
      for (const [field, value] of Object.entries(signed.upload.fields)) {
        form.append(field, value);
      }
      form.append('file', file);
      setPhase('Uploading selfie directly to private S3');
      const uploaded = await fetch(signed.upload.url, {method: 'POST', body: form});
      if (!uploaded.ok) throw new Error('S3 upload failed: ' + uploaded.status);
      setPhase('Creating DynamoDB job through API Gateway');
      jobCreateRequestSent = true;
      const created = await api('/jobs', {
        method: 'POST',
        headers: {'Idempotency-Key': crypto.randomUUID()},
        body: JSON.stringify({
          uploadKey: signed.uploadKey,
          scenarioId: document.getElementById('scenario').value,
          team: document.getElementById('team').value,
          firstName,
          lastName,
        }),
      });
      if (!/^[0-9a-f]{32}$/.test(created.job?.jobId || '')) {
        throw new Error('Job creation response missing a valid job ID. Check status before trying again.');
      }
      // Only one confirmed job can be selected. Do not enable a second upload.
      jobCreateRequestSent = false;
      jobCreated = true;
      stopPolling();
      resetVideoUi();
      currentJobId = created.job.jobId;
      results.replaceChildren();
      generate.hidden = true;
      refresh.hidden = true;
      videoTestSection.hidden = true;

      // This is the same existing server-side image workflow, submitted once
      // after successful S3 upload AND successful job creation.
      setPhase('Starting three Google reference images');
      showStatus('Selfie uploaded. Starting three reference images…');
      try {
        const result = await api('/jobs/' + currentJobId + '/generate', {method:'POST'});
        showStatus('Generating 3 reference images. Status: ' +
          (result.status || result.job?.status || 'STARTING') +
          '\\nStatus updates automatically below.');
      } catch (error) {
        // The POST may already have started work; never retry it blindly.
        showStatus('Selfie uploaded and job created, but image generation status is uncertain: ' +
          error.message + '\\nChecking the existing job; no second paid request will be submitted.');
      }
      // Existing three scene cards, status polling, images and video gate.
      startPolling();
    } catch (error) {
      uploadSubmissionUncertain = jobCreateRequestSent;
      apiConnectionStatus.textContent = /API Gateway refused access/.test(error.message)
        ? 'API access denied. Check x-api-key — no paid request was made.'
        : 'Selfie upload or job creation did not finish successfully.';
      showStatus(describeError(error, currentPhase) +
        (uploadSubmissionUncertain
          ? '\nJob creation response is uncertain. Do not submit again until status is checked.'
          : '\nReference image generation was not started.'));
    } finally {
      uploadInProgress = false;
      for (const field of formFields) field.disabled = false;
      generate.disabled = !apiBaseUrl || jobCreated || !!currentJobId || uploadSubmissionUncertain;
    }
  });
