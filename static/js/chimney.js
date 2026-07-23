/* chimney.js — 3D Inspection dashboard (Chimney / Water Tank / …) */

function openModal(id)  { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

function openAddChimneyModal() {
  document.getElementById('add-chimney-form').reset();
  document.getElementById('ac-error').style.display = 'none';
  openModal('add-chimney-modal');
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str == null ? '' : String(str);
  return d.innerHTML;
}

async function submitAddChimney() {
  const asset  = document.getElementById('ac-asset').value.trim();
  const lat    = document.getElementById('ac-lat').value.trim();
  const lng    = document.getElementById('ac-lng').value.trim();
  const errBox = document.getElementById('ac-error');
  errBox.style.display = 'none';

  if (!asset) { errBox.textContent = 'Asset name is required.'; errBox.style.display = 'block'; return; }
  if (lat === '' || isNaN(parseFloat(lat))) { errBox.textContent = 'A valid latitude is required.'; errBox.style.display = 'block'; return; }
  if (lng === '' || isNaN(parseFloat(lng))) { errBox.textContent = 'A valid longitude is required.'; errBox.style.display = 'block'; return; }

  const fd = new FormData();
  fd.append('asset_category', document.getElementById('ac-project-type').value);
  fd.append('asset_name', asset);
  fd.append('inspection_type', document.getElementById('ac-inspection-type').value);
  fd.append('structure_type', document.getElementById('ac-structure-type').value);
  fd.append('inspection_scope', document.getElementById('ac-inspection-scope').value.trim());
  fd.append('latitude', lat);
  fd.append('longitude', lng);
  fd.append('target_completion_date', document.getElementById('ac-timeline').value);
  fd.append('pilots', document.getElementById('ac-pilots').value.trim());

  const btn = document.getElementById('ac-submit-btn');
  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const res  = await fetch('/api/chimney-projects', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) {
      errBox.textContent = data.error || 'Could not save project.';
      errBox.style.display = 'block';
      return;
    }
    closeModal('add-chimney-modal');
    // Go directly into the new project — the server decides whether that's
    // the full chimney 3D viewer or the (for now, placeholder) water tank
    // cover page, based on the project type chosen above.
    window.location.href = `/chimney-projects/${data.id}`;
  } catch (e) {
    errBox.textContent = 'Network error — please try again.';
    errBox.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'Save Chimney';
  }
}

const DEFAULT_COVER_IMAGE = '/static/images/chimney_cover_default.png';

function openProject(id) {
  window.location.href = '/chimney-projects/' + id;
}

function deleteChimneyProject(event, id, name) {
  event.stopPropagation(); // don't trigger the card's openProject()
  requestDelete({
    url: `/api/chimney-projects/${id}`,
    label: name,
    onSuccess: loadChimneyProjects
  });
}

function chimneyCardHtml(p) {
  const modelTag = p.has_model
    ? '<div class="card-model-tag ready">3D MODEL READY</div>'
    : '<div class="card-model-tag pending">MODEL PENDING</div>';
  const typeTag = p.asset_category === 'water_tank' ? 'WATER TANK' : (p.structure_type || 'CHIMNEY');
  return `
    <div class="project-card" style="position:relative;" onclick="openProject(${p.id})">
      <div class="card-banner" style="background-image:url('${DEFAULT_COVER_IMAGE}');">
        <div class="card-tag">${escapeHtml(typeTag)}</div>
        ${modelTag}
      </div>
      <div class="card-body">
        <div class="card-title">${escapeHtml(p.asset_name)}</div>
        <div class="card-desc">${escapeHtml(p.inspection_type || 'Inspection type not set')} · ${p.latitude.toFixed(4)}, ${p.longitude.toFixed(4)}</div>
        <div class="card-meta">
          <span class="meta-chip active">${escapeHtml(p.status || 'Active')}</span>
        </div>
        <div class="card-footer">
          <span class="card-action">Open Inspection →</span>
          <span class="card-defect-badge">${p.defect_count} finding(s)</span>
        </div>
      </div>
    </div>`;
}

async function loadChimneyProjects() {
  const grid = document.getElementById('chimney-grid');
  if (!grid) return;

  try {
    const res      = await fetch('/api/chimney-projects');
    const data     = await res.json();
    const projects = data.projects || [];

    if (!projects.length) {
      grid.innerHTML = `
        <div style="grid-column:1/-1;padding:60px;text-align:center;color:var(--text-muted);font-size:13px;">
          No projects yet. Click <b>+ Add Project</b> to create one.
        </div>`;
      return;
    }

    grid.innerHTML = projects.map(chimneyCardHtml).join('');

  } catch (e) {
    grid.innerHTML = `
      <div style="grid-column:1/-1;padding:40px;text-align:center;color:var(--danger);font-size:13px;">
        Could not load projects.
      </div>`;
  }
}

document.addEventListener('DOMContentLoaded', loadChimneyProjects);
