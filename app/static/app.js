let MENU_ID = null;
let allItems = [];
let allCategories = [];
let activeFilters = new Set();
let currentLang = "en";

let adminToken = null; // in-memory only, lost on refresh — local demo, not real auth

// icon shown in the small circular badge on each allergen/diet chip.
// emoji where a clear one-to-one match exists, otherwise a short monogram.
const CHIP_ICON = {
  "Contains Wheat/Gluten": "🌾",
  "Contains Crustacea": "🦐",
  "Contains Egg": "🥚",
  "Contains Fish": "🐟",
  "Contains Milk": "🥛",
  "Contains Peanuts": "🥜",
  "Contains Soy": "🫘",
  "Contains Tree Nuts": "🌰",
  "Contains Sesame": "Se",
  "Contains Lupin": "Lu",
  "Contains Sulphites": "SO₂",
  "Gluten-Free": "GF",
  "Dairy-Free": "DF",
};
function chipIcon(label) {
  return CHIP_ICON[label] || label.slice(0, 2);
}

// function added when the user backs to cafe picker
function backToCafePicker() {
  sessionStorage.removeItem("selectedMenuId");
  MENU_ID = null;
  document.getElementById("mainApp").classList.add("hidden");
  document.getElementById("cafePickerScreen").classList.remove("hidden");
  loadCafePicker();
}

// ---------------- 
// login / logout button toggle ----------------
// These two are top-level functions (not nested inside loginAsAdmin) so
// the "onclick" attribute in index.html can actually find them.
function handleAuthClick() {
  if (adminToken) {
    logout();
  } else {
    loginAsAdmin();
  }
}

function logout() {
  adminToken = null;
  sessionStorage.removeItem("adminToken");
  document.getElementById("managementPanel").classList.add("hidden");
  document.getElementById("adminAuthBtn").textContent = "Login as Admin";
  renderGrid();
}

// fuction for login as admin
function loginAsAdmin() {
  document.getElementById("modalTitle").textContent = "Admin Login";
  document.getElementById("modalBody").innerHTML = `
    <div id="loginError" class="muted" style="color:#c0392b; display:none;"></div>
    <label>Username</label>
    <input type="text" class="form-control" id="loginUsername" />
    <label class="mt-2">Password</label>
    <input type="password" class="form-control" id="loginPassword" />
    <div class="modal-actions">
      <button class="btn btn-primary" id="loginSubmitBtn">Log in</button>
    </div>
  `;
  document.getElementById("editModal").classList.remove("hidden");

  const submit = async () => {
    const username = document.getElementById("loginUsername").value.trim();
    const password = document.getElementById("loginPassword").value.trim();
    const errorEl = document.getElementById("loginError");
    errorEl.style.display = "none";
    try {
      const res = await fetch("/api/v2/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) throw new Error("Invalid credentials");
      const data = await res.json();
      adminToken = data.token;
      sessionStorage.setItem("adminToken", adminToken);
      document.getElementById("managementPanel").classList.remove("hidden");
      document.getElementById("adminAuthBtn").textContent = "Logout";
      renderGrid();
      closeModal();
    } catch (err) {
      errorEl.textContent = "Login failed: " + err.message;
      errorEl.style.display = "block";
    }
  };

  document.getElementById("loginSubmitBtn").onclick = submit;
  document.getElementById("loginPassword").addEventListener("keydown", e => {
    if (e.key === "Enter") submit();
  });
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

async function loadCategories() {
  const data = await api("/api/allergen-categories");
  allCategories = data.categories;
  const el = document.getElementById("categoryList");
  el.innerHTML = allCategories.map(c =>
    `<span class="chip"><span class="icon-badge">${chipIcon(c).slice(0, 2)}</span>${c}</span>`
  ).join("");
}

async function loadItems() {
  const data = await api(`/api/v2/menus/${MENU_ID}`);
  allItems = data.items || [];
  renderGrid();
}

// for the inital page cafe picker
async function loadCafePicker() {
  const data = await api("/api/v2/restaurants");
  const container = document.getElementById("cafePicker");
  container.innerHTML = data.restaurants.map(r => `
    <button class="btn btn-outline-secondary m-1" onclick="selectCafe('${r.menu_id}')">${r.menu_id}</button>
  `).join("");
}

function selectCafe(menuId) {
  MENU_ID = menuId;
  sessionStorage.setItem("selectedMenuId", menuId);
  document.getElementById("cafePickerScreen").classList.add("hidden");
  document.getElementById("mainApp").classList.remove("hidden");
  loadCategories();
  loadItems();
}

function translatedView(item) {
  if (currentLang === "en" || !item.translations || !item.translations[currentLang]) {
    return { name: item.name, description: item.description };
  }
  return item.translations[currentLang];
}

function passesFilters(item) {
  if (activeFilters.size === 0) return true;
  const dietTags = new Set(item.diet_tags || []);
  for (const f of activeFilters) {
    if (!dietTags.has(f)) return false;
  }
  return true;
}

function renderGrid() {
  const grid = document.getElementById("dishGrid");
  const visible = allItems.filter(passesFilters);
  if (visible.length === 0) {
    grid.innerHTML = `<p class="muted">No dishes yet - load the sample menu, upload a file, or add one manually.</p>`;
    return;
  }
  grid.innerHTML = visible.map(item => {
    const view = translatedView(item);
    const tags = (item.allergens?.display_tags || []).map(t =>
      `<span class="chip warn"><span class="icon-badge">${chipIcon(t)}</span>${t}</span>`
    ).join("");
    const diet = (item.diet_tags || []).map(t =>
      `<span class="chip diet"><span class="icon-badge">${chipIcon(t)}</span>${t}</span>`
    ).join("");
    const disagree = item.allergens?.disagreements;
    const hasDisagreement = disagree && (disagree.llm_only?.length || disagree.rule_only?.length);
    return `
      <div class="dish-card" data-id="${item.item_id}">
        <h4>${escapeHtml(view.name)}</h4>
        ${currentLang !== "en" ? `<div class="translated-name">EN: ${escapeHtml(item.name)}</div>` : ""}
        <p>${escapeHtml(view.description)}</p>
        <div class="tags">${tags}${diet}</div>
        <div class="status-badge">${item.status}${hasDisagreement ? " · needs review" : ""}</div>
      </div>`;
  }).join("");

  grid.querySelectorAll(".dish-card").forEach(card => {
    if (adminToken) {
      card.addEventListener("click", () => openModal(card.dataset.id));
    } else {
      card.style.cursor = "default";
    }
  });
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------- modal (human-in-the-loop review) ----------------
function openModal(itemId) {
  const item = allItems.find(i => i.item_id === itemId);
  if (!item) return;
  document.getElementById("modalTitle").textContent = item.name;
  const confirmed = new Set(item.allergens?.confirmed || []);
  const checks = allCategories.map(c => `
    <label><input type="checkbox" value="${c}" ${confirmed.has(c) ? "checked" : ""}/> ${c}</label>
  `).join("");

  const disagree = item.allergens?.disagreements || { llm_only: [], rule_only: [] };
  const disagreeHtml = (disagree.llm_only?.length || disagree.rule_only?.length)
    ? `<p class="muted">⚠ AI-only flagged: ${disagree.llm_only.join(", ") || "none"} · Rules-only flagged: ${disagree.rule_only.join(", ") || "none"}</p>`
    : `<p class="muted">AI extraction and rules-engine scan agreed on all allergens.</p>`;

  // NEW: editable dish name + description fields, replacing the old
  // read-only <p> paragraph. editMenu's backend already supports these
  // two fields (see build_update_item_kwargs in handler.py) - this was
  // purely a missing piece on the frontend side.
  document.getElementById("modalBody").innerHTML = `
    <label>Dish name</label>
    <input type="text" id="editName" value="${escapeHtml(item.name)}" />

    <label>Description</label>
    <textarea id="editDescription" rows="3">${escapeHtml(item.description)}</textarea>

    ${disagreeHtml}
    <label>Confirmed allergens (human-in-the-loop override)</label>
    <div class="allergen-checks">${checks}</div>
    <div class="modal-actions">
      <button class="btn primary" id="saveReviewBtn">Save as Human-Verified</button>
      <button class="btn" id="deleteItemBtn">Delete dish</button>
    </div>
  `;
  document.getElementById("editModal").classList.remove("hidden");

  document.getElementById("saveReviewBtn").onclick = async () => {
    const selected = Array.from(document.querySelectorAll("#modalBody .allergen-checks input:checked")).map(i => i.value);
    // NEW: read the (possibly edited) name/description and include them in
    // the PATCH body alongside the allergen selections.
    const name = document.getElementById("editName").value.trim();
    const description = document.getElementById("editDescription").value.trim();
    await api(`/api/v2/menus/${MENU_ID}/items/${itemId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${adminToken}` },
      body: JSON.stringify({ confirmed_allergens: selected, name, description }),
    });
    closeModal();
    await loadItems();
  };
  document.getElementById("deleteItemBtn").onclick = async () => {
    await api(`/api/menus/${MENU_ID}/items/${itemId}`, { method: "DELETE" });
    closeModal();
    await loadItems();
  };
}
function closeModal() { document.getElementById("editModal").classList.add("hidden"); }
document.getElementById("modalClose").onclick = closeModal;
document.getElementById("editModal").addEventListener("click", e => {
  if (e.target.id === "editModal") closeModal();
});

// ---------------- language + filters ----------------
document.getElementById("langSelect").addEventListener("change", e => {
  currentLang = e.target.value;
  renderGrid();
});
document.getElementById("filters").addEventListener("change", e => {
  const val = e.target.value;
  if (e.target.checked) activeFilters.add(val); else activeFilters.delete(val);
  renderGrid();
});
document.getElementById("refreshBtn").addEventListener("click", loadItems);
document.getElementById("clearBtn").addEventListener("click", async () => {
  if (allItems.length === 0) return;
  const confirmed = window.confirm(
    `Clear all ${allItems.length} dishes from the current menu? This cannot be undone.`
  );
  if (!confirmed) return;

  const button = document.getElementById("clearBtn");
  const status = document.getElementById("clearStatus");
  button.disabled = true;
  status.textContent = "Clearing dishes...";
  try {
    const result = await api(`/api/menus/${MENU_ID}`, { method: "DELETE" });
    activeFilters.clear();
    document.querySelectorAll("#filters input").forEach(input => { input.checked = false; });
    await loadItems();
    status.textContent = `Cleared ${result.deleted_count} dish(es). You can upload a new menu now.`;
  } catch (err) {
    status.textContent = `Failed: ${err.message}`;
  } finally {
    button.disabled = false;
  }
});

// ---------------- seed sample menu ----------------
document.getElementById("seedBtn")?.addEventListener("click", async () => {
  const status = document.getElementById("seedStatus");
  status.textContent = "Running OCR-skip → allergen analysis → compliance verify → translation for 8 dishes...";
  try {
    await api(`/api/menus/${MENU_ID}/seed`, { method: "POST" });
    status.textContent = "Sample menu loaded.";
    await loadItems();
  } catch (err) {
    status.textContent = `Failed: ${err.message}`;
  }
});

// ---------------- manual add ----------------
document.getElementById("manualForm").addEventListener("submit", async e => {
  e.preventDefault();
  const name = document.getElementById("dishName").value.trim();
  const description = document.getElementById("dishDesc").value.trim();
  const status = document.getElementById("manualStatus");
  status.textContent = "Analyzing...";
  try {
    await api(`/api/menus/${MENU_ID}/items`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    });
    status.textContent = "Added.";
    document.getElementById("manualForm").reset();
    await loadItems();
  } catch (err) {
    status.textContent = `Failed: ${err.message}`;
  }
});

// ---------------- file upload ----------------
async function prepareUploadFile(file) {
  const isWebp = file.type === "image/webp" || file.name.toLowerCase().endsWith(".webp");
  if (!isWebp) return file;

  const bitmap = await createImageBitmap(file);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, 0, 0);
  bitmap.close();

  const jpegBlob = await new Promise((resolve, reject) => {
    canvas.toBlob(
      blob => blob ? resolve(blob) : reject(new Error("Could not convert WebP image.")),
      "image/jpeg",
      0.95,
    );
  });
  const baseName = file.name.replace(/\.webp$/i, "");
  return new File([jpegBlob], `${baseName}.jpg`, { type: "image/jpeg" });
}

document.getElementById("uploadBtn").addEventListener("click", async () => {
  const fileInput = document.getElementById("fileInput");
  const progress = document.getElementById("uploadProgress");
  if (!fileInput.files.length) {
    progress.innerHTML = `<div>Select a file first.</div>`;
    return;
  }
  const steps = ["Preparing image...", "Uploading to S3...", "Running Textract OCR...", "Analyzing allergens (Bedrock + rules engine)...", "Translating (4 languages)...", "Saving to DynamoDB..."];
  progress.innerHTML = steps.map(s => `<div>${s}</div>`).join("");

  const formData = new FormData();
  try {
    const uploadFile = await prepareUploadFile(fileInput.files[0]);
    formData.append("file", uploadFile);
    const result = await api(`/api/menus/${MENU_ID}/upload`, { method: "POST", body: formData });
    progress.innerHTML += `<div>Done - ${result.items.length} dish(es) extracted.</div>`;
    await loadItems();
  } catch (err) {
    progress.innerHTML += `<div>Failed: ${err.message}</div>`;
  }
});

// ---------------- init ----------------
(async function init() {
  const remembered = sessionStorage.getItem("selectedMenuId");
  if (remembered) {
    selectCafe(remembered);
  } else {
    await loadCafePicker();
  }

  const rememberedToken = sessionStorage.getItem("adminToken");
  if (rememberedToken) {
    adminToken = rememberedToken;
    document.getElementById("managementPanel").classList.remove("hidden");
    document.getElementById("adminAuthBtn").textContent = "Logout";
    renderGrid();
  }
})();
