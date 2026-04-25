const platformConfig = window.PLATFORM_CONFIG || {};

const searchForm = document.getElementById("searchForm");
const platformLink = document.getElementById("platformLink");
const scrollRounds = document.getElementById("scrollRounds");
const collectionType = document.getElementById("collectionType");
const startDate = document.getElementById("startDate");
const endDate = document.getElementById("endDate");
const outputFile = document.getElementById("outputFile");
const overwrite = document.getElementById("overwrite");
const latestMode = document.getElementById("latestMode");
const latestModeLabel = document.querySelector(".checkbox-label");
const reviewSetupBtn = document.getElementById("reviewSetupBtn");
const formMessage = document.getElementById("formMessage");
const overwritePanel = document.getElementById("overwritePanel");
const overwriteText = document.getElementById("overwriteText");
const confirmOverwriteBtn = document.getElementById("confirmOverwriteBtn");
const newFilenameBtn = document.getElementById("newFilenameBtn");

const statusTitle = document.getElementById("statusTitle");
const statusBadge = document.getElementById("statusBadge");
const scrollRoundText = document.getElementById("scrollRoundText");
const currentPostText = document.getElementById("currentPostText");
const progressText = document.getElementById("progressText");
const progressFill = document.getElementById("progressFill");
const successText = document.getElementById("successText");
const successFill = document.getElementById("successFill");
const healthText = document.getElementById("healthText");
const healthFill = document.getElementById("healthFill");
const outputFileText = document.getElementById("outputFileText");
const downloadBtn = document.getElementById("downloadBtn");
const cancelBtn = document.getElementById("cancelBtn");
const runStartBtn = document.getElementById("runStartBtn");
const goBtn = document.getElementById("goBtn");
const focusBrowserBtn = document.getElementById("focusBrowserBtn");
const centerShowLogsBtn = document.getElementById("centerShowLogsBtn");
const browserModeDescription = document.getElementById("browserModeDescription");
const browserModeText = document.getElementById("browserModeText");
const browserSessionStatusText = document.getElementById("browserSessionStatusText");
const browserSessionUrlText = document.getElementById("browserSessionUrlText");
const goStatusText = document.getElementById("goStatusText");
const tagStartDate = document.getElementById("tagStartDate");
const tagMaxScroll = document.getElementById("tagMaxScroll");
const tagRound = document.getElementById("tagRound");
const logsBody = document.getElementById("logsBody");
const expandLogsBtn = document.getElementById("expandLogsBtn");
const refreshStatusBtn = document.getElementById("refreshStatusBtn");
const showLogsBtn = document.getElementById("showLogsBtn");
const clearLogsBtn = document.getElementById("clearLogsBtn");
const statPostsFound = document.getElementById("statPostsFound");
const statProgress = document.getElementById("statProgress");
const statSuccessRate = document.getElementById("statSuccessRate");
const statErrors = document.getElementById("statErrors");

const confirmationModal = document.getElementById("confirmationModal");
const summaryLink = document.getElementById("summaryLink");
const summaryScroll = document.getElementById("summaryScroll");
const summaryCoverage = document.getElementById("summaryCoverage");
const summaryCollectionType = document.getElementById("summaryCollectionType");
const summaryCollectionTypeWrap = document.getElementById("summaryCollectionTypeWrap");
const summaryFile = document.getElementById("summaryFile");
const confirmStartBtn = document.getElementById("confirmStartBtn");
const editConfigBtn = document.getElementById("editConfigBtn");

const logsModal = document.getElementById("logsModal");
const closeLogsModalBtn = document.getElementById("closeLogsModalBtn");
const modalLogsBody = document.getElementById("modalLogsBody");
const selectedLogDetails = document.getElementById("selectedLogDetails");

const aboutUsBtn = document.getElementById("aboutUsBtn");
const aboutModal = document.getElementById("aboutModal");
const closeAboutModalBtn = document.getElementById("closeAboutModalBtn");

let liveLogs = [];
let lastStatus = null;
let socket = null;
let connectTimer = null;
let connectAttempts = 0;
let validatedConfig = null;

const depthTagLabel = platformConfig.depthTagLabel || "Depth";

function apiUrl(path) {
    const base = platformConfig.apiBase || "";
    return `${base}${path}`;
}

function isPlaceholderPlatform() {
    return Boolean(platformConfig.placeholder);
}

function platformLinkPayloadKey() {
    return platformConfig.linkPayloadKey || "instagramLink";
}

function platformLabel() {
    return platformConfig.platformName || "Platform";
}

function buildPayload() {
    const payload = {
        scrollRounds: scrollRounds.value.trim(),
        startDate: String(startDate.value || "").trim(),
        endDate: latestMode.checked ? "" : String(endDate.value || "").trim(),
        latestMode: Boolean(latestMode.checked),
        outputFile: outputFile.value.trim(),
        overwrite: overwrite.value === "true",
    };

    payload[platformLinkPayloadKey()] = platformLink.value.trim();

    if (platformConfig.collectionTypeEnabled && collectionType) {
        payload.collectionType = collectionType.value;
    }

    return payload;
}

async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
    });

    let data = {};
    try {
        data = await response.json();
    } catch (error) {
        data = {};
    }

    if (!response.ok || data.success === false) {
        const message = data.error || `Request failed with status ${response.status}`;
        throw new Error(message);
    }

    return data;
}

function formatTimestamp(timestamp) {
    if (!timestamp) {
        return "-";
    }

    const parts = String(timestamp).split("T");
    if (parts.length === 2) {
        return parts[1].slice(0, 8);
    }

    return timestamp;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function formatDisplayUrl(value, maxLength = 88) {
    const text = String(value || "").trim();
    if (!text) {
        return "None";
    }

    if (text.length <= maxLength) {
        return text;
    }

    return `${text.slice(0, maxLength - 3)}...`;
}

function statusTitleFor(state) {
    switch (state) {
        case "preparing":
            return "Preparing browser";
        case "loading_session":
            return "Loading saved session";
        case "waiting_login":
            return `${platformLabel()} login required`;
        case "waiting_verification":
            return `${platformLabel()} verification required`;
        case "ready":
            return "Ready for GO signal";
        case "running":
            return `Extracting ${platformLabel().toLowerCase()} data`;
        case "completed":
            return "Completed";
        case "failed":
            return "Run failed";
        case "cancelled":
        case "stopped":
            return "Stopped";
        default:
            return "Waiting for input";
    }
}

function browserSessionMessage(state) {
    switch (state) {
        case "loading_session":
            return `Checking saved ${platformLabel()} session.`;
        case "waiting_login":
            return `Waiting for manual ${platformLabel()} login in the opened browser.`;
        case "waiting_verification":
            return `${platformLabel()} verification required. Complete it manually in Chromium and keep the window open.`;
        case "ready":
            return `${platformLabel()} page ready. Click GO / Start Extraction.`;
        case "running":
            return `${platformLabel()} extraction is running in the current browser session.`;
        case "completed":
            return `${platformLabel()} extraction finished.`;
        case "failed":
            return `${platformLabel()} extraction failed. Check logs for details.`;
        case "cancelled":
        case "stopped":
            return `${platformLabel()} extraction was stopped.`;
        default:
            return "Waiting for Run / Start.";
    }
}

function gateMessage(data) {
    if (data.canGo) {
        return "Ready. Click GO / Start Extraction.";
    }
    if (data.status === "waiting_verification") {
        return `Complete ${platformLabel()} verification first.`;
    }
    if (data.status === "waiting_login") {
        return `Complete ${platformLabel()} login first.`;
    }
    if (data.status === "loading_session") {
        return "Checking saved session.";
    }
    return "Waiting for Run / Start.";
}

function setButtonDisabled(button, disabled) {
    if (!button) {
        return;
    }
    button.disabled = disabled;
    button.classList.toggle("disabled", disabled);
}

function renderLogRows(container, logs) {
    if (!container) {
        return;
    }

    if (!logs.length) {
        container.innerHTML = `
            <tr>
                <td>-</td>
                <td><span class="log-pill info">INFO</span></td>
                <td>No logs yet</td>
                <td>Review the setup and start a ${escapeHtml(platformLabel())} extraction job.</td>
            </tr>
        `;
        return;
    }

    container.innerHTML = logs
        .map((log, index) => {
            const level = String(log.level || "INFO").toLowerCase();
            const detail = escapeHtml(log.details || "");
            const action = escapeHtml(log.action || "");
            const time = escapeHtml(formatTimestamp(log.timestamp));
            return `
                <tr data-log-index="${index}">
                    <td>${time}</td>
                    <td><span class="log-pill ${level}">${escapeHtml(String(log.level || "INFO"))}</span></td>
                    <td>${action}</td>
                    <td title="${detail}">${detail}</td>
                </tr>
            `;
        })
        .join("");
}

function renderLogs(logs) {
    liveLogs = Array.isArray(logs) ? logs.slice() : [];
    renderLogRows(logsBody, liveLogs);
    renderLogRows(modalLogsBody, liveLogs);
}

function showSelectedLog(index) {
    const item = liveLogs[index];
    if (!item) {
        selectedLogDetails.textContent = "Select a log item to inspect its details.";
        return;
    }

    selectedLogDetails.innerHTML = `
        <strong>${escapeHtml(item.action || "Log item")}</strong><br>
        <span>${escapeHtml(item.details || "")}</span>
    `;
}

function appendLiveLog(log) {
    liveLogs.push(log);
    renderLogs(liveLogs);
}

function applyBrowserSessionState(data) {
    const browserMode = data.browserMode || "No browser session yet.";
    const browserUrl = data.browserUrl || data.currentPost || "";
    browserModeText.textContent = browserMode;
    browserSessionStatusText.textContent = browserSessionMessage(data.status);
    browserSessionUrlText.textContent = formatDisplayUrl(browserUrl, 96);
    browserSessionUrlText.title = browserUrl || "";
    goStatusText.textContent = gateMessage(data);
    browserModeDescription.textContent = platformConfig.browserSessionDescription || browserSessionMessage(data.status);
}

function setButtonStates(data) {
    const running = data.status === "running";
    const started = !["idle", "completed", "failed", "cancelled", "stopped"].includes(data.status);
    const canFocus = Boolean(data.browserOpen);
    const canDownload = Boolean(data.downloadReady);
    const placeholder = isPlaceholderPlatform();

    setButtonDisabled(runStartBtn, placeholder || running || data.status === "preparing");
    setButtonDisabled(goBtn, placeholder || !data.canGo);
    setButtonDisabled(focusBrowserBtn, placeholder || !canFocus);
    setButtonDisabled(cancelBtn, placeholder || !started);
    setButtonDisabled(downloadBtn, placeholder || !canDownload);

    if (placeholder) {
        setButtonDisabled(reviewSetupBtn, true);
    } else {
        setButtonDisabled(reviewSetupBtn, false);
    }
}

function renderStatus(data) {
    lastStatus = data;
    const roundCurrent = Number(data.scrollRound || 0);
    const roundMax = Number(data.maxScrollRounds || 0);
    const progress = Number(data.progress || 0);
    const successRate = Number(data.successRate || 0);
    const scrapeHealth = Number(data.scrapeHealth || 100);
    const linksFound = Number(data.postsFound || 0);
    const errors = Number(data.errors || 0);
    const itemUrl = data.currentPost || data.browserUrl || "";

    statusTitle.textContent = statusTitleFor(data.status);
    statusBadge.textContent = data.status || "idle";
    statusBadge.className = `status-badge ${data.status || "idle"}`;
    scrollRoundText.textContent = `Round ${roundCurrent} / ${roundMax}`;
    currentPostText.textContent = formatDisplayUrl(itemUrl);
    currentPostText.title = itemUrl || "";

    progressText.textContent = `${progress}%`;
    progressFill.style.width = `${progress}%`;
    successText.textContent = `${successRate}%`;
    successFill.style.width = `${successRate}%`;
    healthText.textContent = `${scrapeHealth}%`;
    healthFill.style.width = `${scrapeHealth}%`;

    outputFileText.textContent = data.outputFile || "Not selected";
    statPostsFound.textContent = String(linksFound);
    statProgress.textContent = `${progress}%`;
    statSuccessRate.textContent = `${successRate}%`;
    statErrors.textContent = String(errors);

    tagStartDate.textContent = `Date: ${data.dateCoverage || "-"}`;
    tagMaxScroll.textContent = `${depthTagLabel}: ${data.maxScrollRounds || "-"}`;
    tagRound.textContent = `Round: ${roundCurrent}`;

    applyBrowserSessionState(data);
    setButtonStates(data);
    renderLogs(data.logs || []);
}

function socketUrl() {
    if (isPlaceholderPlatform()) {
        return null;
    }

    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${window.location.host}${platformConfig.wsPath || "/ws/dashboard"}`;
}

function scheduleReconnect() {
    if (connectTimer || isPlaceholderPlatform()) {
        return;
    }
    const delay = Math.min(5000, 800 * (connectAttempts + 1));
    connectTimer = window.setTimeout(() => {
        connectTimer = null;
        connectSocket(true);
    }, delay);
}

function connectSocket(force = false) {
    if (isPlaceholderPlatform()) {
        return;
    }
    if (socket && socket.readyState <= WebSocket.OPEN && !force) {
        return;
    }
    const url = socketUrl();
    if (!url) {
        return;
    }

    if (socket) {
        socket.close();
    }

    socket = new WebSocket(url);

    socket.addEventListener("open", () => {
        connectAttempts = 0;
    });

    socket.addEventListener("message", (event) => {
        try {
            const payload = JSON.parse(event.data);
            if (payload.type === "status_update") {
                renderStatus(payload.data || {});
            } else if (payload.type === "log_event" && payload.data) {
                appendLiveLog(payload.data);
            }
        } catch (error) {
            console.warn("Failed to parse websocket payload", error);
        }
    });

    socket.addEventListener("close", () => {
        connectAttempts += 1;
        scheduleReconnect();
    });

    socket.addEventListener("error", () => {
        if (socket) {
            socket.close();
        }
    });
}

async function refreshStatus() {
    if (isPlaceholderPlatform()) {
        const placeholderStatus = {
            status: "idle",
            scrollRound: 0,
            maxScrollRounds: 0,
            progress: 0,
            successRate: 0,
            scrapeHealth: 100,
            postsFound: 0,
            errors: 0,
            outputFile: platformConfig.defaultOutputFile || "Not selected",
            currentPost: "",
            browserMode: "TikTok backend not wired yet.",
            browserOpen: false,
            canGo: false,
            downloadReady: false,
            dateCoverage: "-",
            logs: [],
        };
        renderStatus(placeholderStatus);
        formMessage.textContent = "TikTok is a placeholder for now. Instagram and Facebook remain available.";
        formMessage.className = "form-message info";
        return placeholderStatus;
    }

    const data = await fetchJson(apiUrl("/status"));
    renderStatus(data);
    return data;
}

function fillConfirmation(config) {
    summaryLink.textContent = config[platformLinkPayloadKey()] || "-";
    summaryScroll.textContent = config.scrollRounds || "-";
    summaryCoverage.textContent = config.latestMode
        ? `${config.startDate || "-"} to latest visible content`
        : `${config.startDate || "-"} to ${config.endDate || "-"}`;
    summaryFile.textContent = config.outputFile || "-";

    if (platformConfig.collectionTypeEnabled && summaryCollectionTypeWrap) {
        summaryCollectionTypeWrap.classList.remove("hidden");
        summaryCollectionType.textContent = config.collectionType || "-";
    } else if (summaryCollectionTypeWrap) {
        summaryCollectionTypeWrap.classList.add("hidden");
    }
}

async function validateSetup() {
    const payload = buildPayload();
    const data = await fetchJson(apiUrl("/validate"), {
        method: "POST",
        body: JSON.stringify(payload),
    });
    validatedConfig = payload;
    fillConfirmation(payload);
    confirmationModal.classList.remove("hidden");
    formMessage.textContent = data.message || `Setup validated. Confirm to open the browser and prepare ${platformLabel()} extraction.`;
    formMessage.className = "form-message success";
}

async function startScrape() {
    if (isPlaceholderPlatform()) {
        return;
    }
    const payload = validatedConfig || buildPayload();
    const data = await fetchJson(apiUrl("/start"), {
        method: "POST",
        body: JSON.stringify(payload),
    });
    confirmationModal.classList.add("hidden");
    formMessage.textContent = data.message || `Browser session created. Log in to ${platformLabel()} in the opened Chromium window.`;
    formMessage.className = "form-message success";
    await refreshStatus();
}

async function sendGoSignal() {
    if (isPlaceholderPlatform()) {
        return;
    }
    const data = await fetchJson(apiUrl("/go"), { method: "POST" });
    formMessage.textContent = data.message || `${platformLabel()} extraction started.`;
    formMessage.className = "form-message success";
    await refreshStatus();
}

async function focusBrowser() {
    if (isPlaceholderPlatform()) {
        return;
    }
    await fetchJson(apiUrl("/focus-browser"), { method: "POST" });
    formMessage.textContent = "Browser window focused.";
    formMessage.className = "form-message info";
}

async function cancelScrape() {
    if (isPlaceholderPlatform()) {
        return;
    }
    const data = await fetchJson(apiUrl("/cancel"), { method: "POST" });
    formMessage.textContent = data.message || `${platformLabel()} extraction cancelled.`;
    formMessage.className = "form-message info";
    await refreshStatus();
}

async function clearLogs() {
    if (isPlaceholderPlatform()) {
        renderLogs([]);
        return;
    }
    const data = await fetchJson(apiUrl("/clear-logs"), { method: "POST" });
    renderLogs([]);
    showSelectedLog(-1);
    formMessage.textContent = data.message || "Logs cleared.";
    formMessage.className = "form-message info";
}

function triggerDownload() {
    if (!lastStatus || !lastStatus.downloadReady || isPlaceholderPlatform()) {
        return;
    }
    window.open(apiUrl("/download"), "_blank", "noopener");
}

searchForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (isPlaceholderPlatform()) {
        formMessage.textContent = "TikTok is a placeholder right now. We can wire its backend next.";
        formMessage.className = "form-message info";
        return;
    }
    try {
        await validateSetup();
    } catch (error) {
        formMessage.textContent = error.message;
        formMessage.className = "form-message error";
    }
});

confirmStartBtn?.addEventListener("click", async () => {
    try {
        await startScrape();
    } catch (error) {
        formMessage.textContent = error.message;
        formMessage.className = "form-message error";
    }
});

editConfigBtn?.addEventListener("click", () => {
    confirmationModal.classList.add("hidden");
});

runStartBtn?.addEventListener("click", async () => {
    if (reviewSetupBtn.disabled) {
        return;
    }
    try {
        if (!validatedConfig) {
            await validateSetup();
        } else {
            fillConfirmation(validatedConfig);
            confirmationModal.classList.remove("hidden");
        }
    } catch (error) {
        formMessage.textContent = error.message;
        formMessage.className = "form-message error";
    }
});

goBtn?.addEventListener("click", async () => {
    try {
        await sendGoSignal();
    } catch (error) {
        formMessage.textContent = error.message;
        formMessage.className = "form-message error";
    }
});

focusBrowserBtn?.addEventListener("click", async () => {
    try {
        await focusBrowser();
    } catch (error) {
        formMessage.textContent = error.message;
        formMessage.className = "form-message error";
    }
});

cancelBtn?.addEventListener("click", async () => {
    try {
        await cancelScrape();
    } catch (error) {
        formMessage.textContent = error.message;
        formMessage.className = "form-message error";
    }
});

clearLogsBtn?.addEventListener("click", async () => {
    try {
        await clearLogs();
    } catch (error) {
        formMessage.textContent = error.message;
        formMessage.className = "form-message error";
    }
});

downloadBtn?.addEventListener("click", triggerDownload);
refreshStatusBtn?.addEventListener("click", async () => {
    try {
        await refreshStatus();
    } catch (error) {
        formMessage.textContent = error.message;
        formMessage.className = "form-message error";
    }
});

showLogsBtn?.addEventListener("click", () => logsModal.classList.remove("hidden"));
centerShowLogsBtn?.addEventListener("click", () => logsModal.classList.remove("hidden"));
expandLogsBtn?.addEventListener("click", () => logsModal.classList.remove("hidden"));
closeLogsModalBtn?.addEventListener("click", () => logsModal.classList.add("hidden"));

logsBody?.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-log-index]");
    if (!row) {
        return;
    }
    showSelectedLog(Number(row.dataset.logIndex));
});

modalLogsBody?.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-log-index]");
    if (!row) {
        return;
    }
    showSelectedLog(Number(row.dataset.logIndex));
});

aboutUsBtn?.addEventListener("click", () => aboutModal.classList.remove("hidden"));
closeAboutModalBtn?.addEventListener("click", () => aboutModal.classList.add("hidden"));

confirmOverwriteBtn?.addEventListener("click", () => {
    overwrite.value = "true";
    overwritePanel.classList.add("hidden");
});

newFilenameBtn?.addEventListener("click", () => {
    overwrite.value = "false";
    overwritePanel.classList.add("hidden");
    outputFile.focus();
});

latestMode?.addEventListener("change", () => {
    endDate.disabled = latestMode.checked;
    if (latestMode.checked) {
        endDate.value = "";
    }
});

endDate.disabled = latestMode.checked;
if (latestModeLabel) {
    latestModeLabel.textContent = platformConfig.latestModeLabel || latestModeLabel.textContent;
}

if (isPlaceholderPlatform()) {
    document.title = "S&R Extract | TikTok (Coming Soon)";
}

connectSocket();
refreshStatus().catch((error) => {
    formMessage.textContent = error.message;
    formMessage.className = "form-message error";
});
