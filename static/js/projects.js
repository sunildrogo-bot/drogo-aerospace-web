/* projects.js — shared "Add Project" + dynamic project-grid logic.
   Used by projects.html (Transmission Line) and land_survey_dashboard.html
   (Land Survey), and any future module listing page. */

function openModal(id)  { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

function openAddProjectModal() {
  document.getElementById('add-project-form').reset();
  document.getElementById('ap-error').style.display = 'none';
  openModal('add-project-modal');
}

async function submitAddProject() {
  const name    = document.getElementById('ap-name').value.trim();
  const email   = document.getElementById('ap-email').value.trim();
  const errBox  = document.getElementById('ap-error');
  errBox.style.display = 'none';

  if (!name) { errBox.textContent = 'Project name is required.'; errBox.style.display = 'block'; return; }
  if (!email || !email.includes('@')) { errBox.textContent = 'A valid owner email is required.'; errBox.style.display = 'block'; return; }

  const logoInput = document.getElementById('ap-logo');
  const logoFile  = logoInput.files[0];
  if (logoFile) {
    const ext = logoFile.name.split('.').pop().toLowerCase();
    if (!['jpg', 'jpeg', 'png'].includes(ext)) {
      errBox.textContent = 'Company logo must be a .jpg or .png file.';
      errBox.style.display = 'block';
      return;
    }
  }

  const fd = new FormData();
  fd.append('module', window.CURRENT_MODULE || '');
  fd.append('name', name);
  fd.append('contact_no', document.getElementById('ap-contact').value.trim());
  fd.append('email', email);
  fd.append('country', document.getElementById('ap-country') ? document.getElementById('ap-country').value.trim() : '');
  fd.append('state', document.getElementById('ap-state').value.trim());
  if (logoFile) fd.append('logo', logoFile);

  // TRANS module's richer field set — only present (and only sent) on
  // that module's own dedicated Add Project modal.
  const clientNameEl = document.getElementById('ap-client-name');
  const divisionsEl  = document.getElementById('ap-planned-divisions');
  const towersEl     = document.getElementById('ap-planned-towers');
  const timelineEl   = document.getElementById('ap-timeline');
  if (clientNameEl) fd.append('client_name', clientNameEl.value.trim());
  if (divisionsEl && divisionsEl.value !== '') fd.append('planned_divisions', divisionsEl.value);
  if (towersEl && towersEl.value !== '') fd.append('planned_towers', towersEl.value);
  if (timelineEl) fd.append('timeline', timelineEl.value.trim());

  const btn = document.getElementById('ap-submit-btn');
  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const res = await fetch('/api/projects', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) {
      errBox.textContent = data.error || 'Could not save project.';
      errBox.style.display = 'block';
      return;
    }
    closeModal('add-project-modal');
    loadProjects();
  } catch (e) {
    errBox.textContent = 'Network error — please try again.';
    errBox.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'Save Project';
  }
}

/* Default cover photo for project cards — the same transmission-line image
   used across the static/legacy pages (DVC, Kothegudam, Land Survey banner). */
const DEFAULT_COVER_IMAGE = 'https://images.pexels.com/photos/32599646/pexels-photo-32599646.jpeg?auto=compress&cs=tinysrgb&w=800';

function deleteProjectCard(event, id, name) {
  event.preventDefault();
  event.stopPropagation();
  requestDelete({
    url: `/api/projects/${id}`,
    label: name,
    onSuccess: loadProjects
  });
}

function projectCardHtml(p) {
  const banner = p.legacy_banner || DEFAULT_COVER_IMAGE;
  const href = p.legacy_route ? `/${p.legacy_route}` :
    ((window.CURRENT_MODULE === 'Transmission Line' || window.CURRENT_MODULE === 'TRANS') ? `/projects/${p.id}/map` : `/projects/${p.id}/info`);
  const lineBadge = p.legacy_route
    ? ''
    : `<span class="card-lines-badge">${p.division_count} division(s) · ${p.line_count} line(s)</span>`;
  const logoImg = p.logo_url
    ? `<img class="card-logo" src="${escapeHtml(p.logo_url)}" alt="${escapeHtml(p.name)} logo"/>`
    : '';
  return `
    <a href="${href}" class="project-card">
      <div class="card-banner" style="background-image:url('${banner}');">
        <div class="card-tag">${(p.state || p.module || '').toUpperCase() || 'PROJECT'}</div>
      </div>
      <div class="card-body">
        <div class="card-content-row">
          <div class="card-text-col">
            <div class="card-title">${escapeHtml(p.name)}</div>
            <div class="card-desc">${escapeHtml([p.state, p.country].filter(Boolean).join(', ') || 'No location set')}</div>
            <div class="card-meta">
              <span class="meta-chip active">Active</span>
            </div>
          </div>
          ${logoImg}
        </div>
        <div class="card-footer">
          <span class="card-action">Open Project →</span>
          ${lineBadge}
        </div>
      </div>
    </a>`;
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str == null ? '' : String(str);
  return d.innerHTML;
}

async function loadProjects() {
  const grid = document.getElementById('projects-grid');
  if (!grid) return;
  try {
    const res = await fetch(`/api/projects?module=${encodeURIComponent(window.CURRENT_MODULE || '')}`);
    const data = await res.json();
    const projects = data.projects || [];
    if (!projects.length) {
      grid.innerHTML = `<div style="grid-column:1/-1;padding:40px;text-align:center;color:var(--text-muted);font-size:13px;">
        No projects yet. Click <b>+ Add Project</b> to create the first one.</div>`;
      return;
    }
    grid.innerHTML = projects.map(projectCardHtml).join('');
  } catch (e) {
    grid.innerHTML = `<div style="grid-column:1/-1;padding:40px;text-align:center;color:var(--danger);font-size:13px;">
      Could not load projects.</div>`;
  }
}

document.addEventListener('DOMContentLoaded', loadProjects);
