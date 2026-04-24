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
    const previewStatusText = document.getElementById("previewStatusText");
    const previewStage = document.getElementById("previewStage");
    const livePreviewFrame = document.getElementById("livePreviewFrame");
    const previewPlaceholder = document.getElementById("previewPlaceholder");
    const previewNoteText = document.getElementById("previewNoteText");
    const previewUrlText = document.getElementById("previewUrlText");
    const previewRoundLive = document.getElementById("previewRoundLive");
    const previewPostsText = document.getElementById("previewPostsText");
    const pauseResumeBtn = document.getElementById("pauseResumeBtn");
    const scrollUpBtn = document.getElementById("scrollUpBtn");
    const scrollDownBtn = document.getElementById("scrollDownBtn");
    const forceScrollBtn = document.getElementById("forceScrollBtn");
    const capturePreviewBtn = document.getElementById("capturePreviewBtn");

    let confirmedPayload = null;
    let lastValidatedConfig = null;
    let latestLogs = [];
    let latestStatus = { status: "idle" };
    let currentPreview = {
        image: "",
        width: 0,
        height: 0,
        note: "Waiting for live browser preview.",
        url: "",
    };
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
        const latestModeEnabled = Boolean(latestMode.checked);
        return {
            instagramLink: instagramLink.value.trim(),
            scrollRounds: scrollRounds.value.trim(),
            startDate: String(startDate.value || "").trim(),
            endDate: latestModeEnabled ? "" : String(endDate.value || "").trim(),
            latestMode: latestModeEnabled,
            outputFile: outputFile.value.trim(),
            overwrite: overwrite.value === "true",
        };
    }

    function wait(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function fetchJson(url, options = {}) {
        const {
            retries = 0,
            retryDelay = 900,
            ...fetchOptions
        } = options;

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
                    const transient = [502, 503, 504].includes(response.status);
                    const message = response.ok
                        ? "The server returned a non-JSON response."
                        : transient
                            ? "The server returned a temporary gateway error. The app may still be starting, restarting, or timed out."
                            : `The server returned ${response.status}. Please refresh and try again.`;
                    data = {
                        ok: false,
                        errors: [message],
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

    function renderLogs(logs) {
        latestLogs = Array.isArray(logs) ? logs.slice(0, 250) : [];
        renderLogRows(document.getElementById("logsBody"), latestLogs);
        renderLogRows(document.getElementById("modalLogsBody"), latestLogs);
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
            renderLogRows(document.getElementById("logsBody"), latestLogs);
            renderLogRows(document.getElementById("modalLogsBody"), latestLogs);
        }
    }

    function setSocketState(state, label) {
        previewStatusText.dataset.state = state;
        previewStatusText.textContent = label;
    }

    function updatePreviewFrame(preview) {
        currentPreview = {
            image: preview.image || "",
            width: Number(preview.width) || 0,
            height: Number(preview.height) || 0,
            note: preview.note || "Waiting for live browser preview.",
            url: preview.url || "",
            updatedAt: preview.updatedAt || "",
        };

        previewNoteText.textContent = currentPreview.note;
        previewUrlText.textContent = currentPreview.url || "-";

        if (currentPreview.image) {
            livePreviewFrame.src = `data:image/jpeg;base64,${currentPreview.image}`;
            livePreviewFrame.classList.remove("hidden");
            previewPlaceholder.classList.add("hidden");
            previewStage.classList.add("has-image");
        } else {
            livePreviewFrame.removeAttribute("src");
            livePreviewFrame.classList.add("hidden");
            previewPlaceholder.textContent = currentPreview.note || "Waiting for live browser preview.";
            previewPlaceholder.classList.remove("hidden");
            previewStage.classList.remove("has-image");
        }
    }

    function setButtonStates(data) {
        const status = data.status || "idle";
        const running = ["running", "stopping", "paused", "waiting_login"].includes(status);
        const interactive = ["running", "paused", "waiting_login"].includes(status);

        confirmStartBtn.disabled = running;
        runStartBtn.disabled = running;
        cancelBtn.disabled = !running;
        downloadBtn.disabled = !data.canDownload;
        searchForm.querySelector("button[type='submit']").disabled = running;

        pauseResumeBtn.disabled = !interactive;
        pauseResumeBtn.textContent = status === "paused" ? "Resume" : "Pause";
        scrollUpBtn.disabled = !interactive;
        scrollDownBtn.disabled = !interactive;
        forceScrollBtn.disabled = !interactive;
        capturePreviewBtn.disabled = !interactive;
    }

    function renderStatus(data) {
        latestStatus = data || { status: "idle" };
        const status = latestStatus.status || "idle";
        const config = latestStatus.config || {};
        const currentRound = latestStatus.currentScrollRound ?? 0;
        const totalRounds = latestStatus.totalScrollRounds ?? 0;
        const postsFound = latestStatus.postsFound ?? 0;

        document.getElementById("statPostsFound").textContent = postsFound;
        document.getElementById("statProgress").textContent = `${latestStatus.progress ?? 0}%`;
        document.getElementById("statSuccessRate").textContent = `${latestStatus.successRate ?? 0}%`;
        document.getElementById("statErrors").textContent = latestStatus.errors ?? 0;

        document.getElementById("statusTitle").textContent = latestStatus.activeTask || "Waiting for input";
        document.getElementById("statusBadge").textContent = status;
        document.getElementById("statusBadge").dataset.status = status;
        document.getElementById("scrollRoundText").textContent = `Round ${currentRound} / ${totalRounds}`;
        document.getElementById("currentPostText").textContent = latestStatus.currentPost || "None";

        previewRoundLive.textContent = `${currentRound} / ${totalRounds}`;
        previewPostsText.textContent = String(postsFound);

        setProgress("progressFill", "progressText", latestStatus.progress ?? 0);
        setProgress("successFill", "successText", latestStatus.successRate ?? 0);
        setProgress("healthFill", "healthText", latestStatus.health ?? 100);

        document.getElementById("outputFileText").textContent = latestStatus.outputFile || (lastValidatedConfig?.outputFile || "Not selected");
        document.getElementById("tagStartDate").textContent = `Date: ${config.dateCoverage || lastValidatedConfig?.dateCoverage || "-"}`;
        document.getElementById("tagMaxScroll").textContent = `Max Scroll: ${config.scrollRounds || lastValidatedConfig?.scrollRounds || "-"}`;
        document.getElementById("tagRound").textContent = `Round: ${currentRound}`;

        if (Array.isArray(latestStatus.logs)) {
            renderLogs(latestStatus.logs);
        }

        setButtonStates(latestStatus);
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
        if (message.type === "preview") {
            updatePreviewFrame(message.data || {});
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
                retries: 1,
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

        connectSocket();
        try {
            const data = await fetchJson("/api/start", {
                method: "POST",
                body: JSON.stringify(confirmedPayload),
            });
            setModalVisible(confirmationModal, false);
            showMessage("Scraping started. Use the live preview to watch progress or complete login if Instagram asks.", "success");
            renderStatus(data.status);
            sendSocket({ type: "request_snapshot" });
        } catch (error) {
            showMessage(error.message);
        }
    }

    async function cancelScrape() {
        try {
            const data = await fetchJson("/api/cancel", { method: "POST" });
            showMessage("Cancellation requested. The scraper will stop at the next safe checkpoint.", "warn");
            renderStatus(data.status);
        } catch (error) {
            showMessage(error.message, "warn");
            if (error.payload?.status) renderStatus(error.payload.status);
        }
    }

    function sendControl(action, extra = {}) {
        if (!sendSocket({ type: "control", action, ...extra })) {
            showMessage("Live controls are unavailable until the dashboard reconnects.", "warn");
        }
    }

    function handlePreviewClick(event) {
        if (!currentPreview.image) return;

        const rect = previewStage.getBoundingClientRect();
        if (!rect.width || !rect.height) return;

        const relativeX = event.clientX - rect.left;
        const relativeY = event.clientY - rect.top;
        const sourceWidth = currentPreview.width || livePreviewFrame.naturalWidth || rect.width;
        const sourceHeight = currentPreview.height || livePreviewFrame.naturalHeight || rect.height;
        const x = Math.max(0, Math.round(relativeX * sourceWidth / rect.width));
        const y = Math.max(0, Math.round(relativeY * sourceHeight / rect.height));

        previewStage.focus();
        sendControl("preview_click", { x, y });
    }

    function handlePreviewKey(event) {
        if (!["running", "paused", "waiting_login"].includes(latestStatus.status || "")) return;

        const printable = event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
        const payload = {
            key: event.key,
            text: printable ? event.key : "",
        };

        event.preventDefault();
        sendControl("preview_key", payload);
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

    pauseResumeBtn.addEventListener("click", function () {
        if ((latestStatus.status || "") === "paused") {
            sendControl("resume");
        } else {
            sendControl("pause");
        }
    });
    scrollUpBtn.addEventListener("click", function () {
        sendControl("scroll_up");
    });
    scrollDownBtn.addEventListener("click", function () {
        sendControl("scroll_down");
    });
    forceScrollBtn.addEventListener("click", function () {
        sendControl("force_next_scroll");
    });
    capturePreviewBtn.addEventListener("click", function () {
        sendControl("capture_screenshot");
    });

    previewStage.addEventListener("click", handlePreviewClick);
    previewStage.addEventListener("keydown", handlePreviewKey);

    window.addEventListener("beforeunload", function () {
        socketShouldReconnect = false;
        if (reconnectTimer) {
            window.clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        if (dashboardSocket) {
            try {
                dashboardSocket.close();
            } catch (error) {
                // no-op
            }
        }
    });

    updatePreviewFrame(currentPreview);
    connectSocket();
    refreshStatus();
});
