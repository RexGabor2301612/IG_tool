document.addEventListener("DOMContentLoaded", function () {
    const searchForm = document.getElementById("searchForm");
    const facebookLink = document.getElementById("facebookLink");
    const scrollRounds = document.getElementById("scrollRounds");
    const collectionType = document.getElementById("collectionType");
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
    const modalLogsBody = document.getElementById("modalLogsBody");
    const logsBody = document.getElementById("logsBody");
    const selectedLogDetails = document.getElementById("selectedLogDetails");
    const downloadBtn = document.getElementById("downloadBtn");
    const cancelBtn = document.getElementById("cancelBtn");
    const runStartBtn = document.getElementById("runStartBtn");
    const goBtn = document.getElementById("goBtn");
    const focusBrowserBtn = document.getElementById("focusBrowserBtn");
    const showLogsBtn = document.getElementById("showLogsBtn");
    const centerShowLogsBtn = document.getElementById("centerShowLogsBtn");
    const aboutUsBtn = document.getElementById("aboutUsBtn");
    const aboutModal = document.getElementById("aboutModal");
    const closeAboutModalBtn = document.getElementById("closeAboutModalBtn");
    const browserModeDescription = document.getElementById("browserModeDescription");
    const browserModeBadge = document.getElementById("browserModeBadge");
    const browserModeText = document.getElementById("browserModeText");
    const browserSessionStatusText = document.getElementById("browserSessionStatusText");
    const browserSessionUrlText = document.getElementById("browserSessionUrlText");
    const goStatusText = document.getElementById("goStatusText");

    let confirmedPayload = null;
    let lastValidatedConfig = null;
    let latestLogs = [];
    let latestStatus = { status: "idle", config: {} };
    let dashboardSocket = null;
    let reconnectTimer = null;
    let socketShouldReconnect = true;

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function wait(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
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
        logsModal.classList.remove("hidden");
    }

    function closeLogsDrawer() {
        logsModal.classList.add("hidden");
    }

    function buildPayload() {
        const latestModeEnabled = Boolean(latestMode.checked);
        return {
            facebookLink: facebookLink.value.trim(),
            scrollRounds: scrollRounds.value.trim(),
            collectionType: collectionType.value,
            startDate: String(startDate.value || "").trim(),
            endDate: latestModeEnabled ? "" : String(endDate.value || "").trim(),
            latestMode: latestModeEnabled,
            outputFile: outputFile.value.trim(),
            overwrite: overwrite.value === "true",
        };
    }

    async function fetchJson(url, options = {}) {
        const { retries = 0, retryDelay = 900, ...fetchOptions } = options;

        for (let attempt = 0; attempt <= retries; attempt += 1) {
            try {
                const response = await fetch(url, {
                    headers: { "Content-Type": "application/json" },
                    ...fetchOptions,
                });
                const contentType = response.headers.get("content-type") || "";
                let data;

                if (contentType.includes("application/json")) {
                    data = await response.json();
                } else {
                    const text = await response.text();
                    data = {
                        ok: false,
                        errors: [response.ok ? "The server returned a non-JSON response." : `The server returned ${response.status}.`],
                        raw: text.slice(0, 300),
                    };
                }

                if (!response.ok) {
                    const transient = [502, 503, 504].includes(response.status);
                    if (transient && attempt < retries) {
                        await wait(retryDelay * (attempt + 1));
                        continue;
                    }
                    const error = new Error((data.errors || ["Request failed"]).join("\n"));
                    error.payload = data;
                    throw error;
                }

                return data;
            } catch (error) {
                if (attempt < retries && error instanceof TypeError) {
                    await wait(retryDelay * (attempt + 1));
                    continue;
                }
                throw error;
            }
        }

        throw new Error("Request failed after retrying.");
    }

    function setProgress(fillId, textId, value) {
        const safeValue = Math.max(0, Math.min(100, Number(value) || 0));
        document.getElementById(fillId).style.width = `${safeValue}%`;
        document.getElementById(textId).textContent = `${safeValue}%`;
    }

    function setSocketState(state, label) {
        browserModeBadge.dataset.state = state;
        browserModeBadge.textContent = label;
    }

    function renderLogRows(container, logs) {
        if (!logs || logs.length === 0) {
            container.innerHTML = `
                <button class="logs-grid log-row log-item" type="button" data-log-index="-1">
                    <div>-</div>
                    <div><span class="level-badge info">INFO</span></div>
                    <div>No logs yet</div>
                    <div>Review the setup and start a Facebook extraction job.</div>
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

    function renderLogs(logs) {
        latestLogs = Array.isArray(logs) ? logs.slice(0, 250) : [];
        renderLogRows(logsBody, latestLogs);
        renderLogRows(modalLogsBody, latestLogs);
    }

    function showSelectedLog(index) {
        const log = latestLogs[Number(index)];
        if (!log || !selectedLogDetails) return;

        selectedLogDetails.innerHTML = `
            <strong>${escapeHtml(log.level)} - ${escapeHtml(log.time)}</strong>
            <span>${escapeHtml(log.action)}</span>
            <p>${escapeHtml(log.details || "No extra details.")}</p>
        `;

        document.querySelectorAll(".log-item.selected").forEach((row) => row.classList.remove("selected"));
        document.querySelectorAll(`.log-item[data-log-index="${index}"]`).forEach((row) => row.classList.add("selected"));
    }

    function sameLog(a, b) {
        if (!a || !b) return false;
        return a.time === b.time && a.level === b.level && a.action === b.action && a.details === b.details;
    }

    function appendLiveLog(log) {
        if (!log) return;
        if (!sameLog(log, latestLogs[0])) {
            latestLogs.unshift(log);
            latestLogs = latestLogs.slice(0, 250);
            renderLogRows(logsBody, latestLogs);
            renderLogRows(modalLogsBody, latestLogs);
        }
    }

    function formatDisplayUrl(value, maxLength = 88) {
        const raw = String(value || "").trim();
        if (!raw) return "-";
        const compact = raw.replace(/^https?:\/\//i, "");
        if (compact.length <= maxLength) return compact;
        return `${compact.slice(0, maxLength - 1)}…`;
    }

    function applyBrowserSessionState(data) {
        const modeLabel = data.browserModeLabel || "Browser Session";
        const modeNote = data.browserModeNote || "Run / Start opens Chromium for Facebook login when needed.";
        const activeTask = data.activeTask || "Waiting for input";
        const currentUrl = data.browserUrl || data.currentPost || data.config?.facebookLink || "-";

        browserModeText.textContent = modeLabel;
        browserModeDescription.textContent = modeNote;
        browserSessionUrlText.textContent = formatDisplayUrl(currentUrl, 86);
        browserSessionUrlText.title = currentUrl || "-";

        if (data.status === "loading_session") {
            browserSessionStatusText.textContent = "Checking saved Facebook session and page access.";
        } else if (data.status === "preparing") {
            browserSessionStatusText.textContent = "Browser session created. Opening Facebook target.";
        } else if (data.status === "waiting_verification" || data.verificationRequired) {
            browserSessionStatusText.textContent = data.localBrowserWindow
                ? "Verification required. Complete it manually in Chromium and do not refresh or close the browser."
                : "Facebook verification required, but this environment has no local browser window.";
        } else if (data.status === "waiting_login") {
            browserSessionStatusText.textContent = data.localBrowserWindow
                ? "Browser opened. Complete Facebook login in Chromium."
                : "Facebook login is required, but this environment has no local browser window.";
        } else if (data.status === "ready") {
            browserSessionStatusText.textContent = "Facebook page ready. Click GO / Start Extraction.";
        } else if (data.status === "running") {
            browserSessionStatusText.textContent = activeTask;
        } else if (data.status === "completed") {
            browserSessionStatusText.textContent = "Extraction completed. Excel is ready.";
        } else if (data.status === "failed") {
            browserSessionStatusText.textContent = "Extraction failed. Check activity logs.";
        } else if (data.status === "cancelled") {
            browserSessionStatusText.textContent = "Extraction cancelled.";
        } else {
            browserSessionStatusText.textContent = "No browser session yet.";
        }

        if (data.canGo) {
            goStatusText.textContent = "Ready. Click GO / Start Extraction.";
        } else if (data.goRequested) {
            goStatusText.textContent = "GO signal sent. Starting extraction...";
        } else if (data.status === "loading_session") {
            goStatusText.textContent = "Checking saved session.";
        } else if (data.status === "preparing") {
            goStatusText.textContent = "Preparing browser session.";
        } else if (data.status === "waiting_verification" || data.verificationRequired) {
            goStatusText.textContent = "Complete Facebook verification first. Do not refresh or close Chromium.";
        } else if (data.status === "waiting_login") {
            goStatusText.textContent = "Waiting for Facebook login.";
        } else if (data.status === "ready") {
            goStatusText.textContent = "Facebook page ready. Waiting for GO signal.";
        } else if (data.status === "running") {
            goStatusText.textContent = "Extraction in progress.";
        } else if (data.status === "completed") {
            goStatusText.textContent = "Extraction completed.";
        } else {
            goStatusText.textContent = "Waiting for Run / Start.";
        }
    }

    function setButtonStates(data) {
        const status = data.status || "idle";
        const activeStatuses = ["preparing", "loading_session", "running", "waiting_login", "waiting_verification", "ready"].includes(status);

        confirmStartBtn.disabled = activeStatuses;
        runStartBtn.disabled = activeStatuses;
        cancelBtn.disabled = !activeStatuses;
        goBtn.disabled = !data.canGo;
        focusBrowserBtn.disabled = !activeStatuses || !data.localBrowserWindow;
        downloadBtn.disabled = !data.canDownload;
        searchForm.querySelector("button[type='submit']").disabled = activeStatuses;
    }

    function renderStatus(data) {
        latestStatus = data || { status: "idle", config: {} };
        const config = latestStatus.config || {};
        const currentRound = latestStatus.currentScrollRound ?? 0;
        const totalRounds = latestStatus.totalScrollRounds ?? 0;

        document.getElementById("statusTitle").textContent = latestStatus.activeTask || "Waiting for input";
        document.getElementById("statusBadge").textContent = latestStatus.status || "idle";
        document.getElementById("statusBadge").dataset.status = latestStatus.status || "idle";
        document.getElementById("scrollRoundText").textContent = `Round ${currentRound} / ${totalRounds}`;
        const currentItemText = document.getElementById("currentPostText");
        currentItemText.textContent = formatDisplayUrl(latestStatus.currentPost || "None", 110);
        currentItemText.title = latestStatus.currentPost || "None";
        document.getElementById("outputFileText").textContent = latestStatus.outputFile || (lastValidatedConfig?.outputFile || "Not selected");

        document.getElementById("statPostsFound").textContent = latestStatus.postsFound ?? 0;
        document.getElementById("statProgress").textContent = `${latestStatus.progress ?? 0}%`;
        document.getElementById("statSuccessRate").textContent = `${latestStatus.successRate ?? 0}%`;
        document.getElementById("statErrors").textContent = latestStatus.errors ?? 0;

        setProgress("progressFill", "progressText", latestStatus.progress ?? 0);
        setProgress("successFill", "successText", latestStatus.successRate ?? 0);
        setProgress("healthFill", "healthText", latestStatus.health ?? 100);

        document.getElementById("tagStartDate").textContent = `Date: ${config.dateCoverage || lastValidatedConfig?.dateCoverage || "-"}`;
        document.getElementById("tagMaxScroll").textContent = `Depth: ${config.scrollRounds || lastValidatedConfig?.scrollRounds || "-"}`;
        document.getElementById("tagRound").textContent = `Round: ${currentRound}`;

        applyBrowserSessionState(latestStatus);
        setButtonStates(latestStatus);

        if (Array.isArray(latestStatus.logs)) {
            renderLogs(latestStatus.logs);
        }
    }

    function socketUrl() {
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        return `${protocol}://${window.location.host}/ws/dashboard`;
    }

    function isSocketOpen() {
        return dashboardSocket && dashboardSocket.readyState === WebSocket.OPEN;
    }

    function sendSocket(payload) {
        if (!isSocketOpen()) return false;
        dashboardSocket.send(JSON.stringify(payload));
        return true;
    }

    function scheduleReconnect() {
        if (!socketShouldReconnect || reconnectTimer) return;
        reconnectTimer = window.setTimeout(function () {
            reconnectTimer = null;
            connectSocket(true);
        }, 1500);
    }

    function handleSocketMessage(message) {
        if (!message || typeof message !== "object") return;
        if (message.type === "snapshot") {
            renderStatus(message.data || {});
            return;
        }
        if (message.type === "log") {
            appendLiveLog(message.data);
            return;
        }
        if (message.type === "login_required" && message.data?.message) {
            showMessage(message.data.message, "warn");
            return;
        }
        if (message.type === "verification_required" && message.data?.message) {
            showMessage(message.data.message, "warn");
            return;
        }
        if (message.type === "job_completed") {
            showMessage("Facebook extraction completed. The Excel file is ready to download.", "success");
        }
    }

    function connectSocket(force = false) {
        if (!force && dashboardSocket && (dashboardSocket.readyState === WebSocket.OPEN || dashboardSocket.readyState === WebSocket.CONNECTING)) {
            return;
        }

        if (force && dashboardSocket) {
            try {
                dashboardSocket.close();
            } catch (error) {
                // no-op
            }
        }

        setSocketState("connecting", "Connecting...");
        dashboardSocket = new WebSocket(socketUrl());

        dashboardSocket.addEventListener("open", function () {
            setSocketState("connected", "Live");
            sendSocket({ type: "request_snapshot" });
        });

        dashboardSocket.addEventListener("message", function (event) {
            try {
                handleSocketMessage(JSON.parse(event.data));
            } catch (error) {
                console.warn("Failed to parse dashboard socket message.", error);
            }
        });

        dashboardSocket.addEventListener("close", function () {
            setSocketState("disconnected", "Disconnected");
            if (socketShouldReconnect) scheduleReconnect();
        });

        dashboardSocket.addEventListener("error", function () {
            setSocketState("disconnected", "Disconnected");
        });
    }

    async function refreshStatus() {
        if (sendSocket({ type: "request_snapshot" })) {
            return;
        }

        try {
            const data = await fetchJson("/api/status", { retries: 2 });
            renderStatus(data);
        } catch (error) {
            showMessage(error.message);
        }
    }

    function fillConfirmation(config) {
        document.getElementById("summaryLink").textContent = config.facebookLink;
        document.getElementById("summaryScroll").textContent = config.scrollRounds;
        document.getElementById("summaryCoverage").textContent = config.dateCoverage;
        document.getElementById("summaryCollectionType").textContent = config.collectionType;
        document.getElementById("summaryFile").textContent = config.outputFile;
        document.getElementById("outputFileText").textContent = config.outputFile;
        lastValidatedConfig = config;
        setModalVisible(confirmationModal, true);
        try {
            confirmStartBtn.focus({ preventScroll: true });
        } catch (error) {
            confirmStartBtn.focus();
        }
        showMessage("Setup validated. Confirm to open the browser and prepare Facebook extraction.", "success");
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
                retries: 1,
            });
            confirmedPayload = payload;
            fillConfirmation(data.config);
        } catch (error) {
            if (error.payload?.overwriteRequired) {
                overwriteText.textContent = error.message;
                overwritePanel.classList.remove("hidden");
                showMessage("The output file already exists. Choose overwrite or enter a new filename.", "warn");
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

        connectSocket();
        try {
            const data = await fetchJson("/api/start", {
                method: "POST",
                body: JSON.stringify(confirmedPayload),
            });
            setModalVisible(confirmationModal, false);
            renderStatus(data.status);
            if (data.status?.localBrowserWindow) {
                showMessage("Browser session created. Log in to Facebook in the opened Chromium window. GO stays disabled until the page is ready.", "success");
            } else {
                showMessage("Browser session created. This environment has no local browser window, so login requires stored session state or backend login support.", "warn");
            }
            sendSocket({ type: "request_snapshot" });
        } catch (error) {
            showMessage(error.message);
        }
    }

    async function sendGoSignal() {
        try {
            const data = await fetchJson("/api/go", { method: "POST" });
            renderStatus(data.status);
            showMessage("GO signal received. Starting Facebook extraction now.", "success");
        } catch (error) {
            showMessage(error.message, "warn");
            if (error.payload?.status) renderStatus(error.payload.status);
        }
    }

    async function focusBrowser() {
        try {
            const data = await fetchJson("/api/focus-browser", { method: "POST" });
            renderStatus(data.status);
            showMessage("Browser focus requested.", "success");
        } catch (error) {
            showMessage(error.message, "warn");
            if (error.payload?.status) renderStatus(error.payload.status);
        }
    }

    async function cancelScrape() {
        try {
            const data = await fetchJson("/api/cancel", { method: "POST" });
            showMessage("Cancellation requested. The extractor will stop at the next safe checkpoint.", "warn");
            renderStatus(data.status);
        } catch (error) {
            showMessage(error.message, "warn");
            if (error.payload?.status) renderStatus(error.payload.status);
        }
    }

    async function clearLogs() {
        try {
            const data = await fetchJson("/api/clear-logs", { method: "POST" });
            renderStatus(data.status);
            showMessage("Activity logs cleared.", "success");
        } catch (error) {
            showMessage(error.message, "warn");
        }
    }

    function triggerDownload() {
        if (downloadBtn.disabled) return;
        window.location.href = "/api/download";
    }

    searchForm.addEventListener("submit", function (event) {
        event.preventDefault();
        validateSetup();
    });

    confirmStartBtn.addEventListener("click", startScrape);
    runStartBtn.addEventListener("click", startScrape);
    goBtn.addEventListener("click", sendGoSignal);
    focusBrowserBtn.addEventListener("click", focusBrowser);
    cancelBtn.addEventListener("click", cancelScrape);
    refreshStatusBtn.addEventListener("click", refreshStatus);
    clearLogsBtn.addEventListener("click", clearLogs);
    downloadBtn.addEventListener("click", triggerDownload);

    editConfigBtn.addEventListener("click", function () {
        setModalVisible(confirmationModal, false);
        confirmedPayload = null;
        facebookLink.focus();
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

    [facebookLink, scrollRounds, collectionType, startDate, endDate, outputFile].forEach((input) => {
        input.addEventListener("input", function () {
            confirmedPayload = null;
            if (input === outputFile) overwrite.value = "false";
        });
        input.addEventListener("change", function () {
            confirmedPayload = null;
        });
    });

    expandLogsBtn.addEventListener("click", openLogsDrawer);
    showLogsBtn.addEventListener("click", openLogsDrawer);
    centerShowLogsBtn.addEventListener("click", openLogsDrawer);
    closeLogsModalBtn.addEventListener("click", closeLogsDrawer);

    logsModal.addEventListener("click", function (event) {
        if (event.target === logsModal) closeLogsDrawer();
    });

    [logsBody, modalLogsBody].forEach((container) => {
        container.addEventListener("click", function (event) {
            const row = event.target.closest(".log-item");
            if (row && row.dataset.logIndex !== "-1") {
                showSelectedLog(row.dataset.logIndex);
            }
        });
    });

    aboutUsBtn.addEventListener("click", function () {
        setModalVisible(aboutModal, true);
    });

    closeAboutModalBtn.addEventListener("click", function () {
        setModalVisible(aboutModal, false);
    });

    aboutModal.addEventListener("click", function (event) {
        if (event.target === aboutModal) {
            setModalVisible(aboutModal, false);
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeLogsDrawer();
            setModalVisible(aboutModal, false);
            setModalVisible(confirmationModal, false);
        }
    });

    connectSocket();
    refreshStatus();
});
