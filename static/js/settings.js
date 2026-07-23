/* settings.js — Settings page: delete password, activity log, announcements */

function settingsNav(viewId, el) {
  document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.querySelectorAll('.settings-view').forEach(v => v.classList.remove('active'));
  document.getElementById(viewId).classList.add('active');

  if (viewId === 'view-activity') loadActivityLog();
  if (viewId === 'view-announcements') loadAnnouncementsTab();
  if (viewId === 'view-help') loadHelpTab();
  if (viewId === 'view-projects') loadAllProjects();
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('help-tab-badge')) refreshHelpBadge();

  // Cross-page links (the new-ticket dashboard popup, mainly) land here
  // with ?view=help so the Raised Tickets tab opens automatically instead
  // of always falling back to Delete Password.
  const params = new URLSearchParams(window.location.search);
  if (params.get('view') === 'help') {
    const tab = document.querySelector('.settings-tab[onclick*="view-help"]');
    if (tab) settingsNav('view-help', tab);
  }
});

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str == null ? '' : String(str);
  return d.innerHTML;
}

async function saveDeletePassword() {
  const currentInput = document.getElementById('dp-current');
  const newInput = document.getElementById('dp-new');
  const errBox = document.getElementById('dp-error');
  const successMsg = document.getElementById('dp-success');
  const btn = document.getElementById('dp-save-btn');

  errBox.style.display = 'none';
  successMsg.style.display = 'none';

  const newPassword = newInput.value;
  if (!newPassword || newPassword.length < 4) {
    errBox.textContent = 'New password must be at least 4 characters.';
    errBox.style.display = 'block';
    return;
  }

  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const res = await fetch('/api/settings/delete-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        current_password: currentInput ? currentInput.value : '',
        new_password: newPassword
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      errBox.textContent = data.error || 'Could not save password.';
      errBox.style.display = 'block';
      return;
    }
    successMsg.style.display = 'block';
    newInput.value = '';
    if (currentInput) currentInput.value = '';
    setTimeout(() => window.location.reload(), 900);
  } catch (e) {
    errBox.textContent = 'Network error — please try again.';
    errBox.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = currentInput ? 'Change Password' : 'Set Password';
  }
}

async function loadActivityLog() {
  const body = document.getElementById('activity-log-body');
  try {
    const res = await fetch('/api/settings/activity-log');
    const data = await res.json();
    const entries = data.entries || [];
    if (!entries.length) {
      body.innerHTML = `<tr class="empty-row"><td colspan="7">No activity recorded yet.</td></tr>`;
      return;
    }
    body.innerHTML = entries.map(e => `
      <tr>
        <td><span class="action-chip ${escapeHtml(e.action)}">${escapeHtml(e.action)}</span></td>
        <td>${escapeHtml(e.entity_name || '—')}</td>
        <td>${escapeHtml(e.module || '—')}</td>
        <td>${escapeHtml(e.performed_by || '—')}</td>
        <td>${escapeHtml(e.role || '—')}</td>
        <td>${escapeHtml(e.duration || '—')}</td>
        <td title="${escapeHtml(e.created_at)}">${timeAgo(e.created_at_iso)}</td>
      </tr>`).join('');
  } catch (e) {
    body.innerHTML = `<tr class="empty-row"><td colspan="7">Could not load activity log.</td></tr>`;
  }
}

function timeAgo(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (isNaN(then)) return '—';
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return 'Just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

async function submitAnnouncement() {
  const titleInput = document.getElementById('ann-title');
  const messageInput = document.getElementById('ann-message');
  const imageInput = document.getElementById('ann-image');
  const errBox = document.getElementById('ann-error');
  const successMsg = document.getElementById('ann-success');
  const btn = document.getElementById('ann-submit-btn');

  errBox.style.display = 'none';
  successMsg.style.display = 'none';

  const title = titleInput.value.trim();
  if (!title) {
    errBox.textContent = 'Please give the announcement a title.';
    errBox.style.display = 'block';
    return;
  }

  const fd = new FormData();
  fd.append('title', title);
  fd.append('message', messageInput.value.trim());
  if (imageInput.files[0]) fd.append('image', imageInput.files[0]);

  btn.disabled = true; btn.textContent = 'Posting…';
  try {
    const res = await fetch('/api/announcements', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      errBox.textContent = data.error || 'Could not post — please try again.';
      errBox.style.display = 'block';
      return;
    }
    successMsg.style.display = 'block';
    titleInput.value = '';
    messageInput.value = '';
    imageInput.value = '';
    loadAnnouncementsTab();
  } catch (e) {
    errBox.textContent = 'Network error — please try again.';
    errBox.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'Post Announcement';
  }
}

async function loadAnnouncementsTab() {
  const list = document.getElementById('ann-list');
  if (!list) return;
  try {
    const res = await fetch('/api/announcements');
    const data = await res.json();
    const items = data.announcements || [];
    if (!items.length) {
      list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:12.5px;">Nothing posted yet.</div>`;
      return;
    }
    list.innerHTML = items.map(a => `
      <div class="ann-card">
        ${a.image_url ? `<img src="${escapeHtml(a.image_url)}" alt=""/>` : ''}
        <div class="ann-card-body">
          <div class="ann-card-top">
            <span class="ann-title">${escapeHtml(a.title)}</span>
            <button class="ann-delete-btn" onclick="deleteAnnouncement(${a.id})">Delete</button>
          </div>
          <div class="ann-meta">${escapeHtml(a.created_by)} · ${escapeHtml(a.created_at)}</div>
          ${a.message ? `<div class="ann-message">${escapeHtml(a.message)}</div>` : ''}
        </div>
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:12.5px;">Could not load announcements.</div>`;
  }
}

async function deleteAnnouncement(id) {
  if (!confirm('Delete this announcement?')) return;
  try {
    await fetch(`/api/announcements/${id}`, { method: 'DELETE' });
    loadAnnouncementsTab();
  } catch (e) { /* ignore */ }
}

function _tkPillClass(status) {
  return status === 'Resolved' ? 'st-resolved' : status === 'Checking' ? 'st-checking' : 'st-open';
}

async function refreshHelpBadge() {
  const badge = document.getElementById('help-tab-badge');
  if (!badge) return;
  try {
    const res = await fetch('/api/help-tickets');
    const data = await res.json();
    const pending = (data.tickets || []).filter(t => t.status !== 'Resolved').length;
    badge.textContent = pending;
    badge.style.display = pending > 0 ? 'inline-flex' : 'none';
  } catch (e) { /* non-critical */ }
}

async function loadHelpTab() {
  const list = document.getElementById('help-tab-list');
  if (!list) return;
  try {
    const res = await fetch('/api/help-tickets');
    const data = await res.json();
    const tickets = data.tickets || [];
    refreshHelpBadge();
    if (!tickets.length) {
      list.innerHTML = `<div style="padding:30px;text-align:center;color:var(--text-muted);font-size:12.5px;">No help requests yet.</div>`;
      return;
    }
    list.innerHTML = tickets.map(t => `
      <div class="tk-row">
        <div class="tk-row-top">
          <span class="tk-subject">${escapeHtml(t.subject)}</span>
          <span class="tk-pill ${_tkPillClass(t.status)}">${escapeHtml(t.status)}</span>
        </div>
        <div class="tk-meta">${escapeHtml(t.reporter_type)} — ${escapeHtml(t.submitted_by)} · ${escapeHtml(t.created_at)}</div>
        ${t.description ? `<div class="tk-desc">${escapeHtml(t.description)}</div>` : ''}
        ${t.status !== 'Resolved' ? `
        <div class="tk-actions">
          ${t.status !== 'Checking' ? `<button class="tk-action-btn" onclick="setTicketStatus(${t.id}, 'Checking')">Checking</button>` : ''}
          <button class="tk-action-btn primary" onclick="setTicketStatus(${t.id}, 'Resolved')">Problem Resolved</button>
        </div>` : ''}
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = `<div style="padding:30px;text-align:center;color:var(--text-muted);font-size:12.5px;">Could not load help requests.</div>`;
  }
}

async function setTicketStatus(id, status) {
  try {
    await fetch(`/api/help-tickets/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    loadHelpTab();
  } catch (e) { /* ignore */ }
}

async function loadAllProjects() {
  const body = document.getElementById('all-projects-body');
  if (!body) return;
  try {
    const res = await fetch('/api/settings/all-projects');
    const data = await res.json();
    const projects = data.projects || [];
    if (!projects.length) {
      body.innerHTML = `<tr class="empty-row"><td colspan="5">No projects yet.</td></tr>`;
      return;
    }
    body.innerHTML = projects.map(p => `
      <tr>
        <td>${escapeHtml(p.module)}</td>
        <td>${p.open_url ? `<a href="${p.open_url}" style="color:var(--accent);font-weight:600;text-decoration:none;">${escapeHtml(p.name)}</a>` : escapeHtml(p.name)}</td>
        <td>${escapeHtml(p.detail)}</td>
        <td>${escapeHtml(p.created_at)}</td>
        <td style="text-align:right;">
          <button type="button" class="ann-delete-btn" onclick="requestDelete({url:'${p.delete_url}', label:'${escapeHtml(p.name).replace(/'/g, "\\'")}', onSuccess: loadAllProjects})">Delete</button>
        </td>
      </tr>`).join('');
  } catch (e) {
    body.innerHTML = `<tr class="empty-row"><td colspan="5">Could not load projects.</td></tr>`;
  }
}
