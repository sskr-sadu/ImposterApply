const main = document.querySelector("#main");
const banner = document.querySelector("#banner");
const stepNodes = [...document.querySelectorAll(".steps li")];
let session = null;
let step = 1;
let config = { oauth_available: false };
let busy = false;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function showBanner(kind, title, recovery) {
  banner.hidden = !title;
  banner.className = `banner ${kind || ""}`.trim();
  banner.innerHTML = title ? `<strong>${esc(title)}</strong>${recovery ? esc(recovery) : ""}` : "";
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text }; }
  if (!response.ok) {
    const detail = data.detail ?? data;
    let message = "";
    let recovery = "";
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object") {
      message = detail.error || detail.message || "";
      recovery = detail.recovery || "";
    }
    if (!message) message = response.statusText || `Request failed (${response.status})`;
    const error = new Error(message);
    error.recovery = recovery;
    error.status = response.status;
    throw error;
  }
  return data;
}

function setStep(next) {
  step = next;
  stepNodes.forEach((node) => {
    const value = Number(node.dataset.step);
    node.classList.toggle("active", value === step);
    node.classList.toggle("done", value < step);
  });
}

function field(label, name, value, multiline = false) {
  const control = multiline
    ? `<textarea name="${name}">${esc(value)}</textarea>`
    : `<input name="${name}" type="text" value="${esc(value)}">`;
  return `<label>${esc(label)}${control}</label>`;
}

function render() {
  if (step === 1) renderGithub();
  if (step === 2) renderRole();
  if (step === 3) renderIntake();
  if (step === 4) renderDraft();
}

function renderIntake() {
  main.innerHTML = `
    <section class="card">
      <h2>Add the resume facts.</h2>
      <p class="hint">The PDF is a source of facts, not instructions. Scanned and encrypted files are rejected rather than treated as imported experience.</p>
      <form id="intake">
        <input type="hidden" name="job_description" value="${esc(session.job_description || "")}">
        <label class="file">Resume PDF<input type="file" name="resume" accept="application/pdf,.pdf"></label>
        <div class="actions">
          <button class="ghost" type="button" id="back">Back</button>
          <button class="primary" type="submit">Extract experience</button>
          <button class="secondary" type="button" id="manual">Enter experience manually</button>
        </div>
      </form>
    </section>`;
  main.querySelector("#intake").addEventListener("submit", onIntake);
  main.querySelector("#manual").addEventListener("click", onManual);
  main.querySelector("#back").onclick = () => { setStep(2); render(); };
}

async function onIntake(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  if (!String(form.get("job_description") || session.job_description || "").trim()) {
    showBanner("bad", "Job description is empty.", "Go back and paste the role description.");
    return;
  }
  if (!form.get("resume") || !form.get("resume").name) {
    showBanner("bad", "Choose a resume PDF.", "Or enter experience manually. An empty upload is not treated as imported experience.");
    return;
  }
  await run(async () => {
    session = await api("/api/intake", { method: "POST", body: form });
    showBanner(session.extraction_status === "ok" ? "good" : "warn", session.extraction_message, "");
    setStep(3);
    renderReview();
  });
}

async function onManual() {
  const job = session.job_description || "";
  await run(async () => {
    session = await api("/api/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_description: job }),
    });
    showBanner("warn", session.extraction_message, "Add at least one role before generating, or continue later with projects only.");
    setStep(3);
    renderReview();
  });
}

function renderReview() {
  const contact = session.contact || {};
  const experience = session.experience || [];
  main.innerHTML = `
    <section class="card">
      <h2>Confirm what was extracted.</h2>
      <p class="hint">${esc(session.extraction_message || "Add or correct experience. Missing fields can be filled in here.")}</p>
      <form id="review">
        <div class="grid">
          ${field("Name", "name", contact.name)}
          ${field("Email", "email", contact.email)}
          ${field("Phone", "phone", contact.phone)}
          ${field("Location", "location", contact.location)}
        </div>
        ${field("Links, one per line", "links", (contact.links || []).join("\n"), true)}
        <h3>Experience</h3>
        <div id="experience"></div>
        <button class="ghost" type="button" id="add-role">Add role</button>
        <h3>Education</h3>
        <div id="education"></div>
        <button class="ghost" type="button" id="add-school">Add education</button>
        ${field("Skills from the resume", "skills_text", session.skills_text, true)}
        ${field("Summary from the resume", "summary_text", session.summary_text, true)}
        <div id="other"></div>
        <div class="actions">
          <button class="ghost" type="button" id="back">Back</button>
          <button class="primary" type="submit">Save and choose repositories</button>
        </div>
      </form>
    </section>`;
  const exp = main.querySelector("#experience");
  (experience.length ? experience : [blankRole()]).forEach((role) => exp.append(roleCard(role)));
  const edu = main.querySelector("#education");
  (session.education || []).forEach((item) => edu.append(schoolCard(item)));
  const other = main.querySelector("#other");
  (session.other_sections || []).forEach((section, index) => {
    const block = document.createElement("div");
    block.className = "entry";
    block.innerHTML = `<label class="check"><input type="checkbox" data-other="${index}" ${section.include ? "checked" : ""}> Include “${esc(section.title)}” in the resume. Unrecognized sections stay out unless you check this.</label>
      <textarea data-other-text="${index}">${esc(section.text)}</textarea>`;
    other.append(block);
  });
  main.querySelector("#add-role").onclick = () => exp.append(roleCard(blankRole()));
  main.querySelector("#add-school").onclick = () => edu.append(schoolCard({}));
  main.querySelector("#back").onclick = () => { setStep(2); render(); };
  main.querySelector("#review").onsubmit = saveReview;
}

function blankRole() {
  return { employer: "", title: "", start: "", end: "", description: "" };
}

function roleCard(role) {
  const node = document.createElement("div");
  node.className = "entry role";
  node.innerHTML = `<div class="grid">
      ${field("Employer", "employer", role.employer)}
      ${field("Title", "title", role.title)}
      ${field("Start", "start", role.start)}
      ${field("End", "end", role.end)}
    </div>
    ${field("Description", "description", role.description, true)}
    <button class="ghost" type="button">Remove role</button>`;
  node.querySelector("button").onclick = () => node.remove();
  return node;
}

function schoolCard(item) {
  const node = document.createElement("div");
  node.className = "entry school";
  node.innerHTML = `<div class="grid">
      ${field("School", "school", item.school)}
      ${field("Credential", "credential", item.credential)}
      ${field("Start", "start", item.start)}
      ${field("End", "end", item.end)}
    </div>
    ${field("Details", "details", item.details, true)}
    <button class="ghost" type="button">Remove</button>`;
  node.querySelector("button").onclick = () => node.remove();
  return node;
}

function readCards(selector, fields) {
  return [...main.querySelectorAll(selector)].map((node) => {
    const data = {};
    fields.forEach((name) => { data[name] = node.querySelector(`[name=${name}]`).value; });
    return data;
  }).filter((item) => Object.values(item).some((value) => String(value).trim()));
}

async function saveReview(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const other = (session.other_sections || []).map((section, index) => ({
    title: section.title,
    text: main.querySelector(`[data-other-text="${index}"]`)?.value || section.text,
    include: Boolean(main.querySelector(`[data-other="${index}"]`)?.checked),
  }));
  const payload = {
    contact: {
      name: form.get("name"),
      email: form.get("email"),
      phone: form.get("phone"),
      location: form.get("location"),
      links: String(form.get("links") || "").split("\n"),
    },
    experience: readCards(".role", ["employer", "title", "start", "end", "description"]),
    education: readCards(".school", ["school", "credential", "start", "end", "details"]),
    skills_text: form.get("skills_text"),
    summary_text: form.get("summary_text"),
    other_sections: other,
  };
  await run(async () => {
    session = await api("/api/review", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    showBanner("good", "Experience saved.", "Generate the draft from the projects you selected.");
    await generateDraft();
  });
}

function renderGithub() {
  const github = session.github || {};
  const mapped = session.project_map || [];
  main.innerHTML = `
    <section class="card">
      <h2>Start with a GitHub user ID.</h2>
      <p class="hint">Every public repository for that user is scanned. Each map entry is a short description written from the README, repository description, and source files.</p>
      ${session.github_warning ? `<p class="banner warn"><strong>GitHub</strong>${esc(session.github_warning)}</p>` : ""}
      <form id="github">
        ${field("GitHub user ID", "username", github.username)}
        <label>GitHub personal access token
          <input name="token" type="text" autocomplete="off" spellcheck="false" placeholder="Paste a token if GitHub rate-limits the scan">
        </label>
        <p class="hint">First time here? <a href="https://github.com/settings/tokens" target="_blank" rel="noopener noreferrer">Click here to get a personal access token</a>. Create a token, copy it, and paste it above. It is only used to scan repositories and is not written into the resume.</p>
        <div class="actions">
          <button class="primary" type="submit">Scan repositories</button>
        </div>
      </form>
      <div id="repos"></div>
    </section>`;
  main.querySelector("#github").onsubmit = scanRepos;
  const host = main.querySelector("#repos");
  if (!mapped.length) {
    host.innerHTML = "<p class='hint'>No project map yet.</p>";
    return;
  }
  host.innerHTML = `<h3>${mapped.length} projects</h3>` + mapped.map((repo) => `
    <article class="repo">
      <header><strong>${esc(repo.full_name)}</strong></header>
      <p>${esc(repo.summary)}</p>
    </article>`).join("") + `<div class="actions"><button class="primary" type="button" id="to-role">Match a job description</button></div>`;
  host.querySelector("#to-role").onclick = () => { setStep(2); render(); };
}

async function scanRepos(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const username = form.get("username");
  if (!String(username || "").trim()) {
    showBanner("bad", "A GitHub username is required.", "Enter the GitHub user ID whose repositories should be mapped.");
    return;
  }
  await run(async () => {
    session = await api("/api/github/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, token: form.get("token") || "", visibility: "public" }),
    });
    const count = (session.project_map || []).length;
    showBanner(count ? "good" : "warn", session.github_warning || `Mapped ${count} repositories for ${session.github.username}.`, "Each description comes from that repository's README and code. Next, paste a job description.");
    render();
  });
}

function renderRole() {
  const suggestions = session.suggestions || [];
  const others = session.other_projects || [];
  const selected = new Set(session.selected_projects || []);
  main.innerHTML = `
    <section class="card">
      <h2>Paste the job. Keep the top three, or choose another.</h2>
      <p class="hint">Suggestions come only from the project map. Why a project fits is shown here and is not copied into the resume.</p>
      <form id="role">
        <label>Job description<textarea class="job" name="job_description">${esc(session.job_description || "")}</textarea></label>
        <div class="actions"><button class="primary" type="submit">Suggest top 3</button></div>
      </form>
      <div id="suggestions"></div>
      <div id="others" hidden></div>
      <div class="actions">
        <button class="ghost" type="button" id="back">Back</button>
        <button class="secondary" type="button" id="show-other" ${others.length ? "" : "disabled"}>Other</button>
        <button class="primary" type="button" id="use-projects">Use selected projects</button>
      </div>
    </section>`;
  const host = main.querySelector("#suggestions");
  if (!suggestions.length) {
    host.innerHTML = "<p class='hint'>No suggestions yet.</p>";
  } else {
    suggestions.forEach((item) => host.append(suggestionCard(item, selected.has(item.full_name))));
  }
  const otherHost = main.querySelector("#others");
  others.forEach((item) => otherHost.append(suggestionCard(item, selected.has(item.full_name), true)));
  main.querySelector("#role").onsubmit = suggest;
  main.querySelector("#back").onclick = () => { setStep(1); render(); };
  main.querySelector("#show-other").onclick = () => { otherHost.hidden = false; };
  main.querySelector("#use-projects").onclick = saveSelection;
}

function suggestionCard(item, checked, isOther = false) {
  const node = document.createElement("article");
  node.className = "project";
  node.innerHTML = `
    <header><label class="check"><input type="checkbox" name="selected" value="${esc(item.full_name)}" ${checked ? "checked" : ""}> ${esc(item.full_name)}</label>
      <span class="tag ${isOther ? "" : "ok"}">${isOther ? "Other" : "Suggested"}</span></header>
    <p>${esc(item.summary || "")}</p>
    <p class="evidence">Why this fits: ${esc(item.why || "")}</p>`;
  return node;
}

async function suggest(event) {
  event.preventDefault();
  const job = new FormData(event.target).get("job_description");
  await run(async () => {
    session = await api("/api/projects/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_description: job }),
    });
    const count = (session.suggestions || []).length;
    showBanner(count ? "good" : "warn", count ? `Top ${count} project${count === 1 ? "" : "s"} ready.` : "No mapped project overlapped this job.", "Use Other to pick a different mapped project, or continue with experience only.");
    render();
  });
}

async function saveSelection() {
  const selected = [...main.querySelectorAll("[name=selected]:checked")].map((node) => node.value);
  await run(async () => {
    session = await api("/api/projects/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected }),
    });
    setStep(3);
    render();
  });
}

async function generateDraft() {
  session = await api("/api/resume/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      selected: session.selected_projects || [],
      allow_empty: false,
    }),
  });
  showBanner("good", "Draft ready.", session.draft.project_note || "Edit it, then download the PDF. Match reasons stay off the resume.");
  setStep(4);
  render();
}

function renderDraft() {
  const draft = session.draft || { experience: [], education: [], projects: [] };
  main.innerHTML = `
    <section class="card">
      <h2>Edit the draft, then download it.</h2>
      <p class="hint">${esc(draft.project_note || "Private repository details stay out of the PDF unless you explicitly include them.")}</p>
      <form id="draft">
        ${field("Name", "name", draft.name)}
        ${field("Contact line", "contact_line", draft.contact_line)}
        ${field("Summary", "summary", draft.summary, true)}
        <div id="draft-experience"></div>
        <div id="draft-projects"></div>
        ${field("Skills", "skills", draft.skills, true)}
        ${field("Additional", "additional", draft.additional, true)}
        <div class="actions">
          <button class="ghost" type="button" id="back">Back</button>
          <button class="secondary" type="submit">Save edits</button>
          <button class="primary" type="button" id="download">Download PDF</button>
        </div>
      </form>
    </section>`;
  const exp = main.querySelector("#draft-experience");
  (draft.experience || []).forEach((entry) => {
    const node = document.createElement("div");
    node.className = "entry draft-role";
    node.innerHTML = `<h3>Experience</h3><div class="grid">${field("Title", "title", entry.title)}${field("Employer", "employer", entry.employer)}</div>${field("Dates", "dates", entry.dates)}${field("Description", "description", entry.description, true)}`;
    exp.append(node);
  });
  const projects = main.querySelector("#draft-projects");
  (draft.projects || []).forEach((project) => {
    const node = document.createElement("div");
    node.className = "entry draft-project";
    node.dataset.name = project.full_name;
    node.innerHTML = `<h3>${esc(project.heading)}</h3>
      <label class="check"><input type="checkbox" name="include_in_export" ${project.include_in_export ? "checked" : ""}> Include this project in the PDF${project.private ? " (required for private repositories)" : ""}</label>
      <label class="check"><input type="checkbox" name="include_link" ${project.include_link ? "checked" : ""}> Include the repository link</label>
      ${field("Heading", "heading", project.heading)}
      ${field("Description", "description", project.description, true)}`;
    projects.append(node);
  });
  main.querySelector("#back").onclick = () => { setStep(3); render(); };
  main.querySelector("#draft").onsubmit = saveDraft;
  main.querySelector("#download").onclick = downloadPdf;
}

async function saveDraft(event) {
  event.preventDefault();
  await run(async () => {
    session = await api("/api/resume", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft: collectDraft() }),
    });
    showBanner("good", "Edits saved.", "Download uses this version.");
  });
}

function collectDraft() {
  const form = main.querySelector("#draft");
  const data = new FormData(form);
  return {
    name: data.get("name"),
    contact_line: data.get("contact_line"),
    summary: data.get("summary"),
    skills: data.get("skills"),
    additional: data.get("additional"),
    experience: [...form.querySelectorAll(".draft-role")].map((node) => ({
      title: node.querySelector("[name=title]").value,
      employer: node.querySelector("[name=employer]").value,
      dates: node.querySelector("[name=dates]").value,
      description: node.querySelector("[name=description]").value,
    })),
    education: session.draft.education || [],
    projects: [...form.querySelectorAll(".draft-project")].map((node) => {
      const prior = (session.draft.projects || []).find((item) => item.full_name === node.dataset.name) || {};
      return {
        ...prior,
        heading: node.querySelector("[name=heading]").value,
        description: node.querySelector("[name=description]").value,
        include_in_export: node.querySelector("[name=include_in_export]").checked,
        include_link: node.querySelector("[name=include_link]").checked,
      };
    }),
  };
}

async function downloadPdf() {
  await run(async () => {
    session = await api("/api/resume", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft: collectDraft() }),
    });
    const response = await fetch("/api/resume/pdf", { method: "POST", credentials: "same-origin" });
    if (!response.ok) {
      const data = await response.json();
      const detail = data.detail || data;
      throw Object.assign(new Error(detail.error || "Export failed"), { recovery: detail.recovery || "" });
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "resume.pdf";
    link.click();
    URL.revokeObjectURL(url);
    showBanner("good", "PDF downloaded.", "It matches the latest saved edits.");
  });
}

async function run(fn) {
  if (busy) return;
  busy = true;
  try {
    await fn();
  } catch (error) {
    showBanner("bad", error.message || "Something went wrong.", error.recovery || "Your saved inputs are still available. Retry this step.");
  } finally {
    busy = false;
  }
}

async function boot() {
  const params = new URLSearchParams(location.search);
  if (params.get("github") === "denied") showBanner("bad", "GitHub authorization was denied.", "Retry OAuth or paste a token. Other inputs are saved.");
  if (params.get("github") === "failed") showBanner("bad", "GitHub connection failed.", "Retry, or use a personal access token.");
  if (params.get("github") === "connected") showBanner("good", "GitHub connected.", "Choose public, private, or both, then list repositories.");
  [session, config] = await Promise.all([api("/api/session"), api("/api/config")]);
  if (session.draft) step = 4;
  else if (session.extraction_status) step = 3;
  else if ((session.suggestions || []).length || session.job_description) step = 2;
  else if ((session.project_map || []).length || session.github?.connected) step = 1;
  setStep(step);
  render();
}

boot().catch((error) => showBanner("bad", error.message, error.recovery || ""));
