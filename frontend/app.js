/**
 * Radar de Vagas — Frontend Engine
 * Design moderno com suporte nativo a Tema Escuro (padrão), Painel & Kanban,
 * e integração completa com a API Flask (/api/jobs, /api/scan, etc.)
 */

const { KANBAN_COLUMNS, toViewJob, scoreColor, escapeHtml } = JobModel;

class RadarApp {
  constructor() {
    this.jobs = [];
    this.view = "painel"; // 'painel' | 'kanban'
    this.tab = "nova"; // 'nova' | 'enviada' | 'oculta'
    this.pillar = "todos"; // 'todos' | 'nocode' | 'vibecoding' | 'automacao'
    this.workplace = "todos"; // 'todos' | 'remoto' | 'hibrido' | 'presencial'
    this.query = "";
    this.theme = localStorage.getItem("radar_theme") || "dark";
    this.openId = null;
    this.trash = null;
    this.scanInterval = null;

    this.init();
  }

  init() {
    this.applyTheme(this.theme);
    this.bindEvents();
    this.loadJobs();
    this.checkScanStatus();
  }

  // ── Theme Handling ──────────────────────────────────────────────────────────
  applyTheme(theme) {
    this.theme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("radar_theme", theme);
    const themeBtn = document.getElementById("btn-theme");
    if (themeBtn) {
      themeBtn.textContent = theme === "light" ? "☾" : "☀";
      themeBtn.title = theme === "light" ? "Mudar para modo escuro" : "Mudar para modo claro";
    }
  }

  toggleTheme() {
    this.applyTheme(this.theme === "light" ? "dark" : "light");
  }

  // ── API Calls ───────────────────────────────────────────────────────────────
  async loadJobs() {
    try {
      const res = await fetch("/api/jobs?include_rejected=true");
      if (!res.ok) throw new Error("Falha ao carregar vagas.");
      const rawJobs = await res.json();

      this.jobs = rawJobs.map(toViewJob);

      this.render();
      this.updateStats();
    } catch (err) {
      console.error("[Radar] Erro ao buscar vagas:", err);
      this.showToast("Erro ao conectar com o banco de dados.");
    }
  }

  async updateJobStatus(jobId, newStatus, showToastMsg = true) {
    const job = this.jobs.find(j => j.id === jobId);
    if (!job) return;

    const oldStatus = job.status;
    job.status = newStatus;
    this.render();
    this.updateStats();

    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus })
      });
      if (!res.ok) throw new Error("Erro na API");

      if (showToastMsg) {
        const msg = newStatus === "enviada" ? `Candidatura registrada · ${job.company}`
                  : newStatus === "oculta" ? `Vaga ocultada · ${job.title}`
                  : `Movida para ${newStatus}`;
        this.showToast(msg, { kind: "status", job, oldStatus });
      }
    } catch (err) {
      console.error("[Radar] Falha ao salvar status:", err);
      job.status = oldStatus;
      this.render();
      this.updateStats();
      if (this.openId === jobId) this.openDetail(jobId);
      this.showToast("Falha ao atualizar status no servidor.");
    }
  }

  async triggerScan() {
    const scanBtn = document.getElementById("btn-scan");
    if (scanBtn) scanBtn.disabled = true;

    try {
      const res = await fetch("/api/scan", { method: "POST" });
      const data = await res.json();
      if (!res.ok && !data.status?.is_running) throw new Error(data.error || data.message);
      this.showToast(data.message || "Busca de vagas iniciada em segundo plano!");
      this.startScanPolling();
    } catch (err) {
      console.error("[Radar] Erro ao disparar scan:", err);
      this.showToast("Não foi possível iniciar o rastreamento.");
      if (scanBtn) scanBtn.disabled = false;
    }
  }

  startScanPolling() {
    clearInterval(this.scanInterval);
    this._wasScanning = true;
    const scanDot = document.getElementById("scan-dot");
    const scanText = document.getElementById("scan-text");
    const scanline = document.getElementById("scanline");

    if (scanDot) scanDot.classList.add("is-running");
    if (scanline) scanline.hidden = false;
    if (scanText) scanText.textContent = "iniciando busca...";

    this.scanInterval = setInterval(async () => {
      await this.checkScanStatus();
    }, 1500);
  }

  async checkScanStatus() {
    try {
      const res = await fetch("/api/scan/status");
      if (!res.ok) return;
      const data = await res.json();

      const scanDot = document.getElementById("scan-dot");
      const scanText = document.getElementById("scan-text");
      const scanline = document.getElementById("scanline");
      const scanBtn = document.getElementById("btn-scan");

      if (data.is_running) {
        if (!this.scanInterval) this.startScanPolling();
        this._wasScanning = true;
        if (scanDot) scanDot.classList.add("is-running");
        if (scanline) scanline.hidden = false;
        if (scanText) scanText.textContent = (data.progress_text || "rastreando vagas...").toLowerCase();
        if (scanBtn) scanBtn.disabled = true;
      } else {
        if (scanDot) scanDot.classList.remove("is-running");
        if (scanline) scanline.hidden = true;
        if (scanText) scanText.textContent = data.progress_text || "agente ocioso";
        if (scanBtn) scanBtn.disabled = false;

        if (this._wasScanning) {
          this._wasScanning = false;
          clearInterval(this.scanInterval);
          this.scanInterval = null;
          this.loadJobs();
          const sourceErrors = Object.entries(data.result?.sources || {})
            .filter(([, source]) => source.errors?.length)
            .map(([name, source]) => `${name}: ${source.errors.length}`);
          if (sourceErrors.length) {
            this.showToast(`Busca concluída com alertas (${sourceErrors.join(", ")}).`);
          } else if (data.jobs_found_last_run > 0) {
            this.showToast(`Varredura concluída! ${data.jobs_found_last_run} nova(s) vaga(s) encontrada(s).`);
          }
        }
      }
    } catch (err) {
      console.warn("[Radar] Erro ao checar status de scan:", err);
    }
  }

  // ── Toast & Undo ────────────────────────────────────────────────────────────
  showToast(message, trash = null) {
    this.trash = trash;
    const toastEl = document.getElementById("toast-popup");
    const msgEl = document.getElementById("toast-msg");
    const undoBtn = document.getElementById("btn-toast-undo");

    if (!toastEl || !msgEl) return;

    msgEl.textContent = message;
    if (undoBtn) undoBtn.style.display = trash ? "inline-block" : "none";

    toastEl.hidden = false;

    clearTimeout(this._toastTimeout);
    this._toastTimeout = setTimeout(() => {
      toastEl.hidden = true;
      this.trash = null;
    }, 5500);
  }

  async undo() {
    const t = this.trash;
    const toastEl = document.getElementById("toast-popup");
    if (toastEl) toastEl.hidden = true;

    if (!t) return;
    this.trash = null;
    if (t.kind === "status") {
      await this.updateJobStatus(t.job.id, t.oldStatus);
    }
  }

  // ── Filtering Logic ─────────────────────────────────────────────────────────
  getFilteredJobs() {
    const q = this.query.trim().toLowerCase();
    const p = this.pillar;
    const w = this.workplace;

    return this.jobs.filter(j => {
      if (p !== "todos" && j.pillar !== p) return false;
      if (w !== "todos" && j.workplace !== w) return false;
      if (!q) return true;
      const haystack = (
        j.title + " " +
        j.company + " " +
        (j.stack || []).join(" ") + " " +
        j.place + " " +
        (j.reasons || []).join(" ") + " " +
        (j.strengths || []).join(" ") + " " +
        (j.gaps || []).join(" ") + " " +
        (j.description || "")
      ).toLowerCase();
      return haystack.includes(q);
    });
  }

  // ── Stats Calculation ───────────────────────────────────────────────────────
  updateStats() {
    const total = this.jobs.length;
    const novas = this.jobs.filter(j => j.status === "nova").length;
    const enviadas = this.jobs.filter(j => j.status === "enviada").length;
    const active = this.jobs.filter(j => j.status !== "oculta");
    const avgMatch = active.length
      ? Math.round(active.reduce((acc, j) => acc + j.score, 0) / active.length)
      : 0;

    const elTotal = document.getElementById("stat-total");
    const elNovas = document.getElementById("stat-novas");
    const elEnviadas = document.getElementById("stat-candidaturas");
    const elMatch = document.getElementById("stat-match");

    if (elTotal) elTotal.textContent = total;
    if (elNovas) elNovas.textContent = novas;
    if (elEnviadas) elEnviadas.textContent = enviadas;
    if (elMatch) elMatch.textContent = active.length ? `${avgMatch}%` : "—";

    // Update tab counts
    const statuses = KANBAN_COLUMNS;
    const filtered = this.getFilteredJobs();
    statuses.forEach(st => {
      const countEl = document.getElementById(`count-${st}`);
      if (countEl) countEl.textContent = filtered.filter(j => j.status === st).length;
      const kCountEl = document.getElementById(`kanban-count-${st}`);
      if (kCountEl) kCountEl.textContent = filtered.filter(j => j.status === st).length;
    });
  }

  // ── Rendering ───────────────────────────────────────────────────────────────
  render() {
    if (this.view === "painel") {
      this.renderTable();
    } else {
      this.renderKanban();
    }
  }

  renderTable() {
    const tbody = document.getElementById("jobs-table-body");
    const emptyEl = document.getElementById("table-empty");
    if (!tbody) return;

    const filtered = this.getFilteredJobs()
      .filter(j => j.status === this.tab)
      .sort((a, b) => b.score - a.score);

    if (filtered.length === 0) {
      tbody.innerHTML = "";
      if (emptyEl) {
        emptyEl.hidden = false;
        const titleEl = document.getElementById("empty-title");
        const hintEl = document.getElementById("empty-hint");
        if (titleEl) {
          const labels = { nova: "Nenhuma nova vaga no momento", enviada: "Nenhuma candidatura enviada ainda", oculta: "Nenhuma vaga não compatível" };
          titleEl.textContent = labels[this.tab] || "Nenhuma vaga neste estado";
        }
        if (hintEl) {
          hintEl.textContent = this.query ? `Sua busca por "${this.query}" não bateu com nada.`
            : "Clique em 'Rastrear vagas' acima para buscar novas oportunidades.";
        }
      }
      return;
    }

    if (emptyEl) emptyEl.hidden = true;

    tbody.innerHTML = filtered.map(j => {
      const isApplied = j.status === "enviada";
      const isHidden = j.status === "oculta";
      const color = scoreColor(j.score);

      return `
        <div class="job-row" data-id="${escapeHtml(j.id)}">
          <!-- Score Column -->
          <div class="score-col">
            <span class="score-num mono" style="color: ${color}">${j.score}</span>
            <span class="score-track">
              <span class="score-fill" style="background: ${color}; width: ${j.score}%"></span>
            </span>
          </div>

          <!-- Info Column -->
          <div class="info-col" onclick="radarApp.openDetail(${escapeHtml(JSON.stringify(j.id))})">
            <div class="job-title">${escapeHtml(j.title)}</div>
            <div class="job-meta-sub">
              <span class="job-company">${escapeHtml(j.company)}</span>
              <span class="meta-dot"></span>
              <span class="job-place mono">${escapeHtml(j.place)}</span>
              <span class="job-level mono">${escapeHtml(j.level)}</span>
            </div>
          </div>

          <!-- Stack Column -->
          <div class="stack-col">
            ${j.stack.map(s => `<span class="stack-pill mono">${escapeHtml(s)}</span>`).join("")}
          </div>

          <!-- Salary Column -->
          <div class="salary-col mono">${escapeHtml(j.salary)}</div>

          <!-- Origin Column -->
          <div class="origin-col">
            <span class="source-name mono">${escapeHtml(j.source)}</span>
            <span class="posted-time mono">${escapeHtml(j.posted)}</span>
          </div>

          <!-- Actions Column -->
          <div class="actions-col">
            <button type="button" class="btn-applied-toggle mono ${isApplied ? "is-applied" : ""}"
              title="${isApplied ? "Candidatura registrada (clique para desmarcar)" : "Marcar que já me candidatei (ocultar das novas e salvar em Candidaturas)"}"
              onclick="radarApp.toggleApplied(${escapeHtml(JSON.stringify(j.id))})">
              ${isApplied ? "Enviada ✓" : "✓ Candidatei"}
            </button>
            <a href="${escapeHtml(j.link)}" target="_blank" rel="noopener noreferrer" class="action-btn-sm mono" title="Abrir link externo">↗</a>
            <button type="button" class="action-btn-sm mono" title="${isHidden ? "Reativar" : "Ocultar"}"
              onclick="radarApp.toggleHide(${escapeHtml(JSON.stringify(j.id))})">
              ${isHidden ? "◑" : "◐"}
            </button>
          </div>
        </div>
      `;
    }).join("");
  }

  renderKanban() {
    const filtered = this.getFilteredJobs();
    const columns = KANBAN_COLUMNS;

    columns.forEach(colKey => {
      const container = document.getElementById(`kanban-cards-${colKey}`);
      if (!container) return;

      const colJobs = filtered.filter(j => j.status === colKey).sort((a, b) => b.score - a.score);
      const colIndex = KANBAN_COLUMNS.indexOf(colKey);

      if (colJobs.length === 0) {
        container.innerHTML = `<div style="padding:24px 6px;text-align:center;font-family:'JetBrains Mono',monospace;font-size:10.5px;letter-spacing:0.1em;text-transform:uppercase;color:var(--muted);border:1px dashed var(--line-2);border-radius:2px">vazio</div>`;
        return;
      }

      container.innerHTML = colJobs.map(j => {
        const color = scoreColor(j.score);
        const canBack = colIndex > 0;
        const canNext = colIndex < KANBAN_COLUMNS.length - 1;
        const isHidden = j.status === "oculta";

        return `
          <div class="kanban-card" data-id="${escapeHtml(j.id)}">
            <div class="kanban-card-top">
              <div class="kanban-card-title" onclick="radarApp.openDetail(${escapeHtml(JSON.stringify(j.id))})">${escapeHtml(j.title)}</div>
              <span class="kanban-card-score mono" style="color: ${color}">${j.score}</span>
            </div>

            <div class="kanban-card-meta">
              <span class="job-company" style="font-size:11.5px">${escapeHtml(j.company)}</span>
              <span class="meta-dot"></span>
              <span class="job-place mono" style="font-size:10px">${escapeHtml(j.place)}</span>
            </div>

            <div class="detail-stack-pills">
              ${j.stack.map(s => `<span class="stack-pill mono" style="font-size:9.5px">${escapeHtml(s)}</span>`).join("")}
            </div>

            <div class="kanban-card-bottom">
              <div style="display:flex;gap:4px">
                <button type="button" class="kanban-stage-btn mono" title="Voltar etapa"
                  ${canBack ? "" : "disabled"} onclick="radarApp.moveStage(${escapeHtml(JSON.stringify(j.id))}, -1)">←</button>
                <button type="button" class="kanban-stage-btn mono" title="Avançar etapa"
                  ${canNext ? "" : "disabled"} onclick="radarApp.moveStage(${escapeHtml(JSON.stringify(j.id))}, 1)">→</button>
              </div>

              <div style="display:flex;gap:4px">
                <a href="${escapeHtml(j.link)}" target="_blank" rel="noopener noreferrer" class="action-btn-sm mono" style="width:26px;height:24px;font-size:10px" title="Abrir vaga">↗</a>
                <button type="button" class="action-btn-sm mono" style="width:26px;height:24px;font-size:10px" title="${isHidden ? "Reativar para Novas" : "Mover para Não Compatível"}"
                  onclick="radarApp.toggleHide(${escapeHtml(JSON.stringify(j.id))})">${isHidden ? "◑" : "◐"}</button>
              </div>
            </div>
          </div>
        `;
      }).join("");
    });
  }

  // ── Actions ─────────────────────────────────────────────────────────────────
  toggleApplied(jobId) {
    const job = this.jobs.find(j => j.id === jobId);
    if (!job) return;

    const request = this.updateJobStatus(jobId, job.status === "enviada" ? "nova" : "enviada");
    if (this.openId === jobId) this.openDetail(jobId);
    return request;
  }

  toggleHide(jobId) {
    const job = this.jobs.find(j => j.id === jobId);
    if (!job) return;
    const newStatus = job.status === "oculta" ? "nova" : "oculta";
    return this.updateJobStatus(jobId, newStatus);
  }

  moveStage(jobId, dir) {
    const job = this.jobs.find(j => j.id === jobId);
    if (!job) return;
    const currentIdx = KANBAN_COLUMNS.indexOf(job.status);
    if (currentIdx < 0) return;
    const nextIdx = Math.max(0, Math.min(KANBAN_COLUMNS.length - 1, currentIdx + dir));
    if (nextIdx !== currentIdx) {
      this.updateJobStatus(jobId, KANBAN_COLUMNS[nextIdx]);
    }
  }

  // ── Detail Modal ────────────────────────────────────────────────────────────
  openDetail(jobId) {
    const job = this.jobs.find(j => j.id === jobId);
    if (!job) return;

    this.openId = jobId;
    const drawer = document.getElementById("detail-drawer");
    const backdrop = document.getElementById("detail-backdrop");

    if (!drawer || !backdrop) return;

    const color = scoreColor(job.score);

    // Populate Fields
    document.getElementById("detail-source-meta").textContent = `${job.source.toUpperCase()} · ${job.posted.toUpperCase()}`;
    document.getElementById("detail-title").textContent = job.title;
    document.getElementById("detail-company").textContent = job.company;
    document.getElementById("detail-place").textContent = job.place;
    document.getElementById("detail-salary").textContent = job.salary;

    // Score
    const scoreVal = document.getElementById("detail-score-val");
    const barFill = document.getElementById("detail-bar-fill");
    if (scoreVal) {
      scoreVal.textContent = job.score;
      scoreVal.style.color = color;
    }
    if (barFill) {
      barFill.style.background = color;
      barFill.style.width = `${job.score}%`;
    }

    // Sub-scores Breakdown
    const domainVal = document.getElementById("subscore-domain-val");
    const domainFill = document.getElementById("subscore-domain-fill");
    const skillsVal = document.getElementById("subscore-skills-val");
    const skillsFill = document.getElementById("subscore-skills-fill");
    const seniorityVal = document.getElementById("subscore-seniority-val");
    const seniorityFill = document.getElementById("subscore-seniority-fill");
    const locationVal = document.getElementById("subscore-location-val");
    const locationFill = document.getElementById("subscore-location-fill");

    if (domainVal && domainFill) {
      domainVal.textContent = `${job.domainScore}%`;
      domainFill.style.width = `${job.domainScore}%`;
      domainFill.style.background = scoreColor(job.domainScore);
    }
    if (skillsVal && skillsFill) {
      skillsVal.textContent = `${job.skillsScore}%`;
      skillsFill.style.width = `${job.skillsScore}%`;
      skillsFill.style.background = scoreColor(job.skillsScore);
    }
    if (seniorityVal && seniorityFill) {
      seniorityVal.textContent = `${job.seniorityScore}%`;
      seniorityFill.style.width = `${job.seniorityScore}%`;
      seniorityFill.style.background = scoreColor(job.seniorityScore);
    }
    if (locationVal && locationFill) {
      locationVal.textContent = `${job.locationScore}%`;
      locationFill.style.width = `${job.locationScore}%`;
      locationFill.style.background = scoreColor(job.locationScore);
    }

    // Reasons
    const reasonsList = document.getElementById("detail-reasons-list");
    if (reasonsList) {
      reasonsList.innerHTML = job.reasons.map(r => `
        <div class="reason-item">
          <span class="reason-plus mono">+</span>
          <span class="reason-text">${escapeHtml(r)}</span>
        </div>
      `).join("");
    }

    // Strengths (O que você domina)
    const strengthsSection = document.getElementById("detail-strengths-section");
    const strengthsList = document.getElementById("detail-strengths-list");
    if (strengthsSection && strengthsList) {
      if (job.strengths && job.strengths.length > 0) {
        strengthsSection.hidden = false;
        strengthsList.innerHTML = job.strengths.map(s => `
          <div class="strength-item">
            <span class="strength-icon mono">✓</span>
            <span>${escapeHtml(s)}</span>
          </div>
        `).join("");
      } else {
        strengthsSection.hidden = true;
      }
    }

    // Gaps (Pontos de atenção)
    const gapsSection = document.getElementById("detail-gaps-section");
    const gapsList = document.getElementById("detail-gaps-list");
    if (gapsSection && gapsList) {
      const items = job.gaps && job.gaps.length > 0 ? job.gaps : (job.gap ? [job.gap] : []);
      if (items.length > 0) {
        gapsSection.hidden = false;
        gapsList.innerHTML = items.map(g => `
          <div class="gap-item">
            <span class="gap-icon mono">!</span>
            <span>${escapeHtml(g)}</span>
          </div>
        `).join("");
      } else {
        gapsSection.hidden = true;
      }
    }

    // Gap antigo
    const gapBox = document.getElementById("detail-gap-box");
    const gapText = document.getElementById("detail-gap-text");
    if (gapBox && gapText) {
      if (job.gap && (!job.gaps || job.gaps.length === 0)) {
        gapBox.hidden = false;
        gapText.textContent = job.gap;
      } else {
        gapBox.hidden = true;
      }
    }

    // Stack
    const stackWrap = document.getElementById("detail-stack-pills");
    if (stackWrap) {
      stackWrap.innerHTML = job.stack.map(s => `<span class="stack-pill mono">${escapeHtml(s)}</span>`).join("");
    }

    // Full Description
    const descContent = document.getElementById("detail-full-description");
    if (descContent) {
      descContent.textContent = job.description;
    }

    // Action buttons inside modal
    const btnApply = document.getElementById("detail-btn-apply");
    const btnLink = document.getElementById("detail-btn-link");
    const btnHide = document.getElementById("detail-btn-hide");

    if (btnApply) {
      if (job.status === "enviada") {
        btnApply.textContent = "Candidatura Registrada ✓";
        btnApply.className = "btn-primary-apply mono is-applied";
        btnApply.title = "Clique para desmarcar e retornar a vaga para Novas";
      } else {
        btnApply.textContent = "✓ Já me candidatei";
        btnApply.className = "btn-primary-apply mono";
        btnApply.title = "Marcar como candidatura enviada e ocultar da lista de triagem";
      }
      btnApply.onclick = () => this.toggleApplied(job.id);
    }
    if (btnLink) {
      btnLink.href = job.link;
    }
    if (btnHide) {
      btnHide.textContent = job.status === "oculta" ? "Reativar em Novas" : "Mover para Não Compatível";
      btnHide.onclick = () => {
        this.toggleHide(job.id);
        this.closeDetail();
      };
    }

    backdrop.hidden = false;
    drawer.hidden = false;
  }

  closeDetail() {
    this.openId = null;
    const drawer = document.getElementById("detail-drawer");
    const backdrop = document.getElementById("detail-backdrop");
    if (drawer) drawer.hidden = true;
    if (backdrop) backdrop.hidden = true;
  }

  // ── Configuration Drawer ────────────────────────────────────────────────────
  async openConfig() {
    const drawer = document.getElementById("config-drawer");
    const backdrop = document.getElementById("config-backdrop");
    if (!drawer || !backdrop) return;

    backdrop.hidden = false;
    drawer.hidden = false;

    // Load Resume
    try {
      const res = await fetch("/api/resume");
      if (res.ok) {
        const data = await res.json();
        const resumeArea = document.getElementById("cfg-resume-text");
        if (resumeArea) resumeArea.value = data.resume || "";
      }
    } catch (e) {
      console.warn("Erro ao carregar currículo:", e);
    }

    // Load Queries & Exclusions
    try {
      const res = await fetch("/api/config");
      if (res.ok) {
        const data = await res.json();
        const gupyArea = document.getElementById("cfg-gupy-queries");
        const linkedinArea = document.getElementById("cfg-linkedin-queries");
        const remotarArea = document.getElementById("cfg-remotar-queries");
        const programathorArea = document.getElementById("cfg-programathor-queries");
        const exclusionsArea = document.getElementById("cfg-exclusions-text");
        const maxAge = document.getElementById("cfg-max-age");
        const minScore = document.getElementById("cfg-min-score");
        const filterDate = document.getElementById("cfg-filter-gupy-date");
        const unknownLocation = document.getElementById("cfg-allow-unknown-location");

        if (gupyArea && data.gupy_queries) gupyArea.value = data.gupy_queries.join("\n");
        if (linkedinArea && data.linkedin_queries) linkedinArea.value = data.linkedin_queries.join("\n");
        if (remotarArea && data.remotar_queries) remotarArea.value = data.remotar_queries.join("\n");
        if (programathorArea && data.programathor_queries) programathorArea.value = data.programathor_queries.join("\n");
        if (exclusionsArea && data.excluded_keywords) exclusionsArea.value = data.excluded_keywords.join("\n");
        if (maxAge) maxAge.value = data.policy?.max_job_age_days ?? 7;
        if (minScore) minScore.value = data.policy?.min_match_score ?? 50;
        if (filterDate) filterDate.checked = Boolean(data.policy?.filter_gupy_by_date);
        if (unknownLocation) unknownLocation.checked = data.policy?.allow_unknown_location !== false;
      }
    } catch (e) {
      console.warn("Erro ao carregar configurações:", e);
    }
  }

  closeConfig() {
    const drawer = document.getElementById("config-drawer");
    const backdrop = document.getElementById("config-backdrop");
    if (drawer) drawer.hidden = true;
    if (backdrop) backdrop.hidden = true;
  }

  async saveResume() {
    const resumeArea = document.getElementById("cfg-resume-text");
    if (!resumeArea) return;
    const content = resumeArea.value.trim();

    try {
      const res = await fetch("/api/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume: content })
      });
      if (!res.ok) throw new Error("Erro ao salvar");
      this.showToast("Currículo atualizado com sucesso!");
    } catch (e) {
      this.showToast("Falha ao salvar currículo.");
    }
  }

  async saveQueries() {
    const gupyArea = document.getElementById("cfg-gupy-queries");
    const linkedinArea = document.getElementById("cfg-linkedin-queries");
    const remotarArea = document.getElementById("cfg-remotar-queries");
    const programathorArea = document.getElementById("cfg-programathor-queries");

    const gupy = (gupyArea ? gupyArea.value : "").split("\n").map(s => s.trim()).filter(Boolean);
    const linkedin = (linkedinArea ? linkedinArea.value : "").split("\n").map(s => s.trim()).filter(Boolean);
    const remotar = (remotarArea ? remotarArea.value : "").split("\n").map(s => s.trim()).filter(Boolean);
    const programathor = (programathorArea ? programathorArea.value : "").split("\n").map(s => s.trim()).filter(Boolean);

    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gupy_queries: gupy,
          linkedin_queries: linkedin,
          remotar_queries: remotar,
          programathor_queries: programathor
        })
      });
      if (!res.ok) throw new Error("Erro ao salvar");
      this.showToast("Termos de busca atualizados!");
    } catch (e) {
      this.showToast("Falha ao salvar termos de busca.");
    }
  }

  async saveExclusions() {
    const exclusionsArea = document.getElementById("cfg-exclusions-text");
    const excluded = (exclusionsArea ? exclusionsArea.value : "").split("\n").map(s => s.trim()).filter(Boolean);

    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          excluded_keywords: excluded
        })
      });
      if (!res.ok) throw new Error("Erro ao salvar");
      this.showToast("Termos proibidos atualizados com sucesso!");
    } catch (e) {
      this.showToast("Falha ao salvar termos proibidos.");
    }
  }

  async savePolicy() {
    const maxAge = Number(document.getElementById("cfg-max-age")?.value);
    const minScore = Number(document.getElementById("cfg-min-score")?.value);
    const filterDate = Boolean(document.getElementById("cfg-filter-gupy-date")?.checked);
    const unknownLocation = Boolean(document.getElementById("cfg-allow-unknown-location")?.checked);
    if (!Number.isInteger(maxAge) || maxAge < 0 || maxAge > 3650 || !Number.isInteger(minScore) || minScore < 0 || minScore > 100) {
      this.showToast("Idade e score devem estar dentro dos limites permitidos.");
      return;
    }
    try {
      const res = await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ policy: { max_job_age_days: maxAge, min_match_score: minScore, filter_gupy_by_date: filterDate, allow_unknown_location: unknownLocation } }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Erro na API");
      this.showToast(data.message || "Política atualizada!");
    } catch (error) {
      this.showToast(error.message || "Falha ao salvar política.");
    }
  }

  async clearAllNew() {
    if (!confirm("Tem certeza que deseja ocultar todas as vagas com status 'Nova'?")) return;
    try {
      const res = await fetch("/api/jobs/clear-new", { method: "POST" });
      if (!res.ok) throw new Error("Erro na API");
      const data = await res.json();
      this.showToast(data.message || "Vagas ocultadas!");
      this.loadJobs();
    } catch (e) {
      this.showToast("Erro ao ocultar vagas.");
    }
  }

  async purgeDatabase() {
    if (!confirm("Isso apagará permanentemente vagas novas e não compatíveis. As vagas enviadas serão preservadas e as removidas poderão reaparecer nas próximas buscas. Continuar?")) return;
    try {
      const res = await fetch("/api/jobs/purge", { method: "POST" });
      if (!res.ok) throw new Error("Erro na API");
      const data = await res.json();
      this.showToast(data.message || "Limpeza concluída!");
      this.loadJobs();
    } catch (e) {
      this.showToast("Erro ao executar limpeza.");
    }
  }

  // ── Event Bindings ──────────────────────────────────────────────────────────
  bindEvents() {
    // Theme Toggle
    const themeBtn = document.getElementById("btn-theme");
    if (themeBtn) themeBtn.addEventListener("click", () => this.toggleTheme());

    // View Switching (Painel vs Kanban)
    const btnPainel = document.getElementById("btn-view-painel");
    const btnKanban = document.getElementById("btn-view-kanban");
    const viewPainel = document.getElementById("view-painel");
    const viewKanban = document.getElementById("view-kanban");

    if (btnPainel && btnKanban) {
      btnPainel.addEventListener("click", () => {
        this.view = "painel";
        btnPainel.classList.add("is-active");
        btnKanban.classList.remove("is-active");
        if (viewPainel) viewPainel.hidden = false;
        if (viewKanban) viewKanban.hidden = true;
        this.render();
      });

      btnKanban.addEventListener("click", () => {
        this.view = "kanban";
        btnKanban.classList.add("is-active");
        btnPainel.classList.remove("is-active");
        if (viewPainel) viewPainel.hidden = true;
        if (viewKanban) viewKanban.hidden = false;
        this.render();
      });
    }

    // Tabs Navigation in Painel
    const tabsNav = document.getElementById("tabs-nav");
    if (tabsNav) {
      tabsNav.addEventListener("click", (e) => {
        const btn = e.target.closest(".tab-btn");
        if (!btn) return;
        tabsNav.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        this.tab = btn.dataset.tab;
        this.renderTable();
      });
    }

    // Pillars Filter
    const pillarsList = document.getElementById("pillars-list");
    if (pillarsList) {
      pillarsList.addEventListener("click", (e) => {
        const btn = e.target.closest(".pillar-btn");
        if (!btn) return;
        pillarsList.querySelectorAll(".pillar-btn").forEach(b => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        this.pillar = btn.dataset.pillar;
        this.render();
        this.updateStats();
      });
    }

    // Workplace Filter
    const workplaceList = document.getElementById("workplace-list");
    if (workplaceList) {
      workplaceList.addEventListener("click", (e) => {
        const btn = e.target.closest(".workplace-btn");
        if (!btn) return;
        workplaceList.querySelectorAll(".workplace-btn").forEach(b => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        this.workplace = btn.dataset.workplace;
        this.render();
        this.updateStats();
      });
    }

    // Search Input
    const searchInput = document.getElementById("search-input");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        this.query = e.target.value;
        this.render();
        this.updateStats();
      });
    }

    // Scan Trigger
    const btnScan = document.getElementById("btn-scan");
    if (btnScan) btnScan.addEventListener("click", () => this.triggerScan());

    // Undo Toast
    const btnUndo = document.getElementById("btn-toast-undo");
    if (btnUndo) btnUndo.addEventListener("click", () => this.undo());

    // Close Detail
    const btnCloseDetail = document.getElementById("btn-close-detail");
    const detailBackdrop = document.getElementById("detail-backdrop");
    if (btnCloseDetail) btnCloseDetail.addEventListener("click", () => this.closeDetail());
    if (detailBackdrop) detailBackdrop.addEventListener("click", () => this.closeDetail());

    // Config Drawer
    const btnConfig = document.getElementById("btn-config");
    const btnCloseConfig = document.getElementById("btn-close-config");
    const configBackdrop = document.getElementById("config-backdrop");
    if (btnConfig) btnConfig.addEventListener("click", () => this.openConfig());
    if (btnCloseConfig) btnCloseConfig.addEventListener("click", () => this.closeConfig());
    if (configBackdrop) configBackdrop.addEventListener("click", () => this.closeConfig());

    // Config Drawer Tabs
    document.querySelectorAll(".config-tab-btn").forEach(tabBtn => {
      tabBtn.addEventListener("click", (e) => {
        document.querySelectorAll(".config-tab-btn").forEach(b => b.classList.remove("is-active"));
        tabBtn.classList.add("is-active");
        const target = tabBtn.dataset.cfgTab;
        document.querySelectorAll(".config-pane").forEach(pane => pane.hidden = true);
        const targetPane = document.getElementById(`cfg-pane-${target}`);
        if (targetPane) targetPane.hidden = false;
      });
    });

    // Export CSV
    const btnExportCsv = document.getElementById("btn-export-csv");
    if (btnExportCsv) {
      btnExportCsv.addEventListener("click", () => {
        window.open(`/api/jobs/export?status=${this.tab || "enviada"}`, "_blank");
      });
    }

    // Config Actions
    const btnSaveResume = document.getElementById("btn-save-resume");
    const btnSaveQueries = document.getElementById("btn-save-queries");
    const btnSaveExclusions = document.getElementById("btn-save-exclusions");
    const btnClearNew = document.getElementById("btn-clear-new");
    const btnPurgeDb = document.getElementById("btn-purge-db");
    const btnSavePolicy = document.getElementById("btn-save-policy");

    if (btnSaveResume) btnSaveResume.addEventListener("click", () => this.saveResume());
    if (btnSaveQueries) btnSaveQueries.addEventListener("click", () => this.saveQueries());
    if (btnSaveExclusions) btnSaveExclusions.addEventListener("click", () => this.saveExclusions());
    if (btnClearNew) btnClearNew.addEventListener("click", () => this.clearAllNew());
    if (btnPurgeDb) btnPurgeDb.addEventListener("click", () => this.purgeDatabase());
    if (btnSavePolicy) btnSavePolicy.addEventListener("click", () => this.savePolicy());
    // Fechamento dos painéis pelo teclado
    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

      if (e.key === "Escape") {
        this.closeDetail();
        this.closeConfig();
      }
    });
  }

}

// Instantiate global app instance
const radarApp = new RadarApp();
window.radarApp = radarApp;
