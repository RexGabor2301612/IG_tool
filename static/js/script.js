document.addEventListener("DOMContentLoaded", function () {
    const searchForm = document.getElementById("searchForm");
    const instagramLink = document.getElementById("instagramLink");
    const scrollRounds = document.getElementById("scrollRounds");
    const startDate = document.getElementById("startDate");
    const endDate = document.getElementById("endDate");
    const latestMode = document.getElementById("latestMode");
    const outputFile = document.getElementById("outputFile");
    const overwrite = document.getElementById("overwrite");
    const formMessage = document.getElementById("formMessage");
    const overwritePanel = document.getElementById("overwritePanel");
    const overwriteText = document.getElementById("overwriteText");
    const confirmOverwriteBtn = document.getElementById("confirmOverwriteBtn");
    const newFilenameBtn = document.getElementById("newFilenameBtn");
    const confirmationModal = document.getElementById("confirmationModal");
    const confirmStartBtn = document.getElementById("confirmStartBtn");
    const editConfigBtn = document.getElementById("editConfigBtn");
    const refreshStatusBtn = document.getElementById("refreshStatusBtn");
    const clearLogsBtn = document.getElementById("clearLogsBtn");
    const expandLogsBtn = document.getElementById("expandLogsBtn");
    const logsModal = document.getElementById("logsModal");
    const closeLogsModalBtn = document.getElementById("closeLogsModalBtn");
    const downloadBtn = document.getElementById("downloadBtn");
    const cancelBtn = document.getElementById("cancelBtn");
    const runStartBtn = document.getElementById("runStartBtn");
    const showLogsBtn = document.getElementById("showLogsBtn");
    const centerShowLogsBtn = document.getElementById("centerShowLogsBtn");
    const aboutUsBtn = document.getElementById("aboutUsBtn");
    const aboutModal = document.getElementById("aboutModal");
    const closeAboutModalBtn = document.getElementById("closeAboutModalBtn");
    const selectedLogDetails = document.getElementById("selectedLogDetails");

    let confirmedPayload = null;
    let lastValidatedConfig = null;
    let pollTimer = null;
    let latestLogs = [];

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function showMessage(message, type = "error") {
        formMessage.textContent = message || "";
        formMessage.className = `form-message ${type}`;
    }

    function setModalVisible(modal, visible) {
        modal.classList.toggle("hidden", !visible);
        if (modal === confirmationModal) {
            document.body.classList.toggle("review-confirmation-open", visible);
        }
        if (modal === aboutModal) {
            document.body.classList.toggle("about-modal-open", visible);
        }
    }

    function openLogsDrawer() {
        renderLogs(latestLogs);
        setModalVisible(logsModal, true);
    }

    function closeLogsDrawer() {
        setModalVisible(logsModal, false);
    }

    function buildPayload() {
        return {
            instagramLink: instagramLink.value.trim(),
            scrollRounds: scrollRounds.value.trim(),
            startDate: startDate.value,
            endDate: endDate.value,
            latestMode: latestMode.checked,
            outputFile: outputFile.value.trim(),
            overwrite: overwrite.value === "true",
        };
    }

    async function fetchJson(url, options = {}) {
        const response = await fetch(url, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        const data = await response.json();
        if (!response.ok) {
            const error = new Error((data.errors || ["Request failed"]).join("\n"));
            error.payload = data;
            throw error;
        }
        return data;
    }

    function setProgress(fillId, textId, value) {
        const safeValue = Math.max(0, Math.min(100, Number(value) || 0));
        document.getElementById(fillId).style.width = `${safeValue}%`;
        document.getElementById(textId).textContent = `${safeValue}%`;
    }

    function renderLogRows(container, logs) {
        if (!logs || logs.length === 0) {
            container.innerHTML = `
                <button class="logs-grid log-row log-item" type="button" data-log-index="-1">
                    <div>-</div>
                    <div><span class="level-badge info">INFO</span></div>
                    <div>No logs yet</div>
                    <div>Submit a setup and start scraping.</div>
                </button>
            `;
            return;
        }

        container.innerHTML = logs.map((log, index) => {
            const level = String(log.level || "INFO").toLowerCase();
            return `
                <button class="logs-grid log-row log-item" type="button" data-log-index="${index}">
                    <div>${escapeHtml(log.time)}</div>
                    <div><span class="level-badge ${escapeHtml(level)}">${escapeHtml(log.level)}</span></div>
                    <div>${escapeHtml(log.action)}</div>
                    <div>${escapeHtml(log.details)}</div>
                </button>
            `;
        }).join("");
    }

    function showSelectedLog(index) {
        const log = latestLogs[Number(index)];
        if (!log || !selectedLogDetails) return;

        selectedLogDetails.innerHTML = `
            <strong>${escapeHtml(log.level)} • ${escapeHtml(log.time)}</strong>
            <span>${escapeHtml(log.action)}</span>
            <p>${escapeHtml(log.details || "No extra details.")}</p>
        `;

        document.querySelectorAll(".log-item.selected").forEach((row) => row.classList.remove("selected"));
        document.querySelectorAll(`.log-item[data-log-index="${index}"]`).forEach((row) => row.classList.add("selected"));
    }

    function renderLogs(logs) {
        latestLogs = logs || [];
        renderLogRows(document.getElementById("logsBody"), latestLogs);
        renderLogRows(document.getElementById("modalLogsBody"), latestLogs);
    }

    function setButtonStates(data) {
        const status = data.status || "idle";
        const running = status === "running" || status === "stopping";

        confirmStartBtn.disabled = running;
        runStartBtn.disabled = running;
        cancelBtn.disabled = !running;
        downloadBtn.disabled = !data.canDownload;
        searchForm.querySelector("button[type='submit']").disabled = running;
    }

    function renderStatus(data) {
        const status = data.status || "idle";
        const config = data.config || {};

        document.getElementById("statPostsFound").textContent = data.postsFound ?? 0;
        document.getElementById("statProgress").textContent = `${data.progress ?? 0}%`;
        document.getElementById("statSuccessRate").textContent = `${data.successRate ?? 0}%`;
        document.getElementById("statErrors").textContent = data.errors ?? 0;

        document.getElementById("statusTitle").textContent = data.activeTask || "Waiting for input";
        document.getElementById("statusBadge").textContent = status;
        document.getElementById("statusBadge").dataset.status = status;
        document.getElementById("scrollRoundText").textContent = `Round ${data.currentScrollRound ?? 0} / ${data.totalScrollRounds ?? 0}`;
        document.getElementById("currentPostText").textContent = data.currentPost || "None";

        setProgress("progressFill", "progressText", data.progress ?? 0);
        setProgress("successFill", "successText", data.successRate ?? 0);
        setProgress("healthFill", "healthText", data.health ?? 100);

        document.getElementById("outputFileText").textContent = data.outputFile || (lastValidatedConfig?.outputFile || "Not selected");
        document.getElementById("tagStartDate").textContent = `Date: ${config.dateCoverage || lastValidatedConfig?.dateCoverage || "-"}`;
        document.getElementById("tagMaxScroll").textContent = `Max Scroll: ${config.scrollRounds || lastValidatedConfig?.scrollRounds || "-"}`;
        document.getElementById("tagRound").textContent = `Round: ${data.currentScrollRound ?? 0}`;

        renderLogs(data.logs || []);
        setButtonStates(data);

        if (!["running", "stopping"].includes(status) && pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    async function refreshStatus() {
        try {
            const data = await fetchJson("/api/status");
            renderStatus(data);
        } catch (error) {
            showMessage(error.message);
        }
    }

    function fillConfirmation(config) {
        document.getElementById("summaryLink").textContent = config.instagramLink;
        document.getElementById("summaryScroll").textContent = config.scrollRounds;
        document.getElementById("summaryCoverage").textContent = config.dateCoverage;
        document.getElementById("summaryFile").textContent = config.outputFile;
        document.getElementById("outputFileText").textContent = config.outputFile;
        lastValidatedConfig = config;
        setModalVisible(confirmationModal, true);
        try {
            confirmStartBtn.focus({ preventScroll: true });
        } catch (error) {
            confirmStartBtn.focus();
        }
        showMessage("Setup validated. Confirm to start scraping.", "success");
    }

    async function validateSetup() {
        confirmedPayload = null;
        overwritePanel.classList.add("hidden");
        setModalVisible(confirmationModal, false);
        showMessage("");

        const payload = buildPayload();
        try {
            const data = await fetchJson("/api/validate", {
                method: "POST",
                body: JSON.stringify(payload),
            });
            confirmedPayload = payload;
            fillConfirmation(data.config);
        } catch (error) {
            if (error.payload?.overwriteRequired) {
                overwriteText.textContent = error.message;
                overwritePanel.classList.remove("hidden");
                showMessage("The output file already exists. Choose overwrite or enter a new name.", "warn");
                return;
            }

            showMessage(error.message);
        }
    }

    async function startScrape() {
        if (!confirmedPayload) {
            await validateSetup();
            if (!confirmedPayload) return;
        }

        try {
            const data = await fetchJson("/api/start", {
                method: "POST",
                body: JSON.stringify(confirmedPayload),
            });
            setModalVisible(confirmationModal, false);
            showMessage("Scraping started. Log in in the opened browser if Instagram asks.", "success");
            renderStatus(data.status);

            if (pollTimer) clearInterval(pollTimer);
            pollTimer = setInterval(refreshStatus, 1500);
        } catch (error) {
            showMessage(error.message);
        }
    }

    async function cancelScrape() {
        try {
            const data = await fetchJson("/api/cancel", { method: "POST" });
            showMessage("Cancellation requested. The scraper will stop at the next safe checkpoint.", "warn");
            renderStatus(data.status);
            if (!pollTimer) pollTimer = setInterval(refreshStatus, 1500);
        } catch (error) {
            showMessage(error.message, "warn");
            if (error.payload?.status) renderStatus(error.payload.status);
        }
    }

    searchForm.addEventListener("submit", function (event) {
        event.preventDefault();
        validateSetup();
    });

    confirmStartBtn.addEventListener("click", startScrape);
    runStartBtn.addEventListener("click", startScrape);
    editConfigBtn.addEventListener("click", function () {
        setModalVisible(confirmationModal, false);
        confirmedPayload = null;
        instagramLink.focus();
        showMessage("Edit the inputs, then review setup again.", "success");
    });

    confirmOverwriteBtn.addEventListener("click", function () {
        overwrite.value = "true";
        overwritePanel.classList.add("hidden");
        validateSetup();
    });

    newFilenameBtn.addEventListener("click", function () {
        overwrite.value = "false";
        overwritePanel.classList.add("hidden");
        outputFile.focus();
        showMessage("Enter a new Excel filename, then review setup again.", "warn");
    });

    latestMode.addEventListener("change", function () {
        endDate.disabled = latestMode.checked;
        if (latestMode.checked) endDate.value = "";
        confirmedPayload = null;
    });
    latestMode.dispatchEvent(new Event("change"));

    [instagramLink, scrollRounds, startDate, endDate, outputFile].forEach((input) => {
        input.addEventListener("input", function () {
            confirmedPayload = null;
            if (input === outputFile) overwrite.value = "false";
        });
    });

    refreshStatusBtn.addEventListener("click", refreshStatus);
    expandLogsBtn.addEventListener("click", openLogsDrawer);
    showLogsBtn.addEventListener("click", openLogsDrawer);
    centerShowLogsBtn.addEventListener("click", openLogsDrawer);
    closeLogsModalBtn.addEventListener("click", closeLogsDrawer);
    logsModal.addEventListener("click", function (event) {
        if (event.target === logsModal) closeLogsDrawer();
    });
    document.getElementById("modalLogsBody").addEventListener("click", function (event) {
        const row = event.target.closest(".log-item");
        if (row && row.dataset.logIndex !== "-1") {
            showSelectedLog(row.dataset.logIndex);
        }
    });
    document.getElementById("logsBody").addEventListener("click", function (event) {
        const row = event.target.closest(".log-item");
        if (row && row.dataset.logIndex !== "-1") {
            showSelectedLog(row.dataset.logIndex);
            openLogsDrawer();
        }
    });
    confirmationModal.addEventListener("click", function (event) {
        if (event.target === confirmationModal) setModalVisible(confirmationModal, false);
    });
    aboutUsBtn.addEventListener("click", function () {
        setModalVisible(aboutModal, true);
    });
    closeAboutModalBtn.addEventListener("click", function () {
        setModalVisible(aboutModal, false);
    });
    aboutModal.addEventListener("click", function (event) {
        if (event.target === aboutModal) setModalVisible(aboutModal, false);
    });

    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") return;
        if (!logsModal.classList.contains("hidden")) closeLogsDrawer();
        if (!aboutModal.classList.contains("hidden")) setModalVisible(aboutModal, false);
        if (!confirmationModal.classList.contains("hidden")) setModalVisible(confirmationModal, false);
    });

    clearLogsBtn.addEventListener("click", async function () {
        try {
            const data = await fetchJson("/api/clear-logs", { method: "POST" });
            renderStatus(data.status);
        } catch (error) {
            showMessage(error.message);
        }
    });

    downloadBtn.addEventListener("click", function () {
        if (!downloadBtn.disabled) {
            window.location.href = "/api/download";
        }
    });

    cancelBtn.addEventListener("click", cancelScrape);

    refreshStatus();
});
