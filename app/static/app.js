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
    const detail = data.detail || data;
    const error = new Error(detail.error || detail.message || response.statusText);
    error.recovery = detail.recovery || "";
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
  if (step === 1) renderIntake();
  if (step === 2) renderReview();
  if (step === 3) renderGithub();
  if (step === 4) renderProjects();
  if (step === 5) renderDraft();
}

function renderIntake() {
  main.innerHTML = `
    <section class="card">
      <h2>Start from the role and your resume.</h2>
      <p class="hint">The PDF is a source of facts, not instructions. Scanned and encrypted files are rejected rather than treated as imported experience.</p>
      <form id="intake">
        <label>Job description<textarea class="job" name="job_description" required>${esc(session.job_description || "")}</textarea></label>
        <label class="file">Resume PDF<input type="file" name="resume" accept="application/pdf,.pdf"></label>
        <div class="actions">
          <button class="primary" type="submit">Extract experience</button>
          <button class="secondary" type="button" id="manual">Enter experience manually</button>
        </div>
      </form>
    </section>`;
  main.querySelector("#intake").addEventListener("submit", onIntake);
  main.querySelector("#manual").addEventListener("click", onManual);
}

async function onIntake(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  if (!String(form.get("job_description") || "").trim()) {
    showBanner("bad", "Job description is empty.", "Paste the role description and try again.");
    return;
  }
  if (!form.get("resume") || !form.get("resume").name) {
    showBanner("bad", "Choose a resume PDF.", "Or enter experience manually. An empty upload is not treated as imported experience.");
    return;
  }
  await run(async () => {
    session = await api("/api/intake", { method: "POST", body: form });
    showBanner(session.extraction_status === "ok" ? "good" : "warn", session.extraction_message, "");
    setStep(2);
    render();
  });
}

async function onManual() {
  const job = main.querySelector("[name=job_description]").value;
  await run(async () => {
    session = await api("/api/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_description: job }),
    });
    showBanner("warn", session.extraction_message, "Add at least one role before generating, or continue later with projects only.");
    setStep(2);
    render();
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
  main.querySelector("#back").onclick = () => { setStep(1); render(); };
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
    showBanner("good", "Experience saved.", "Next, choose which repositories may be analyzed.");
    setStep(3);
    render();
  });
}

function renderGithub() {
  const github = session.github || {};
  const visibility = github.visibility || "public";
  main.innerHTML = `
    <section class="card">
      <h2>Choose what GitHub may be read.</h2>
      <p class="hint">Public-only is the default. Private repositories are listed only after you authorize access, and nothing is analyzed until you select it.</p>
      ${session.github_warning ? `<p class="banner warn"><strong>GitHub</strong>${esc(session.github_warning)}</p>` : ""}
      <form id="github">
        <div class="choices">
          ${["public", "private", "both"].map((value) => `<label><input type="radio" name="visibility" value="${value}" ${visibility === value ? "checked" : ""}> ${value === "both" ? "Public and private" : value[0].toUpperCase() + value.slice(1) + " only"}</label>`).join("")}
        </div>
        ${field("GitHub username", "username", github.username)}
        <label>Personal access token <input name="token" type="password" autocomplete="off" placeholder="${github.auth_method ? "Saved token stays on the server" : "Required for private repositories"}"></label>
        <p class="hint">The token is stored only for this session and is never written into the resume. ${config.oauth_available ? "OAuth is available." : "OAuth is not configured in this environment; a token or public username still works."}</p>
        <div class="actions">
          <button class="ghost" type="button" id="back">Back</button>
          ${config.oauth_available ? `<a class="secondary" href="/api/github/oauth/start">Connect with GitHub</a>` : ""}
          <button class="primary" type="submit">List permitted repositories</button>
        </div>
      </form>
      <div id="repos"></div>
    </section>`;
  main.querySelector("#back").onclick = () => { setStep(2); render(); };
  main.querySelector("#github").onsubmit = listRepos;
  drawRepos();
}

function drawRepos() {
  const host = main.querySelector("#repos");
  if (!host) return;
  const repos = session.eligible_repos || [];
  if (!repos.length) {
    host.innerHTML = "<p class='hint'>No repositories listed yet.</p>";
    return;
  }
  host.innerHTML = `<h3>${repos.length} eligible repositories</h3>` + repos.map((repo) => `
    <article class="repo">
      <header><label class="check"><input type="checkbox" name="repo" value="${esc(repo.full_name)}"> ${esc(repo.full_name)}</label>
        <span class="tag ${repo.private ? "private" : ""}">${repo.private ? "Private" : "Public"}${repo.fork ? " · fork" : ""}</span></header>
      <p class="evidence">${esc(repo.description || "No description")}${repo.language ? " · " + esc(repo.language) : ""}</p>
    </article>`).join("") + `<div class="actions"><button class="primary" type="button" id="analyze">Analyze selected</button></div>`;
  host.querySelector("#analyze").onclick = analyze;
}

async function listRepos(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  await run(async () => {
    session = await api("/api/github/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        visibility: form.get("visibility"),
        username: form.get("username"),
        token: form.get("token"),
      }),
    });
    showBanner(session.github_warning ? "warn" : "good", session.github_warning || `Listed repositories for ${session.github.username}.`, "Select repositories, then analyze. Unselected repositories are not read.");
    render();
  });
}

async function analyze() {
  const names = [...main.querySelectorAll("[name=repo]:checked")].map((node) => node.value);
  await run(async () => {
    session = await api("/api/github/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repositories: names }),
    });
    const found = (session.recommendations || []).filter((item) => item.recommended).length;
    showBanner(found ? "good" : "warn", found ? `${found} repositories overlap the role.` : "No relevant projects were found.", "You can still generate from confirmed experience. Deselected projects will be left out.");
    setStep(4);
    render();
  });
}

function renderProjects() {
  const items = session.recommendations || [];
  main.innerHTML = `
    <section class="card">
      <h2>Keep the projects that belong.</h2>
      <p class="hint">Recommendations include the overlapping terms and the repository evidence. A fork or owner flag is not treated as your contribution unless commit authorship matches.</p>
      <div id="projects">${items.length ? "" : "<p>No repositories were analyzed. You can generate from confirmed experience without adding projects.</p>"}</div>
      <label class="check"><input type="checkbox" id="allow-empty"> Continue even if experience and projects are both empty</label>
      <div class="actions">
        <button class="ghost" type="button" id="back">Back</button>
        <button class="primary" type="button" id="generate">Generate draft</button>
      </div>
    </section>`;
  const host = main.querySelector("#projects");
  items.forEach((item) => host.append(projectCard(item)));
  main.querySelector("#back").onclick = () => { setStep(3); render(); };
  main.querySelector("#generate").onclick = generate;
}

function projectCard(item) {
  const node = document.createElement("article");
  node.className = "project";
  const evidence = (item.evidence || []).map((entry) => `<p class="evidence">${esc(entry.source)}: ${esc(entry.detail)}</p>`).join("");
  node.innerHTML = `
    <header><label class="check"><input type="checkbox" name="selected" value="${esc(item.full_name)}" ${item.selected ? "checked" : ""}> ${esc(item.full_name)}</label>
      <span class="tag ${item.recommended ? "ok" : ""}">${item.recommended ? "Recommended" : "No overlap"}</span></header>
    <p>${item.contribution_verified ? "Commit authorship matches the connected account." : "Personal contribution was not verified."} ${item.private ? "Private evidence stays in this review unless you later opt to include the project." : ""}</p>
    ${evidence}`;
  return node;
}

async function generate() {
  const selected = [...main.querySelectorAll("[name=selected]:checked")].map((node) => node.value);
  await run(async () => {
    session = await api("/api/resume/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        selected,
        allow_empty: main.querySelector("#allow-empty").checked,
      }),
    });
    showBanner("good", "Draft ready.", session.draft.project_note || "Edit it, then download the PDF.");
    setStep(5);
    render();
  });
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
      ${field("Description", "description", project.description, true)}
      ${field("Attribution", "attribution", project.attribution, true)}
      ${field("Relevance", "relevance", project.relevance, true)}`;
    projects.append(node);
  });
  main.querySelector("#back").onclick = () => { setStep(4); render(); };
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
        attribution: node.querySelector("[name=attribution]").value,
        relevance: node.querySelector("[name=relevance]").value,
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
  if (session.draft) step = 5;
  else if ((session.recommendations || []).length) step = 4;
  else if ((session.eligible_repos || []).length || session.github?.connected) step = 3;
  else if (session.extraction_status) step = 2;
  setStep(step);
  render();
}

boot().catch((error) => showBanner("bad", error.message, error.recovery || ""));
