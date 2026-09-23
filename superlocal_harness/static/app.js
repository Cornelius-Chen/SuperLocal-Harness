const state = {
  config: null,
  missions: [],
  selectedId: null,
  detail: null,
  token: localStorage.getItem("superlocal_access_token") || "",
  polling: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers["X-Harness-Token"] = state.token;
  let response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    const token = prompt("This remote harness requires its access token:");
    if (token) {
      state.token = token;
      localStorage.setItem("superlocal_access_token", token);
      headers["X-Harness-Token"] = token;
      response = await fetch(path, { ...options, headers });
    }
  }
  const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.remove("hidden");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.add("hidden"), 4200);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);
}

function formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function statusDot(status) {
  return `<span class="status-dot ${escapeHtml(status)}"></span>`;
}

async function initialize() {
  try {
    state.config = await api("/api/config");
    populateControls();
    await refreshMissions();
    state.polling = setInterval(refreshSelected, 1600);
  } catch (error) {
    toast(error.message, true);
  }
}

function populateControls() {
  const models = state.config.models;
  const options = models.map((model) => {
    const suffix = model.id === "auto" ? "" : model.configured ? "" : " · not configured";
    return `<option value="${escapeHtml(model.id)}" ${model.id === "auto" ? "selected" : ""}>${escapeHtml(model.label + suffix)}</option>`;
  }).join("");
  $("quickModel").innerHTML = options;
  $("profileSelect").innerHTML = state.config.profiles.map((profile) =>
    `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.label)}</option>`
  ).join("");
  $("workflowSelect").innerHTML = state.config.workflows.map((workflow) =>
    `<option value="${escapeHtml(workflow.id)}">${escapeHtml(workflow.label)}</option>`
  ).join("");
  $("budgetInput").value = Number(state.config.default_mission_budget_usd).toFixed(2);
  $("rootHint").textContent = `Allowed roots: ${state.config.project_roots.join(" · ")}`;
  if (state.config.project_roots.length) $("projectPath").value = state.config.project_roots[0];
}

async function refreshMissions() {
  const data = await api("/api/missions");
  state.missions = data.missions;
  renderMissionList();
}

async function refreshSelected() {
  try {
    await refreshMissions();
    if (state.selectedId) {
      state.detail = await api(`/api/missions/${state.selectedId}`);
      renderDetail();
    }
  } catch (error) {
    console.warn(error);
  }
}

function renderMissionList() {
  const list = $("missionList");
  if (!state.missions.length) {
    list.innerHTML = `<div class="empty-inspector">No missions yet.</div>`;
    return;
  }
  list.innerHTML = state.missions.map((mission) => `
    <button class="mission-item ${mission.id === state.selectedId ? "active" : ""}" data-id="${mission.id}">
      <div class="mission-item-title">${escapeHtml(mission.title)}</div>
      <div class="mission-item-meta">${statusDot(mission.status)}<span>${escapeHtml(mission.status.replaceAll("_", " "))}</span><span>·</span><span>${escapeHtml(mission.profile_id)}</span></div>
    </button>
  `).join("");
  list.querySelectorAll(".mission-item").forEach((button) => {
    button.addEventListener("click", () => selectMission(button.dataset.id));
  });
}

async function selectMission(id) {
  state.selectedId = id;
  state.detail = await api(`/api/missions/${id}`);
  $("newMissionView").classList.add("hidden");
  $("missionView").classList.remove("hidden");
  $("emptyInspector").classList.add("hidden");
  $("activeInspector").classList.remove("hidden");
  renderMissionList();
  renderDetail();
}

function showNewMission() {
  state.selectedId = null;
  state.detail = null;
  $("newMissionView").classList.remove("hidden");
  $("missionView").classList.add("hidden");
  $("emptyInspector").classList.remove("hidden");
  $("activeInspector").classList.add("hidden");
  $("missionTitle").textContent = "Start a mission";
  $("projectEyebrow").textContent = "MISSION CONTROL";
  $("quickModel").disabled = false;
  renderMissionList();
}

function renderDetail() {
  if (!state.detail) return;
  const { mission, messages, events, approvals, integrity, state: durable } = state.detail;
  $("missionTitle").textContent = mission.title;
  $("projectEyebrow").textContent = mission.project_path;
  $("quickModel").value = mission.requested_model_id;
  $("quickModel").disabled = true;
  $("metricStatus").textContent = mission.status.replaceAll("_", " ");
  $("metricStage").textContent = mission.stage;
  const calls = events.filter((event) => event.event_type === "ModelCallCompleted");
  const spendKnown = calls.every((event) => event.payload.cost_known === true);
  $("metricSpend").textContent = spendKnown
    ? `$${Number(mission.spent_usd || 0).toFixed(4)} / $${Number(mission.budget_usd).toFixed(2)}`
    : `Unknown / $${Number(mission.budget_usd).toFixed(2)}`;
  $("metricSpend").title = spendKnown ? "Recorded model spend" : "A model call lacks reported usage or a configured price";
  $("metricSteps").textContent = `${mission.step_count} / ${mission.max_steps}`;

  renderStages(mission);
  renderConversation(messages);
  renderRoute(mission, events);
  renderApprovals(approvals);
  renderState(durable);
  renderEvents(events, integrity);

  const busy = ["running", "queued", "waiting_approval"].includes(mission.status);
  $("followupInput").disabled = busy;
  $("followupForm").querySelector("button").disabled = busy;
}

function renderStages(mission) {
  const stages = mission.workflow === "solo" ? ["execution"] : ["planning", "execution", "verification"];
  const activeIndex = stages.indexOf(mission.stage);
  $("stageRail").innerHTML = stages.map((stage, index) => {
    const klass = mission.status === "completed" || index < activeIndex ? "done" : index === activeIndex ? "active" : "";
    return `<span class="stage-chip ${klass}">${index + 1}. ${escapeHtml(stage)}</span>`;
  }).join("") + `<span class="stage-chip ${mission.status === "completed" ? "done" : ""}">Outcome</span>`;
}

function renderConversation(messages) {
  const conversation = $("conversation");
  conversation.innerHTML = messages.map((message) => {
    const name = message.name || message.role;
    const klass = [message.role, name].join(" ");
    const toolCalls = (message.meta?.tool_calls || []).map((call) =>
      `<div class="tool-call">proposed tool · ${escapeHtml(call.function?.name)}<br>${escapeHtml(call.function?.arguments || "")}</div>`
    ).join("");
    return `<article class="message ${escapeHtml(klass)}">
      <div class="message-head"><span class="message-role">${escapeHtml(name)}</span><span class="message-time">${formatTime(message.created_at)}</span></div>
      <div class="message-body">${escapeHtml(message.content || "")}${toolCalls}</div>
    </article>`;
  }).join("");
  conversation.scrollTop = conversation.scrollHeight;
}

function renderRoute(mission, events) {
  const latest = [...events].reverse().find((event) => ["ModelRouted", "VerifierRouted", "ModelFallbackUsed"].includes(event.event_type));
  let detail = "No model call yet.";
  if (latest) {
    if (latest.event_type === "ModelRouted") detail = latest.payload.reason || "Static visible route";
    else if (latest.event_type === "VerifierRouted") detail = `Verifier candidates: ${(latest.payload.candidates || []).join(" → ")}`;
    else detail = `Fallback selected after ${latest.payload.failures?.length || 0} failed endpoint(s).`;
  }
  const actual = mission.verifier_model_id || mission.actual_model_id || mission.requested_model_id;
  $("routeCard").innerHTML = `<strong>${escapeHtml(actual)}</strong>${escapeHtml(detail)}${mission.local_only ? "<br><span>Local-only boundary enabled.</span>" : ""}`;
}

function renderApprovals(approvals) {
  const pending = approvals.filter((item) => item.status === "pending");
  $("approvalCount").textContent = pending.length;
  $("approvalList").innerHTML = pending.length ? pending.map((item) => `
    <div class="approval-card">
      <strong>${escapeHtml(item.tool_name)}</strong>
      <div class="event-meta">${escapeHtml(item.reason)}</div>
      <pre>${escapeHtml(JSON.stringify(item.args, null, 2))}</pre>
      <div class="approval-actions">
        <button class="secondary-button approve" data-id="${item.id}">Approve once</button>
        <button class="danger-button deny" data-id="${item.id}">Deny</button>
      </div>
    </div>
  `).join("") : `<div class="event-meta">No action needs your authority.</div>`;
  $("approvalList").querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => resolveApproval(button.dataset.id, button.classList.contains("approve")));
  });
}

function renderState(durable) {
  const keys = ["plan", "facts", "decisions", "hypotheses", "next_actions", "acceptance_checks", "artifacts"];
  $("stateSummary").innerHTML = keys.map((key) => {
    const value = durable[key] || [];
    return `<div class="state-row"><strong>${escapeHtml(key.replaceAll("_", " "))}</strong> · ${value.length}${value.length ? `<br>${escapeHtml(value.slice(0, 2).join(" · "))}` : ""}</div>`;
  }).join("");
}

function renderEvents(events, integrity) {
  const badge = $("integrityBadge");
  badge.textContent = integrity.ok ? "chain valid" : "chain broken";
  badge.className = `pill ${integrity.ok ? "good" : "bad"}`;
  $("eventList").innerHTML = [...events].reverse().slice(0, 14).map((event) => `
    <div class="event"><div><div class="event-name">${escapeHtml(event.event_type)}</div><div class="event-meta">#${event.sequence} · ${formatTime(event.created_at)} · ${escapeHtml(event.actor)}</div></div></div>
  `).join("");
}

async function resolveApproval(id, approve) {
  try {
    state.detail = await api(`/api/approvals/${id}/${approve ? "approve" : "deny"}`, { method: "POST", body: "{}" });
    renderDetail();
    toast(approve ? "Action approved once and recorded." : "Action denied and recorded.");
  } catch (error) {
    toast(error.message, true);
  }
}

$("missionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const payload = {
      project_path: $("projectPath").value.trim(),
      prompt: $("promptInput").value.trim(),
      model_id: $("quickModel").value,
      profile_id: $("profileSelect").value,
      workflow: $("workflowSelect").value,
      budget_usd: Number($("budgetInput").value),
      local_only: $("localOnly").checked,
    };
    const detail = await api("/api/missions", { method: "POST", body: JSON.stringify(payload) });
    state.selectedId = detail.mission.id;
    state.detail = detail;
    await refreshMissions();
    await selectMission(state.selectedId);
    toast("Mission launched. State is checkpointed locally.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("followupForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = $("followupInput").value.trim();
  if (!content || !state.selectedId) return;
  try {
    state.detail = await api(`/api/missions/${state.selectedId}/messages`, {
      method: "POST", body: JSON.stringify({ content })
    });
    $("followupInput").value = "";
    renderDetail();
  } catch (error) {
    toast(error.message, true);
  }
});

$("profileSelect").addEventListener("change", () => {
  const profile = state.config.profiles.find((item) => item.id === $("profileSelect").value);
  if (profile) $("workflowSelect").value = profile.default_workflow;
});

$("healthButton").addEventListener("click", async () => {
  const id = $("quickModel").value;
  if (id === "auto") return toast("Auto is a routing policy; select a concrete model to test its endpoint.");
  try {
    const result = await api(`/api/models/${id}/health`, { method: "POST", body: "{}" });
    toast(result.ok ? `${id} ready · ${result.latency_ms} ms` : `${id} not ready · ${result.detail}`, !result.ok);
  } catch (error) {
    toast(error.message, true);
  }
});

$("newMissionButton").addEventListener("click", showNewMission);
$("refreshButton").addEventListener("click", refreshSelected);

initialize();
