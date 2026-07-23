/* chimney_viewer.js — Chimney Inspection module.
   Features:
   - .3tz/.zip model upload, CesiumJS 3D viewer
   - "Add Finding" defect-pin tool
   - Distance measurement tool
   - KML defect overlay upload
   - Drawing toolbar: Line / Rectangle / Polygon / Circle
     * Markings project onto chimney surface (multi-ray sampling)
     * Lines use depthFailMaterial so they stay visible on curved surfaces
   Fixes:
   - Markings now properly stick to chimney surface
   - Defect click correctly flies to defect position
   - Image download works correctly using toBlob
   - KML alignment bar removed
   - Observations panel hidden by default, opens via Defect List button
   - Camera now uses LOCAL SURFACE NORMAL for straight-on perpendicular view
     (works for flat roofs, sloped surfaces, and vertical chimney walls)
*/

const CHIM_SEVERITY_COLOR = {
  Minor:    Cesium.Color.fromCssColorString('#1f9d68'),
  Moderate: Cesium.Color.fromCssColorString('#b9821f'),
  Critical: Cesium.Color.fromCssColorString('#c94b42'),
};

let chimViewer   = null;
let chimTileset  = null;
let chimDefectMode  = false;
let chimMeasureMode = false;
let chimMeasurePoints  = [];
let chimMeasureEntities = [];
let chimPendingPosition = null;
let chimDefects = [];
let chimGroundHeight = 0;

// KML layers state
let kmlLayers = [];
let kmlSelectedFile = null;

// ── Drawing tool state ─────────────────────────────────────────────────────
let activeDrawTool  = null;
let drawPoints      = [];
let drawTempEntities = [];
let drawShapeEntity  = null;
let drawMouseHandler = null;

// ── Polyline appearance constants ──────────────────────────────────────────
const LINE_WIDTH      = 6;
const LINE_COLOR      = Cesium.Color.fromCssColorString('#ffffff');
const LINE_DEPTH_FAIL = Cesium.Color.fromCssColorString('#ffffffaa');

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str == null ? '' : String(str);
  return d.innerHTML;
}

function showHint(text, ms) {
  const el = document.getElementById('hint-toast');
  el.textContent = text;
  el.classList.add('open');
  if (ms) setTimeout(() => el.classList.remove('open'), ms);
}
function hideHint() { document.getElementById('hint-toast').classList.remove('open'); }

/* ── Upload flow (3D model) ─────────────────────────────────────────────── */

function setupUpload() {
  const box   = document.getElementById('upload-box');
  const input = document.getElementById('tileset-input');
  const status = document.getElementById('upload-status');
  if (!box) return;

  input.addEventListener('change', () => { if (input.files[0]) uploadTileset(input.files[0]); });

  ['dragenter','dragover'].forEach(evt =>
    box.addEventListener(evt, e => { e.preventDefault(); box.classList.add('drag'); }));
  ['dragleave','drop'].forEach(evt =>
    box.addEventListener(evt, e => { e.preventDefault(); box.classList.remove('drag'); }));
  box.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) uploadTileset(f); });

  async function uploadTileset(file) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['3tz','zip'].includes(ext)) {
      status.textContent = 'File must be a .3tz or .zip archive.';
      status.className = 'upload-status error'; return;
    }

    const track = document.getElementById('upload-progress-track');
    const fill  = document.getElementById('upload-progress-fill');
    const pct   = document.getElementById('upload-progress-pct');

    status.textContent = 'Uploading…';
    status.className = 'upload-status';
    track.classList.add('show');
    fill.classList.remove('indeterminate');
    fill.style.width = '0%';
    pct.textContent = '0%';

    const fd = new FormData();
    fd.append('tileset_file', file);

    await new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `/api/chimney-projects/${window.CHIM_PROJECT_ID}/tileset`);

      // Real upload-transfer percentage (file → server).
      xhr.upload.addEventListener('progress', (e) => {
        if (!e.lengthComputable) return;
        const p = Math.round((e.loaded / e.total) * 100);
        fill.style.width = p + '%';
        pct.textContent = `Uploading… ${p}%`;
      });

      // Upload finished — server is now extracting the archive. There's no
      // byte-level signal for that phase, so switch to an indeterminate bar
      // instead of freezing at 100%.
      xhr.upload.addEventListener('load', () => {
        status.textContent = 'Extracting the model…';
        fill.classList.add('indeterminate');
        pct.textContent = 'Extracting…';
      });

      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) {}
        if (xhr.status >= 200 && xhr.status < 300) {
          fill.classList.remove('indeterminate');
          fill.style.width = '100%';
          pct.textContent = '100%';
          status.textContent = 'Model ready — loading viewer…';
          status.className = 'upload-status ok';
          setTimeout(() => window.location.reload(), 500);
        } else {
          fill.classList.remove('indeterminate');
          track.classList.remove('show');
          status.textContent = data.error || 'Upload failed.';
          status.className = 'upload-status error';
        }
        resolve();
      };

      xhr.onerror = () => {
        fill.classList.remove('indeterminate');
        track.classList.remove('show');
        status.textContent = 'Network error — please try again.';
        status.className = 'upload-status error';
        resolve();
      };

      xhr.send(fd);
    });
  }
}

/* ── Viewer setup ─────────────────────────────────────────────────────────── */

function showModelLoadingOverlay(msg) {
  const el = document.getElementById('model-loading-overlay');
  if (!el) return;
  const label = document.getElementById('model-loading-label');
  if (label && msg) label.textContent = msg;
  el.classList.add('show');
}

function hideModelLoadingOverlay() {
  const el = document.getElementById('model-loading-overlay');
  if (el) el.classList.remove('show');
}

function fadeOutCoverSnapshot() {
  const img = document.getElementById('cover-snapshot');
  if (!img) return;
  img.style.opacity = '0';
  setTimeout(() => img.remove(), 550);
}

// The very first time an Admin session finishes loading a project that
// doesn't have a placeholder snapshot yet, quietly capture one and save
// it — every visit after this one (by anyone) will show it instantly
// instead of a blank container while the real model streams in behind it.
function maybeCaptureCoverSnapshot() {
  if (!window.CHIM_IS_ADMIN || window.CHIM_HAS_SNAPSHOT || !chimViewer || !chimTileset) return;
  window.CHIM_HAS_SNAPSHOT = true; // avoid firing twice even if this listener somehow re-runs
  try {
    chimViewer.scene.render();
    chimViewer.scene.canvas.toBlob((blob) => {
      if (!blob) return;
      const fd = new FormData();
      fd.append('snapshot', blob, 'cover.jpg');
      fetch(`/api/chimney-projects/${window.CHIM_PROJECT_ID}/cover-snapshot`, { method: 'POST', body: fd }).catch(() => {});
    }, 'image/jpeg', 0.85);
  } catch (e) { /* non-critical — worst case, no placeholder next visit */ }
}

let _refiningBadgeTimer = null;
function updateRefiningBadge(busy) {
  const el = document.getElementById('refining-badge');
  if (!el) return;
  // Don't show the small badge while the big "preparing" overlay is
  // already up for the initial load — avoids showing two loading
  // indicators at once.
  const overlay = document.getElementById('model-loading-overlay');
  if (overlay && overlay.classList.contains('show')) { el.classList.remove('show'); return; }
  clearTimeout(_refiningBadgeTimer);
  if (busy) {
    el.classList.add('show');
  } else {
    // Small debounce so it doesn't flash on/off for every single tile.
    _refiningBadgeTimer = setTimeout(() => el.classList.remove('show'), 250);
  }
}

// ── Resilient render loop ────────────────────────────────────────────────
// Replaces Cesium's default requestAnimationFrame loop, which stops
// rendering entirely (and shows a fatal red panel) the first time any
// single frame throws — even from something transient/internal to Cesium
// like one malformed tile. This version catches that per-frame error,
// logs it, and keeps going, so the viewer stays interactive instead of
// going dark. Only if errors happen on many consecutive frames (a truly
// stuck state, not a one-off) does it stop and tell the user plainly.
let _renderErrorStreak = 0;
let _lastRenderErrorToast = 0;
const RENDER_ERROR_STREAK_LIMIT = 120; // ~2s of continuous failure at 60fps

// ── Compass widget ───────────────────────────────────────────────────────
const COMPASS_16 = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];

function buildCompassTicks() {
  const ring = document.getElementById('chim-compass-ring');
  if (!ring || ring.dataset.built) return;
  ring.dataset.built = '1';
  const size = ring.offsetWidth || 118;
  const center = size / 2;
  const R = size * 0.37;
  const dirs = [
    [0, 'N', true], [22.5, 'NNE', false], [45, 'NE', true], [67.5, 'ENE', false],
    [90, 'E', true], [112.5, 'ESE', false], [135, 'SE', true], [157.5, 'SSE', false],
    [180, 'S', true], [202.5, 'SSW', false], [225, 'SW', true], [247.5, 'WSW', false],
    [270, 'W', true], [292.5, 'WNW', false], [315, 'NW', true], [337.5, 'NNW', false],
  ];
  dirs.forEach(([bearing, label, major]) => {
    const rad = bearing * Math.PI / 180;
    const x = center + R * Math.sin(rad);
    const y = center - R * Math.cos(rad);
    const el = document.createElement('div');
    el.className = 'chim-compass-tick' + (major ? ' major' : '');
    el.textContent = label;
    el.style.left = x + 'px';
    el.style.top = y + 'px';
    el.style.transform = 'translate(-50%,-50%)';
    ring.appendChild(el);
  });
}

// Position the compass just under the toolbar row (navbar + infobar,
// which contains Generate Report) rather than a guessed pixel offset —
// that row wraps onto two lines at narrow widths, so its real rendered
// height is read directly instead of hardcoding a value that would drift
// out of sync with it. Also shifts left of the findings panel whenever
// it's open, since that panel sits on the right edge and would otherwise
// sit underneath (and visually collide with) the compass.
// Hides the compass while a popup that would otherwise sit underneath/
// collide with it is open (the add-finding form, primarily) — restored
// whenever that popup closes.
function setCompassVisible(visible) {
  const compass = document.getElementById('chim-compass');
  if (compass) compass.style.display = visible ? '' : 'none';
}

// Position the compass just under the toolbar row (navbar + infobar,
// which contains Generate Report) rather than a guessed pixel offset —
// that row wraps onto two lines at narrow widths, so its real rendered
// height is read directly instead of hardcoding a value that would drift
// out of sync with it. Also shifts left of whichever right-side overlay
// (the findings panel, or the add/edit-defect form popup) is currently
// open, since either would otherwise sit underneath — and visually
// collide with — the compass. Checked by actual rendered position rather
// than assuming a particular open/close mechanism, since the two panels
// use different ones (display toggle vs. a CSS slide-in transform).
function positionCompass() {
  const compass = document.getElementById('chim-compass');
  const infobar = document.querySelector('.chim-infobar');
  if (!compass || !infobar) return;
  compass.style.top = (infobar.getBoundingClientRect().bottom + 14) + 'px';

  let rightOffset = 22;
  ['chim-toolbar-panel', 'defect-form'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const rect = el.getBoundingClientRect();
    // A closed panel is either display:none (zero-width rect) or slid
    // off-screen via a transform (its left edge is at/past the viewport
    // edge) — only count it if it's actually visible on screen.
    if (rect.width > 0 && rect.left < window.innerWidth) {
      rightOffset = Math.max(rightOffset, (window.innerWidth - rect.left) + 22);
    }
  });
  compass.style.right = rightOffset + 'px';
}

// Catches the compass overlap regardless of which code path opened or
// closed either panel — rather than needing every current and future
// open/close call site to remember to call positionCompass() itself.
function watchCompassOverlapPanels() {
  const targets = ['chim-toolbar-panel', 'defect-form']
    .map(id => document.getElementById(id))
    .filter(Boolean);
  if (!targets.length) return;
  const observer = new MutationObserver(() => {
    positionCompass();
    // .defect-form slides in via a CSS transition rather than an instant
    // display toggle — its bounding rect right when the class changes is
    // still mid-animation, so reposition again once that settles.
    setTimeout(positionCompass, 260);
  });
  targets.forEach(el => observer.observe(el, { attributes: true, attributeFilter: ['class', 'style'] }));
}

let _lastStableCompassDeg = 0;

// Needle points in the direction the camera is currently facing, against
// the FIXED ring of true compass directions — so turning the camera right
// visibly swings the needle right, and the readout below spells out
// exactly which of the 16 points (N, NNE, NE, … ) it's facing, with the
// bearing in degrees alongside it.
//
// This is deliberately computed from the camera's actual VIEW DIRECTION
// projected onto the local horizontal plane, rather than from Cesium's
// camera.heading property directly. heading becomes numerically unstable
// right at steep pitch (looking almost straight down/up) — exactly the
// close-up, near-perpendicular angles flyToDefect often lands on — which
// is what made the needle visibly jitter/spin every time a defect was
// clicked from the list. When the view is that close to vertical, the
// horizontal bearing is genuinely close to undefined, so instead of
// jittering it freezes on the last stable reading.
function updateCompass() {
  const needle = document.getElementById('chim-compass-needle');
  const readout = document.getElementById('chim-compass-readout');
  if (!needle || !readout || !chimViewer || chimViewer.isDestroyed()) return;

  const camera = chimViewer.camera;
  const enu = Cesium.Transforms.eastNorthUpToFixedFrame(camera.positionWC);
  const invEnu = Cesium.Matrix4.inverseTransformation(enu, new Cesium.Matrix4());
  const localDir = Cesium.Matrix4.multiplyByPointAsVector(invEnu, camera.directionWC, new Cesium.Cartesian3());

  let norm;
  const horizMag = Math.sqrt(localDir.x * localDir.x + localDir.y * localDir.y);
  if (horizMag > 0.02) {
    // atan2(east, north): 0° when facing due north, increasing clockwise
    // toward east — matches standard compass bearing convention.
    const bearingDeg = Cesium.Math.toDegrees(Math.atan2(localDir.x, localDir.y));
    norm = ((bearingDeg % 360) + 360) % 360;
    _lastStableCompassDeg = norm;
  } else {
    norm = _lastStableCompassDeg;
  }
  needle.style.transform = `rotate(${norm}deg)`;
  const idx = Math.round(norm / 22.5) % 16;
  readout.textContent = `${COMPASS_16[idx]} · ${Math.round(norm)}°`;
}

function startResilientRenderLoop() {
  let compassFrameSkip = 0;
  function tick() {
    if (!chimViewer || chimViewer.isDestroyed()) return;
    try {
      chimViewer.resize();
      chimViewer.render();
      _renderErrorStreak = 0;
    } catch (err) {
      _renderErrorStreak++;
      console.error('Cesium render error (frame skipped):', err);
      const now = Date.now();
      if (now - _lastRenderErrorToast > 4000) {
        _lastRenderErrorToast = now;
        showHint('A rendering hiccup occurred — the viewer will keep working, but let us know if something looks off.', 3200);
      }
      if (_renderErrorStreak >= RENDER_ERROR_STREAK_LIMIT) {
        showHint('The 3D viewer hit a persistent rendering problem and had to pause. Try refreshing the page.', 6000);
        return; // stop the loop for real — this is no longer a one-off
      }
    }
    // Cheap DOM update — updates the compass needle/readout roughly every
    // other frame, which is smooth enough for a gauge while cutting the
    // number of style/text writes in half.
    if ((compassFrameSkip++ % 2) === 0) {
      try { updateCompass(); } catch (_) {}
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

async function initViewer() {
  Cesium.Ion.defaultAccessToken = undefined;
  buildCompassTicks();
  positionCompass();
  watchCompassOverlapPanels();
  window.addEventListener('resize', positionCompass);

  chimViewer = new Cesium.Viewer('cesiumContainer', {
    imageryProvider: false, baseLayerPicker: false, geocoder: false,
    homeButton: false, sceneModePicker: false, navigationHelpButton: false,
    animation: false, timeline: false, infoBox: false,
    selectionIndicator: false, fullscreenButton: true,
    // Without this, the WebGL drawing buffer clears itself right after
    // compositing to the screen — so capturing the canvas afterward (for
    // defect snapshots / the PDF cover image) can come out black,
    // especially now that requestRenderMode means frames aren't redrawn
    // continuously. This keeps the buffer around so captures are reliable.
    contextOptions: { webgl: { preserveDrawingBuffer: true } },
    // We run our own render loop below instead of Cesium's built-in one.
    // Cesium's default loop wraps each frame in a try/catch that, on ANY
    // uncaught error (even a single bad tile deep in its own internals),
    // stops the loop for good and shows the big red "Rendering has
    // stopped" panel — killing the whole viewer over what's often a
    // one-frame hiccup. Our loop below catches and skips the bad frame
    // instead, so the viewer keeps working.
    useDefaultRenderLoop: false,
  });

  chimViewer.imageryLayers.removeAll();
  chimViewer.scene.globe.show = false;
  chimViewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#1b1e24');
  chimViewer.scene.skyAtmosphere.show = false;
  if (chimViewer.scene.sun) chimViewer.scene.sun.show = false;
  if (chimViewer.scene.moon) chimViewer.scene.moon.show = false;
  if (chimViewer.scene.skyBox) chimViewer.scene.skyBox.show = false;

  // Only re-render the frame when something actually changes (camera move,
  // tile load, entity update) instead of Cesium's default of redrawing
  // every single frame continuously. Cesium still triggers a render
  // automatically on camera movement/tile updates, so nothing looks
  // different — it just stops burning GPU cycles redrawing an unchanged
  // scene, which is what was showing up as constant "re-rendering".
  chimViewer.scene.requestRenderMode = true;
  chimViewer.scene.maximumRenderTimeChange = Infinity;

  // Remove the Cesium ion credit/watermark from the viewport entirely.
  if (chimViewer.cesiumWidget && chimViewer.cesiumWidget.creditContainer) {
    chimViewer.cesiumWidget.creditContainer.style.display = 'none';
  }
  const creditEl = chimViewer.container.querySelector('.cesium-viewer-bottom, .cesium-credit-lightbox-overlay');
  if (creditEl) creditEl.style.display = 'none';

  // Belt-and-braces: some Cesium internals call showErrorPanel() directly
  // (not just the default render loop), which would still throw up the big
  // red overlay even with useDefaultRenderLoop off. Make it a no-op and
  // route the same info to our own quieter toast/console instead.
  if (chimViewer.cesiumWidget) {
    chimViewer.cesiumWidget.showErrorPanel = function(title, message, error) {
      console.error('Cesium error (panel suppressed):', title, message, error);
    };
  }
  startResilientRenderLoop();

  try {
    chimTileset = await Cesium.Cesium3DTileset.fromUrl(window.CHIM_TILESET_URL);

    // NOTE: an earlier pass here pushed these settings too far toward raw
    // quality (maximumScreenSpaceError:2, both dynamic/foveated error OFF,
    // progressiveResolutionHeightFraction:1, a 3GB+1GB cache) — that traded
    // away too much smoothness for sharpness and is what made the viewer
    // feel laggy. This is a more balanced set: still noticeably sharper
    // and less "poppy" than Cesium's defaults, but tuned to stay smooth.
    //  - maximumScreenSpaceError: 8 (Cesium's default is 16). Requests
    //    meaningfully higher detail than default without demanding the
    //    near-maximum detail level 2 did.
    //  - dynamicScreenSpaceError / foveatedScreenSpaceError: back ON.
    //    These are real performance optimizations (lower detail toward the
    //    screen edges / periphery) — turning them off was a big part of
    //    the slowdown. The mild edge softening they cause is a fair trade
    //    for staying smooth.
    //  - progressiveResolutionHeightFraction: 0.5 (Cesium default 0.3).
    //    Still shows a quick low-res pass during camera moves rather than
    //    stalling the frame waiting for full detail, just a less extreme
    //    one than default.
    //  - cacheBytes/maximumCacheOverflowBytes: ~1.5GB / 512MB — enough
    //    headroom that re-visited tiles are reused instead of re-fetched
    //    on zoom in/out, without over-committing memory on the UAT
    //    server or lower-end client machines.
    //  - preloadWhenHidden keeps tiles warm in the cache even if briefly
    //    off-screen during a camera move, instead of discarding them.
    chimTileset.maximumScreenSpaceError = 8;
    chimTileset.dynamicScreenSpaceError = true;
    chimTileset.dynamicScreenSpaceErrorDensity = 0.00278;
    chimTileset.dynamicScreenSpaceErrorFactor = 4;
    chimTileset.foveatedScreenSpaceError = true;
    chimTileset.progressiveResolutionHeightFraction = 0.5;
    chimTileset.cacheBytes = 1024 * 1024 * 1024 * 2.5;         // ~2.5GB resident cache
    chimTileset.maximumCacheOverflowBytes = 1024 * 1024 * 1024; // ~1GB overflow before eviction
    chimTileset.preloadWhenHidden = true;
    chimTileset.preferLeaves = true;

    chimViewer.scene.primitives.add(chimTileset);
    await chimViewer.zoomTo(chimTileset);

    const hasPlaceholder = !!document.getElementById('cover-snapshot');

    // Keep a full-screen "preparing" overlay up until every tile needed
    // for the opening view has actually finished streaming in, so the
    // reveal is a single already-rendered model rather than a live
    // sharpening animation. Falls back to a timeout in case a very large
    // tileset never quite reaches "all tiles loaded" while idle.
    // Skipped entirely when a placeholder snapshot is already covering
    // the view — showing a spinner on top of an "already there"-looking
    // image would undercut the whole point of having one.
    if (!hasPlaceholder) showModelLoadingOverlay('Preparing high-resolution view…');
    let settleTimer = setTimeout(hideModelLoadingOverlay, 20000);
    const onAllTilesLoaded = () => {
      clearTimeout(settleTimer);
      hideModelLoadingOverlay();
      fadeOutCoverSnapshot();
      maybeCaptureCoverSnapshot();
      chimTileset.allTilesLoaded.removeEventListener(onAllTilesLoaded);
      // Ground level, model centre, and structure height all depend on
      // ray-casting against the model's actual geometry — re-run them now
      // that real detail has streamed in, rather than trusting only the
      // immediate measurement taken below at init time (when tiles are
      // very likely still coarse placeholders, or haven't rendered at
      // all yet). Re-render the defect list afterward so any displayed
      // "height from ground" values pick up the refined number too.
      computeGroundHeight();
      reportModelCenter();
      reportStructureHeight();
      renderDefectList();
    };
    chimTileset.allTilesLoaded.addEventListener(onAllTilesLoaded);

    // While panning/zooming later on, any brief re-streaming shows as a
    // small non-blocking "Refining detail…" badge instead of an
    // unexplained flicker — the model stays interactive the whole time.
    chimTileset.loadProgress.addEventListener((numberOfPendingRequests, numberOfTilesProcessing) => {
      updateRefiningBadge(numberOfPendingRequests + numberOfTilesProcessing > 0);
    });
  } catch (err) {
    showHint('Could not load the 3D model — the archive may be missing required tile files.');
    console.error(err); return;
  }

  document.getElementById('chim-toolbar').style.display = 'flex';
  const drawTb = document.getElementById('draw-toolbar');
  if (drawTb) drawTb.classList.add('visible');

  chimViewer.screenSpaceEventHandler.setInputAction(onStageClick, Cesium.ScreenSpaceEventType.LEFT_CLICK);
  chimViewer.screenSpaceEventHandler.setInputAction(onStageRightClick, Cesium.ScreenSpaceEventType.RIGHT_CLICK);

  computeGroundHeight();
  reportModelCenter();
  reportStructureHeight();
  await loadDefects();
}

/* ── Report the tileset's real geographic centre ────────────────────────────
   The manually-entered project lat/lon is just the asset's approximate
   registered location and can be noticeably off from where the tileset is
   actually geo-referenced. Using it as the compass reference is what made
   defect "Direction" come out wrong.

   A first fix used the tileset's bounding-SPHERE centre as the reference
   point instead — better, but still biased: a bounding sphere is fit to the
   extreme corners of the model, so if the captured mesh/point-cloud has any
   asymmetry (denser coverage on one side, a stray artifact, uneven base
   footprint) the sphere's centre gets pulled off the chimney's true vertical
   axis. That's exactly the kind of thing that makes EVERY defect end up
   biased toward the same compass direction (e.g. always "South-West"),
   because they're all being measured from the same off-axis point.

   Fix: sample the model's actual footprint instead of trusting the bounding
   sphere. We drop a grid of vertical rays down through the model (inside its
   circular footprint) and average the lon/lat of every point where a ray
   actually hits the mesh. That average is a true "centre of mass" of the
   chimney's footprint and sits much closer to its real axis, regardless of
   asymmetries in the captured geometry. Falls back to the bounding-sphere
   centre if for some reason too few rays connect (e.g. very sparse tiles). */
function computeFootprintCenter() {
  if (!chimTileset || !chimViewer) return null;
  try {
    const sphere = chimTileset.boundingSphere;
    const center = sphere.center;
    const enu = Cesium.Transforms.eastNorthUpToFixedFrame(center);
    const worldUp = Cesium.Cartesian3.normalize(
      Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(0, 0, 1), new Cesium.Cartesian3()),
      new Cesium.Cartesian3());
    const worldE = Cesium.Cartesian3.normalize(
      Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(1, 0, 0), new Cesium.Cartesian3()),
      new Cesium.Cartesian3());
    const worldN = Cesium.Cartesian3.normalize(
      Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(0, 1, 0), new Cesium.Cartesian3()),
      new Cesium.Cartesian3());
    const downDir = Cesium.Cartesian3.negate(worldUp, new Cesium.Cartesian3());

    // Sampling radius: the tileset's own oriented bounding box, not the
    // bounding SPHERE — for a tall, thin chimney the sphere's radius is
    // roughly half the model's total HEIGHT (same reason it's avoided
    // for ground detection and settling elsewhere in this file), so using
    // it here made the 9x9 grid sample a horizontal area far bigger than
    // the chimney's actual footprint. Most sample points landed
    // completely outside the real model and missed it, silently failing
    // (falling back to the raw, potentially terrain-skewed bounding-sphere
    // centre) far more often than intended.
    let R = sphere.radius * 0.4; // sane fallback if no OBB is available
    const obb = chimTileset.root && chimTileset.root.boundingVolume && chimTileset.root.boundingVolume.boundingVolume;
    if (obb && obb.halfAxes) {
      const cols = [0, 1, 2].map(i => Cesium.Matrix3.getColumn(obb.halfAxes, i, new Cesium.Cartesian3()));
      let vertIdx = 0, vertDot = -1;
      cols.forEach((c, i) => {
        const n = Cesium.Cartesian3.normalize(Cesium.Cartesian3.clone(c), new Cesium.Cartesian3());
        const d = Math.abs(Cesium.Cartesian3.dot(n, worldUp));
        if (d > vertDot) { vertDot = d; vertIdx = i; }
      });
      const horizIdx = [0, 1, 2].filter(i => i !== vertIdx);
      const h0 = Cesium.Cartesian3.magnitude(cols[horizIdx[0]]);
      const h1 = Cesium.Cartesian3.magnitude(cols[horizIdx[1]]);
      R = Math.max(h0, h1); // the wider of the two horizontal half-extents, so the circular sample grid comfortably covers the whole footprint
    }

    const GRID = 9; // 9x9 grid, clipped to a circle so we sample the whole footprint evenly
    const hitSum = new Cesium.Cartesian3(0, 0, 0);
    let hitCount = 0;

    for (let i = 0; i < GRID; i++) {
      for (let j = 0; j < GRID; j++) {
        const u = (i / (GRID - 1)) * 2 - 1; // -1..1
        const v = (j / (GRID - 1)) * 2 - 1;
        if (u * u + v * v > 1) continue;    // stay inside the circular footprint

        const eOff = Cesium.Cartesian3.multiplyByScalar(worldE, u * R, new Cesium.Cartesian3());
        const nOff = Cesium.Cartesian3.multiplyByScalar(worldN, v * R, new Cesium.Cartesian3());
        const basePoint = Cesium.Cartesian3.add(center, Cesium.Cartesian3.add(eOff, nOff, new Cesium.Cartesian3()), new Cesium.Cartesian3());
        const rayOrigin = Cesium.Cartesian3.add(basePoint, Cesium.Cartesian3.multiplyByScalar(worldUp, R * 2.5, new Cesium.Cartesian3()), new Cesium.Cartesian3());
        const ray = new Cesium.Ray(rayOrigin, downDir);

        let picks = [];
        try { picks = chimViewer.scene.drillPickFromRay(ray, 4) || []; } catch (_) { picks = []; }
        let nearest = null, nearestDist = Infinity;
        for (const p of picks) {
          if (!Cesium.defined(p.position)) continue;
          const d = Cesium.Cartesian3.distance(p.position, rayOrigin);
          if (d < nearestDist) { nearestDist = d; nearest = p.position; }
        }
        if (nearest) {
          Cesium.Cartesian3.add(hitSum, nearest, hitSum);
          hitCount++;
        }
      }
    }

    if (hitCount < 6) return null; // not enough coverage to trust the average — fall back

    const avg = Cesium.Cartesian3.divideByScalar(hitSum, hitCount, new Cesium.Cartesian3());
    const carto = Cesium.Cartographic.fromCartesian(avg);
    return {
      lat: Cesium.Math.toDegrees(carto.latitude),
      lng: Cesium.Math.toDegrees(carto.longitude),
    };
  } catch (e) {
    console.warn('Footprint-centre sampling failed, will fall back to bounding-sphere centre.', e);
    return null;
  }
}

function reportModelCenter() {
  if (!chimTileset) return;
  try {
    let lat, lng;
    const footprint = computeFootprintCenter();
    if (footprint) {
      lat = footprint.lat;
      lng = footprint.lng;
    } else {
      const center = chimTileset.boundingSphere.center;
      const carto  = Cesium.Cartographic.fromCartesian(center);
      lat = Cesium.Math.toDegrees(carto.latitude);
      lng = Cesium.Math.toDegrees(carto.longitude);
    }
    window.CHIM_MODEL_CENTER_LAT = lat;
    window.CHIM_MODEL_CENTER_LNG = lng;
    fetch(`/api/chimney-projects/${window.CHIM_PROJECT_ID}/model-center`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat, lng }),
    }).catch(() => {});
  } catch (e) {
    console.warn('Could not determine model centre for direction calculations.', e);
  }
}

// Structure height = top of the model's real geometric extent (from its
// oriented bounding box, same technique used for ground detection and
// settling on the ground — accurate for a tall thin shape, unlike the
// bounding sphere) minus the already-detected ground level. Reported once
// per load so the Project Timeline card can show it automatically without
// anyone having to measure and type it in by hand.
function reportStructureHeight() {
  if (!chimTileset || !window.CHIM_PROJECT_ID) return;
  const ellipsoid = Cesium.Ellipsoid.WGS84;

  // OBB-based extent as a hard ceiling/fallback only (see computeGroundHeight
  // for the same pattern on the ground side) — never used directly as the
  // reported height on its own, because a single stray/noisy vertex near
  // the top (common right at a chimney's rim, where drone coverage is
  // often sparser) pulls the WHOLE bounding box up with it. That's what
  // was making the auto-measured total height come out taller than a
  // shape a user actually draws on the visible top edge.
  let obbTop = null;
  try {
    const obb = chimTileset.root && chimTileset.root.boundingVolume && chimTileset.root.boundingVolume.boundingVolume;
    if (obb && obb.halfAxes) {
      const c = obb.center;
      const upC = ellipsoid.geodeticSurfaceNormal(c, new Cesium.Cartesian3());
      const cols = [0, 1, 2].map(i => Cesium.Matrix3.getColumn(obb.halfAxes, i, new Cesium.Cartesian3()));
      const halfVert = cols.reduce((sum, col) => sum + Math.abs(Cesium.Cartesian3.dot(upC, col)), 0);
      const carto = Cesium.Cartographic.fromCartesian(c);
      obbTop = carto.height + halfVert;
    } else {
      const sphere = chimTileset.boundingSphere;
      const carto = Cesium.Cartographic.fromCartesian(sphere.center);
      obbTop = carto.height + sphere.radius;
    }
  } catch (_) { /* leave obbTop null */ }

  let topHeight = null;
  try {
    const sphere = chimTileset.boundingSphere;
    const center = sphere.center;
    const up = ellipsoid.geodeticSurfaceNormal(center, new Cesium.Cartesian3());

    let east, north, halfE, halfN;
    const obb = chimTileset.root && chimTileset.root.boundingVolume && chimTileset.root.boundingVolume.boundingVolume;
    if (obb && obb.halfAxes) {
      const cols = [0, 1, 2].map(i => Cesium.Matrix3.getColumn(obb.halfAxes, i, new Cesium.Cartesian3()));
      let vertIdx = 0, vertDot = -1;
      cols.forEach((c, i) => {
        const n = Cesium.Cartesian3.normalize(Cesium.Cartesian3.clone(c), new Cesium.Cartesian3());
        const d = Math.abs(Cesium.Cartesian3.dot(n, up));
        if (d > vertDot) { vertDot = d; vertIdx = i; }
      });
      const horizIdx = [0, 1, 2].filter(i => i !== vertIdx);
      east  = cols[horizIdx[0]];
      north = cols[horizIdx[1]];
      halfE = Cesium.Cartesian3.magnitude(east)  * 0.7;
      halfN = Cesium.Cartesian3.magnitude(north) * 0.7;
      Cesium.Cartesian3.normalize(east, east);
      Cesium.Cartesian3.normalize(north, north);
    } else {
      const enu = Cesium.Transforms.eastNorthUpToFixedFrame(center);
      east  = Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(1, 0, 0), new Cesium.Cartesian3());
      north = Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(0, 1, 0), new Cesium.Cartesian3());
      halfE = halfN = sphere.radius * 0.4;
    }

    // Sample near the PERIMETER, not a grid across the whole footprint.
    // A chimney is typically a hollow tube — the solid material at the
    // very top is only a thin rim right at the edge of the footprint,
    // with an open flue in the middle. A grid spanning the whole
    // footprint (like computeGroundHeight uses, which is appropriate
    // there since the base usually IS solid across its footprint) mostly
    // falls straight through that opening and registers whatever's deep
    // inside the tube or at the bottom instead — badly skewing the
    // result toward something far below the true rim height. Sampling a
    // ring at several radii near the edge specifically targets where the
    // actual wall material is.
    const heights = [];
    const ringSamples = 16;
    const radialFractions = [0.98, 0.9, 0.8];
    for (let k = 0; k < ringSamples; k++) {
      const theta = (k / ringSamples) * Math.PI * 2;
      for (const rFrac of radialFractions) {
        const offset = Cesium.Cartesian3.add(
          Cesium.Cartesian3.multiplyByScalar(east, Math.cos(theta) * halfE * rFrac, new Cesium.Cartesian3()),
          Cesium.Cartesian3.multiplyByScalar(north, Math.sin(theta) * halfN * rFrac, new Cesium.Cartesian3()),
          new Cesium.Cartesian3()
        );
        const samplePoint = Cesium.Cartesian3.add(center, offset, new Cesium.Cartesian3());
        const rayOrigin = Cesium.Cartesian3.add(
          samplePoint,
          Cesium.Cartesian3.multiplyByScalar(up, sphere.radius * 3, new Cesium.Cartesian3()),
          new Cesium.Cartesian3()
        );
        const ray = new Cesium.Ray(rayOrigin, Cesium.Cartesian3.negate(up, new Cesium.Cartesian3()));
        let hits = [];
        try { hits = chimViewer.scene.drillPickFromRay(ray, 32) || []; } catch (_) {}
        // First hit along a downward ray from above = the topmost point
        // at that horizontal location.
        let highest = null;
        hits.forEach(hit => {
          if (Cesium.defined(hit.position)) {
            const h = Cesium.Cartographic.fromCartesian(hit.position).height;
            if (highest === null || h > highest) highest = h;
          }
        });
        if (highest !== null) heights.push(highest);
      }
    }

    if (heights.length >= 3) {
      heights.sort((a, b) => a - b);
      // 85th percentile, not the strict median — every sample here is
      // already concentrated near the rim (unlike computeGroundHeight's
      // whole-footprint grid, where the median is the right choice), so a
      // successful hit is trustworthy and we want the highest reliable
      // one rather than being dragged down by rays that slipped through
      // a small gap in the scanned rim. Still rejects a genuine one-off
      // noise spike at the very top of the sorted list.
      const idx = Math.min(heights.length - 1, Math.floor(heights.length * 0.85));
      topHeight = heights[idx];
    } else if (heights.length > 0) {
      topHeight = Math.max(...heights);
    }
  } catch (e) {
    console.warn('Structure top detection failed, falling back to bounding-box estimate.', e);
  }

  if (topHeight === null) topHeight = obbTop;
  if (topHeight === null) return;
  // Clamp against the OBB ceiling — the median result can never
  // legitimately exceed it (nothing in the model sits above the OBB's own
  // extent), so this only ever guards against a logic error rather than
  // changing typical results.
  if (obbTop !== null) topHeight = Math.min(topHeight, obbTop);

  const heightM = topHeight - chimGroundHeight;
  if (!isFinite(heightM) || heightM <= 0) return;
  fetch(`/api/chimney-projects/${window.CHIM_PROJECT_ID}/structure-height`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ height_m: heightM }),
  }).catch(() => {});
}

function computeGroundHeight() {
  chimGroundHeight = 0;
  if (!chimTileset || !chimViewer) return;
  const ellipsoid = Cesium.Ellipsoid.WGS84;

  // The oriented bounding box's own bottom edge is a hard mathematical
  // floor: by definition, nothing in the model's geometry can sit below
  // it, so the true ground (which the model rests on) can never be below
  // this either. Computed first, unconditionally, as a safety clamp for
  // the ray-cast result below — and as the fallback on its own if
  // ray-casting doesn't turn up anything usable.
  let obbBottom = null;
  try {
    const obb = chimTileset.root && chimTileset.root.boundingVolume && chimTileset.root.boundingVolume.boundingVolume;
    if (obb && obb.halfAxes) {
      const c = obb.center;
      const upC = ellipsoid.geodeticSurfaceNormal(c, new Cesium.Cartesian3());
      const cols = [0, 1, 2].map(i => Cesium.Matrix3.getColumn(obb.halfAxes, i, new Cesium.Cartesian3()));
      const halfVert = cols.reduce((sum, col) => sum + Math.abs(Cesium.Cartesian3.dot(upC, col)), 0);
      const carto = Cesium.Cartographic.fromCartesian(c);
      obbBottom = carto.height - halfVert;
    } else {
      const sphere = chimTileset.boundingSphere;
      const carto = Cesium.Cartographic.fromCartesian(sphere.center);
      obbBottom = carto.height - sphere.radius; // sphere overestimates for a tall thin shape — coarse fallback only
    }
  } catch (_) { /* leave obbBottom null, handled below */ }

  try {
    const sphere = chimTileset.boundingSphere;
    const center = sphere.center;
    const up = ellipsoid.geodeticSurfaceNormal(center, new Cesium.Cartesian3());

    // Horizontal footprint to sample across, taken from the tileset's own
    // oriented bounding box when available (accurate for an elongated,
    // possibly-rotated chimney) rather than assuming a fixed size.
    let east, north, halfE, halfN;
    const obb = chimTileset.root && chimTileset.root.boundingVolume && chimTileset.root.boundingVolume.boundingVolume;
    if (obb && obb.halfAxes) {
      const cols = [0, 1, 2].map(i => Cesium.Matrix3.getColumn(obb.halfAxes, i, new Cesium.Cartesian3()));
      let vertIdx = 0, vertDot = -1;
      cols.forEach((c, i) => {
        const n = Cesium.Cartesian3.normalize(Cesium.Cartesian3.clone(c), new Cesium.Cartesian3());
        const d = Math.abs(Cesium.Cartesian3.dot(n, up));
        if (d > vertDot) { vertDot = d; vertIdx = i; }
      });
      const horizIdx = [0, 1, 2].filter(i => i !== vertIdx);
      east  = cols[horizIdx[0]];
      north = cols[horizIdx[1]];
      halfE = Cesium.Cartesian3.magnitude(east)  * 0.7;
      halfN = Cesium.Cartesian3.magnitude(north) * 0.7;
      Cesium.Cartesian3.normalize(east, east);
      Cesium.Cartesian3.normalize(north, north);
    } else {
      const enu = Cesium.Transforms.eastNorthUpToFixedFrame(center);
      east  = Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(1, 0, 0), new Cesium.Cartesian3());
      north = Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(0, 1, 0), new Cesium.Cartesian3());
      halfE = halfN = sphere.radius * 0.4;
    }

    // Sample a grid of downward rays across the footprint instead of a
    // single ray through the exact center. A chimney is typically a
    // hollow tube, so the center alone can pass straight through the
    // opening and miss all solid material — and any single ray is also
    // vulnerable to landing on one noisy/outlier point near the base from
    // the photogrammetry mesh. Taking the MEDIAN across several samples
    // (instead of the bare minimum of one ray) rejects that kind of
    // one-off error, which is what was making the reported "ground"
    // level drift to wherever that single ray happened to land rather
    // than the chimney's true base.
    const heights = [];
    const steps = [-1, -0.5, 0, 0.5, 1];
    for (const se of steps) {
      for (const sn of steps) {
        const offset = Cesium.Cartesian3.add(
          Cesium.Cartesian3.multiplyByScalar(east, se * halfE, new Cesium.Cartesian3()),
          Cesium.Cartesian3.multiplyByScalar(north, sn * halfN, new Cesium.Cartesian3()),
          new Cesium.Cartesian3()
        );
        const samplePoint = Cesium.Cartesian3.add(center, offset, new Cesium.Cartesian3());
        const rayOrigin = Cesium.Cartesian3.add(
          samplePoint,
          Cesium.Cartesian3.multiplyByScalar(up, sphere.radius * 3, new Cesium.Cartesian3()),
          new Cesium.Cartesian3()
        );
        const ray = new Cesium.Ray(rayOrigin, Cesium.Cartesian3.negate(up, new Cesium.Cartesian3()));
        let hits = [];
        try { hits = chimViewer.scene.drillPickFromRay(ray, 32) || []; } catch (_) {}
        let lowest = null;
        hits.forEach(hit => {
          if (Cesium.defined(hit.position)) {
            const h = Cesium.Cartographic.fromCartesian(hit.position).height;
            if (lowest === null || h < lowest) lowest = h;
          }
        });
        if (lowest !== null) heights.push(lowest);
      }
    }

    let rayResult = null;
    if (heights.length >= 3) {
      heights.sort((a, b) => a - b);
      rayResult = heights[Math.floor(heights.length / 2)]; // median — robust to outliers
    } else if (heights.length > 0) {
      rayResult = Math.min(...heights);
    }

    if (rayResult !== null) {
      // Clamp against the hard OBB floor — a ray-cast result somehow
      // below the model's own true bottom extent is geometrically
      // impossible for anything actually IN the model, so if that
      // happens it means a ray grazed some unrelated stray geometry
      // rather than the chimney's real base; the OBB floor is what's
      // trustworthy in that case.
      chimGroundHeight = (obbBottom !== null) ? Math.max(rayResult, obbBottom) : rayResult;
      return;
    }
  } catch (e) {
    console.warn('Ground-level detection failed, falling back to bounding-box estimate.', e);
  }

  // Fallback: no usable ray-cast hits at all — use the OBB floor computed
  // above (far more accurate for a tall, thin chimney than the bounding
  // SPHERE radius, which overestimates by roughly half the model's
  // height — the same reason it isn't used for settling the model onto
  // the ground either; see settleTilesetOnGround()).
  if (obbBottom !== null) {
    chimGroundHeight = obbBottom;
    return;
  }
  const centerCarto = Cesium.Cartographic.fromCartesian(chimTileset.boundingSphere.center);
  chimGroundHeight = centerCarto.height - chimTileset.boundingSphere.radius;
}

function heightFromGround(absoluteHeight) {
  if (typeof absoluteHeight !== 'number' || isNaN(absoluteHeight)) return absoluteHeight;
  return absoluteHeight - chimGroundHeight;
}

/* ══════════════════════════════════════════════════════════════════════════
   SURFACE NORMAL CAMERA
   ══════════════════════════════════════════════════════════════════════════
   Strategy: cast a ray from OUTSIDE the model directly toward the defect
   point. The direction of that inbound ray, once negated, IS the outward
   surface normal — guaranteed to point away from the model no matter what
   surface orientation we're looking at (flat roof, vertical wall, slope).

   We try 8 candidate "outside" origins spaced evenly around the model at
   increasing elevations, pick the one whose inbound ray actually hits the
   tileset closest to the target point, and use that ray direction.
   ══════════════════════════════════════════════════════════════════════════ */

/**
 * getStraightOnView — returns { position, direction, up } for a camera that
 * looks PERPENDICULARLY at `target` from the correct outside face.
 *
 * @param {Cesium.Cartesian3} target  – defect world position
 * @param {number}            range   – camera stand-off distance in metres
 */
function getStraightOnView(target, range, shapePoints) {
  const sphere = chimTileset.boundingSphere;
  const center = sphere.center;
  const R      = sphere.radius;
  const enu    = Cesium.Transforms.eastNorthUpToFixedFrame(target);
  const worldUp = Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(0,0,1), new Cesium.Cartesian3());
  const worldE  = Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(1,0,0), new Cesium.Cartesian3());
  const worldN  = Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(0,1,0), new Cesium.Cartesian3());

  // 16 exhaustive candidate outward directions covering every surface orientation
  const _neg = v => Cesium.Cartesian3.negate(v, new Cesium.Cartesian3());
  const _norm = v => Cesium.Cartesian3.normalize(Cesium.Cartesian3.clone(v), new Cesium.Cartesian3());
  const _add  = (a,b) => Cesium.Cartesian3.normalize(Cesium.Cartesian3.add(a, b, new Cesium.Cartesian3()), new Cesium.Cartesian3());
  const candidateDirs = [
    worldE, _neg(worldE), worldN, _neg(worldN), worldUp,
    _add(worldE,  worldUp), _add(_neg(worldE), worldUp),
    _add(worldN,  worldUp), _add(_neg(worldN), worldUp),
    _add(worldE,  worldN),  _add(_neg(worldE), worldN),
    _add(worldE,  _neg(worldN)), _add(_neg(worldE), _neg(worldN)),
    _add(_add(worldE, worldN), worldUp), _add(_add(_neg(worldE), worldN), worldUp),
  ];

  // STAGE 1 — coarse search: find which outside "face" the defect sits on,
  // by picking the candidate direction whose inbound ray lands closest to
  // the target. This is only used to seed stage 2 below; on its own it can
  // be a few degrees off, which is what made the old fly-to feel "not quite
  // straight".
  let bestDir  = null;
  let bestDist = Infinity;
  let bestHit  = null;

  for (const outDir of candidateDirs) {
    const norm = _norm(outDir);
    const rayOrigin = Cesium.Cartesian3.add(
      target,
      Cesium.Cartesian3.multiplyByScalar(norm, R * 4 + range, new Cesium.Cartesian3()),
      new Cesium.Cartesian3()
    );
    const ray = new Cesium.Ray(rayOrigin, _neg(norm));
    let hits = [];
    try { hits = chimViewer.scene.drillPickFromRay(ray, 8) || []; } catch(_) {}
    for (const h of hits) {
      if (!Cesium.defined(h.position)) continue;
      const dist = Cesium.Cartesian3.distance(h.position, target);
      if (dist < bestDist) { bestDist = dist; bestDir = Cesium.Cartesian3.clone(norm); bestHit = Cesium.Cartesian3.clone(h.position); }
    }
  }

  // Fallback: horizontal radial direction only (no vertical bias)
  if (!bestDir || bestDist > R * 0.4) {
    const radial = Cesium.Cartesian3.subtract(target, center, new Cesium.Cartesian3());
    const vert   = Cesium.Cartesian3.multiplyByScalar(worldUp, Cesium.Cartesian3.dot(radial, worldUp), new Cesium.Cartesian3());
    Cesium.Cartesian3.subtract(radial, vert, radial);
    bestDir = Cesium.Cartesian3.magnitude(radial) > 0.001 ? _norm(radial) : _norm(worldUp);
    bestHit = target;
  }
  Cesium.Cartesian3.normalize(bestDir, bestDir);

  // If given the shape's own points (not just its centroid), average each
  // point's individual outward direction instead of relying on the single
  // direction found at the centroid alone. For an elongated or multi-point
  // shape — a long wandering crack, say — the local surface orientation
  // right at the centroid doesn't necessarily represent the best overall
  // viewing angle for the WHOLE shape; a capture aimed using only that one
  // spot's normal can leave the far ends of the shape looking angled/
  // side-on even though the middle looks straight. Averaging across
  // several points spread along the shape keeps it evenly straight-on
  // end to end. Capped at 6 sample points so this stays fast even for a
  // long multi-point line.
  if (shapePoints && shapePoints.length > 1) {
    const step = Math.max(1, Math.floor(shapePoints.length / 6));
    const sampled = [];
    for (let i = 0; i < shapePoints.length; i += step) sampled.push(shapePoints[i]);
    if (sampled[sampled.length - 1] !== shapePoints[shapePoints.length - 1]) {
      sampled.push(shapePoints[shapePoints.length - 1]);
    }

    const dirSum = new Cesium.Cartesian3(0, 0, 0);
    let dirCount = 0;
    for (const pt of sampled) {
      let localBest = null, localBestDist = Infinity;
      for (const outDir of candidateDirs) {
        const norm = _norm(outDir);
        const rayOrigin = Cesium.Cartesian3.add(pt, Cesium.Cartesian3.multiplyByScalar(norm, R * 4 + range, new Cesium.Cartesian3()), new Cesium.Cartesian3());
        const ray = new Cesium.Ray(rayOrigin, _neg(norm));
        let hits = [];
        try { hits = chimViewer.scene.drillPickFromRay(ray, 4) || []; } catch (_) {}
        for (const h of hits) {
          if (!Cesium.defined(h.position)) continue;
          const dist = Cesium.Cartesian3.distance(h.position, pt);
          if (dist < localBestDist) { localBestDist = dist; localBest = norm; }
        }
      }
      if (localBest && localBestDist < R * 0.4) {
        Cesium.Cartesian3.add(dirSum, localBest, dirSum);
        dirCount++;
      }
    }
    if (dirCount >= 2 && Cesium.Cartesian3.magnitude(dirSum) > 0.001) {
      Cesium.Cartesian3.normalize(dirSum, dirSum);
      bestDir = dirSum;
      bestHit = target; // keep the finite-difference refinement below centered on the actual target/centroid
    }
  }

  // STAGE 2 — true finite-difference surface normal at the hit point:
  // probe two more rays offset a small distance across the local surface
  // tangent plane, then take the cross product of the resulting surface
  // vectors. This gives the ACTUAL local normal (works on curved/sloped
  // chimney surfaces, not just the 16 approximate global directions above),
  // which is what makes the camera land exactly perpendicular.
  const probeBase = bestHit || target;
  let refinedNormal = bestDir;

  try {
    let tangentA = Cesium.Cartesian3.cross(bestDir, worldUp, new Cesium.Cartesian3());
    if (Cesium.Cartesian3.magnitude(tangentA) < 1e-6) {
      tangentA = Cesium.Cartesian3.cross(bestDir, worldE, new Cesium.Cartesian3());
    }
    Cesium.Cartesian3.normalize(tangentA, tangentA);
    const tangentB = Cesium.Cartesian3.cross(bestDir, tangentA, new Cesium.Cartesian3());
    Cesium.Cartesian3.normalize(tangentB, tangentB);

    // Wide enough to capture the wall's overall orientation rather than a
    // single brick, mortar joint, or surface bump — a narrow probe (this
    // used to cap out at ~1m) sits well within the size of normal surface
    // texture on masonry/concrete, so the two sample points could land on
    // opposite sides of one small bump and report ITS tilt as if it were
    // the whole wall's angle. That's what was producing a visibly
    // non-straight capture specifically on textured/uneven surfaces.
    const eps = Math.max(0.8, Math.min(R * 0.02, 2.5));
    const probeHits = [];

    for (const t of [tangentA, tangentB]) {
      const probeTarget = Cesium.Cartesian3.add(probeBase, Cesium.Cartesian3.multiplyByScalar(t, eps, new Cesium.Cartesian3()), new Cesium.Cartesian3());
      const rayOrigin = Cesium.Cartesian3.add(probeTarget, Cesium.Cartesian3.multiplyByScalar(bestDir, R * 4 + range, new Cesium.Cartesian3()), new Cesium.Cartesian3());
      const ray = new Cesium.Ray(rayOrigin, _neg(bestDir));
      let hits = [];
      try { hits = chimViewer.scene.drillPickFromRay(ray, 8) || []; } catch(_) {}
      let closest = null, closestDist = Infinity;
      for (const h of hits) {
        if (!Cesium.defined(h.position)) continue;
        const d = Cesium.Cartesian3.distance(h.position, probeTarget);
        if (d < closestDist) { closestDist = d; closest = h.position; }
      }
      if (closest) probeHits.push(closest);
    }

    if (probeHits.length === 2) {
      const v1 = Cesium.Cartesian3.subtract(probeHits[0], probeBase, new Cesium.Cartesian3());
      const v2 = Cesium.Cartesian3.subtract(probeHits[1], probeBase, new Cesium.Cartesian3());
      const cross = Cesium.Cartesian3.cross(v1, v2, new Cesium.Cartesian3());
      if (Cesium.Cartesian3.magnitude(cross) > 1e-6) {
        Cesium.Cartesian3.normalize(cross, cross);
        // Keep it pointing outward, same general side as the coarse normal.
        if (Cesium.Cartesian3.dot(cross, bestDir) < 0) Cesium.Cartesian3.negate(cross, cross);
        // Reject it if it deviates too far from the coarse direction —
        // a large deviation usually means one of the two probe rays
        // landed on unrelated nearby geometry (an edge, gap, bump, or a
        // neighboring face) rather than genuinely finding a different
        // true surface angle. Using a bad refined normal here is what
        // made the auto-captured close-up's viewing angle come out
        // rotated/inconsistent between saves, or visibly non-straight on
        // textured surfaces — falling back to the coarse (but reliable)
        // direction instead avoids that. Tighter than before (was 0.8,
        // ~37°) since a wider probe still can't fully rule out landing
        // near a bump's edge.
        if (Cesium.Cartesian3.dot(cross, bestDir) > 0.92) {
          refinedNormal = cross;
        }
      }
    }
  } catch (_) { /* fall back silently to the coarse normal below */ }

  const position  = Cesium.Cartesian3.add(target, Cesium.Cartesian3.multiplyByScalar(refinedNormal, range, new Cesium.Cartesian3()), new Cesium.Cartesian3());
  const direction = Cesium.Cartesian3.negate(refinedNormal, new Cesium.Cartesian3());
  Cesium.Cartesian3.normalize(direction, direction);

  // Camera "up" = world vertical ALWAYS (keeps the structure visually
  // level/upright, by construction — via explicit Gram-Schmidt against
  // the actual view direction, rather than leaving that orthogonalization
  // to Cesium internally). Only exception: looking almost straight
  // down/up, where "vertical" is a degenerate up-reference, so East is
  // used instead. Doing this explicitly — rather than just handing Cesium
  // a raw worldUp/worldE and trusting its own internal orthogonalization —
  // is what actually fixes captured images coming out visibly
  // rotated/tilted: that internal step is fine most of the time but was
  // inconsistent right in the range of angles a lot of close-up defect
  // shots land in.
  const upDot = Math.abs(Cesium.Cartesian3.dot(direction, worldUp));
  const upRef = upDot > 0.9 ? worldE : worldUp;
  const alongDir = Cesium.Cartesian3.multiplyByScalar(direction, Cesium.Cartesian3.dot(upRef, direction), new Cesium.Cartesian3());
  const up = Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(upRef, alongDir, new Cesium.Cartesian3()), new Cesium.Cartesian3());

  return { position, direction, up };
}

function chimResetView() {
  if (chimTileset) chimViewer.zoomTo(chimTileset);
}

/* ── Map style switcher ───────────────────────────────────────────────────
   Three mutually-exclusive base styles, picked with pushbuttons:
     - 'dark'      : plain dark background, no globe (the original look —
                     default on load)
     - 'satellite' : free ArcGIS World Imagery draped on the globe (no
                     Cesium Ion token required, unlike Bing)
     - 'osm'       : free OpenStreetMap tiles draped on the globe
   Imagery layers are created once on first use and just shown/hidden on
   later switches, so toggling back and forth doesn't re-fetch tiles. */
let satelliteLayer = null;
let osmLayer = null;
let currentMapStyle = 'dark';
let tilesetOriginalMatrix = null;

/* Without real elevation/terrain data (the globe here is a flat WGS84
   ellipsoid with only satellite imagery painted on it, no ion terrain
   token configured), a tileset's own registered height almost never lines
   up with that flat surface — real ground elevation is rarely exactly 0.
   That's what caused the base to look clipped/submerged.

   This settles the model by finding its true lowest point and nudging it
   vertically so that point sits right at the flat ground surface — using
   the tileset's oriented bounding box (not just the bounding sphere) to
   get an accurate base height for a tall, thin shape like a chimney,
   where the sphere radius would overestimate by roughly half the model's
   height and make it look like it's floating instead of settled. */
function settleTilesetOnGround() {
  if (!chimTileset) return;
  if (!tilesetOriginalMatrix) tilesetOriginalMatrix = Cesium.Matrix4.clone(chimTileset.modelMatrix);

  const ellipsoid = Cesium.Ellipsoid.WGS84;
  const root = chimTileset.root;
  const boundingVolume = root && root.boundingVolume && root.boundingVolume.boundingVolume;

  let center, halfVerticalExtent;
  if (boundingVolume && boundingVolume.halfAxes) {
    // Oriented bounding box — accurate for elongated shapes regardless of
    // how the model is rotated.
    center = boundingVolume.center;
    const up = ellipsoid.geodeticSurfaceNormal(center, new Cesium.Cartesian3());
    const halfAxes = boundingVolume.halfAxes;
    const col0 = Cesium.Matrix3.getColumn(halfAxes, 0, new Cesium.Cartesian3());
    const col1 = Cesium.Matrix3.getColumn(halfAxes, 1, new Cesium.Cartesian3());
    const col2 = Cesium.Matrix3.getColumn(halfAxes, 2, new Cesium.Cartesian3());
    halfVerticalExtent = Math.abs(Cesium.Cartesian3.dot(up, col0))
                        + Math.abs(Cesium.Cartesian3.dot(up, col1))
                        + Math.abs(Cesium.Cartesian3.dot(up, col2));
  } else {
    const sphere = chimTileset.boundingSphere;
    center = sphere.center;
    halfVerticalExtent = sphere.radius;
  }

  const cartographic = Cesium.Cartographic.fromCartesian(center);
  const baseHeight = cartographic.height - halfVerticalExtent;
  // Rest the base slightly ABOVE the flat ground surface rather than
  // embedding it below. Embedding the base under the opaque ground plane
  // is exactly what made the bottom of the chimney disappear once you
  // zoomed in close enough to see it — from a distance the sinking wasn't
  // noticeable, but up close it read as "the model isn't fixed in place".
  // A small gap also keeps the base from sitting almost exactly coplanar
  // with the globe surface, which is what causes flickering z-fighting
  // between the two surfaces at close range. 30cm is imperceptible at any
  // normal viewing distance/altitude but is enough separation to avoid
  // both problems.
  const nudge = -baseHeight + 0.3;

  const surfaceNormal = ellipsoid.geodeticSurfaceNormal(center, new Cesium.Cartesian3());
  const offset = Cesium.Cartesian3.multiplyByScalar(surfaceNormal, nudge, new Cesium.Cartesian3());
  const translation = Cesium.Matrix4.fromTranslation(offset);
  chimTileset.modelMatrix = Cesium.Matrix4.multiply(translation, tilesetOriginalMatrix, new Cesium.Matrix4());
}

function restoreTilesetPosition() {
  if (chimTileset && tilesetOriginalMatrix) {
    chimTileset.modelMatrix = Cesium.Matrix4.clone(tilesetOriginalMatrix);
  }
}

const MS_LABELS = { dark: 'Dark', satellite: 'Satellite', osm: 'OSM' };

function _updateMapStyleButtons() {
  ['dark', 'satellite', 'osm'].forEach((s) => {
    const sw = document.getElementById('ms-switch-' + s);
    if (sw) {
      const on = s === currentMapStyle;
      sw.classList.toggle('active', on);
      sw.setAttribute('aria-checked', on ? 'true' : 'false');
    }
  });
  const label = document.getElementById('map-style-current');
  if (label) label.textContent = MS_LABELS[currentMapStyle] || 'Dark';
}

function _closeMapStyleMenu() {
  const dropdown = document.getElementById('map-style-dropdown');
  const menu = document.getElementById('map-style-menu');
  if (menu) menu.classList.remove('open');
  if (dropdown) dropdown.classList.remove('open');
}

function toggleMapStyleMenu(ev) {
  if (ev) ev.stopPropagation();
  const dropdown = document.getElementById('map-style-dropdown');
  const menu = document.getElementById('map-style-menu');
  if (!dropdown || !menu) return;
  const opening = !menu.classList.contains('open');
  menu.classList.toggle('open', opening);
  dropdown.classList.toggle('open', opening);
}

document.addEventListener('click', (e) => {
  const dropdown = document.getElementById('map-style-dropdown');
  const menu = document.getElementById('map-style-menu');
  if (dropdown && menu && menu.classList.contains('open') && !dropdown.contains(e.target)) {
    menu.classList.remove('open');
    dropdown.classList.remove('open');
  }
});

function _setGlobeChromeVisible(visible) {
  chimViewer.scene.globe.show = visible;
  chimViewer.scene.skyAtmosphere.show = visible;
  if (chimViewer.scene.sun) chimViewer.scene.sun.show = visible;
  if (chimViewer.scene.skyBox) chimViewer.scene.skyBox.show = visible;
  // The default camera controller pushes the camera away whenever it thinks
  // it's about to collide with the globe surface. Since there's no real
  // terrain matching the tileset's actual height, that "safety" push fires
  // while orbiting close to the model when the globe is shown, which looks
  // like the chimney itself moving/swaying while rotating — so it's only
  // enabled again once we're back to the plain dark (no-globe) style.
  chimViewer.scene.screenSpaceCameraController.enableCollisionDetection = !visible;
}

async function setMapStyle(style) {
  if (!chimViewer || style === currentMapStyle) return;
  const prevStyle = currentMapStyle;

  if (style === 'dark') {
    if (satelliteLayer) satelliteLayer.show = false;
    if (osmLayer) osmLayer.show = false;
    _setGlobeChromeVisible(false);
    restoreTilesetPosition();
    currentMapStyle = 'dark';
    _updateMapStyleButtons();
    _closeMapStyleMenu();
    chimViewer.scene.requestRender();
    return;
  }

  // 'satellite' or 'osm' — both need the globe on and the model settled
  // onto the flat ground surface so there's no floating/clipping.
  // IMPORTANT: always reset to the pristine (original) position first,
  // THEN measure and settle. settleTilesetOnGround() measures the
  // tileset's CURRENT bounding volume to work out how far to drop it —
  // if the model is already settled from a previous style (e.g. going
  // straight from Satellite to OSM without passing through Dark), that
  // measurement is taken from the already-moved position, which throws
  // the result off and is exactly why OSM looked right after Dark but
  // wrong after Satellite. Restoring first guarantees every settle is
  // computed fresh from the same known-good starting point.
  _setGlobeChromeVisible(true);
  restoreTilesetPosition();
  settleTilesetOnGround();
  if (satelliteLayer) satelliteLayer.show = (style === 'satellite');
  if (osmLayer) osmLayer.show = (style === 'osm');

  try {
    if (style === 'satellite' && !satelliteLayer) {
      const provider = await Cesium.ArcGisMapServerImageryProvider.fromUrl(
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer'
      );
      satelliteLayer = chimViewer.imageryLayers.addImageryProvider(provider);
    } else if (style === 'osm' && !osmLayer) {
      const provider = new Cesium.OpenStreetMapImageryProvider({ url: 'https://a.tile.openstreetmap.org/' });
      osmLayer = chimViewer.imageryLayers.addImageryProvider(provider);
    }
  } catch (err) {
    console.error(`Could not load ${style} imagery:`, err);
    showHint(`Could not load ${style === 'satellite' ? 'satellite' : 'OpenStreetMap'} imagery — check your internet connection.`);
    // Revert to whichever style was active before the failed switch.
    currentMapStyle = prevStyle;
    if (prevStyle === 'dark') {
      _setGlobeChromeVisible(false);
      restoreTilesetPosition();
    } else {
      if (satelliteLayer) satelliteLayer.show = (prevStyle === 'satellite');
      if (osmLayer) osmLayer.show = (prevStyle === 'osm');
    }
    _updateMapStyleButtons();
    _closeMapStyleMenu();
    chimViewer.scene.requestRender();
    return;
  }

  currentMapStyle = style;
  _updateMapStyleButtons();
  _closeMapStyleMenu();
  chimViewer.scene.requestRender();
}

/* ── Compass direction (relative to the chimney's registered lat/lon) ──────── */
function computeCompassDirection(toLat, toLon) {
  const fromLat = (typeof window.CHIM_MODEL_CENTER_LAT === 'number') ? window.CHIM_MODEL_CENTER_LAT : window.CHIM_PROJECT_LAT;
  const fromLon = (typeof window.CHIM_MODEL_CENTER_LNG === 'number') ? window.CHIM_MODEL_CENTER_LNG : window.CHIM_PROJECT_LNG;
  if (typeof fromLat !== 'number' || typeof fromLon !== 'number') return '';
  const phi1 = Cesium.Math.toRadians(fromLat);
  const phi2 = Cesium.Math.toRadians(toLat);
  const dLambda = Cesium.Math.toRadians(toLon - fromLon);
  const x = Math.sin(dLambda) * Math.cos(phi2);
  const y = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLambda);
  const theta = Math.atan2(x, y);
  const bearing = (Cesium.Math.toDegrees(theta) + 360) % 360;
  const dirs = ['North','North-East','East','South-East','South','South-West','West','North-West'];
  return dirs[Math.round(bearing / 45) % 8];
}

/* ── Tight framing distance for a defect (point OR drawn shape) ─────────────
   Point defects: a small fixed close-up range.
   Shape defects: sized to the shape's own extent so the whole marking fills
   the frame (matches the "maximum zoom, defect fully in view" requirement).
─────────────────────────────────────────────────────────────────────────── */
function computeDefectRange(defect) {
  const bsr = chimTileset ? chimTileset.boundingSphere.radius : 20;
  if (defect.shape_coords && defect.shape_coords.length >= 2) {
    try {
      const pts = defect.shape_coords.map(p => Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height));
      let maxD = 0;
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const d = Cesium.Cartesian3.distance(pts[i], pts[j]);
          if (d > maxD) maxD = d;
        }
      }
      if (maxD > 0.05) {
        // The stand-off ceiling here (bsr * ...) exists only as a sanity
        // check against something absurd (e.g. a corrupted coordinate
        // producing a huge maxD by mistake) — it should never be what
        // actually limits how far back the camera sits for a normal,
        // legitimately large shape.
        //
        // The multiplier on maxD (2.0, up from an earlier 1.6) is extra
        // safety margin against the camera's target not being exactly
        // centered on the shape — if the computed centroid sits a bit
        // off toward one side (see reprojectPointToSurface's tolerance),
        // the far edge of a large shape needs more headroom to still
        // land inside the frame than a shape framed around a perfectly
        // centered point would. This is what caused "trimming" to only
        // show up on bigger shapes: the same centroid error is a small,
        // unnoticeable fraction of a small shape's frame, but a much
        // bigger fraction of a tight, barely-sufficient frame on a large
        // one.
        return Math.max(2, Math.min(maxD * 2.0, bsr * 3));
      }
    } catch (_) {}
  }
  // Plain pin defect — close-up, but with enough stand-off to see context around it.
  return Math.max(2.5, Math.min(bsr * 0.035, 6));
}

/* ── Observations panel toggle ───────────────────────────────────────────── */
function toggleObservationsPanel() {
  const panel = document.getElementById('chim-toolbar-panel');
  if (!panel) return;
  const isVisible = panel.style.display !== 'none' && panel.style.display !== '';
  panel.style.display = isVisible ? 'none' : 'flex';
  positionCompass();
}

/* ── Click router ─────────────────────────────────────────────────────────── */

function onStageClick(movement) {
  if (activeDrawTool)  { handleDrawClick(movement.position); return; }
  if (chimDefectMode)  { handleDefectPick(movement.position); return; }
  if (chimMeasureMode) { handleMeasurePick(movement.position); return; }
}

function onStageRightClick(movement) {
  if (activeDrawTool === 'polygon' && drawPoints.length >= 3) { finaliseDrawing(); return; }
  if (activeDrawTool === 'line'    && drawPoints.length >= 2) { finaliseDrawing(); return; }
}

function pickWorldPosition(screenPos) {
  const picked = chimViewer.scene.pick(screenPos);
  if (!Cesium.defined(picked)) return null;
  const cartesian = chimViewer.scene.pickPosition(screenPos);
  if (!Cesium.defined(cartesian)) return null;
  return cartesian;
}

/* ── Surface-projection utilities ─────────────────────────────────────────*/

const SURFACE_SEGMENTS = 32;
// How far drawn shapes/pins get nudged outward, toward the camera, off the
// model's surface — just enough to win normal depth-test z-fighting
// against the surface itself, without using disableDepthTestDistance
// (which would also defeat the INTENTIONAL depth testing that hides a
// line when it's genuinely on the far/occluded side of the chimney while
// rotating — see makeLineAppearance() below).
//
// This needs to be more generous than it looks like it should: the
// geometry is picked against whatever 3D tile detail is loaded at DRAW
// time, but Cesium keeps streaming in higher-detail tiles for a while
// after that, and the real surface can shift by several centimeters as it
// refines. A too-small offset (this used to be 5cm) gets swallowed by
// that drift after a bit of browsing, which is what caused shapes to
// render as broken/dashed instead of a clean solid line — parts of it
// alternately winning and losing the depth test against the
// now-more-detailed surface underneath.
const SURFACE_OFFSET   = 0.18;

function nudgeOutward(cartesian) {
  if (!chimViewer) return cartesian;
  // Toward the camera is always the correct "away from the surface,
  // toward the viewer" direction regardless of where on the model this
  // point sits — unlike "away from the bounding-sphere centre", which
  // only works for roughly round/squat shapes. On a tall chimney, a
  // point near the top has its away-from-centre direction pointing
  // mostly upward instead of outward, which visibly shifted the drawn
  // point away from where it was actually clicked.
  const cameraPos = chimViewer.camera.positionWC;
  const dir = Cesium.Cartesian3.subtract(cameraPos, cartesian, new Cesium.Cartesian3());
  const distToCamera = Cesium.Cartesian3.magnitude(dir);
  Cesium.Cartesian3.normalize(dir, dir);
  // Scaled to viewing distance rather than a flat real-world distance —
  // a fixed offset (this used to always be 18cm) looks like a genuine,
  // noticeable gap between the drawing and the model surface in a
  // close-up shot, even though that same 18cm is imperceptible zoomed
  // out. Floor raised from an earlier, too-thin 1cm minimum, which
  // wasn't enough margin to survive the 3D tiles' own level-of-detail
  // refinement — the actual surface position shifts slightly as Cesium
  // streams in more/different detail over time, and a too-small offset
  // let shapes get swallowed by that shift and appear to sink inside the
  // model. 4cm is still a barely-visible sliver at close range but
  // enough margin to reliably clear normal LOD jitter.
  const offset = Cesium.Math.clamp(distToCamera * 0.002, 0.04, SURFACE_OFFSET);
  Cesium.Cartesian3.multiplyByScalar(dir, offset, dir);
  return Cesium.Cartesian3.add(cartesian, dir, new Cesium.Cartesian3());
}

// Re-snap an approximate 3D point (e.g. a straight-line average of two or
// more on-surface picks) back onto the model's actual surface. Averaging
// points that sit on a curved surface (like the chimney's cylindrical
// wall) pulls the midpoint slightly *inside* the model — this undoes that
// by projecting to screen space and re-picking, the same technique already
// used for surface-hugging preview lines below. Only works for points
// currently visible on screen (true right after the user finishes drawing).
function reprojectPointToSurface(point, maxJump) {
  if (maxJump === undefined) maxJump = 0.5;
  const screenPt = Cesium.SceneTransforms.wgs84ToWindowCoordinates(chimViewer.scene, point);
  if (screenPt) {
    const hit = chimViewer.scene.pick(screenPt);
    if (Cesium.defined(hit)) {
      const pos = chimViewer.scene.pickPosition(screenPt);
      if (Cesium.defined(pos)) {
        // On an irregular/bumpy surface (e.g. a recessed, slatted vent),
        // the nearest visible pixel at this exact screen position can
        // belong to a bump or ridge meaningfully displaced from the
        // intended point, rather than the flat surface right there —
        // reject a reprojection that jumps too far and keep the original
        // point instead of silently accepting a marker that's visibly
        // landed off to one side. maxJump scales with the shape's own
        // size at the call site: a small mark needs a tight tolerance to
        // catch a bad snap, but a rectangle spanning a couple of metres
        // on an angled surface can legitimately need a bigger one — using
        // one fixed small threshold for everything is what caused a
        // valid reprojection on a larger shape to get rejected, falling
        // back to a raw, un-projected point that visibly floated away
        // from the actual drawn shape.
        const jump = Cesium.Cartesian3.distance(pos, point);
        if (jump < maxJump) return pos;
      }
    }
  }
  return point; // fallback — best effort if off-screen/unpickable/unreliable
}

function sampleSurfaceEdge(a, b, segments) {
  segments = segments || SURFACE_SEGMENTS;
  const result = [];
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const interp = Cesium.Cartesian3.lerp(a, b, t, new Cesium.Cartesian3());
    const nudged = nudgeOutward(Cesium.Cartesian3.clone(interp));
    const screenPt = Cesium.SceneTransforms.wgs84ToWindowCoordinates(chimViewer.scene, nudged);
    if (screenPt) {
      const hit = chimViewer.scene.pick(screenPt);
      if (Cesium.defined(hit)) {
        const pos = chimViewer.scene.pickPosition(screenPt);
        if (Cesium.defined(pos)) { result.push(nudgeOutward(pos)); continue; }
      }
    }
    result.push(nudged);
  }
  return result;
}

// A local drawing frame at a point on the chimney, oriented so shapes
// actually follow the structure: `up` is true vertical (so a rectangle
// dragged top-to-bottom keeps its full height instead of collapsing onto
// one flat plane), and `horizontal` is the circumferential direction
// tangent to the curved wall at that point (found from the outward radial
// direction off the tileset's central axis, not raw east/north — east/north
// only make sense for something flat like the ground, not a vertical wall).
function getLocalTangentFrame(point) {
  const enu = Cesium.Transforms.eastNorthUpToFixedFrame(point);
  const up = Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(0, 0, 1), new Cesium.Cartesian3());
  Cesium.Cartesian3.normalize(up, up);

  let horizontal = null;
  if (chimTileset) {
    const radial = Cesium.Cartesian3.subtract(point, chimTileset.boundingSphere.center, new Cesium.Cartesian3());
    const vertComponent = Cesium.Cartesian3.multiplyByScalar(up, Cesium.Cartesian3.dot(radial, up), new Cesium.Cartesian3());
    Cesium.Cartesian3.subtract(radial, vertComponent, radial);
    if (Cesium.Cartesian3.magnitude(radial) > 0.001) {
      horizontal = Cesium.Cartesian3.cross(up, Cesium.Cartesian3.normalize(radial, new Cesium.Cartesian3()), new Cesium.Cartesian3());
      Cesium.Cartesian3.normalize(horizontal, horizontal);
    }
  }
  if (!horizontal) {
    horizontal = Cesium.Matrix4.multiplyByPointAsVector(enu, new Cesium.Cartesian3(1, 0, 0), new Cesium.Cartesian3());
    Cesium.Cartesian3.normalize(horizontal, horizontal);
  }
  return { up, horizontal };
}

function buildSurfacePolygon(corners, segments) {
  segments = segments || SURFACE_SEGMENTS;
  const pts = [];
  for (let i = 0; i < corners.length; i++) {
    const a = corners[i];
    const b = corners[(i + 1) % corners.length];
    const edge = sampleSurfaceEdge(a, b, segments);
    pts.push(...edge.slice(0, -1));
  }
  pts.push(pts[0]);
  return pts;
}

// Same idea as buildSurfacePolygon, but for an OPEN chain of waypoints —
// no closing segment back to the start. Used for a multi-point line (more
// than just a start and end point), so you can trace an irregular path
// (e.g. a wandering crack) precisely instead of only a single straight
// (surface-hugging) segment between two endpoints.
function buildSurfacePolyline(points, segments) {
  segments = segments || SURFACE_SEGMENTS;
  if (points.length < 2) return points.slice();
  const pts = [];
  for (let i = 0; i < points.length - 1; i++) {
    const edge = sampleSurfaceEdge(points[i], points[i + 1], segments);
    pts.push(...(i === points.length - 2 ? edge : edge.slice(0, -1)));
  }
  return pts;
}

function buildSurfaceRing(center, edgePoint, segments) {
  segments = segments || 64;
  const radius = Cesium.Cartesian3.distance(center, edgePoint);
  const frame = getLocalTangentFrame(center);
  const result = [];
  for (let i = 0; i <= segments; i++) {
    const theta = (i / segments) * Math.PI * 2;
    const offset = Cesium.Cartesian3.add(
      Cesium.Cartesian3.multiplyByScalar(frame.horizontal, radius * Math.cos(theta), new Cesium.Cartesian3()),
      Cesium.Cartesian3.multiplyByScalar(frame.up, radius * Math.sin(theta), new Cesium.Cartesian3()),
      new Cesium.Cartesian3()
    );
    const worldPt = Cesium.Cartesian3.add(center, offset, new Cesium.Cartesian3());
    const screenPt = Cesium.SceneTransforms.wgs84ToWindowCoordinates(chimViewer.scene, worldPt);
    if (screenPt) {
      const hit = chimViewer.scene.pick(screenPt);
      if (Cesium.defined(hit)) {
        const pos = chimViewer.scene.pickPosition(screenPt);
        if (Cesium.defined(pos)) { result.push(nudgeOutward(pos)); continue; }
      }
    }
    result.push(nudgeOutward(worldPt));
  }
  return result;
}

/* ── Defect (finding) pin tool ────────────────────────────────────────────── */

function chimToggleDefectMode() {
  if (!window.CHIM_IS_ADMIN) return;
  chimDefectMode = !chimDefectMode;
  chimMeasureMode = false;
  cancelDrawTool();
  document.getElementById('btn-add-defect').classList.toggle('active', chimDefectMode);
  document.getElementById('btn-measure').classList.remove('active');
  if (chimDefectMode) showHint('Click a point on the model to drop a finding pin.');
  else hideHint();
}

function handleDefectPick(screenPos) {
  const cartesian = pickWorldPosition(screenPos);
  if (!cartesian) return;
  const carto = Cesium.Cartographic.fromCartesian(cartesian);
  chimPendingPosition = {
    lon: Cesium.Math.toDegrees(carto.longitude),
    lat: Cesium.Math.toDegrees(carto.latitude),
    height: carto.height,
  };
  openDefectForm(screenPos);
}

const KNOWN_DEFECT_TYPES = ['Crack','Spalling','Corrosion','Erosion','Delamination','Water Ingress','Structural Gap','Efflorescence'];

function chimToggleOtherDefectType() {
  const sel  = document.getElementById('df-defect-type');
  const wrap = document.getElementById('df-defect-type-other-wrap');
  const isOther = sel.value === 'Other';
  wrap.style.display = isOther ? 'block' : 'none';
  if (isOther) document.getElementById('df-defect-type-other').focus();
}

function openDefectForm(screenPos, shapeType, shapeCoords, prefill) {
  const form = document.getElementById('defect-form');

  document.getElementById('df-title').value       = prefill?.title       || '';
  document.getElementById('df-severity').value    = prefill?.severity    || 'Minor';
  document.getElementById('df-notes').value       = prefill?.notes       || '';
  document.getElementById('df-location').value    = prefill?.location    || '';

  const incomingType = prefill?.defect_type || 'Crack';
  const otherWrap  = document.getElementById('df-defect-type-other-wrap');
  const otherInput = document.getElementById('df-defect-type-other');
  if (incomingType && !KNOWN_DEFECT_TYPES.includes(incomingType)) {
    document.getElementById('df-defect-type').value = 'Other';
    otherInput.value = incomingType;
    otherWrap.style.display = 'block';
  } else {
    document.getElementById('df-defect-type').value = incomingType;
    otherInput.value = '';
    otherWrap.style.display = 'none';
  }

  let lat = '', lon = '', height = '', area = '';
  const pos = chimPendingPosition;

  if (pos) {
    lat    = pos.lat.toFixed(6);
    lon    = pos.lon.toFixed(6);
    height = heightFromGround(pos.height).toFixed(1) + ' m';
  } else if (shapeCoords && shapeCoords.length > 0) {
    lat    = (shapeCoords.reduce((s, p) => s + p.lat, 0) / shapeCoords.length).toFixed(6);
    lon    = (shapeCoords.reduce((s, p) => s + p.lon, 0) / shapeCoords.length).toFixed(6);
    const avgH = shapeCoords.reduce((s, p) => s + p.height, 0) / shapeCoords.length;
    height = heightFromGround(avgH).toFixed(1) + ' m';
  }

  let calcArea = '';
  if (shapeCoords && shapeCoords.length > 0) {
    if (shapeType === 'circle' && shapeCoords.length === 2) {
      const c = Cesium.Cartesian3.fromDegrees(shapeCoords[0].lon, shapeCoords[0].lat, shapeCoords[0].height);
      const e = Cesium.Cartesian3.fromDegrees(shapeCoords[1].lon, shapeCoords[1].lat, shapeCoords[1].height);
      const r = Cesium.Cartesian3.distance(c, e);
      calcArea = (Math.PI * r * r).toFixed(2) + ' m²';
    } else if ((shapeType === 'polygon' || shapeType === 'rect') && shapeCoords.length >= 3) {
      const pts = shapeCoords.map(p => Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height));
      let a = 0;
      for (let i = 0; i < pts.length; i++) {
        const j = (i + 1) % pts.length;
        a += pts[i].x * pts[j].y - pts[j].x * pts[i].y;
      }
      calcArea = Math.abs(a / 2).toFixed(2) + ' m²';
    } else if (shapeType === 'line' && shapeCoords.length === 2) {
      const p1 = Cesium.Cartesian3.fromDegrees(shapeCoords[0].lon, shapeCoords[0].lat, shapeCoords[0].height);
      const p2 = Cesium.Cartesian3.fromDegrees(shapeCoords[1].lon, shapeCoords[1].lat, shapeCoords[1].height);
      calcArea = Cesium.Cartesian3.distance(p1, p2).toFixed(2) + ' m (length)';
    }
  }
  area = prefill?.area || calcArea;

  document.getElementById('df-lat').value    = lat;
  document.getElementById('df-lon').value    = lon;
  document.getElementById('df-height').value = prefill?.height || height;
  document.getElementById('df-direction').value =
    prefill?.direction || (lat && lon ? (computeCompassDirection(parseFloat(lat), parseFloat(lon)) || '—') : '');
  _setAreaDropdown(area);

  form.dataset.shapeType   = shapeType   || '';
  form.dataset.shapeCoords = shapeCoords ? JSON.stringify(shapeCoords) : '';
  form.dataset.editId      = prefill?.id || '';
  form.classList.add('open');
  setCompassVisible(false);
  document.getElementById('df-title').focus();
}

function _setAreaDropdown(value) {
  const sel = document.getElementById('df-area');
  const prev = sel.querySelector('option[data-auto]');
  if (prev) prev.remove();
  if (value) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = value + ' (calculated)';
    opt.setAttribute('data-auto', '1');
    sel.insertBefore(opt, sel.firstChild);
    sel.value = value;
  } else {
    sel.value = sel.options[0]?.value || '';
  }
}

function chimCancelDefectForm() {
  document.getElementById('defect-form').classList.remove('open');
  chimPendingPosition = null;
  setCompassVisible(true);
}

async function chimSaveDefect() {
  const title = document.getElementById('df-title').value.trim();
  if (!title) { document.getElementById('df-title').focus(); return; }

  const form        = document.getElementById('defect-form');
  const shapeType   = form.dataset.shapeType || null;
  const shapeCoordsRaw = form.dataset.shapeCoords;
  const shapeCoords = shapeCoordsRaw ? JSON.parse(shapeCoordsRaw) : null;
  const editId      = form.dataset.editId ? parseInt(form.dataset.editId) : null;

  let pos = chimPendingPosition;
  if (!pos && shapeCoords && shapeCoords.length > 0) {
    const avgLon = shapeCoords.reduce((s, p) => s + p.lon, 0) / shapeCoords.length;
    const avgLat = shapeCoords.reduce((s, p) => s + p.lat, 0) / shapeCoords.length;
    const avgH   = shapeCoords.reduce((s, p) => s + p.height, 0) / shapeCoords.length;
    pos = { lon: avgLon, lat: avgLat, height: avgH };
  }
  if (!pos && !editId) { alert('No position — click on the model first.'); return; }
  if (!pos && editId) {
    const existing = chimDefects.find(d => d.id === editId);
    if (existing) pos = existing.position;
  }

  const areaEl = document.getElementById('df-area');
  const areaVal = areaEl.value || areaEl.options[areaEl.selectedIndex]?.text?.replace(' (calculated)','') || '';

  const typeSel = document.getElementById('df-defect-type').value;
  let defectTypeVal = typeSel;
  if (typeSel === 'Other') {
    const other = document.getElementById('df-defect-type-other').value.trim();
    if (!other) { document.getElementById('df-defect-type-other').focus(); return; }
    defectTypeVal = other;
  }

  const body = {
    title,
    severity:    document.getElementById('df-severity').value,
    defect_type: defectTypeVal,
    notes:       document.getElementById('df-notes').value.trim(),
    area:        areaVal.trim(),
    height:      document.getElementById('df-height').value.trim(),
    location:    document.getElementById('df-location').value.trim(),
    position:    pos,
    shape_type:  shapeType,
    shape_coords: shapeCoords,
  };

  // Compute the surface-fit outline ONCE, right now, while the camera is
  // genuinely close-up and this area's detail is loaded (the same
  // trusted conditions the fit has always been computed under) — and
  // persist the actual result rather than leaving it to be re-derived
  // via fresh ray-casting every time the project is opened again. See
  // the rendered_positions model field for why re-deriving it later is
  // what caused a shape to look different (a gap, or sunk into a
  // surface bump) between sessions.
  if (shapeType && shapeCoords && shapeCoords.length) {
    const builtPositions = buildShapePositions({ shape_type: shapeType, shape_coords: shapeCoords });
    if (builtPositions && builtPositions.length >= 2) {
      body.rendered_positions = builtPositions.map(p => {
        const c = Cesium.Cartographic.fromCartesian(p);
        return { lon: Cesium.Math.toDegrees(c.longitude), lat: Cesium.Math.toDegrees(c.latitude), height: c.height };
      });
    }
  }

  try {
    if (editId) {
      const res = await fetch(`/api/chimney-defects/${editId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) { alert(data.error || 'Could not update finding.'); return; }
      const idx = chimDefects.findIndex(d => d.id === editId);
      if (idx !== -1) {
        chimDefects[idx] = { ...chimDefects[idx], ...body, ...data };
        ['defect-' + editId, 'defect-shape-' + editId].forEach(eid => {
          const e = chimViewer.entities.getById(eid);
          if (e) chimViewer.entities.remove(e);
        });
        addDefectEntity(chimDefects[idx]);
      }
    } else {
      const res  = await fetch(`/api/chimney-projects/${window.CHIM_PROJECT_ID}/defects`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) { alert(data.error || 'Could not save finding.'); return; }
      data.shape_type   = shapeType;
      data.shape_coords = shapeCoords;
      data.rendered_positions = body.rendered_positions || null;
      data.defect_type  = body.defect_type;
      data.area         = body.area;
      data.height       = body.height;
      chimDefects.push(data);
      addDefectEntity(data);
      renderDefectList();
      // Auto-capture a tight, max-zoom snapshot of the new finding and save
      // it to the server/database, so the eye/download icons work right away.
      showHint('Saved. Capturing a close-up image for the report…', 2200);
      captureAndSaveDefectImage(data);
    }
    if (editId) renderDefectList();
    const panel = document.getElementById('chim-toolbar-panel');
    if (panel) panel.style.display = 'flex';
    positionCompass();
  } finally {
    form.classList.remove('open');
    setCompassVisible(true);
    chimPendingPosition    = null;
    form.dataset.shapeType   = '';
    form.dataset.shapeCoords = '';
    form.dataset.editId      = '';
  }
}

function makeLineAppearance(color) {
  return {
    width: LINE_WIDTH,
    material: new Cesium.ColorMaterialProperty(color),
    // No depthFailMaterial here on purpose: from a normal viewing
    // distance (e.g. rotating around the whole chimney), a line on the
    // far/opposite side should stay properly hidden rather than "showing
    // through" — a fallback material, even a faint one, still reads as
    // visible through the model, which is exactly what shouldn't happen.
    //
    // disableDepthTestDistance covers the CLOSE-UP case instead: once the
    // camera is within ~3m, depth testing turns off entirely, so the line
    // always renders regardless of small differences between where it was
    // originally placed and wherever the surface sits right now — this is
    // what stops a shape drawn on a bumpy/complex area from visually
    // sinking into the model as 3D tile detail refines over time.
    //
    // This used to be 25m, which was far too generous: it made the line
    // render straight through empty space whenever viewed from an angle
    // where a DIFFERENT, unrelated face of the model should legitimately
    // be occluding it (e.g. a side view of a crack that's actually on the
    // front face) — the line has no way to distinguish "this is the same
    // local surface, just shifted slightly from LOD refinement" from "a
    // whole different part of the model should be hiding this right now".
    // The actual LOD-driven surface jitter this needs to survive is only
    // ever a few centimeters, so 3m (already proven safe on the point
    // marker) is still enormous margin for that specific problem, without
    // being big enough to defeat normal occlusion between different faces.
    disableDepthTestDistance: 3,
    clampToGround: false,
  };
}

// Computes the surface-hugging polyline positions for a shape-type
// defect, ray-cast against whatever 3D tile detail is currently loaded.
// Used once, at the moment the shape is placed — a periodic "re-fit
// later against newer tile detail" version of this was tried and
// reverted: re-running the ray-casting from a different/oblique camera
// angle than the one it was originally drawn from turned out to
// occasionally produce a badly corrupted zigzag outline instead of an
// improvement, which is worse than the slow drift it was meant to fix.
function buildShapePositions(defect) {
  if (!defect.shape_type || !defect.shape_coords || !defect.shape_coords.length) return null;
  const rawPositions = defect.shape_coords.map(p =>
    Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height));

  if (defect.shape_type === 'line' && rawPositions.length >= 2) {
    return rawPositions.length === 2
      ? sampleSurfaceEdge(rawPositions[0], rawPositions[1], SURFACE_SEGMENTS)
      : buildSurfacePolyline(rawPositions);
  }
  if ((defect.shape_type === 'polygon' || defect.shape_type === 'rect') && rawPositions.length >= 3) {
    return buildSurfacePolygon(rawPositions);
  }
  if (defect.shape_type === 'circle' && rawPositions.length === 2) {
    return buildSurfaceRing(rawPositions[0], rawPositions[1], 64);
  }
  return null;
}

function addDefectEntity(defect) {
  const color = CHIM_SEVERITY_COLOR[defect.severity] || CHIM_SEVERITY_COLOR.Minor;

  // Use the persisted, already-computed outline if we have one — this is
  // what makes a shape render IDENTICALLY every time the project loads,
  // regardless of what 3D tile detail happens to be loaded at that
  // particular moment. Re-deriving it live via fresh ray-casting (the old
  // behaviour, still used as a fallback below for older defects) bakes in
  // whatever detail level happens to be loaded RIGHT NOW — which, right
  // after a fresh page load, is often still the coarse wide-overview
  // level rather than the close-up detail the shape was actually drawn
  // against — and is exactly why the same shape could look different (a
  // gap, or sunk into a surface bump) between sessions.
  let finalPositions = null;
  if (defect.rendered_positions && defect.rendered_positions.length >= 2) {
    finalPositions = defect.rendered_positions.map(p => Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height));
  } else {
    finalPositions = buildShapePositions(defect);
  }
  if (finalPositions) {
    chimViewer.entities.add({
      id: `defect-shape-${defect.id}`,
      polyline: { positions: finalPositions, ...makeLineAppearance(color) },
    });
  }

  addDefectEntityPointOnly(defect, color);
}

// Just the marker/label — split out from addDefectEntity() so
// recentreDefectPin() can refresh only this after fixing its position,
// without touching (or needing to recompute) the shape outline at all.
//
// Shape-type defects (line/rect/polygon/circle) get a LABEL ONLY, no
// point/dot graphic — the drawn shape itself already marks exactly where
// the defect is, and the separate dot has been the single biggest
// recurring source of positioning bugs across many rounds of fixes
// (reprojection snapping to the wrong spot, occlusion, floating outside
// the shape...). Removing it for shapes eliminates that whole class of
// problems rather than continuing to chase it. Plain point-only defects
// (a single click, no shape drawn) still get the dot, since there's
// nothing else on the model to show where they are.
function addDefectEntityPointOnly(defect, color) {
  if (!color) color = CHIM_SEVERITY_COLOR[defect.severity] || CHIM_SEVERITY_COLOR.Minor;
  const hasShape = !!defect.shape_type;
  chimViewer.entities.add({
    id: `defect-${defect.id}`,
    position: nudgeOutward(Cesium.Cartesian3.fromDegrees(defect.position.lon, defect.position.lat, defect.position.height)),
    point: hasShape ? undefined : {
      pixelSize: 13,
      color: color,
      outlineColor: Cesium.Color.WHITE,
      outlineWidth: 2,
      // A small, safe radius — NOT the 25m used for the drawn lines,
      // which is fine for those (a chimney's diameter is comfortably
      // bigger than 25m in the wrong case... but a single marker point
      // sitting right at a local bump/ridge on a highly irregular
      // surface, like a recessed slatted vent, can end up occluded by
      // its own immediate surroundings even at close range). 3m is
      // enough to guarantee the dot stays visible against that kind of
      // local surface noise without ever being able to "show through"
      // from the opposite side of the structure.
      disableDepthTestDistance: 3,
    },
    label: {
      text: defect.title,
      font: '11px sans-serif',
      fillColor: Cesium.Color.WHITE,
      pixelOffset: new Cesium.Cartesian2(0, -18),
      showBackground: true,
      backgroundColor: Cesium.Color.fromCssColorString('#1b1e24cc'),
    },
  });
}

/* ══════════════════════════════════════════════════════════════════════════
   flyToDefect — uses surface-normal camera for exact face-on positioning
   ══════════════════════════════════════════════════════════════════════════ */
function flyToDefect(defect) {
  if (!defect) return;

  if (defect.position && chimTileset) {
    let view;

    // Reuse the EXACT camera pose captured right when this defect was
    // saved, if we have one, instead of recomputing it live. Recomputing
    // live depends on whatever 3D tile detail happens to be loaded at
    // that moment — which genuinely changes as you orbit around for a
    // few minutes (different tiles stream in, others get evicted from
    // cache) — so clicking the same defect again later could land on a
    // different, "side-on"/"inner-angle" view than the one that was
    // actually captured. A stored pose is immune to that drift.
    if (defect.cam_pos && defect.cam_dir && defect.cam_up) {
      view = {
        position: new Cesium.Cartesian3(defect.cam_pos.x, defect.cam_pos.y, defect.cam_pos.z),
        direction: new Cesium.Cartesian3(defect.cam_dir.x, defect.cam_dir.y, defect.cam_dir.z),
        up: new Cesium.Cartesian3(defect.cam_up.x, defect.cam_up.y, defect.cam_up.z),
      };
    } else {
      // Older defect saved before pose-storage existed — fall back to
      // live computation (same single calculation captureDefectCanvasBlob
      // uses), the best we can do without a stored reference.
      const defectPos = Cesium.Cartesian3.fromDegrees(
        defect.position.lon,
        defect.position.lat,
        defect.position.height
      );
      const closeRange = computeDefectRange(defect);
      const shapePoints = (defect.shape_coords && defect.shape_coords.length > 1)
        ? defect.shape_coords.map(p => Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height))
        : null;
      view = getStraightOnView(defectPos, closeRange, shapePoints);
    }

    chimViewer.camera.flyTo({
      destination: view.position,
      orientation: { direction: view.direction, up: view.up },
      duration: 1.4,
      easingFunction: Cesium.EasingFunction.QUADRATIC_IN_OUT,
    });
  } else if (chimTileset) {
    chimViewer.zoomTo(chimTileset);
  }

  document.querySelectorAll('.obs-card').forEach(c => c.classList.remove('active-card'));
  const card = document.getElementById('obs-card-' + defect.id);
  if (card) {
    card.classList.add('active-card');
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

// Shape outlines are computed exactly ONCE, at the moment they're drawn
// (see buildShapePositions / addDefectEntity) and are never automatically
// recomputed after that. Two different attempts at "refresh a shape's fit
// later against newer tile detail" were tried and both caused visible
// corruption in practice — a broad periodic version produced a zigzag
// mess, and even a version narrowly scoped to only run right after
// flyToDefect's verified straight-on view still caused shapes to visibly
// jump/distort within seconds. Ray-casting against a 3D Tileset that's
// still actively streaming/refining detail is evidently not reliable
// enough to safely re-run after the fact, no matter how the timing is
// guarded — so this is intentionally NOT attempted again. A shape may
// very rarely show a small, cosmetic gap from the model surface after
// long sessions as tile detail settles, but that's a far smaller problem
// than corrupting the drawing.

/* ── Edit existing defect ─────────────────────────────────────────────────── */
function showDefectDetails(id, ev) {
  if (ev) ev.stopPropagation();
  window.location.href = `/chimney-projects/${window.CHIM_PROJECT_ID}/detail?highlight=${id}`;
}

// Explicit, user-triggered fix for a defect whose pin was placed off to
// one side by an older, less accurate centroid calculation. Recomputes
// ONLY the marker/pin position from the shape's EXISTING, unchanged
// outline (shape_coords) using the current best centroid logic — the
// shape's own geometry is never touched, since that's proven stable and
// isn't what needed fixing. This only ever runs when explicitly clicked,
// never automatically in the background — the earlier attempts at
// automatic background re-fitting of DRAWN SHAPES caused real corruption
// (see buildShapePositions' comment); this is a much simpler, lower-risk
// operation (recomputing a single point, not re-deriving a whole
// outline) and is always visible/undoable since it's one explicit click.
async function recentreDefectPin(id, ev) {
  if (ev) ev.stopPropagation();
  const defect = chimDefects.find(d => d.id === id);
  if (!defect || !defect.shape_type || !defect.shape_coords || !defect.shape_coords.length) return;
  if (!chimViewer || !chimTileset) { showHint('Model still loading — try again in a moment.', 2000); return; }

  try {
    // shape_coords already contains exactly what computeShapeCentroid
    // needs for every shape type as-is: a rect's 4 already-computed
    // corners, a polygon's vertices, a line's 2 endpoints, or a circle's
    // center+edge — no reconstruction needed.
    const renderCorners = defect.shape_coords.map(p => Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height));
    const newCentroid = computeShapeCentroid(defect.shape_type, renderCorners);
    const carto = Cesium.Cartographic.fromCartesian(newCentroid);
    const newPos = {
      lon: Cesium.Math.toDegrees(carto.longitude),
      lat: Cesium.Math.toDegrees(carto.latitude),
      height: carto.height,
    };

    const res = await fetch(`/api/chimney-defects/${id}/position`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newPos),
    });
    if (!res.ok) { showHint('Could not update pin position.', 2200); return; }

    defect.position = newPos;
    ['defect-' + id].forEach(eid => {
      const e = chimViewer.entities.getById(eid);
      if (e) chimViewer.entities.remove(e);
    });
    addDefectEntityPointOnly(defect);
    showHint('Pin re-centered on the shape.', 1800);
  } catch (e) {
    console.warn('Recenter pin failed:', e);
    showHint('Could not update pin position.', 2200);
  }
}

function editDefect(id, ev) {
  ev.stopPropagation();
  if (!window.CHIM_IS_ADMIN) return;
  const defect = chimDefects.find(d => d.id === id);
  if (!defect) return;
  chimPendingPosition = defect.position ? { ...defect.position } : null;
  openDefectForm(null, defect.shape_type, defect.shape_coords, {
    id:          defect.id,
    title:       defect.title,
    severity:    defect.severity,
    defect_type: defect.defect_type,
    notes:       defect.notes,
    area:        defect.area,
    location:    defect.location,
    height:      defect.height || (defect.position ? heightFromGround(defect.position.height).toFixed(1) + ' m' : ''),
  });
}

async function deleteDefect(id, ev) {
  ev.stopPropagation();
  if (!window.CHIM_IS_ADMIN) return;
  if (!confirm('Remove this finding?')) return;
  await fetch(`/api/chimney-defects/${id}`, { method:'DELETE' });
  chimDefects = chimDefects.filter(d => d.id !== id);
  ['defect-' + id, 'defect-shape-' + id].forEach(eid => {
    const e = chimViewer.entities.getById(eid);
    if (e) chimViewer.entities.remove(e);
  });
  renderDefectList();
}

let _obsFilterSeverity = '';
let _obsFilterType = '';

function filterObservations() {
  _obsFilterSeverity = (document.getElementById('obs-filter-severity')?.value || '').toLowerCase();
  _obsFilterType = (document.getElementById('obs-filter-type')?.value || '').toLowerCase();
  renderDefectList();
}

function _populateTypeFilter() {
  const select = document.getElementById('obs-filter-type');
  if (!select) return;
  const current = select.value;
  const types = [...new Set(chimDefects.map(d => (d.defect_type || '').trim()).filter(Boolean))].sort();
  select.innerHTML = '<option value="">All Types</option>' +
    types.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');
  if (types.includes(current)) select.value = current;
}

function renderDefectList() {
  const list = document.getElementById('defect-list');
  const total = chimDefects.length;
  const countEl = document.getElementById('defect-count-badge');
  if (countEl) countEl.textContent = `Observation Records (${String(total).padStart(2,'0')})`;

  const btnBadge = document.getElementById('defect-list-badge');
  if (btnBadge) {
    btnBadge.textContent = total;
    btnBadge.style.display = total > 0 ? 'flex' : 'none';
  }

  _populateTypeFilter();

  let visible = chimDefects;
  if (_obsFilterSeverity) {
    visible = visible.filter(d => (d.severity||'').toLowerCase() === _obsFilterSeverity);
  }
  if (_obsFilterType) {
    visible = visible.filter(d => (d.defect_type||'').toLowerCase() === _obsFilterType);
  }

  if (!visible.length) {
    list.innerHTML = window.CHIM_IS_ADMIN
      ? `<div class="defect-empty">No findings yet.<br>Use <b>Add Finding</b> or <b>Draw</b> tools to add defects.</div>`
      : `<div class="defect-empty">No findings recorded yet.</div>`;
    renderKmlLayersSection(); return;
  }

  // Sequence numbers must match the PDF report: based on the FULL list's
  // created-at order, not the filtered/visible subset — otherwise the ID
  // shown here would shift around whenever a filter is applied.
  const seqById = {};
  chimDefects.forEach((d, i) => { seqById[d.id] = i + 1; });

  list.innerHTML = visible.map((d) => {
    const obsId    = 'D' + seqById[d.id];
    const sev      = d.severity || 'Minor';
    const dtype    = d.defect_type || '—';
    const location = d.location || '';

    return `
    <div class="obs-card sevb-${sev}" id="obs-card-${d.id}" onclick="flyToDefect(chimDefects.find(x=>x.id===${d.id}))">
      <div class="obs-card-top">
        <span class="obs-id">${obsId}</span>
        <span class="obs-sev-tag sev-${sev}">${sev}</span>
        <div class="obs-card-actions">
          <button class="obs-btn-info" onclick="showDefectDetails(${d.id},event)" title="View in Project Overview">ℹ️</button>
          <button class="obs-btn-dl"   onclick="downloadDefectImage(${d.id},event)" title="Download JPG">⬇</button>
          ${window.CHIM_IS_ADMIN && d.shape_type ? `<button class="obs-btn-info" onclick="recentreDefectPin(${d.id},event)" title="Re-center pin on this shape">🎯</button>` : ''}
          ${window.CHIM_IS_ADMIN ? `<button class="obs-btn-edit" onclick="editDefect(${d.id},event)" title="Edit">✏️</button>` : ''}
          ${window.CHIM_IS_ADMIN ? `<button class="obs-btn-del"  onclick="deleteDefect(${d.id},event)" title="Delete">🗑</button>` : ''}
        </div>
      </div>
      <div class="obs-title-line">${escapeHtml(d.title)}</div>
      <div class="obs-meta-line">${escapeHtml(dtype)}${location ? ' · ' + escapeHtml(location) : ''}</div>
    </div>`;
  }).join('');

  renderKmlLayersSection();
}

/* ── Capture a framed screenshot (with overlay data-card) of a defect ───────
   Flies the camera to a tight, max-zoom, straight-on view of the defect (or
   its drawn shape), waits for tiles to settle, then rasterises the Cesium
   canvas plus an info card into a JPEG blob. Used both to auto-save an image
   to the server right after a finding is created, and as a fallback capture
   path if a finding somehow has no saved image yet.
─────────────────────────────────────────────────────────────────────────── */
function captureDefectCanvasBlob(defect, callback) {
  if (!defect.position || !chimTileset) { callback(null, null); return; }

  const target = Cesium.Cartesian3.fromDegrees(
    defect.position.lon, defect.position.lat, defect.position.height
  );
  const closeRange = computeDefectRange(defect);
  const shapePoints = (defect.shape_coords && defect.shape_coords.length > 1)
    ? defect.shape_coords.map(p => Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height))
    : null;
  const view = getStraightOnView(target, closeRange, shapePoints);

  chimViewer.camera.setView({
    destination: view.position,
    orientation: { direction: view.direction, up: view.up },
  });

  setTimeout(() => {
    chimViewer.scene.render();
    chimViewer.scene.render();
    chimViewer.scene.render();

    const cesiumCanvas = chimViewer.scene.canvas;
    const W = cesiumCanvas.width;
    const H = cesiumCanvas.height;

    const out = document.createElement('canvas');
    out.width  = W;
    out.height = H;
    const ctx = out.getContext('2d');

    try {
      ctx.drawImage(cesiumCanvas, 0, 0);
    } catch(e) {
      console.warn('Canvas draw error:', e);
    }

    out.toBlob(blob => callback(blob, view), 'image/jpeg', 0.92);
  }, 2200);
}

/* ── Auto-save a defect's snapshot image to the server/database ────────────
   Also persists the EXACT camera pose (position/direction/up) used for this
   capture, so flyToDefect can later fly back to that identical view instead
   of recomputing it live. Recomputing live depends on whatever 3D tile
   detail happens to be loaded/cached at that moment — which drifts as you
   orbit around for a few minutes (Cesium streams in different tiles, or
   evicts previously-loaded ones) — which is exactly what made clicking the
   same defect a little while later land on a different, wrong-looking
   angle. Reusing the stored pose makes fly-to deterministic. */
function captureAndSaveDefectImage(defect) {
  return new Promise(resolve => {
    captureDefectCanvasBlob(defect, async (blob, view) => {
      if (!blob) { resolve(null); return; }
      const fd = new FormData();
      fd.append('image', blob, `defect_${defect.id}.jpg`);
      if (view) {
        fd.append('cam_pos_x', view.position.x);
        fd.append('cam_pos_y', view.position.y);
        fd.append('cam_pos_z', view.position.z);
        fd.append('cam_dir_x', view.direction.x);
        fd.append('cam_dir_y', view.direction.y);
        fd.append('cam_dir_z', view.direction.z);
        fd.append('cam_up_x', view.up.x);
        fd.append('cam_up_y', view.up.y);
        fd.append('cam_up_z', view.up.z);
      }
      try {
        const res = await fetch(`/api/chimney-defects/${defect.id}/image`, { method: 'POST', body: fd });
        const data = await res.json();
        if (res.ok) {
          defect.image_url = data.image_url;
          // Keep the in-memory defect in sync so flyToDefect can use the
          // stored pose immediately, without needing a page reload.
          if (view) {
            defect.cam_pos = { x: view.position.x, y: view.position.y, z: view.position.z };
            defect.cam_dir = { x: view.direction.x, y: view.direction.y, z: view.direction.z };
            defect.cam_up  = { x: view.up.x, y: view.up.y, z: view.up.z };
          }
          renderDefectList();
          resolve(data.image_url);
          return;
        }
      } catch (e) {
        console.warn('Image upload failed:', e);
      }
      resolve(null);
    });
  });
}

/* ── View the saved server image in a lightbox ──────────────────────────────*/
function viewDefectImage(id, ev) {
  if (ev) ev.stopPropagation();
  const defect = chimDefects.find(d => d.id === id);
  if (!defect || !defect.image_url) {
    showHint('No saved image yet for this finding — it is captured automatically right after you save it.', 2600);
    return;
  }
  document.getElementById('img-modal-img').src = defect.image_url;
  document.getElementById('img-modal-backdrop').classList.add('open');
}

function closeImageModal(ev) {
  if (ev) ev.stopPropagation();
  document.getElementById('img-modal-backdrop').classList.remove('open');
  document.getElementById('img-modal-img').src = '';
}

/* ── Download the defect image straight from the server ─────────────────────
   If a snapshot was already saved to the database, this simply downloads that
   exact file. Only if a finding somehow has no saved image yet (e.g. a very
   old record) do we fall back to capturing a fresh screenshot on the spot.
─────────────────────────────────────────────────────────────────────────── */
function downloadDefectImage(id, ev) {
  ev.stopPropagation();
  const defect = chimDefects.find(d => d.id === id);
  if (!defect) return;

  if (defect.image_url) {
    const link = document.createElement('a');
    link.href = `/api/chimney-defects/${id}/image/download`;
    link.download = `defect_${id}.jpg`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    return;
  }

  showHint('No saved image yet — capturing one now…', 1800);
  captureAndSaveDefectImage(defect).then(url => {
    if (url) downloadDefectImage(id, { stopPropagation(){} });
    else alert('Could not capture a screenshot. Please try again.');
  });
}

function _roundRect(ctx, x, y, w, h, r) {
  if (typeof r === 'number') r = { tl:r, tr:r, br:r, bl:r };
  ctx.beginPath();
  ctx.moveTo(x + r.tl, y);
  ctx.lineTo(x + w - r.tr, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r.tr);
  ctx.lineTo(x + w, y + h - r.br);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r.br, y + h);
  ctx.lineTo(x + r.bl, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r.bl);
  ctx.lineTo(x, y + r.tl);
  ctx.quadraticCurveTo(x, y, x + r.tl, y);
  ctx.closePath();
}

async function loadDefects() {
  try {
    const res  = await fetch(`/api/chimney-projects/${window.CHIM_PROJECT_ID}/defects`);
    const data = await res.json();
    chimDefects = (data.defects || []).map(d => {
      // Always trust a fresh live recalculation over whatever height was
      // frozen into the database at creation time — that stored value
      // used whichever ground-detection result the browser happened to
      // have AT THAT MOMENT, which can go stale as the detection
      // algorithm keeps improving (this is exactly what caused a defect
      // drawn right at the top to show a height nowhere near the
      // separately-calculated total structure height). Falls back to the
      // stored value only if there's no position to recompute from.
      const liveHeight = d.position ? heightFromGround(d.position.height).toFixed(1) + ' m' : null;
      const height = liveHeight || d.height || '';
      // Quietly correct the server's stored copy if it's meaningfully out
      // of date, so the project overview page (which has no way to
      // recompute this itself) self-heals over time instead of staying
      // wrong forever. Best-effort — silently ignored if it fails (e.g.
      // logged in as a non-admin).
      if (liveHeight && d.height && d.height !== liveHeight) {
        fetch(`/api/chimney-defects/${d.id}/height-sync`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ height: liveHeight }),
        }).catch(() => {});
      }
      return { ...d, height };
    });
    chimDefects.forEach(addDefectEntity);
    renderDefectList();
  } catch (e) { renderDefectList(); }
}

/* ── Generate the PDF inspection report (server-rendered, opens in a tab) ──*/
/* Captures a full, zoomed-out shot of the whole chimney for the report's
   cover page. Resets the camera to frame the entire tileset, waits a couple
   of frames for tiles to settle, then reads the canvas back as a JPEG blob.
   Returns null (never throws) if anything about this fails — the report
   still generates fine without a cover photo, it just shows a placeholder. */
function captureCoverImage() {
  return new Promise((resolve) => {
    if (!chimTileset || !chimViewer) { resolve(null); return; }
    try {
      chimViewer.zoomTo(chimTileset).then(() => {
        setTimeout(() => {
          try {
            chimViewer.scene.render();
            chimViewer.scene.render();
            chimViewer.scene.render();
            chimViewer.scene.canvas.toBlob((blob) => resolve(blob || null), 'image/jpeg', 0.92);
          } catch (_) { resolve(null); }
        }, 300);
      }).catch(() => resolve(null));
    } catch (_) { resolve(null); }
  });
}

async function generateChimneyReport() {
  if (!chimDefects.length) {
    showHint('Add at least one finding before generating a report.', 2400);
    return;
  }
  const btn = document.getElementById('btn-generate-report');
  const originalLabel = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }
  showHint('Building the PDF report…', 4000);

  try {
    const coverBlob = await captureCoverImage();
    const fd = new FormData();
    if (coverBlob) fd.append('cover_image', coverBlob, 'cover.jpg');

    const res = await fetch(`/api/chimney-projects/${window.CHIM_PROJECT_ID}/report`, {
      method: 'POST',
      body: fd,
    });
    if (!res.ok) {
      let msg = `Report generation failed (HTTP ${res.status}).`;
      try {
        const data = await res.json();
        if (data && data.error) msg = data.error;
      } catch (_) { /* response wasn't JSON — keep the generic message */ }
      console.error('Report generation error:', msg);
      alert(msg);
      return;
    }
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    window.open(url, '_blank');
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (e) {
    console.error('Report generation error:', e);
    alert('Could not generate the report — network or server error. Please try again.');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
    hideHint();
  }
}

/* ── Measurement tool ─────────────────────────────────────────────────────── */

function chimToggleMeasureMode() {
  chimMeasureMode = !chimMeasureMode;
  chimDefectMode  = false;
  cancelDrawTool();
  document.getElementById('btn-measure').classList.toggle('active', chimMeasureMode);
  document.getElementById('btn-add-defect').classList.remove('active');
  clearMeasurement();
  if (chimMeasureMode) showHint('Click two points on the model to measure the distance between them.');
  else hideHint();
}

function clearMeasurement() {
  chimMeasurePoints = [];
  chimMeasureEntities.forEach(e => chimViewer.entities.remove(e));
  chimMeasureEntities = [];
  document.getElementById('measure-readout').classList.remove('open');
}

function handleMeasurePick(screenPos) {
  const cartesian = pickWorldPosition(screenPos);
  if (!cartesian) return;
  if (chimMeasurePoints.length === 2) clearMeasurement();
  chimMeasurePoints.push(cartesian);
  chimMeasureEntities.push(chimViewer.entities.add({
    position: cartesian,
    point: { pixelSize:9, color: Cesium.Color.fromCssColorString('#7fa7c9'), outlineColor: Cesium.Color.WHITE, outlineWidth:2, disableDepthTestDistance: Number.POSITIVE_INFINITY },
  }));
  if (chimMeasurePoints.length === 2) {
    const [p1,p2] = chimMeasurePoints;
    const dist = Cesium.Cartesian3.distance(p1, p2);
    chimMeasureEntities.push(chimViewer.entities.add({
      polyline: { positions: sampleSurfaceEdge(p1, p2, 20), width: 2,
        material: Cesium.Color.fromCssColorString('#7fa7c9'),
        depthFailMaterial: new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString('#7fa7c9').withAlpha(0.4)),
        clampToGround: false },
    }));
    const readout = document.getElementById('measure-readout');
    readout.textContent = `Distance: ${dist.toFixed(2)} m`;
    readout.classList.add('open');
    showHint('Click again to start a new measurement.', 2000);
  }
}

/* ── KML Upload & Visualization ───────────────────────────────────────────── */

function openKmlModal()  { if (!window.CHIM_IS_ADMIN) return; document.getElementById('kml-modal-backdrop').classList.add('open'); clearKmlSelection(); setKmlStatus('',''); }
function closeKmlModal() { document.getElementById('kml-modal-backdrop').classList.remove('open'); clearKmlSelection(); }

function onKmlFileSelected(file) {
  if (!file) return;
  const ext = file.name.split('.').pop().toLowerCase();
  if (ext !== 'kml') { setKmlStatus('Please select a valid .kml file.','error'); return; }
  kmlSelectedFile = file;
  document.getElementById('kml-selected-name').textContent = file.name;
  document.getElementById('kml-selected-file').style.display = 'flex';
  document.getElementById('kml-drop-zone').style.display    = 'none';
  document.getElementById('kml-upload-btn').disabled = false;
  setKmlStatus('','');
}

function clearKmlSelection() {
  kmlSelectedFile = null;
  document.getElementById('kml-file-input').value = '';
  document.getElementById('kml-selected-file').style.display = 'none';
  document.getElementById('kml-drop-zone').style.display     = 'block';
  document.getElementById('kml-upload-btn').disabled = true;
}

function setKmlStatus(msg, type) {
  const el = document.getElementById('kml-modal-status');
  el.textContent = msg;
  el.className = 'kml-status' + (type ? ` ${type}` : '');
}

function setupKmlDragDrop() {
  const zone = document.getElementById('kml-drop-zone');
  if (!zone) return;
  ['dragenter','dragover'].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.add('drag'); }));
  ['dragleave','drop'].forEach(ev =>     zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.remove('drag'); }));
  zone.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) onKmlFileSelected(f); });
}

async function uploadKml() {
  if (!kmlSelectedFile) return;
  const btn = document.getElementById('kml-upload-btn');
  btn.disabled = true; btn.textContent = 'Loading…'; setKmlStatus('','');

  try {
    const kmlText = await kmlSelectedFile.text();
    const blob    = new Blob([kmlText], { type:'application/vnd.google-earth.kml+xml' });
    const blobUrl = URL.createObjectURL(blob);

    const dataSource = await Cesium.KmlDataSource.load(blobUrl, {
      camera: chimViewer.scene.camera,
      canvas: chimViewer.scene.canvas,
      clampToGround: true,
    });
    URL.revokeObjectURL(blobUrl);

    styleKmlEntities(dataSource.entities.values);

    const layerId = `kml_${Date.now()}`;
    const layer = { id: layerId, name: kmlSelectedFile.name, dataSource, visible: true };
    chimViewer.dataSources.add(dataSource);
    kmlLayers.push(layer);

    try { await chimViewer.flyTo(dataSource, { duration:1.2 }); } catch(_) {}

    setKmlStatus(`✓ "${kmlSelectedFile.name}" loaded with ${dataSource.entities.values.length} features.`, 'ok');
    updateKmlBadge();
    renderKmlLayersSection();
    setTimeout(() => closeKmlModal(), 900);
  } catch (err) {
    console.error(err);
    setKmlStatus('Failed to parse KML — please check the file format.', 'error');
    btn.disabled = false; btn.textContent = 'Load KML';
  }
}

function styleKmlEntities(entities) {
  entities.forEach(entity => {
    const text = ((entity.name||'') + ' ' + (entity.description?.getValue()||'')).toLowerCase();
    let color;
    if      (text.includes('critical')||text.includes('severe')||text.includes('high'))   color = Cesium.Color.fromCssColorString('#c94b42').withAlpha(0.85);
    else if (text.includes('moderate')||text.includes('medium')||text.includes('warning')) color = Cesium.Color.fromCssColorString('#b9821f').withAlpha(0.85);
    else                                                                                    color = Cesium.Color.fromCssColorString('#1f9d68').withAlpha(0.85);

    if (entity.point)    { entity.point.color = color; entity.point.pixelSize = 12; entity.point.outlineColor = Cesium.Color.WHITE; entity.point.outlineWidth = 2; entity.point.disableDepthTestDistance = Number.POSITIVE_INFINITY; }
    if (entity.polyline) { entity.polyline.material = color; entity.polyline.width = 3; entity.polyline.depthFailMaterial = new Cesium.ColorMaterialProperty(color.withAlpha(0.4)); }
    if (entity.polygon)  { entity.polygon.material = color.withAlpha(0.35); entity.polygon.outlineColor = color; entity.polygon.outline = true; }
    if (entity.billboard){ entity.billboard.color = color; entity.billboard.disableDepthTestDistance = Number.POSITIVE_INFINITY; }
    if (entity.label)    { entity.label.disableDepthTestDistance = Number.POSITIVE_INFINITY; entity.label.showBackground = true; entity.label.backgroundColor = Cesium.Color.fromCssColorString('#1b1e24cc'); entity.label.fillColor = Cesium.Color.WHITE; }
  });
}

function toggleKmlLayer(layerId) {
  const layer = kmlLayers.find(l => l.id === layerId);
  if (!layer) return;
  layer.visible = !layer.visible;
  layer.dataSource.show = layer.visible;
  renderKmlLayersSection();
}

function removeKmlLayer(layerId) {
  const idx = kmlLayers.findIndex(l => l.id === layerId);
  if (idx === -1) return;
  chimViewer.dataSources.remove(kmlLayers[idx].dataSource, true);
  kmlLayers.splice(idx, 1);
  updateKmlBadge();
  renderKmlLayersSection();
}

function updateKmlBadge() {
  const badge = document.getElementById('kml-layer-count');
  if (kmlLayers.length > 0) { badge.textContent = kmlLayers.length; badge.style.display = 'flex'; }
  else badge.style.display = 'none';
}

function renderKmlLayersSection() {
  const el = document.getElementById('kml-layers-section');
  if (!el) return;
  if (!kmlLayers.length) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <div class="kml-layer-section">
      <div class="kml-layer-head">
        <span>KML Overlays</span>
        <span style="color:var(--text-muted);font-size:10px;">${kmlLayers.length} layer${kmlLayers.length>1?'s':''}</span>
      </div>
      ${kmlLayers.map(l => `
        <div class="kml-layer-item">
          <button class="kml-layer-toggle ${l.visible?'':'off'}" onclick="toggleKmlLayer('${l.id}')" title="${l.visible?'Hide':'Show'}"></button>
          <span class="kml-layer-name" title="${escapeHtml(l.name)}">📄 ${escapeHtml(l.name)}</span>
          <button class="kml-layer-del" onclick="removeKmlLayer('${l.id}')" title="Remove layer">✕</button>
        </div>`).join('')}
    </div>`;
}

/* ═══════════════════════════════════════════════════════════════════════════
   DRAWING TOOLBAR
═══════════════════════════════════════════════════════════════════════════ */

function setDrawTool(tool) {
  if (!window.CHIM_IS_ADMIN) return;
  if (activeDrawTool === tool) { cancelDrawTool(); return; }
  cancelDrawTool();

  chimDefectMode  = false;
  chimMeasureMode = false;
  document.getElementById('btn-add-defect').classList.remove('active');
  document.getElementById('btn-measure').classList.remove('active');

  activeDrawTool = tool;

  ['draw-line','draw-rect','draw-polygon','draw-circle'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('active', id === 'draw-' + tool);
  });

  const hints = {
    line:    'Click 2 points on the chimney to draw a line defect.',
    rect:    'Click 2 diagonal corners on the chimney to draw a rectangle.',
    polygon: 'Click points on the chimney. Right-click or press ✓ to finish (min 3 pts).',
    circle:  'Click the centre, then a second point to set the radius.',
  };
  showHint(hints[tool] || '');

  drawMouseHandler = new Cesium.ScreenSpaceEventHandler(chimViewer.scene.canvas);
  drawMouseHandler.setInputAction(onDrawMouseMove, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
}

function cancelDrawTool() {
  if (!activeDrawTool) return;
  activeDrawTool = null;
  drawPoints = [];
  drawTempEntities.forEach(e => chimViewer.entities.remove(e));
  drawTempEntities = [];
  if (drawShapeEntity) { chimViewer.entities.remove(drawShapeEntity); drawShapeEntity = null; }
  if (drawMouseHandler) { drawMouseHandler.destroy(); drawMouseHandler = null; }
  ['draw-line','draw-rect','draw-polygon','draw-circle'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
  });
  const confirmBtn = document.getElementById('draw-confirm-btn');
  if (confirmBtn) confirmBtn.style.display = 'none';
  hideHint();
}

function cartesianToCarto(c) {
  const carto = Cesium.Cartographic.fromCartesian(c);
  return { lon: Cesium.Math.toDegrees(carto.longitude), lat: Cesium.Math.toDegrees(carto.latitude), height: carto.height };
}

function onDrawMouseMove(movement) {
  if (!activeDrawTool || drawPoints.length === 0) return;
  const pos = pickWorldPosition(movement.endPosition);
  if (!pos) return;

  if (drawShapeEntity) { chimViewer.entities.remove(drawShapeEntity); drawShapeEntity = null; }

  const pts = [...drawPoints, pos];
  drawShapeEntity = buildPreviewShape(activeDrawTool, pts);
}

const PREVIEW_COLOR = new Cesium.ColorMaterialProperty(Cesium.Color.YELLOW.withAlpha(0.85));

// Cheap, no-raycast circle preview — same local tangent-plane orientation
// as buildSurfaceRing's final version, but skipping the per-segment
// scene.pick()/pickPosition() calls (each one is a synchronous GPU
// readback). Those are fine to pay once, on commit, but doing 32+ of them
// on every single mousemove is what made the live radius preview lag so
// far behind the cursor it looked like there wasn't one.
function buildLocalRing(center, edgePoint, segments) {
  segments = segments || 48;
  const radius = Cesium.Cartesian3.distance(center, edgePoint);
  const frame = getLocalTangentFrame(center);
  const result = [];
  for (let i = 0; i <= segments; i++) {
    const theta = (i / segments) * Math.PI * 2;
    const offset = Cesium.Cartesian3.add(
      Cesium.Cartesian3.multiplyByScalar(frame.horizontal, radius * Math.cos(theta), new Cesium.Cartesian3()),
      Cesium.Cartesian3.multiplyByScalar(frame.up, radius * Math.sin(theta), new Cesium.Cartesian3()),
      new Cesium.Cartesian3()
    );
    result.push(nudgeOutward(Cesium.Cartesian3.add(center, offset, new Cesium.Cartesian3())));
  }
  return result;
}

// Live drag preview for all draw tools. Deliberately cheap: straight
// segments between the actual picked points (just nudged outward so they
// don't z-fight with the model), with NO per-segment surface ray-casting.
// The precise curve-hugging fit (sampleSurfaceEdge / buildSurfacePolygon /
// buildSurfaceRing) still runs, but only once, when the shape is actually
// committed — see finaliseDrawing(). Running that same expensive fit on
// every mousemove is what made polygon/circle drawing feel laggy and
// unresponsive while dragging.
function buildPreviewShape(tool, pts) {
  const nudged = pts.map(p => nudgeOutward(Cesium.Cartesian3.clone(p)));

  if (tool === 'line' && pts.length >= 2) {
    return chimViewer.entities.add({
      polyline: { positions: nudged, width: 3, material: PREVIEW_COLOR },
    });
  }
  if (tool === 'rect' && pts.length >= 2) {
    const corners = rectCorners(pts[0], pts[pts.length - 1]).map(c => nudgeOutward(c));
    return chimViewer.entities.add({
      polyline: { positions: [...corners, corners[0]], width: 3, material: PREVIEW_COLOR },
    });
  }
  if (tool === 'polygon' && pts.length >= 2) {
    const positions = pts.length === 2 ? nudged : [...nudged, nudged[0]];
    return chimViewer.entities.add({
      polyline: { positions, width: 3, material: PREVIEW_COLOR },
    });
  }
  if (tool === 'circle' && pts.length >= 2) {
    return chimViewer.entities.add({
      polyline: { positions: buildLocalRing(pts[0], pts[pts.length - 1], 48), width: 3, material: PREVIEW_COLOR },
    });
  }
  return null;
}

function rectCorners(p1, p2) {
  // Build the rectangle in the local tangent frame (circumferential ×
  // vertical) at the drag's midpoint, instead of mixing raw longitude and
  // latitude — that old approach also forced every corner to the SAME
  // height, so a rectangle dragged top-to-bottom collapsed onto one flat
  // plane and read as lying horizontally no matter how it was drawn.
  const mid = Cesium.Cartesian3.midpoint(p1, p2, new Cesium.Cartesian3());
  const frame = getLocalTangentFrame(mid);

  const toLocal = (p) => {
    const d = Cesium.Cartesian3.subtract(p, mid, new Cesium.Cartesian3());
    return {
      h: Cesium.Cartesian3.dot(d, frame.horizontal),
      v: Cesium.Cartesian3.dot(d, frame.up),
    };
  };
  const fromLocal = (h, v) => {
    const out = Cesium.Cartesian3.clone(mid, new Cesium.Cartesian3());
    Cesium.Cartesian3.add(out, Cesium.Cartesian3.multiplyByScalar(frame.horizontal, h, new Cesium.Cartesian3()), out);
    Cesium.Cartesian3.add(out, Cesium.Cartesian3.multiplyByScalar(frame.up, v, new Cesium.Cartesian3()), out);
    return out;
  };

  const l1 = toLocal(p1);
  const l2 = toLocal(p2);
  return [
    fromLocal(l1.h, l1.v),
    fromLocal(l2.h, l1.v),
    fromLocal(l2.h, l2.v),
    fromLocal(l1.h, l2.v),
  ];
}

// Computes where a shape's marker/pin should sit: the midpoint of the
// shape's own bounding box (in its local horizontal/up frame), snapped
// back onto the surface — not a simple average of its outline points.
// Averaging outline/perimeter points directly gets pulled toward
// wherever points are denser along that outline (e.g. a longer or more
// curved side of an irregular, elongated shape like a traced crack),
// which is what was visibly displacing the marker away from the shape's
// actual visual middle. The bounding-box midpoint lands at the true
// visual center regardless of how unevenly the outline points happen to
// be distributed. Shared between finaliseDrawing() (new shapes) and
// recentreDefectPin() (fixing existing ones without redrawing them).
function computeShapeCentroid(tool, renderCorners) {
  if (tool === 'circle') {
    // renderCorners[0] is the center click itself — an exact on-surface
    // pick, nothing to compute.
    return Cesium.Cartesian3.clone(renderCorners[0]);
  }

  let fittedPositions = null;
  try {
    fittedPositions = (tool === 'line')
      ? (renderCorners.length === 2
          ? sampleSurfaceEdge(renderCorners[0], renderCorners[1], SURFACE_SEGMENTS)
          : buildSurfacePolyline(renderCorners))
      : buildSurfacePolygon(renderCorners);
  } catch (_) { fittedPositions = null; }

  if (fittedPositions && fittedPositions.length > 0) {
    const origin = fittedPositions[0];
    const frame = getLocalTangentFrame(origin);
    let minH = Infinity, maxH = -Infinity, minV = Infinity, maxV = -Infinity;
    fittedPositions.forEach(p => {
      const d = Cesium.Cartesian3.subtract(p, origin, new Cesium.Cartesian3());
      const h = Cesium.Cartesian3.dot(d, frame.horizontal);
      const v = Cesium.Cartesian3.dot(d, frame.up);
      if (h < minH) minH = h;
      if (h > maxH) maxH = h;
      if (v < minV) minV = v;
      if (v > maxV) maxV = v;
    });
    const midH = (minH + maxH) / 2;
    const midV = (minV + maxV) / 2;
    const bboxMid = Cesium.Cartesian3.add(
      origin,
      Cesium.Cartesian3.add(
        Cesium.Cartesian3.multiplyByScalar(frame.horizontal, midH, new Cesium.Cartesian3()),
        Cesium.Cartesian3.multiplyByScalar(frame.up, midV, new Cesium.Cartesian3()),
        new Cesium.Cartesian3()
      ),
      new Cesium.Cartesian3()
    );
    // The bounding-box midpoint is synthesized from h/v coordinates, not
    // itself a point that was ever picked on the model — snap it back
    // onto the actual surface. Tolerance scales with the shape's own
    // bounding box size, same reasoning as the fallback path below.
    const maxJump = Cesium.Math.clamp(((maxH - minH) + (maxV - minV)) * 0.3, 0.5, 3.0);
    return reprojectPointToSurface(bboxMid, maxJump);
  }

  // Fallback if the surface fit failed outright — the old
  // raw-average-then-reproject approach, better than nothing.
  const rawAvg = renderCorners.reduce(
    (acc, p) => Cesium.Cartesian3.add(acc, p, new Cesium.Cartesian3()),
    new Cesium.Cartesian3()
  );
  Cesium.Cartesian3.divideByScalar(rawAvg, renderCorners.length, rawAvg);
  let span = 0;
  for (let i = 0; i < renderCorners.length; i++) {
    for (let j = i + 1; j < renderCorners.length; j++) {
      span = Math.max(span, Cesium.Cartesian3.distance(renderCorners[i], renderCorners[j]));
    }
  }
  const maxJump = Cesium.Math.clamp(span * 0.4, 0.5, 3.0);
  return reprojectPointToSurface(rawAvg, maxJump);
}

function handleDrawClick(screenPos) {
  const pos = pickWorldPosition(screenPos);
  if (!pos) { showHint('Click directly on the chimney model.', 1800); return; }

  const dot = chimViewer.entities.add({
    position: nudgeOutward(pos),
    point: { pixelSize: 8, color: Cesium.Color.YELLOW, outlineColor: Cesium.Color.WHITE, outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY },
  });
  drawTempEntities.push(dot);
  drawPoints.push(pos);

  if (activeDrawTool === 'polygon' && drawPoints.length >= 3) {
    const confirmBtn = document.getElementById('draw-confirm-btn');
    if (confirmBtn) confirmBtn.style.display = 'flex';
  }

  // A line can be just a straight start-to-end mark (2 points), or a
  // multi-point trace following an irregular path (e.g. a wandering
  // crack) — after the 2nd point, show the confirm button so more points
  // CAN be added, but don't force it: right-click or the confirm button
  // finishes it whenever the user is done, same as polygon.
  if (activeDrawTool === 'line' && drawPoints.length >= 2) {
    const confirmBtn = document.getElementById('draw-confirm-btn');
    if (confirmBtn) confirmBtn.style.display = 'flex';
  }

  if ((activeDrawTool === 'rect' || activeDrawTool === 'circle') && drawPoints.length === 2) {
    finaliseDrawing(screenPos);
  }
}

function finaliseDrawing(screenPos) {
  if (!activeDrawTool || drawPoints.length < 2) return;

  const tool   = activeDrawTool;
  const pts    = [...drawPoints];

  let shapeCoords;
  let renderCorners; // the same corner set addDefectEntity() will later reconstruct from shapeCoords — used below so the marker matches the actual rendered outline exactly
  if (tool === 'rect') {
    renderCorners = rectCorners(pts[0], pts[1]);
    shapeCoords = renderCorners.map(cartesianToCarto);
  } else {
    renderCorners = pts;
    shapeCoords = pts.map(cartesianToCarto);
  }

  // Marker/pin position — this is what flyToDefect and the auto-captured
  // close-up image both target, so it needs to be exactly on the model
  // surface and at the true visual center of the shape.
  const centroidCartesian = computeShapeCentroid(tool, renderCorners);

  const centroidScreen = Cesium.SceneTransforms.wgs84ToWindowCoordinates(chimViewer.scene, centroidCartesian);
  const popupPos = centroidScreen || { x: window.innerWidth / 2, y: window.innerHeight / 2 };

  const savedTool = activeDrawTool;
  cancelDrawTool();

  const centCarto = Cesium.Cartographic.fromCartesian(centroidCartesian);
  chimPendingPosition = { lon: Cesium.Math.toDegrees(centCarto.longitude), lat: Cesium.Math.toDegrees(centCarto.latitude), height: centCarto.height };

  openDefectForm(popupPos, savedTool, shapeCoords);
}

/* ── Boot ─────────────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  setupUpload();
  setupKmlDragDrop();
  if (window.CHIM_TILESET_URL) initViewer();
});