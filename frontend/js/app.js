// IT Operations Command Center - Dashboard frontend.
// Plain vanilla JS (per the spec's tech-stack option), talking to the
// central-platform REST API. No framework, no build step - open
// index.html directly, or serve via the nginx container in docker-compose.
//
// Priority 4 (auth): every API call below now requires a bearer token,
// obtained by a real login against POST /auth/login. The token is kept
// in localStorage (fine here - this is a real served web page, not a
// Claude.ai artifact sandbox) so a page refresh doesn't force a re-login
// until the token actually expires.

const SESSION_STORAGE_KEY = "itopscc_session";

const state = {
  services: [],
  incidents: [],
  currentIncident: null,
  currentRecommendation: null,
  serviceHealthLatest: {},
  selectedServiceId: null,
  selectedServiceMetricRows: [],
  slaRules: [],
  token: null,
  currentUser: null, // { user_id, username, role }
  incidentsPage: 1,
  incidentsLimit: 20,
  incidentsTotal: 0,
};

// Role -> allowed incident-lifecycle actions, mirroring the backend's own
// require_roles(...) checks (routers/incidents.py, routers/sla.py). This
// is a UI convenience only - the API is the real enforcement point, so a
// hidden button here is not a security control, just a clearer demo of
// "the UI understands who you are."
const ROLE_PERMISSIONS = {
  acknowledge: ["it_support", "incident_manager", "admin"],
  resolve: ["incident_manager", "admin"],
  verify: ["rca_reviewer", "admin"],
  edit_sla: ["admin"],
};

function canDo(action) {
  const role = state.currentUser && state.currentUser.role;
  return !!role && (ROLE_PERMISSIONS[action] || []).includes(role);
}

function apiBase() {
  return document.getElementById("api-base").value.replace(/\/$/, "");
}

function authHeaders() {
  return state.token ? { Authorization: `Bearer ${state.token}` } : {};
}

async function apiGet(path) {
  const res = await fetch(`${apiBase()}${path}`, { headers: { ...authHeaders() } });
  if (res.status === 401) {
    handleSessionExpired();
    throw new Error("Session expired - please sign in again.");
  }
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status} ${await safeErrorDetail(res)}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body || {}),
  });
  if (res.status === 401) {
    handleSessionExpired();
    throw new Error("Session expired - please sign in again.");
  }
  if (!res.ok) throw new Error(`POST ${path} failed: ${res.status} ${await safeErrorDetail(res)}`);
  return res.json();
}

async function apiPut(path, body) {
  const res = await fetch(`${apiBase()}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body || {}),
  });
  if (res.status === 401) {
    handleSessionExpired();
    throw new Error("Session expired - please sign in again.");
  }
  if (!res.ok) throw new Error(`PUT ${path} failed: ${res.status} ${await safeErrorDetail(res)}`);
  return res.json();
}

async function safeErrorDetail(res) {
  try {
    const body = await res.json();
    return body.detail ? `(${body.detail})` : "";
  } catch {
    return "";
  }
}

function severityBadge(sev) {
  // sev is a DB-level Enum (critical|high|medium|low), not free text, so
  // it's safe as a CSS class name - but still escape the displayed text
  // for defense in depth.
  return `<span class="badge badge-${sev}">${escapeHtml(sev)}</span>`;
}

function breachBadge(incident) {
  const breached = incident.response_breached || incident.resolution_breached;
  return breached
    ? `<span class="badge badge-breach">BREACHED</span>`
    : `<span class="badge badge-ok">on track</span>`;
}

function fmtTime(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleString();
}

// SECURITY: incident titles / service names can originate from
// user-controlled data (e.g. the legacy CSV conversion endpoint lets
// anyone upload a "title" column). Every such value is rendered into
// innerHTML below, so it MUST be escaped first or a malicious CSV
// upload becomes a stored-XSS payload against every operator who later
// opens the dashboard. This was found during the security audit (see
// AUDIT_REPORT.md) - not previously escaped anywhere in this file.
function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value === null || value === undefined ? "" : String(value);
  return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Auth (Priority 4): login/logout + session persistence.
//
// A real JWT from POST /auth/login is required for every other endpoint.
// Saved to localStorage so a page refresh doesn't force a re-login (this
// is a real served page, not an artifact sandbox - localStorage is fine
// here). On load, a saved token is validated against GET /auth/me before
// the dashboard is shown, so an expired/invalid saved token doesn't leave
// the user staring at a broken dashboard.
// ---------------------------------------------------------------------------

function saveSession(token, user) {
  state.token = token;
  state.currentUser = user;
  localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ token, user }));
}

function clearSession() {
  state.token = null;
  state.currentUser = null;
  localStorage.removeItem(SESSION_STORAGE_KEY);
}

function loadSessionFromStorage() {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function showLogin(message) {
  document.getElementById("login-overlay").classList.remove("hidden");
  document.getElementById("app-main").classList.add("hidden");
  const errEl = document.getElementById("login-error");
  errEl.textContent = message || "";
}

function showApp() {
  document.getElementById("login-overlay").classList.add("hidden");
  document.getElementById("app-main").classList.remove("hidden");
  const badge = document.getElementById("current-user-badge");
  badge.textContent = `Signed in as ${state.currentUser.username} (${state.currentUser.role})`;
}

function handleSessionExpired() {
  clearSession();
  showLogin("Your session expired - please sign in again.");
}

async function login(username, password) {
  const res = await fetch(`${apiBase()}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const detail = await safeErrorDetail(res);
    throw new Error(`Sign-in failed ${detail || "(invalid username or password)"}`);
  }
  const body = await res.json();
  saveSession(body.access_token, { user_id: body.user_id, username: body.username, role: body.role });
}

async function tryResumeSession() {
  const saved = loadSessionFromStorage();
  if (!saved || !saved.token) return false;
  state.token = saved.token;
  try {
    const me = await apiGet("/auth/me");
    state.currentUser = me;
    return true;
  } catch {
    clearSession();
    return false;
  }
}

function logout() {
  clearSession();
  showLogin("");
}

async function loadSummary() {
  const summary = await apiGet("/sla/summary");
  document.getElementById("stat-total").textContent = summary.total_incidents;
  document.getElementById("stat-open").textContent = summary.currently_open;
  document.getElementById("stat-breached").textContent = summary.breached_incidents;
  document.getElementById("stat-compliance").textContent = `${summary.sla_compliance_pct}%`;
}

async function loadServices() {
  const services = await apiGet("/services");
  state.services = services;
  const tbody = document.querySelector("#services-table tbody");
  tbody.innerHTML = services.map(s => `
    <tr>
      <td>${escapeHtml(s.name)}</td>
      <td class="muted">${escapeHtml(s.base_url)}</td>
      <td>${s.is_active ? "yes" : "no"}</td>
    </tr>
  `).join("");
}

function serviceName(serviceId) {
  const s = state.services.find(s => s.id === serviceId);
  return s ? s.name : `#${serviceId}`;
}

// ---------------------------------------------------------------------------
// SLA Policy panel (Priority 1 demo surface).
//
// Renders the REAL sla_rules table via GET /sla/rules. For an ADMIN user,
// each row is editable and saves via PUT /sla/rules/{severity} - this is
// the live interview-demo proof that changing this table changes what
// new incidents actually use (see app/sla/repository.py).
// ---------------------------------------------------------------------------

async function loadSlaRules() {
  const rules = await apiGet("/sla/rules");
  state.slaRules = rules;
  renderSlaRules();
}

function renderSlaRules() {
  const tbody = document.querySelector("#sla-rules-table tbody");
  if (!tbody) return;
  const editable = canDo("edit_sla");

  tbody.innerHTML = state.slaRules.map(rule => `
    <tr data-severity="${escapeHtml(rule.severity)}">
      <td>${severityBadge(rule.severity)}</td>
      <td>${editable
        ? `<input type="number" min="1" class="sla-input" data-field="response_minutes" value="${rule.response_minutes}" />`
        : escapeHtml(rule.response_minutes)}</td>
      <td>${editable
        ? `<input type="number" min="1" class="sla-input" data-field="resolution_minutes" value="${rule.resolution_minutes}" />`
        : escapeHtml(rule.resolution_minutes)}</td>
      <td>${editable ? `<button class="action sla-save-btn" data-severity="${escapeHtml(rule.severity)}">Save</button>` : ""}</td>
    </tr>
  `).join("");

  if (!editable) return;
  tbody.querySelectorAll(".sla-save-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const severity = btn.dataset.severity;
      const row = tbody.querySelector(`tr[data-severity="${severity}"]`);
      const responseMinutes = Number(row.querySelector('[data-field="response_minutes"]').value);
      const resolutionMinutes = Number(row.querySelector('[data-field="resolution_minutes"]').value);
      try {
        await apiPut(`/sla/rules/${severity}`, {
          response_minutes: responseMinutes,
          resolution_minutes: resolutionMinutes,
        });
        await loadSlaRules();
        await loadAuditTrail(); // the change writes a real audit row - show it immediately
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

function recoveredCell(incident) {
  if (incident.recovered_at) {
    return `<span class="badge badge-recovered" title="System-detected - not yet human-resolved">${escapeHtml(fmtTime(incident.recovered_at))}</span>`;
  }
  return `<span class="muted">-</span>`;
}

function actionButtonsHtml(incident) {
  const buttons = [];
  if (canDo("acknowledge")) {
    buttons.push(`<button class="action" data-action="ack" data-id="${incident.id}">Ack</button>`);
  }
  if (canDo("resolve")) {
    buttons.push(`<button class="action" data-action="resolve" data-id="${incident.id}">Resolve</button>`);
  }
  buttons.push(`<button class="action" data-action="rca" data-id="${incident.id}">Run RCA</button>`);
  return buttons.join(" ");
}

async function loadIncidents(page = state.incidentsPage) {
  const result = await apiGet(`/incidents?page=${page}&limit=${state.incidentsLimit}`);
  state.incidents = result.items;
  state.incidentsPage = result.page;
  state.incidentsLimit = result.limit;
  state.incidentsTotal = result.total;

  const tbody = document.querySelector("#incidents-table tbody");
  tbody.innerHTML = state.incidents.map(inc => `
    <tr data-id="${inc.id}">
      <td>${inc.id}</td>
      <td>${escapeHtml(serviceName(inc.service_id))}</td>
      <td>${escapeHtml(inc.title)}</td>
      <td>${severityBadge(inc.severity)}</td>
      <td>${escapeHtml(inc.status)}</td>
      <td class="muted">${fmtTime(inc.opened_at)}</td>
      <td>${recoveredCell(inc)}</td>
      <td class="muted">${fmtTime(inc.response_due_at)}</td>
      <td class="muted">${fmtTime(inc.resolution_due_at)}</td>
      <td>${breachBadge(inc)}</td>
      <td>${actionButtonsHtml(inc)}</td>
    </tr>
  `).join("");

  tbody.querySelectorAll("tr").forEach(row => {
    row.addEventListener("click", (e) => {
      if (e.target.tagName === "BUTTON") return;
      showIncidentDetail(Number(row.dataset.id));
    });
  });

  tbody.querySelectorAll("button.action").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.id);
      const action = btn.dataset.action;
      try {
        if (action === "ack") await apiPost(`/incidents/${id}/acknowledge`, {});
        if (action === "resolve") await apiPost(`/incidents/${id}/resolve`, {});
        if (action === "rca") await openIncidentAndRunRca(id);
        await refreshAll();
        // If the row action affected the incident currently shown in the
        // investigation panel, refresh that panel too so its status/
        // timestamps don't go stale after an ack/resolve click elsewhere.
        if (state.currentIncident && state.currentIncident.id === id && action !== "rca") {
          await showIncidentDetail(id);
        }
      } catch (err) {
        alert(err.message);
      }
    });
  });

  renderIncidentsPagination();
}

function renderIncidentsPagination() {
  const el = document.getElementById("incidents-pagination");
  if (!el) return;
  const totalPages = Math.max(1, Math.ceil(state.incidentsTotal / state.incidentsLimit));
  el.innerHTML = `
    <button id="incidents-prev" class="action" ${state.incidentsPage <= 1 ? "disabled" : ""}>&larr; Prev</button>
    <span class="muted">Page ${state.incidentsPage} of ${totalPages} (${state.incidentsTotal} total incidents)</span>
    <button id="incidents-next" class="action" ${state.incidentsPage >= totalPages ? "disabled" : ""}>Next &rarr;</button>
  `;
  const prevBtn = document.getElementById("incidents-prev");
  const nextBtn = document.getElementById("incidents-next");
  if (prevBtn) prevBtn.addEventListener("click", () => loadIncidents(state.incidentsPage - 1));
  if (nextBtn) nextBtn.addEventListener("click", () => loadIncidents(state.incidentsPage + 1));
}

// ---------------------------------------------------------------------------
// Incident Investigation panel (Day 2 - Task 1)
//
// Renders three sub-sections inside the existing #incident-detail
// container: incident info grid, RCA results, and Human Verification.
// All three are re-rendered together from `state.currentIncident` /
// `state.currentRecommendation` whenever either changes, which keeps
// the panel's DOM and app state from drifting apart - the same pattern
// the rest of this file already uses for the services/incidents tables.
//
// RCA logic itself lives entirely in the backend (app/rca/rules.py +
// app/rca/llm_explainer.py) - this file only displays what the API
// returns and never computes a root cause client-side.
// ---------------------------------------------------------------------------

function fmtBool(value) {
  return value ? "Yes" : "No";
}

function fmtValue(value) {
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

function singleBreachBadge(breached) {
  return breached
    ? `<span class="badge badge-breach">BREACHED</span>`
    : `<span class="badge badge-ok">on track</span>`;
}

function confidenceBadge(confidence) {
  // confidence is one of "high" | "medium" | "low" from the rule engine,
  // but escape it anyway for defense in depth per the project's XSS rule.
  const cls = ["high", "medium", "low"].includes(confidence) ? confidence : "low";
  return `<span class="badge badge-conf-${cls}">${escapeHtml(confidence)}</span>`;
}

function usedLlmBadge(usedLlm) {
  return usedLlm
    ? `<span class="badge badge-llm-yes">Yes - LLM explanation</span>`
    : `<span class="badge badge-llm-no">No - rule summary only</span>`;
}

function aiValidationBadge(status) {
  // PASSED | FLAGGED | UNAVAILABLE (see app/rca/ai_validator.py). This is
  // NOT a hallucination-free guarantee - a deterministic consistency
  // check between the AI text and the evidence it was given.
  const cls = { passed: "ok", flagged: "breach", unavailable: "status" }[status] || "status";
  const label = { passed: "Passed", flagged: "Flagged for review", unavailable: "Not applicable" }[status] || status;
  return `<span class="badge badge-${cls}">${escapeHtml(label)}</span>`;
}

/** Build one info-grid "field" card. `value` is always escaped. */
function infoField(label, valueHtml, extraClass = "") {
  return `
    <div class="info-field ${extraClass}">
      <span class="info-label">${label}</span>
      <span class="info-value">${valueHtml}</span>
    </div>`;
}

function renderIncidentInfoGrid(incident) {
  return `
    <div class="info-grid">
      ${infoField("Incident ID", escapeHtml(incident.id))}
      ${infoField("Service", escapeHtml(serviceName(incident.service_id)))}
      ${infoField("Title", escapeHtml(incident.title))}
      ${infoField("Severity", severityBadge(incident.severity))}
      ${infoField("Status", `<span class="badge badge-status">${escapeHtml(incident.status)}</span>`)}
      ${infoField("Opened", escapeHtml(fmtTime(incident.opened_at)))}
      ${infoField("Recovered At", incident.recovered_at
        ? `<span class="badge badge-recovered">${escapeHtml(fmtTime(incident.recovered_at))} (system-detected)</span>`
        : escapeHtml("-"))}
      ${infoField("Response Deadline", escapeHtml(fmtTime(incident.response_due_at)))}
      ${infoField("Resolution Deadline", escapeHtml(fmtTime(incident.resolution_due_at)))}
      ${infoField("Response Breach", singleBreachBadge(incident.response_breached), "breach-field")}
      ${infoField("Resolution Breach", singleBreachBadge(incident.resolution_breached), "breach-field")}
      ${infoField("Trigger Metric", escapeHtml(fmtValue(incident.trigger_metric)))}
      ${infoField("Trigger Value", escapeHtml(fmtValue(incident.trigger_value)))}
      ${infoField("Trigger Threshold", escapeHtml(fmtValue(incident.trigger_threshold)))}
    </div>`;
}

function renderRcaSection(incidentId, rec) {
  if (!rec) {
    return `
      <div class="sub-panel rca-panel">
        <div class="sub-panel-header">
          <h4>Root Cause Analysis</h4>
          <button id="run-rca-btn" class="action primary" data-id="${incidentId}">Run RCA</button>
        </div>
        <p class="placeholder-note">No RCA has been run yet for this incident in this session. Click "Run RCA" to analyze correlated metrics and logs.</p>
      </div>`;
  }

  return `
    <div class="sub-panel rca-panel">
      <div class="sub-panel-header">
        <h4>Root Cause Analysis</h4>
        <button id="run-rca-btn" class="action primary" data-id="${incidentId}">Re-run RCA</button>
      </div>
      <div class="info-grid">
        ${infoField("Root Cause Category", escapeHtml(rec.root_cause_category))}
        ${infoField("Confidence", confidenceBadge(rec.confidence))}
        ${infoField("LLM Used", usedLlmBadge(rec.used_llm))}
        ${infoField("AI Validation", aiValidationBadge(rec.ai_validation_status))}
      </div>
      <p><strong>Rule-based summary:</strong> ${escapeHtml(rec.rule_based_summary)}</p>
      <div class="ai-box"><strong>AI explanation (unverified until confirmed below):</strong>\n${escapeHtml(rec.ai_explanation)}</div>
      ${rec.ai_validation_reason ? `<p class="muted ai-validation-reason"><strong>Validator note:</strong> ${escapeHtml(rec.ai_validation_reason)}</p>` : ""}
    </div>`;
}

function renderVerificationSection(rec) {
  if (!rec) {
    return `
      <div class="sub-panel verification-panel">
        <div class="sub-panel-header"><h4>Human Verification</h4></div>
        <p class="placeholder-note">Run RCA first - a recommendation must exist before it can be verified.</p>
      </div>`;
  }

  if (rec.verified_by_human) {
    return `
      <div class="sub-panel verification-panel">
        <div class="sub-panel-header"><h4>Human Verification</h4></div>
        <div class="verification-result">
          <div class="info-grid">
            ${infoField("Verified By Human", fmtBool(rec.verified_by_human))}
            ${infoField("Verdict", escapeHtml(rec.verdict))}
            ${infoField("Operator Note", escapeHtml(fmtValue(rec.note)))}
          </div>
        </div>
      </div>`;
  }

  if (!canDo("verify")) {
    return `
      <div class="sub-panel verification-panel">
        <div class="sub-panel-header"><h4>Human Verification</h4></div>
        <p class="placeholder-note">This RCA result has not been verified yet. Verifying requires the RCA_REVIEWER or ADMIN role - sign in as <code>rca_reviewer</code> to accept/reject/modify it.</p>
      </div>`;
  }

  return `
    <div class="sub-panel verification-panel">
      <div class="sub-panel-header"><h4>Human Verification</h4></div>
      <p class="placeholder-note">This RCA result has NOT been verified. An operator must explicitly accept, reject, or mark it modified below - it is never verified automatically.</p>
      <textarea id="verify-note" placeholder="Optional note explaining your decision..."></textarea>
      <div class="verification-actions">
        <button class="verify-btn accept" data-verdict="accepted">Accept</button>
        <button class="verify-btn reject" data-verdict="rejected">Reject</button>
        <button class="verify-btn modify" data-verdict="modified">Modify</button>
      </div>
    </div>`;
}

function renderInvestigationPanel() {
  const detail = document.getElementById("incident-detail");
  const incident = state.currentIncident;
  if (!incident) {
    detail.innerHTML = `<p class="muted">Click an incident row to load its detail here.</p>`;
    return;
  }

  detail.innerHTML = `
    <div class="investigation">
      <div class="investigation-header">
        <h3>Incident #${incident.id}: ${escapeHtml(incident.title)}</h3>
        <div class="investigation-badges">
          ${severityBadge(incident.severity)}
          <span class="badge badge-status">${escapeHtml(incident.status)}</span>
        </div>
      </div>
      ${renderIncidentInfoGrid(incident)}
      ${renderRcaSection(incident.id, state.currentRecommendation)}
      ${renderVerificationSection(state.currentRecommendation)}
    </div>`;

  const rcaBtn = document.getElementById("run-rca-btn");
  if (rcaBtn) {
    rcaBtn.addEventListener("click", () => runRcaForCurrentIncident());
  }

  detail.querySelectorAll("button.verify-btn").forEach(btn => {
    btn.addEventListener("click", () => submitVerification(btn.dataset.verdict));
  });
}

async function showIncidentDetail(incidentId) {
  const incident = await apiGet(`/incidents/${incidentId}`);
  state.currentIncident = incident;
  // A freshly (re)loaded incident has no known recommendation yet in this
  // session - the API has no "get latest recommendation" endpoint, so RCA
  // must be explicitly (re)run to see/verify a result. This is stated here
  // rather than fabricating a fetch that doesn't exist.
  if (!state.currentRecommendation || state.currentRecommendation.incidentId !== incidentId) {
    state.currentRecommendation = null;
  }
  renderInvestigationPanel();
}

/** Used by both the investigation panel's "Run RCA" button and the
 * per-row "Run RCA" button in the incidents table, so there is exactly
 * one code path that calls the RCA endpoint. */
async function openIncidentAndRunRca(incidentId) {
  await showIncidentDetail(incidentId);
  await runRcaForCurrentIncident();
}

async function runRcaForCurrentIncident() {
  if (!state.currentIncident) return;
  const incidentId = state.currentIncident.id;
  try {
    const result = await apiPost(`/incidents/${incidentId}/rca`, {});
    state.currentRecommendation = {
      incidentId,
      recommendationId: result.recommendation_id,
      root_cause_category: result.root_cause_category,
      confidence: result.confidence,
      rule_based_summary: result.rule_based_summary,
      ai_explanation: result.ai_explanation,
      used_llm: result.used_llm,
      ai_validation_status: result.ai_validation_status,
      ai_validation_reason: result.ai_validation_reason,
      verified_by_human: false,
      verdict: null,
      note: null,
    };
    renderInvestigationPanel();
  } catch (err) {
    alert(err.message);
  }
}

async function submitVerification(verdict) {
  const rec = state.currentRecommendation;
  if (!rec || !rec.recommendationId) return;
  const noteEl = document.getElementById("verify-note");
  const note = noteEl ? noteEl.value.trim() : "";

  try {
    // Priority 4: user_id is no longer sent in the body at all - the
    // acting user is derived from the verified JWT on the server (see
    // routers/incidents.py::verify_recommendation), so there is no way
    // for the frontend (or anyone else) to claim a different user's
    // identity. This replaces the old "user_id: null" workaround from
    // before real login existed.
    const result = await apiPost(`/incidents/recommendations/${rec.recommendationId}/verify`, {
      verdict,
      note: note || null,
    });
    state.currentRecommendation = {
      ...rec,
      verified_by_human: result.verified_by_human,
      verdict,
      note,
    };
    renderInvestigationPanel();
  } catch (err) {
    alert(err.message);
  }
}

// ---------------------------------------------------------------------------
// Service Health panel (Day 2 - Task 2)
//
// Uses ONLY the existing GET /services, GET /services/{id}/metrics, and
// POST /services/{id}/poll-now endpoints - no new backend logic, no
// duplicate API-calling code (reuses apiGet/apiPost from above). Every
// number displayed here comes directly from a real API response; any
// metric the API hasn't reported yet is shown as "No data", never
// invented.
// ---------------------------------------------------------------------------

function fmtRatio(value) {
  // availability / error_rate are 0..1 fractions from the real API.
  return `${(value * 100).toFixed(1)}%`;
}

function fmtSeconds(value) {
  return `${value.toFixed(3)}s`;
}

function noDataSpan() {
  return `<span class="muted">No data</span>`;
}

/** GET /services/{id}/metrics returns a FLAT list of one row per
 * (metric_name, timestamp) - the collector writes availability,
 * error_rate, and latency_p95 together in the same poll with an
 * identical timestamp (see app/ingestion/collector.py poll_service()),
 * so grouping these real rows by exact timestamp reconstructs one row
 * per poll without inventing anything. Rows the API didn't return are
 * simply left undefined for that field, not filled with a guess. */
function groupMetricSamplesByTimestamp(samples) {
  const byTimestamp = new Map();
  for (const s of samples) {
    const key = s.timestamp;
    if (!byTimestamp.has(key)) byTimestamp.set(key, { timestamp: key });
    byTimestamp.get(key)[s.metric_name] = s.value;
  }
  // samples arrive ordered desc by timestamp from the API; Map preserves
  // insertion order, so this stays desc (most recent first).
  return Array.from(byTimestamp.values());
}

/** Reduces a service's real recent samples to "latest known value per
 * metric", used for the summary cards. Returns {} (all fields
 * undefined) if the API had no samples at all for this service yet. */
function latestMetricsForService(samples) {
  const latest = {};
  for (const s of samples) {
    if (!(s.metric_name in latest)) latest[s.metric_name] = s.value;
  }
  return latest;
}

function populateServiceSelect() {
  const select = document.getElementById("service-select");
  if (!select) return;
  select.innerHTML = `<option value="">-- Select a service --</option>` +
    state.services.map(s => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}</option>`).join("");
  if (state.selectedServiceId && state.services.some(s => s.id === state.selectedServiceId)) {
    select.value = String(state.selectedServiceId);
  }
}

function renderServiceHealthCards() {
  const container = document.getElementById("service-health-cards");
  if (!container) return;

  if (state.services.length === 0) {
    container.innerHTML = `<p class="placeholder-note">No services returned by the API.</p>`;
    return;
  }

  container.innerHTML = state.services.map(s => {
    const latest = state.serviceHealthLatest[s.id] || {};
    const hasAvail = latest.availability !== undefined;
    const hasErr = latest.error_rate !== undefined;
    const hasLat = latest.latency_p95 !== undefined;

    const availHtml = hasAvail
      ? `<span class="badge ${latest.availability >= 1 ? "badge-ok" : "badge-breach"}">${escapeHtml(fmtRatio(latest.availability))}</span>`
      : noDataSpan();
    const errHtml = hasErr ? escapeHtml(fmtRatio(latest.error_rate)) : noDataSpan();
    const latHtml = hasLat ? escapeHtml(fmtSeconds(latest.latency_p95)) : noDataSpan();

    return `
      <div class="service-card ${state.selectedServiceId === s.id ? "selected" : ""}" data-service-id="${s.id}">
        <div class="service-card-header">
          <span class="service-card-name">${escapeHtml(s.name)}</span>
          <span class="badge ${s.is_active ? "badge-ok" : "badge-breach"}">${s.is_active ? "active" : "inactive"}</span>
        </div>
        <div class="service-metric-row"><span class="info-label">Availability</span><span>${availHtml}</span></div>
        <div class="service-metric-row"><span class="info-label">Error Rate</span><span>${errHtml}</span></div>
        <div class="service-metric-row"><span class="info-label">Latency P95</span><span>${latHtml}</span></div>
      </div>`;
  }).join("");

  container.querySelectorAll(".service-card").forEach(card => {
    card.addEventListener("click", () => selectService(Number(card.dataset.serviceId)));
  });
}

/** Minimal SVG sparkline built ONLY from real fetched sample values
 * (Requirement 11/12: no fake chart data, no charting library). Refuses
 * to draw - and says so - when there aren't at least 2 real points,
 * rather than padding/interpolating a fake trend line. */
function renderSparkline(rows, key, label, color) {
  const points = rows.slice().reverse().map(r => r[key]).filter(v => v !== undefined && v !== null);
  if (points.length < 2) {
    return `
      <div class="sparkline-block">
        <span class="info-label">${escapeHtml(label)}</span>
        <p class="placeholder-note">Not enough real samples yet for a trend (need at least 2; have ${points.length}).</p>
      </div>`;
  }
  const w = 240, h = 40, pad = 4;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = (max - min) || 1;
  const stepX = (w - pad * 2) / (points.length - 1);
  const coords = points.map((v, i) => {
    const x = pad + i * stepX;
    const y = h - pad - ((v - min) / range) * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `
    <div class="sparkline-block">
      <span class="info-label">${escapeHtml(label)} trend (${points.length} real samples)</span>
      <svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polyline points="${coords}" fill="none" stroke="${color}" stroke-width="2" />
      </svg>
    </div>`;
}

function renderServiceMetricsBody() {
  const body = document.getElementById("service-metrics-body");
  if (!body) return;

  if (!state.selectedServiceId) {
    body.innerHTML = `<p class="placeholder-note">Select a service above to view its recent real metric samples.</p>`;
    return;
  }

  const rows = state.selectedServiceMetricRows;
  if (!rows || rows.length === 0) {
    body.innerHTML = `<p class="placeholder-note">No metric data yet for this service. Click "Poll Now" to collect a real sample.</p>`;
    return;
  }

  const tableRows = rows.map(r => `
    <tr>
      <td class="muted">${escapeHtml(fmtTime(r.timestamp))}</td>
      <td>${r.availability !== undefined ? escapeHtml(fmtRatio(r.availability)) : noDataSpan()}</td>
      <td>${r.error_rate !== undefined ? escapeHtml(fmtRatio(r.error_rate)) : noDataSpan()}</td>
      <td>${r.latency_p95 !== undefined ? escapeHtml(fmtSeconds(r.latency_p95)) : noDataSpan()}</td>
    </tr>`).join("");

  body.innerHTML = `
    <table class="metrics-table">
      <thead><tr><th>Timestamp</th><th>Availability</th><th>Error Rate</th><th>Latency P95</th></tr></thead>
      <tbody>${tableRows}</tbody>
    </table>
    <div class="sparkline-grid">
      ${renderSparkline(rows, "availability", "Availability", "var(--ok)")}
      ${renderSparkline(rows, "error_rate", "Error Rate", "var(--critical)")}
      ${renderSparkline(rows, "latency_p95", "Latency P95", "var(--medium)")}
    </div>`;
}

async function loadSelectedServiceMetrics() {
  if (!state.selectedServiceId) {
    state.selectedServiceMetricRows = [];
    renderServiceMetricsBody();
    return;
  }
  const samples = await apiGet(`/services/${state.selectedServiceId}/metrics?limit=30`);
  state.selectedServiceMetricRows = groupMetricSamplesByTimestamp(samples);
  renderServiceMetricsBody();
}

async function selectService(serviceId) {
  state.selectedServiceId = serviceId;
  const select = document.getElementById("service-select");
  if (select) select.value = String(serviceId);
  renderServiceHealthCards();
  try {
    await loadSelectedServiceMetrics();
  } catch (err) {
    alert(err.message);
  }
}

function renderPollResult(result) {
  const el = document.getElementById("poll-result");
  if (!el) return;

  if (result.error) {
    el.innerHTML = `<p class="muted">Poll failed: ${escapeHtml(result.error)}</p>`;
    return;
  }

  const m = result.metrics || {};
  const incidentHtml = result.incident_opened
    ? `<span class="badge badge-breach">New incident opened: #${escapeHtml(result.incident_opened.id)} ${escapeHtml(result.incident_opened.title)} (${escapeHtml(result.incident_opened.severity)})</span>`
    : `<span class="badge badge-ok">No new incident opened</span>`;
  const recoveryHtml = result.incident_recovered
    ? ` <span class="badge badge-recovered">Incident #${escapeHtml(result.incident_recovered.id)} recovered</span>`
    : "";

  el.innerHTML = `
    <p>
      Polled <strong>${escapeHtml(result.service)}</strong> at ${escapeHtml(fmtTime(result.polled_at))}:
      availability ${escapeHtml(fmtRatio(m.availability))},
      error rate ${escapeHtml(fmtRatio(m.error_rate))},
      latency p95 ${escapeHtml(fmtSeconds(m.latency_p95))}.
      ${incidentHtml}${recoveryHtml}
    </p>`;
}

async function pollNowForSelectedService() {
  if (!state.selectedServiceId) {
    alert("Select a service first.");
    return;
  }
  try {
    const result = await apiPost(`/services/${state.selectedServiceId}/poll-now`, {});
    renderPollResult(result);
    await loadServiceHealth();  // refresh every card's latest values + the selected service's samples
    await refreshAll();         // polling can open a new incident, so refresh incidents/SLA too
  } catch (err) {
    alert(err.message);
  }
}

/** Fetches each service's latest real metrics (one GET per service,
 * reusing the same /services/{id}/metrics endpoint the samples table
 * uses - no separate/duplicate API logic) and re-renders the health
 * cards, select list, and (if one is selected) the samples table. */
async function loadServiceHealth() {
  populateServiceSelect();

  await Promise.all(state.services.map(async (s) => {
    try {
      const samples = await apiGet(`/services/${s.id}/metrics?limit=12`);
      state.serviceHealthLatest[s.id] = latestMetricsForService(samples);
    } catch (err) {
      // A single service's metrics call failing shouldn't blank out the
      // whole panel or fabricate a value - record "no data" and log it.
      state.serviceHealthLatest[s.id] = {};
      console.error(`Failed to load metrics for service ${s.id}:`, err.message);
    }
  }));

  renderServiceHealthCards();

  if (state.selectedServiceId) {
    await loadSelectedServiceMetrics();
  } else {
    renderServiceMetricsBody();
  }
}

// ---------------------------------------------------------------------------
// Audit Trail panel (Task 4)
//
// Uses ONLY the existing GET /audit-logs?limit=50 endpoint (reuses the
// same apiGet helper everything else in this file uses - no duplicate
// fetch logic). Every row displayed is exactly what the API returned;
// nothing is fabricated, and a null user_id is rendered as
// "System/Unknown" rather than invented as a real user.
// ---------------------------------------------------------------------------

function userIdBadge(userId) {
  return userId === null || userId === undefined
    ? `<span class="badge badge-user-system">System/Unknown</span>`
    : escapeHtml(userId);
}

function renderAuditTrailRows(rows) {
  return rows.map(row => `
    <tr>
      <td class="muted">${escapeHtml(fmtTime(row.timestamp))}</td>
      <td>${userIdBadge(row.user_id)}</td>
      <td><span class="badge badge-action">${escapeHtml(row.action)}</span></td>
      <td class="audit-target">${escapeHtml(row.target_type)} #${escapeHtml(fmtValue(row.target_id))}</td>
      <td class="audit-details">${escapeHtml(fmtValue(row.details))}</td>
    </tr>`).join("");
}

function renderAuditTrail(rows) {
  const body = document.getElementById("audit-trail-body");
  if (!body) return;

  if (!rows || rows.length === 0) {
    body.innerHTML = `<p class="placeholder-note">No audit records yet. Actions like acknowledging, resolving, or verifying an RCA recommendation will appear here.</p>`;
    return;
  }

  body.innerHTML = `
    <table class="audit-table">
      <thead>
        <tr><th>Timestamp</th><th>User</th><th>Action</th><th>Target</th><th>Details</th></tr>
      </thead>
      <tbody>${renderAuditTrailRows(rows)}</tbody>
    </table>`;
}

async function loadAuditTrail() {
  const body = document.getElementById("audit-trail-body");
  try {
    const rows = await apiGet("/audit-logs?limit=50");
    // API already orders by timestamp desc; no client-side re-sorting or
    // re-shaping of the data beyond formatting for display.
    renderAuditTrail(rows);
  } catch (err) {
    if (body) {
      body.innerHTML = `<p class="audit-error">Failed to load audit trail: ${escapeHtml(err.message)}</p>`;
    }
  }
}

async function refreshAll() {
  // NOTE (Task 4 fix): previously this was
  // `await Promise.all([loadSummary(), loadServices()]); await loadIncidents(); ...`
  // which meant a single failure (e.g. the backend being unreachable)
  // threw out of refreshAll() immediately and the later sections -
  // including the new Audit Trail panel - never ran their own error
  // handling at all, leaving them stuck on a "Loading..." placeholder
  // forever instead of showing a real error. Each section is now
  // isolated so one failing section still lets the others load or show
  // their own real error state.
  const settled = await Promise.allSettled([loadSummary(), loadServices()]);
  settled.forEach(r => {
    if (r.status === "rejected") console.error("refreshAll: failed to load summary/services:", r.reason);
  });

  try {
    await loadSlaRules();
  } catch (err) {
    console.error("refreshAll: failed to load SLA rules:", err);
  }

  try {
    await loadIncidents();
  } catch (err) {
    console.error("refreshAll: failed to load incidents:", err);
  }

  try {
    await loadServiceHealth();
  } catch (err) {
    console.error("refreshAll: failed to load service health:", err);
  }

  await loadAuditTrail(); // has its own internal try/catch (see above)
}

document.getElementById("refresh-btn").addEventListener("click", refreshAll);
document.getElementById("service-select").addEventListener("change", (e) => {
  const id = e.target.value ? Number(e.target.value) : null;
  if (id) selectService(id);
});
document.getElementById("poll-now-btn").addEventListener("click", pollNowForSelectedService);
document.getElementById("audit-refresh-btn").addEventListener("click", loadAuditTrail);
document.getElementById("logout-btn").addEventListener("click", logout);

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  try {
    await login(username, password);
    showApp();
    await refreshAll();
  } catch (err) {
    document.getElementById("login-error").textContent = err.message;
  }
});

// Initial load: try to resume a saved session; only load the dashboard's
// data once we actually have a valid, verified token - otherwise every
// call below would immediately 401 and the user would just see a wall of
// error toasts before ever reaching the login form.
(async function init() {
  const resumed = await tryResumeSession();
  if (resumed) {
    showApp();
    await refreshAll();
  } else {
    showLogin("");
  }
})();
