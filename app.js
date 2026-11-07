const authView = document.getElementById("authView");
const authForm = document.getElementById("authForm");
const authNotice = document.getElementById("authNotice");
const authToggle = document.getElementById("authToggle");
const authSubmit = document.getElementById("authSubmit");
const authUsername = document.getElementById("authUsername");
const authPassword = document.getElementById("authPassword");
const showAuthPassword = document.getElementById("showAuthPassword");
const chatApp = document.getElementById("chatApp");
const userList = document.getElementById("userList");
const recentList = document.getElementById("recentList");
const messagesBox = document.getElementById("messages");
const messageForm = document.getElementById("messageForm");
const messageInput = document.getElementById("messageInput");
const chatTitle = document.getElementById("chatTitle");
const chatSubtitle = document.getElementById("chatSubtitle");
const typingIndicator = document.getElementById("typingIndicator");
const presenceIndicator = document.getElementById("presenceIndicator");
const chatAvatar = document.getElementById("chatAvatar");
const groupList = document.getElementById("groupList");
let isRegistering = false;
let currentUser = null;
let activeChat = null;
let activeChatType = null;
let activeGroup = null;
let lastMessageId = 0;
let lastMetadataRefreshAt = 0;
let pollTimer = null;
let typingPollTimer = null;
let typingIdleTimer = null;
let typingHeartbeatTimer = null;
let typingConversation = null;
let typingActive = false;
let presenceHeartbeatTimer = null;
let presencePollTimer = null;
let loadingMessages = false;
let knownUsers = [];
let recentChats = [];
let knownGroups = [];
let searchTimer = null;
let searchRequest = 0;
let recentChatsTimer = null;
let incomingCallTimer = null;
let notificationTimer = null;
let incomingCall = null;
let activeCall = null;
let peerConnection = null;
let localStream = null;
let screenStream = null;
let cameraTrack = null;
let voiceRecorder = null;
let voiceStream = null;
let voiceStartedAt = 0;
let voiceConversation = null;
let voiceLimitTimer = null;
let voiceCancelled = false;
let pendingIceCandidates = [];
let callSignalCursor = 0;
let callPollTimer = null;
let replyToMessage = null;
let unreadCounts = {};
const processedIncomingCalls = new Set();
const themeToggle = document.getElementById("themeToggle");
const settingsDialog = document.getElementById("settingsDialog");
const displayNameInput = document.getElementById("displayNameInput");
const avatarInput = document.getElementById("avatarInput");
const profileAvatar = document.getElementById("profileAvatar");
const profileStatus = document.getElementById("profileStatus");
const notificationsToggle = document.getElementById("notificationsToggle");
const notificationsStatus = document.getElementById("notificationsStatus");
const pollDialog = document.getElementById("pollDialog");
const emojiPicker = document.getElementById("emojiPicker");
const mediaInput = document.getElementById("mediaInput");
const callDialog = document.getElementById("callDialog");
const remoteAudio = document.getElementById("remoteAudio");
const groupDialog = document.getElementById("groupDialog");
const commandDialog = document.getElementById("commandDialog");
const commandInput = document.getElementById("commandInput");
const callStatus = document.getElementById("callStatus");
const MESSAGE_EMOJIS = ["😀", "😂", "😊", "😍", "🥰", "😎", "😭", "😡", "👍", "👎", "❤️", "🎉", "🔥", "🙏", "🤔", "✅", "👋", "💯"];

async function api(path, options) {
    let response;
    try {
        response = await fetch(path, options);
    } catch (error) {
        throw new Error("Не удалось связаться с сервером. Проверь, что программа запущена и интернет работает.");
    }
    let data;
    try {
        data = await response.json();
    } catch (error) {
        throw new Error("Сервер вернул непонятный ответ. Попробуй обновить страницу.");
    }
    if (!response.ok) {
        throw new Error(data && typeof data.error === "string"
            ? data.error : "Запрос не выполнен.");
    }
    return data;
}

function sendTypingStatus(conversation, typing) {
    api("/api/typing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: conversation, typing })
    }).catch(error => {
        if (error.message === "Нужно войти в аккаунт.") {
            showAuth(error.message);
        } else if (activeChat === conversation) {
            typingIndicator.textContent = "Не удалось обновить статус набора.";
            typingIndicator.classList.remove("hidden");
        }
    });
}

function stopTyping() {
    if (typingIdleTimer) window.clearTimeout(typingIdleTimer);
    if (typingHeartbeatTimer) window.clearInterval(typingHeartbeatTimer);
    typingIdleTimer = null;
    typingHeartbeatTimer = null;
    if (typingActive && typingConversation) {
        sendTypingStatus(typingConversation, false);
    }
    typingActive = false;
    typingConversation = null;
}

function stopTypingPolling() {
    if (typingPollTimer) window.clearTimeout(typingPollTimer);
    typingPollTimer = null;
    typingIndicator.textContent = "";
    typingIndicator.classList.add("hidden");
}

async function pollTypingStatus() {
    if (!currentUser || !activeChat) return;
    const conversation = activeChat;
    try {
        const result = await api(
            "/api/typing?with=" + encodeURIComponent(conversation)
        );
        if (conversation !== activeChat) return;
        const typers = result.typing || [];
        const status = activeChatType === "group"
            ? typers.length > 1
                ? typers.slice(0, 2).map(user => user.display_name).join(", ")
                    + (typers.length > 2 ? " и ещё печатают…" : " печатают…")
                : typers.length === 1 ? typers[0].display_name + " печатает…" : ""
            : typers.length ? "Печатает…" : "";
        typingIndicator.textContent = status;
        typingIndicator.classList.toggle("hidden", !status);
    } catch (error) {
        if (error.message === "Нужно войти в аккаунт.") {
            showAuth(error.message);
            return;
        }
        if (conversation === activeChat) {
            typingIndicator.textContent = "Не удалось обновить статус набора.";
            typingIndicator.classList.remove("hidden");
        }
    } finally {
        if (currentUser && conversation === activeChat) {
            typingPollTimer = window.setTimeout(pollTypingStatus, 1500);
        }
    }
}

function startTypingPolling() {
    stopTypingPolling();
    pollTypingStatus();
}

function handleTypingInput() {
    emojiPicker.classList.add("hidden");
    if (!activeChat || !messageInput.value.trim()) {
        stopTyping();
        return;
    }
    if (typingConversation !== activeChat) stopTyping();
    typingConversation = activeChat;
    if (!typingActive) {
        typingActive = true;
        sendTypingStatus(typingConversation, true);
        typingHeartbeatTimer = window.setInterval(function () {
            if (typingActive && typingConversation) {
                sendTypingStatus(typingConversation, true);
            }
        }, 2000);
    }
    if (typingIdleTimer) window.clearTimeout(typingIdleTimer);
    typingIdleTimer = window.setTimeout(stopTyping, 1800);
}

async function updateOwnPresence() {
    if (!currentUser) return;
    try {
        await api("/api/presence", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ active: true })
        });
    } catch (error) {
        if (error.message === "Нужно войти в аккаунт.") {
            showAuth(error.message);
        } else if (activeChatType === "user") {
            presenceIndicator.textContent = "Не удалось обновить свой статус.";
            presenceIndicator.classList.remove("hidden");
        }
    }
}

function startPresenceHeartbeat() {
    if (presenceHeartbeatTimer) window.clearInterval(presenceHeartbeatTimer);
    updateOwnPresence();
    presenceHeartbeatTimer = window.setInterval(updateOwnPresence, 15000);
}

function stopPresencePolling() {
    if (presencePollTimer) window.clearTimeout(presencePollTimer);
    presencePollTimer = null;
    presenceIndicator.textContent = "";
    presenceIndicator.classList.add("hidden");
}

async function pollPresence(username) {
    if (!currentUser || activeChatType !== "user" || username !== activeChat) return;
    try {
        const result = await api(
            "/api/presence?with=" + encodeURIComponent(username)
        );
        if (username !== activeChat || activeChatType !== "user") return;
        presenceIndicator.textContent = result.status;
        presenceIndicator.classList.remove("hidden");
        presenceIndicator.classList.toggle("online", result.online);
        chatAvatar.classList.toggle("online", result.online);
    } catch (error) {
        if (error.message === "Нужно войти в аккаунт.") {
            showAuth(error.message);
            return;
        }
        if (username === activeChat) {
            presenceIndicator.textContent = "Статус недоступен.";
            presenceIndicator.classList.remove("online");
            presenceIndicator.classList.remove("hidden");
        }
    } finally {
        if (currentUser && username === activeChat && activeChatType === "user") {
            presencePollTimer = window.setTimeout(
                function () { pollPresence(username); },
                15000
            );
        }
    }
}

function startPresencePolling(username) {
    stopPresencePolling();
    pollPresence(username);
}

function renderAvatar(element, user) {
    if (!element) return;
    element.querySelector(".avatar-image")?.remove();
    element.classList.toggle("online", Boolean(user && user.online));
    const name = user && (user.display_name || user.username);
    element.textContent = name ? name.charAt(0).toUpperCase() : "?";
    if (!user || !user.has_avatar || !user.id) return;

    const image = document.createElement("img");
    image.className = "avatar-image";
    image.alt = "";
    image.src = "/api/avatar?id=" + encodeURIComponent(user.id);
    image.addEventListener("error", function () { image.remove(); }, { once: true });
    element.append(image);
}

function showAuth(message) {
    stopTyping();
    stopTypingPolling();
    stopPresencePolling();
    if (presenceHeartbeatTimer) window.clearInterval(presenceHeartbeatTimer);
    presenceHeartbeatTimer = null;
    currentUser = null;
    activeChat = null;
    activeChatType = null;
    activeGroup = null;
    lastMetadataRefreshAt = 0;
    isRegistering = false;
    authSubmit.textContent = "Войти";
    authToggle.textContent = "Создать аккаунт";
    authPassword.value = "";
    authPassword.type = "password";
    authPassword.autocomplete = "current-password";
    showAuthPassword.checked = false;
    if (settingsDialog.open) settingsDialog.close();
    if (incomingCallTimer) window.clearTimeout(incomingCallTimer);
    if (recentChatsTimer) window.clearTimeout(recentChatsTimer);
    if (notificationTimer) window.clearTimeout(notificationTimer);
    if (pollTimer) window.clearTimeout(pollTimer);
    if (callPollTimer) window.clearTimeout(callPollTimer);
    stopVoiceRecording(true);
    cleanupCall();
    chatApp.classList.add("hidden");
    authView.classList.remove("hidden");
    authNotice.textContent = message || "";
}

function showChatApp(user) {
    currentUser = user;
    startPresenceHeartbeat();
    authView.classList.add("hidden");
    chatApp.classList.remove("hidden");
    document.getElementById("userSearch").value = "";
    knownUsers = [];
    recentChats = [];
    activeChat = null;
    activeChatType = null;
    activeGroup = null;
    displayNameInput.value = user.display_name || user.username;
    renderAvatar(profileAvatar, user);
    document.getElementById("digitalIdValue").textContent = user.digital_id || "";
    loadUnreadCounts();
    updateNotificationsButton();
    renderUsers("Введи не меньше 2 символов, чтобы найти пользователя.");
    pollRecentChats();
    pollNotifications();
    loadGroups().catch(error => window.alert(error.message));
    pollIncomingCalls();
}

document.addEventListener("visibilitychange", function () {
    if (currentUser && document.visibilityState === "visible") {
        updateOwnPresence();
    }
});

authToggle.addEventListener("click", function () {
    isRegistering = !isRegistering;
    authSubmit.textContent = isRegistering ? "Создать аккаунт" : "Войти";
    authToggle.textContent = isRegistering ? "Уже есть аккаунт? Войти" : "Создать аккаунт";
    authPassword.autocomplete = isRegistering ? "new-password" : "current-password";
    authNotice.textContent = "";
});

showAuthPassword.addEventListener("change", function () {
    authPassword.type = this.checked ? "text" : "password";
});
document.getElementById("showSettingsPasswords").addEventListener("change", function () {
    const type = this.checked ? "text" : "password";
    document.querySelectorAll("#oldPasswordInput, #newPasswordInput, #confirmPasswordInput")
        .forEach(input => { input.type = type; });
});

authForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    authSubmit.disabled = true;
    authNotice.textContent = "Проверяю данные...";
    try {
        const user = await api(isRegistering ? "/api/register" : "/api/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                username: authUsername.value.trim(),
                password: authPassword.value
            })
        });
        authPassword.value = "";
        showChatApp(user);
    } catch (error) {
        authNotice.textContent = error.message;
    } finally {
        authSubmit.disabled = false;
    }
});

async function searchUsers() {
    const query = document.getElementById("userSearch").value.trim();
    const requestId = ++searchRequest;
    if (query.length < 2) {
        knownUsers = [];
        renderUsers(query ? "Введи ещё хотя бы один символ." : "Введи имя пользователя для поиска.");
        return;
    }

    try {
        knownUsers = await api("/api/users?search=" + encodeURIComponent(query));
        if (requestId !== searchRequest) return;
        renderUsers(knownUsers.length ? "" : "Пользователь не найден.");
    } catch (error) {
        if (requestId === searchRequest) {
            knownUsers = [];
            renderUsers(error.message);
            if (error.message === "Нужно войти в аккаунт.") showAuth(error.message);
        }
    }
}

async function loadRecentChats() {
    recentChats = await api("/api/chats");
    renderRecentChats();
}

async function pollRecentChats() {
    if (!currentUser) return;
    try {
        await loadRecentChats();
    } catch (error) {
        recentList.textContent = error.message;
    } finally {
        if (currentUser) {
            recentChatsTimer = window.setTimeout(pollRecentChats, 5000);
        }
    }
}

function makeUserRow(user, isRecent) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "user-row" + (activeChat === user.username ? " selected" : "");
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    renderAvatar(avatar, user);
    const info = document.createElement("span");
    info.className = "user-info";
    const name = document.createElement("span");
    name.className = "user-name";
    name.textContent = user.display_name || user.username;
    const preview = document.createElement("span");
    preview.className = "user-last";
    preview.textContent = isRecent
        ? (user.last_message || "Начать переписку")
        : user.last_message
            ? "@" + user.username + " · " + user.last_message
            : "@" + user.username + (user.digital_id ? " · " + user.digital_id : "");
    info.append(name, preview);
    button.append(avatar, info);
    button.addEventListener("click", function () {
        openChat(user.username, user.display_name, user);
    });
    return button;
}

function renderRecentChats() {
    recentList.replaceChildren();
    if (recentChats.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-chat";
        empty.textContent = "Здесь появятся люди, с которыми ты переписывался.";
        recentList.append(empty);
        return;
    }
    for (const user of recentChats) recentList.append(makeUserRow(user, true));
}

function renderUsers(emptyMessage) {
    userList.replaceChildren();

    if (knownUsers.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-chat";
        empty.textContent = emptyMessage || "Пользователь не найден.";
        userList.append(empty);
        return;
    }

    for (const user of knownUsers) {
        userList.append(makeUserRow(user, false));
    }
}

function renderGroups() {
    groupList.replaceChildren();
    for (const group of knownGroups) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "user-row group-row"
            + (activeChat === "group:" + group.id ? " selected" : "");
        const avatar = document.createElement("span");
        avatar.className = "avatar";
        avatar.textContent = group.name.charAt(0).toUpperCase();
        const info = document.createElement("span");
        info.className = "user-info";
        const name = document.createElement("span");
        name.className = "user-name";
        name.textContent = group.name;
        const details = document.createElement("span");
        details.className = "user-last";
        details.textContent = "Код " + group.invite_code + " · " + group.member_count + " участников";
        info.append(name, details);
        button.append(avatar, info);
        button.addEventListener("click", function () { openGroup(group); });
        groupList.append(button);
    }
}

async function loadGroups() {
    knownGroups = await api("/api/groups");
    renderGroups();
}

document.getElementById("groupCreate").addEventListener("click", function () {
    document.getElementById("groupStatus").textContent = "";
    groupDialog.showModal();
    document.getElementById("groupNameInput").focus();
});

document.getElementById("groupJoin").addEventListener("click", function () {
    document.getElementById("groupStatus").textContent = "";
    groupDialog.showModal();
    document.getElementById("groupInviteInput").focus();
});

document.getElementById("groupDialogClose").addEventListener("click", function () {
    groupDialog.close();
});

document.getElementById("groupCreateSubmit").addEventListener("click", async function () {
    const name = document.getElementById("groupNameInput").value.trim();
    const status = document.getElementById("groupStatus");
    if (!name) {
        status.textContent = "Введи название группы.";
        return;
    }
    try {
        const group = await api("/api/groups", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name })
        });
        await loadGroups();
        await openGroup(knownGroups.find(item => item.id === group.id) || group);
        status.textContent = "Группа создана. Код приглашения: " + group.invite_code;
    } catch (error) {
        status.textContent = error.message;
    }
});

document.getElementById("groupJoinSubmit").addEventListener("click", async function () {
    const inviteCode = document.getElementById("groupInviteInput").value.trim();
    const status = document.getElementById("groupStatus");
    if (!inviteCode) {
        status.textContent = "Введи код приглашения.";
        return;
    }
    try {
        const group = await api("/api/groups/join", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ invite_code: inviteCode })
        });
        await loadGroups();
        await openGroup(knownGroups.find(item => item.id === group.id) || group);
        status.textContent = "Ты вошёл в группу.";
    } catch (error) {
        status.textContent = error.message;
    }
});

async function openChat(username, displayName, user) {
    if (pollTimer) window.clearTimeout(pollTimer);
    stopTyping();
    stopTypingPolling();
    stopPresencePolling();
    const chatUser = user
        || knownUsers.find(item => item.username === username)
        || recentChats.find(item => item.username === username);
    activeChat = username;
    activeChatType = "user";
    activeGroup = null;
    lastMetadataRefreshAt = 0;
    clearReply();
    markConversationRead(username);
    lastMessageId = 0;
    chatTitle.textContent = displayName || username;
    chatSubtitle.textContent = "Личные сообщения";
    renderAvatar(chatAvatar, chatUser || { username, display_name: displayName });
    document.getElementById("callActions").classList.remove("hidden");
    messageForm.classList.remove("hidden");
    chatApp.classList.add("chat-open");
    renderUsers();
    renderGroups();
    startTypingPolling();
    startPresencePolling(username);
    messagesBox.replaceChildren();
    await loadPinnedMessages();
    await loadMessages(true);
    messageInput.focus();
}

async function openGroup(group) {
    if (pollTimer) window.clearTimeout(pollTimer);
    stopTyping();
    stopTypingPolling();
    stopPresencePolling();
    chatAvatar.classList.remove("online");
    activeChat = "group:" + group.id;
    activeChatType = "group";
    activeGroup = group;
    lastMetadataRefreshAt = 0;
    clearReply();
    markConversationRead("group:" + group.id);
    lastMessageId = 0;
    chatTitle.textContent = group.name;
    chatSubtitle.textContent = "Группа · " + (group.member_count || 1) + " участников";
    renderAvatar(chatAvatar, { display_name: group.name });
    document.getElementById("callActions").classList.add("hidden");
    messageForm.classList.remove("hidden");
    chatApp.classList.add("chat-open");
    renderUsers();
    renderGroups();
    startTypingPolling();
    messagesBox.replaceChildren();
    await loadPinnedMessages();
    await loadMessages(true);
    messageInput.focus();
}

function callSignalUrl(callId) {
    return "/api/calls/" + encodeURIComponent(callId) + "/signal";
}

async function sendCallSignal(kind, payload) {
    if (!activeCall) return;
    await api(callSignalUrl(activeCall.id), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, payload: payload || {} })
    });
}

function showCallDialog(call, status) {
    document.getElementById("callTitle").textContent =
        (call.mode === "video" ? "Видеозвонок" : "Голосовой звонок")
        + (call.peer ? " · " + call.peer : "");
    callStatus.textContent = status;
    document.getElementById("screenShare").disabled = true;
    document.getElementById("callMute").disabled = true;
    document.getElementById("callCamera").disabled = true;
    callDialog.dataset.video = String(call.mode === "video");
    callDialog.dataset.incoming = "false";
    callDialog.classList.remove("has-remote-video");
    document.getElementById("callPlaceholderAvatar").textContent =
        (call.peer || "?").charAt(0).toUpperCase();
    document.getElementById("callPlaceholderText").textContent = status;
    document.getElementById("callMute").innerHTML =
        '<span class="call-control-icon">🎙</span>Микрофон';
    document.getElementById("callCamera").innerHTML =
        '<span class="call-control-icon">▣</span>Камера';
    if (!callDialog.open) callDialog.showModal();
}

async function startCall(mode) {
    if (!activeChat || activeChatType !== "user" || activeCall) return;
    try {
        const call = await api("/api/calls", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ to: activeChat, mode })
        });
        activeCall = {
            id: call.id, mode, peer: activeChat, isCaller: true
        };
        callSignalCursor = 0;
        pendingIceCandidates = [];
        showCallDialog(activeCall, "Звоним. Ждём ответа...");
        pollActiveCall();
    } catch (error) {
        window.alert(error.message);
    }
}

document.getElementById("callAudio").addEventListener("click", function () {
    startCall("audio");
});
document.getElementById("callVideo").addEventListener("click", function () {
    startCall("video");
});

function processIncomingCall(call) {
    processedIncomingCalls.add(call.id);
    incomingCall = call;
    showCallDialog({
        mode: call.mode,
        peer: call.display_name || call.username
    }, "Входящий звонок от @" + call.username);
    callDialog.dataset.incoming = "true";
}

async function acceptIncomingCall() {
    if (!incomingCall || activeCall) return;
    const call = incomingCall;
    incomingCall = null;
    activeCall = {
        id: call.id, mode: call.mode,
        peer: call.display_name || call.username, isCaller: false
    };
    callSignalCursor = 0;
    pendingIceCandidates = [];
    showCallDialog(activeCall, "Подключаем камеру и микрофон...");
    try {
        await sendCallSignal("accept");
        await setupPeerConnection();
        pollActiveCall();
    } catch (error) {
        callStatus.textContent = error.message;
        try {
            await sendCallSignal("end");
        } catch (signalError) {
            callStatus.textContent = error.message + " " + signalError.message;
        }
        cleanupCall();
    }
}

async function rejectIncomingCall() {
    if (!incomingCall) return;
    const call = incomingCall;
    incomingCall = null;
    try {
        await api(callSignalUrl(call.id), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ kind: "reject" })
        });
    } catch (error) {
        callStatus.textContent = error.message;
    }
    if (callDialog.open) callDialog.close();
}

document.getElementById("callAccept").addEventListener("click", acceptIncomingCall);
document.getElementById("callReject").addEventListener("click", rejectIncomingCall);
callDialog.addEventListener("cancel", function (event) {
    event.preventDefault();
});

async function pollIncomingCalls() {
    if (!currentUser) return;
    try {
        const calls = await api("/api/calls/incoming");
        const incoming = calls.find(call => !processedIncomingCalls.has(call.id));
        if (incoming && !activeCall && !incomingCall) processIncomingCall(incoming);
    } catch (error) {
        if (error.message === "Нужно войти в аккаунт.") {
            showAuth(error.message);
            return;
        }
    } finally {
        if (currentUser) {
            incomingCallTimer = window.setTimeout(pollIncomingCalls, 2500);
        }
    }
}

async function setupPeerConnection() {
    if (!activeCall || peerConnection) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error("Браузер не поддерживает звонки. Открой сайт по HTTPS.");
    }
    localStream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: activeCall.mode === "video"
    });
    cameraTrack = localStream.getVideoTracks()[0] || null;
    document.getElementById("localVideo").srcObject = localStream;
    document.getElementById("callMute").disabled = false;
    document.getElementById("callCamera").disabled = !cameraTrack;
    peerConnection = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
    });
    document.getElementById("screenShare").disabled = activeCall.mode !== "video";
    for (const track of localStream.getTracks()) {
        peerConnection.addTrack(track, localStream);
    }
    peerConnection.ontrack = function (event) {
        const stream = event.streams[0] || new MediaStream([event.track]);
        if (event.track.kind === "audio") remoteAudio.srcObject = stream;
        if (event.track.kind === "video") {
            document.getElementById("remoteVideo").srcObject = stream;
            callDialog.classList.add("has-remote-video");
        }
        callStatus.textContent = "Соединение установлено.";
    };
    peerConnection.onicecandidate = function (event) {
        if (event.candidate) {
            sendCallSignal("ice", event.candidate.toJSON()).catch(error => {
                callStatus.textContent = error.message;
            });
        }
    };
    peerConnection.onconnectionstatechange = function () {
        if (peerConnection && peerConnection.connectionState === "failed") {
            callStatus.textContent =
                "Не удалось соединиться. Сеть может требовать TURN-сервер.";
        }
    };

    if (activeCall.isCaller) {
        const offer = await peerConnection.createOffer();
        await peerConnection.setLocalDescription(offer);
        await sendCallSignal("offer", peerConnection.localDescription.toJSON());
        callStatus.textContent = "Соединяемся...";
    } else {
        callStatus.textContent = "Ожидаем соединение...";
    }
}

async function applyPendingIceCandidates() {
    if (!peerConnection || !peerConnection.remoteDescription) return;
    const candidates = pendingIceCandidates;
    pendingIceCandidates = [];
    for (const candidate of candidates) {
        await peerConnection.addIceCandidate(candidate);
    }
}

async function receiveCallSignal(signal) {
    if (!peerConnection && signal.kind === "offer") {
        await setupPeerConnection();
    }
    if (signal.kind === "ice") {
        if (peerConnection && peerConnection.remoteDescription) {
            await peerConnection.addIceCandidate(signal.payload);
        } else {
            pendingIceCandidates.push(signal.payload);
        }
        return;
    }
    if (signal.kind === "offer" && peerConnection) {
        await peerConnection.setRemoteDescription(signal.payload);
        await applyPendingIceCandidates();
        const answer = await peerConnection.createAnswer();
        await peerConnection.setLocalDescription(answer);
        await sendCallSignal("answer", peerConnection.localDescription.toJSON());
        callStatus.textContent = "Соединяемся...";
    } else if (signal.kind === "answer" && peerConnection) {
        await peerConnection.setRemoteDescription(signal.payload);
        await applyPendingIceCandidates();
    }
}

async function pollActiveCall() {
    if (!activeCall) return;
    const call = activeCall;
    try {
        const state = await api(
            "/api/calls/" + encodeURIComponent(call.id)
            + "?after=" + callSignalCursor
        );
        if (!activeCall || activeCall.id !== call.id) return;
        if (state.status === "rejected") {
            callStatus.textContent = "Звонок отклонён.";
            cleanupCall();
            return;
        }
        if (state.status === "ended") {
            callStatus.textContent = "Звонок завершён.";
            cleanupCall();
            return;
        }
        if (state.status === "accepted" && call.isCaller && !peerConnection) {
            call.accepted = true;
            await setupPeerConnection();
        }
        for (const signal of state.signals) {
            callSignalCursor = Math.max(callSignalCursor, signal.id);
            await receiveCallSignal(signal);
        }
    } catch (error) {
        if (call.isCaller && call.accepted && !peerConnection) {
            try {
                await sendCallSignal("end");
            } catch (signalError) {
                callStatus.textContent = signalError.message;
            }
            window.alert(error.message);
            cleanupCall();
            return;
        }
        if (error.message === "Звонок не найден.") {
            callStatus.textContent = error.message;
            cleanupCall();
            return;
        }
        callStatus.textContent = error.message;
    } finally {
        if (activeCall && activeCall.id === call.id) {
            callPollTimer = window.setTimeout(pollActiveCall, 900);
        }
    }
}

function cleanupCall() {
    if (callPollTimer) window.clearTimeout(callPollTimer);
    callPollTimer = null;
    if (peerConnection) peerConnection.close();
    peerConnection = null;
    if (localStream) {
        for (const track of localStream.getTracks()) track.stop();
    }
    if (screenStream) {
        for (const track of screenStream.getTracks()) track.stop();
    }
    localStream = null;
    screenStream = null;
    cameraTrack = null;
    pendingIceCandidates = [];
    document.getElementById("localVideo").srcObject = null;
    document.getElementById("remoteVideo").srcObject = null;
    remoteAudio.srcObject = null;
    activeCall = null;
    incomingCall = null;
    callDialog.classList.remove("has-remote-video");
    callDialog.dataset.incoming = "false";
    document.getElementById("callMute").disabled = true;
    document.getElementById("callCamera").disabled = true;
    document.getElementById("screenShare").disabled = true;
    document.getElementById("callMute").innerHTML =
        '<span class="call-control-icon">🎙</span>Микрофон';
    document.getElementById("callCamera").innerHTML =
        '<span class="call-control-icon">▣</span>Камера';
    if (callDialog.open) callDialog.close();
}

document.getElementById("callEnd").addEventListener("click", async function () {
    if (activeCall) {
        try {
            await sendCallSignal("end");
        } catch (error) {
            window.alert(error.message);
        }
    }
    cleanupCall();
});

document.getElementById("callMute").addEventListener("click", function () {
    if (!localStream) return;
    const audioTracks = localStream.getAudioTracks();
    if (audioTracks.length === 0) return;
    const enabled = !audioTracks[0].enabled;
    for (const track of audioTracks) track.enabled = enabled;
    this.innerHTML = enabled
        ? '<span class="call-control-icon">🎙</span>Микрофон'
        : '<span class="call-control-icon">🔇</span>Без звука';
    this.setAttribute("aria-label", enabled ? "Выключить микрофон" : "Включить микрофон");
});

document.getElementById("callCamera").addEventListener("click", function () {
    if (!cameraTrack) return;
    cameraTrack.enabled = !cameraTrack.enabled;
    this.innerHTML = cameraTrack.enabled
        ? '<span class="call-control-icon">▣</span>Камера'
        : '<span class="call-control-icon">▧</span>Камера выкл.';
    this.setAttribute("aria-label", cameraTrack.enabled ? "Выключить камеру" : "Включить камеру");
});

document.getElementById("screenShare").addEventListener("click", async function () {
    if (!activeCall || activeCall.mode !== "video" || !peerConnection) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
        callStatus.textContent = "Браузер не поддерживает демонстрацию экрана.";
        return;
    }
    try {
        screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true });
        const screenTrack = screenStream.getVideoTracks()[0];
        const videoSender = peerConnection.getSenders()
            .find(sender => sender.track && sender.track.kind === "video");
        if (!screenTrack || !videoSender) {
            screenStream.getTracks().forEach(track => track.stop());
            screenStream = null;
            callStatus.textContent = "Не удалось включить демонстрацию экрана.";
            return;
        }
        await videoSender.replaceTrack(screenTrack);
        document.getElementById("localVideo").srcObject = screenStream;
        screenTrack.onended = async function () {
            if (peerConnection && cameraTrack && videoSender.track === screenTrack) {
                await videoSender.replaceTrack(cameraTrack);
                document.getElementById("localVideo").srcObject = localStream;
            }
            screenStream = null;
        };
        callStatus.textContent = "Ты показываешь экран.";
    } catch (error) {
        callStatus.textContent = "Не удалось показать экран: " + error.message;
    }
});

function appendMessage(message) {
    const bubble = document.createElement("article");
    bubble.className = "bubble" + (message.sender === currentUser.username ? " mine" : "");
    bubble.dataset.messageId = String(message.id);
    if (message.sender !== currentUser.username && message.sender_display) {
        const senderName = document.createElement("strong");
        senderName.textContent = message.sender_display;
        senderName.style.display = "block";
        senderName.style.marginBottom = "4px";
        bubble.append(senderName);
    }
    if (message.reply) {
        const quote = document.createElement("div");
        quote.className = "reply-quote";
        const quotedSender = document.createElement("strong");
        quotedSender.textContent = message.reply.sender || "Ответ";
        const quotedText = document.createElement("span");
        quotedText.textContent = message.reply.kind === "media"
            ? "Фото или видео"
            : message.reply.kind === "voice"
                ? "Голосовое сообщение"
                : message.reply.text || "";
        quote.append(quotedSender, quotedText);
        bubble.append(quote);
    }

    if (message.deleted) {
        const deleted = document.createElement("div");
        deleted.className = "deleted-message";
        deleted.textContent = "Сообщение удалено";
        bubble.append(deleted);
    } else if (message.kind === "media") {
        bubble.classList.add("media-message");
        const url = "/api/media?id=" + encodeURIComponent(message.id);
        if (message.media_type.startsWith("image/")) {
            const image = document.createElement("img");
            image.src = url;
            image.alt = message.media_name || "Изображение";
            image.loading = "lazy";
            bubble.append(image);
        } else {
            const video = document.createElement("video");
            video.src = url;
            video.controls = true;
            video.preload = "metadata";
            video.setAttribute("aria-label", message.media_name || "Видео");
            bubble.append(video);
        }
        const fileName = document.createElement("div");
        fileName.className = "media-name";
        fileName.textContent = message.media_name || "Медиафайл";
        bubble.append(fileName);
    } else if (message.kind === "voice") {
        const audio = document.createElement("audio");
        audio.src = "/api/media?id=" + encodeURIComponent(message.id);
        audio.controls = true;
        audio.preload = "metadata";
        audio.setAttribute("aria-label", "Голосовое сообщение");
        bubble.append(audio);
    } else if (message.kind === "poll" && message.poll) {
        bubble.append(buildPollCard(message.poll));
    } else {
        const text = document.createElement("div");
        text.textContent = message.text;
        bubble.append(text);
    }
    if (message.edited && !message.deleted) {
        const edited = document.createElement("span");
        edited.className = "edited-label";
        edited.textContent = "изменено";
        bubble.append(edited);
    }

    const time = document.createElement("div");
    time.className = "bubble-time";
    time.textContent = message.time;
    bubble.append(time);

    if (!message.deleted) {
        const actionBar = document.createElement("div");
        actionBar.className = "message-actions";
        const replyButton = document.createElement("button");
        replyButton.type = "button";
        replyButton.textContent = "Ответить";
        replyButton.addEventListener("click", function () {
            setReplyMessage(message);
        });
        actionBar.append(replyButton);

        const reactionButton = document.createElement("button");
        reactionButton.type = "button";
        reactionButton.textContent = "Реакция";
        reactionButton.addEventListener("click", function () {
            toggleReactionPicker(bubble, message);
        });
        actionBar.append(reactionButton);

        const pinButton = document.createElement("button");
        pinButton.type = "button";
        pinButton.textContent = message.pinned ? "Открепить" : "Закрепить";
        pinButton.addEventListener("click", async function () {
            pinButton.disabled = true;
            try {
                await api("/api/messages/" + message.id + "/pin", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ pinned: !message.pinned })
                });
                await reloadActiveMessages();
                await loadPinnedMessages();
            } catch (error) {
                window.alert(error.message);
            } finally {
                pinButton.disabled = false;
            }
        });
        actionBar.append(pinButton);

        if (message.sender === currentUser.username) {
            if (message.kind === "text") {
                const editButton = document.createElement("button");
                editButton.type = "button";
                editButton.textContent = "Изменить";
                editButton.addEventListener("click", async function () {
                    const updatedText = window.prompt("Измени сообщение:", message.text);
                    if (updatedText === null || !updatedText.trim()) return;
                    try {
                        await api("/api/messages/" + message.id + "/edit", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ text: updatedText })
                        });
                        await reloadActiveMessages();
                    } catch (error) {
                        window.alert(error.message);
                    }
                });
                actionBar.append(editButton);
            }
            const deleteButton = document.createElement("button");
            deleteButton.type = "button";
            deleteButton.textContent = "Удалить";
            deleteButton.addEventListener("click", async function () {
                if (!window.confirm("Удалить это сообщение?")) return;
                try {
                    await api("/api/messages/" + message.id + "/delete", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({})
                    });
                    await reloadActiveMessages();
                    await loadPinnedMessages();
                } catch (error) {
                    window.alert(error.message);
                }
            });
            actionBar.append(deleteButton);
        }
        bubble.append(actionBar);

        if (message.reactions && message.reactions.length) {
            const reactionList = document.createElement("div");
            reactionList.className = "message-reactions";
            for (const reaction of message.reactions) {
                const chip = document.createElement("button");
                chip.type = "button";
                chip.textContent = reaction.emoji + " " + reaction.count;
                chip.classList.toggle("reacted", reaction.reacted);
                chip.setAttribute("aria-label", reaction.emoji + ", " + reaction.count + " реакций");
                chip.addEventListener("click", function () {
                    sendReaction(message.id, reaction.emoji);
                });
                reactionList.append(chip);
            }
            bubble.append(reactionList);
        }
    }
    messagesBox.append(bubble);
}

function toggleReactionPicker(bubble, message) {
    const oldPicker = bubble.querySelector(".message-reaction-picker");
    if (oldPicker) {
        oldPicker.remove();
        return;
    }
    const picker = document.createElement("div");
    picker.className = "message-reaction-picker";
    for (const emoji of MESSAGE_EMOJIS) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = emoji;
        button.setAttribute("aria-label", "Реакция " + emoji);
        button.addEventListener("click", function () {
            sendReaction(message.id, emoji);
        });
        picker.append(button);
    }
    bubble.append(picker);
}

async function sendReaction(messageId, emoji) {
    try {
        await api("/api/messages/" + messageId + "/reactions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ emoji })
        });
        await reloadActiveMessages();
    } catch (error) {
        window.alert(error.message);
    }
}

function setReplyMessage(message) {
    replyToMessage = message;
    const text = message.kind === "media"
        ? "Фото или видео"
        : message.kind === "voice"
            ? "Голосовое сообщение"
            : message.text;
    document.getElementById("replyPreviewText").textContent =
        "Ответ " + (message.sender_display || message.sender) + ": " + text;
    document.getElementById("replyPreview").classList.remove("hidden");
    messageInput.focus();
}

function clearReply() {
    replyToMessage = null;
    document.getElementById("replyPreview").classList.add("hidden");
    document.getElementById("replyPreviewText").textContent = "";
}

async function reloadActiveMessages() {
    if (!activeChat) return;
    if (loadingMessages) {
        window.setTimeout(reloadActiveMessages, 100);
        return;
    }
    lastMessageId = 0;
    messagesBox.replaceChildren();
    await loadMessages(true);
}

async function refreshVisibleMessages(requestedChat) {
    const wasAtBottom = messagesBox.scrollHeight - messagesBox.clientHeight - messagesBox.scrollTop < 60;
    const previousScrollTop = messagesBox.scrollTop;
    const params = new URLSearchParams({ with: requestedChat, after: "0" });
    const latestMessages = await api("/api/messages?" + params.toString());
    if (requestedChat !== activeChat) return;
    messagesBox.replaceChildren();
    lastMessageId = 0;
    for (const message of latestMessages) {
        appendMessage(message);
        lastMessageId = Math.max(lastMessageId, message.id);
    }
    messagesBox.scrollTop = wasAtBottom ? messagesBox.scrollHeight : previousScrollTop;
    await loadPinnedMessages();
}

async function loadPinnedMessages() {
    const panel = document.getElementById("pinnedMessages");
    const pinnedList = document.getElementById("pinnedList");
    pinnedList.replaceChildren();
    if (!activeChat) {
        panel.classList.add("hidden");
        return;
    }
    const requestedChat = activeChat;
    try {
        const pins = await api("/api/pins?with=" + encodeURIComponent(requestedChat));
        if (requestedChat !== activeChat) return;
        for (const pin of pins) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "pinned-item";
            button.textContent = pin.sender + ": " + (pin.text || pin.media_name || "Сообщение");
            button.addEventListener("click", async function () {
                let target = messagesBox.querySelector('[data-message-id="' + pin.id + '"]');
                if (!target) {
                    try {
                        const params = new URLSearchParams({
                            with: requestedChat,
                            before: String(pin.id + 1)
                        });
                        const history = await api("/api/messages?" + params.toString());
                        if (requestedChat !== activeChat) return;
                        messagesBox.replaceChildren();
                        lastMessageId = 0;
                        for (const message of history) {
                            appendMessage(message);
                            lastMessageId = Math.max(lastMessageId, message.id);
                        }
                        lastMetadataRefreshAt = Date.now();
                        target = messagesBox.querySelector('[data-message-id="' + pin.id + '"]');
                    } catch (error) {
                        window.alert(error.message);
                        return;
                    }
                }
                if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
            });
            pinnedList.append(button);
        }
        panel.classList.toggle("hidden", pins.length === 0);
    } catch (error) {
        panel.classList.add("hidden");
        chatSubtitle.textContent = "Не удалось загрузить закреплённые: " + error.message;
    }
}

function buildPollCard(poll) {
    const card = document.createElement("section");
    card.className = "poll-card";
    card.dataset.pollId = String(poll.id);
    const question = document.createElement("div");
    question.className = "poll-question";
    question.textContent = poll.question;
    card.append(question);
    const totalVotes = poll.options.reduce((total, option) => total + option.votes, 0);

    for (const option of poll.options) {
        const label = document.createElement("label");
        label.className = "poll-option";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = String(option.id);
        checkbox.checked = poll.selected.includes(option.id);
        const choice = document.createElement("span");
        choice.textContent = option.text + " (" + option.votes + ")";
        label.append(checkbox, choice);
        card.append(label);
        const bar = document.createElement("div");
        bar.className = "poll-bar";
        const fill = document.createElement("span");
        fill.style.width = (totalVotes ? option.votes / totalVotes * 100 : 0) + "%";
        bar.append(fill);
        card.append(bar);
    }

    const voteButton = document.createElement("button");
    voteButton.className = "poll-vote-button";
    voteButton.type = "button";
    voteButton.textContent = "Голосовать";
    voteButton.addEventListener("click", async function () {
        const optionIds = Array.from(card.querySelectorAll('input[type="checkbox"]:checked'))
            .map(input => Number(input.value));
        if (optionIds.length === 0) {
            window.alert("Выбери хотя бы один вариант.");
            return;
        }
        voteButton.disabled = true;
        try {
            await api("/api/polls/" + poll.id + "/vote", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ option_ids: optionIds })
            });
            await refreshPolls();
        } catch (error) {
            window.alert(error.message);
        } finally {
            voteButton.disabled = false;
        }
    });
    card.append(voteButton);
    const footer = document.createElement("div");
    footer.className = "poll-footer";
    footer.textContent = totalVotes + (totalVotes === 1 ? " голос" : " голосов") + " · можно выбрать несколько";
    card.append(footer);
    return card;
}

async function refreshPolls() {
    if (!activeChat) return;
    const polls = await api("/api/polls?with=" + encodeURIComponent(activeChat));
    for (const poll of polls) {
        const oldCard = messagesBox.querySelector('[data-poll-id="' + poll.id + '"]');
        if (oldCard) oldCard.replaceWith(buildPollCard(poll));
    }
}

async function loadMessages(initial) {
    if (!activeChat || loadingMessages) return;
    const requestedChat = activeChat;
    loadingMessages = true;
    try {
        const params = new URLSearchParams({
            with: requestedChat,
            after: initial ? "0" : String(lastMessageId)
        });
        const newMessages = await api("/api/messages?" + params.toString());
        if (requestedChat !== activeChat) return;
        for (const message of newMessages) {
            appendMessage(message);
            lastMessageId = Math.max(lastMessageId, message.id);
        }
        if (newMessages.length > 0) messagesBox.scrollTop = messagesBox.scrollHeight;
        if (newMessages.length > 0 && activeChatType === "user") {
            loadRecentChats().catch(error => {
                recentList.textContent = error.message;
            });
        }
        if (!initial && Date.now() - lastMetadataRefreshAt >= 10_000) {
            await refreshVisibleMessages(requestedChat);
            lastMetadataRefreshAt = Date.now();
        }
        if (!initial) await refreshPolls();
        chatSubtitle.textContent = activeChatType === "group"
            ? "Группа · " + (activeGroup ? activeGroup.member_count : 1) + " участников"
            : "Личные сообщения";
    } catch (error) {
        if (error.message === "Нужно войти в аккаунт.") showAuth(error.message);
        else if (requestedChat === activeChat) {
            chatSubtitle.textContent = "Не удалось обновить: " + error.message;
        }
    } finally {
        loadingMessages = false;
        if (activeChat) {
            if (pollTimer) window.clearTimeout(pollTimer);
            pollTimer = window.setTimeout(function () { loadMessages(false); }, 1200);
        }
    }
}

messageForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    const text = messageInput.value.trim();
    if (!text || !activeChat) return;
    stopTyping();
    const submit = messageForm.querySelector("button");
    submit.disabled = true;
    try {
        const payload = { to: activeChat, text };
        if (replyToMessage) payload.reply_to_id = replyToMessage.id;
        await api("/api/messages", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        messageInput.value = "";
        clearReply();
        await loadMessages(false);
        const selectedUser = knownUsers.find(user => user.username === activeChat);
        if (selectedUser) selectedUser.last_message = text;
        if (activeChatType === "user") await loadRecentChats();
        renderUsers();
        messageInput.focus();
    } catch (error) {
        window.alert(error.message);
    } finally {
        submit.disabled = false;
    }
});
document.getElementById("replyCancel").addEventListener("click", clearReply);

document.getElementById("userSearch").addEventListener("input", function () {
    if (searchTimer) window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(searchUsers, 250);
});
for (const emoji of MESSAGE_EMOJIS) {
    const emojiButton = document.createElement("button");
    emojiButton.type = "button";
    emojiButton.textContent = emoji;
    emojiButton.setAttribute("aria-label", "Вставить " + emoji);
    emojiButton.addEventListener("click", function () {
        const start = messageInput.selectionStart;
        const end = messageInput.selectionEnd;
        messageInput.setRangeText(emoji, start, end, "end");
        messageInput.focus();
    });
    emojiPicker.append(emojiButton);
}
document.getElementById("emojiToggle").addEventListener("click", function () {
    emojiPicker.classList.toggle("hidden");
});
messageInput.addEventListener("input", function () {
    handleTypingInput();
});
messageInput.addEventListener("blur", stopTyping);
document.getElementById("pollOpen").addEventListener("click", function () {
    if (!activeChat) return;
    document.getElementById("pollStatus").textContent = "";
    document.getElementById("pollForm").reset();
    pollDialog.showModal();
});
document.getElementById("pollClose").addEventListener("click", function () {
    pollDialog.close();
});
document.getElementById("pollForm").addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!activeChat) return;
    const optionInputs = Array.from(document.querySelectorAll(".poll-option-input"));
    const options = optionInputs.map(input => input.value.trim()).filter(Boolean);
    const question = document.getElementById("pollQuestion").value.trim();
    if (options.length < 2) {
        document.getElementById("pollStatus").textContent = "Добавь хотя бы два варианта.";
        return;
    }
    try {
        await api("/api/polls", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ to: activeChat, question, options })
        });
        pollDialog.close();
        await loadMessages(false);
    } catch (error) {
        document.getElementById("pollStatus").textContent = error.message;
    }
});

async function uploadMedia(file) {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
        window.alert("Файл больше 20 МБ.");
        mediaInput.value = "";
        return;
    }
    if (!file.type.startsWith("image/") && !file.type.startsWith("video/")) {
        window.alert("Можно отправлять только фото или видео.");
        mediaInput.value = "";
        return;
    }
    if (!activeChat) {
        window.alert("Сначала открой чат с собеседником.");
        mediaInput.value = "";
        return;
    }

    const reader = new FileReader();
    reader.onerror = function () {
        window.alert("Не удалось прочитать файл.");
        mediaInput.value = "";
    };
    reader.onload = async function () {
        try {
            const encoded = String(reader.result).split(",", 2)[1];
            await api("/api/media", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    to: activeChat,
                    name: file.name,
                    mime: file.type,
                    data: encoded
                })
            });
            await loadMessages(false);
        } catch (error) {
            window.alert(error.message);
        } finally {
            mediaInput.value = "";
        }
    };
    reader.readAsDataURL(file);
}
mediaInput.addEventListener("change", function () {
    uploadMedia(this.files[0]);
});

function stopVoiceRecording(discard) {
    voiceCancelled = Boolean(discard);
    if (voiceLimitTimer) window.clearTimeout(voiceLimitTimer);
    voiceLimitTimer = null;
    if (voiceRecorder && voiceRecorder.state !== "inactive") {
        voiceRecorder.stop();
    } else if (voiceStream) {
        voiceStream.getTracks().forEach(track => track.stop());
        voiceStream = null;
    }
}

document.getElementById("voiceToggle").addEventListener("click", async function () {
    const button = this;
    if (voiceRecorder) {
        stopVoiceRecording(false);
        return;
    }
    if (!activeChat) {
        window.alert("Сначала открой чат.");
        return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia
        || typeof MediaRecorder === "undefined") {
        window.alert("Браузер не поддерживает запись аудио.");
        return;
    }
    const mimeType = ["audio/webm;codecs=opus", "audio/webm"]
        .find(type => MediaRecorder.isTypeSupported(type));
    if (!mimeType) {
        window.alert("Для голосовых сообщений нужен браузер с поддержкой аудио WebM.");
        return;
    }
    const targetConversation = activeChat;
    const recordingUserId = currentUser.id;
    try {
        voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (!currentUser || currentUser.id !== recordingUserId || activeChat !== targetConversation) {
            voiceStream.getTracks().forEach(track => track.stop());
            voiceStream = null;
            return;
        }
        const recorder = new MediaRecorder(voiceStream, { mimeType });
        const chunks = [];
        voiceRecorder = recorder;
        voiceConversation = targetConversation;
        voiceStartedAt = performance.now();
        voiceCancelled = false;
        recorder.addEventListener("dataavailable", function (event) {
            if (event.data.size) chunks.push(event.data);
        });
        recorder.addEventListener("error", function () {
            window.alert("Не удалось записать голосовое сообщение.");
            stopVoiceRecording(true);
        });
        recorder.addEventListener("stop", async function () {
            const durationMs = Math.round(performance.now() - voiceStartedAt);
            const blob = new Blob(chunks, { type: recorder.mimeType || mimeType });
            const recordedConversation = voiceConversation;
            if (voiceStream) voiceStream.getTracks().forEach(track => track.stop());
            voiceStream = null;
            voiceRecorder = null;
            voiceConversation = null;
            button.classList.remove("recording");
            button.textContent = "🎙";
            button.setAttribute("aria-label", "Записать голосовое сообщение");
            if (voiceCancelled || !blob.size) return;
            if (durationMs > 120_000) {
                window.alert("Голосовое сообщение не может быть длиннее 2 минут.");
                return;
            }
            const reader = new FileReader();
            reader.onerror = function () {
                window.alert("Не удалось подготовить запись к отправке.");
            };
            reader.onload = async function () {
                try {
                    await api("/api/voice", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            to: recordedConversation,
                            duration_ms: durationMs,
                            data: String(reader.result).split(",", 2)[1]
                        })
                    });
                    if (activeChat === recordedConversation) await loadMessages(false);
                } catch (error) {
                    window.alert(error.message);
                }
            };
            reader.readAsDataURL(blob);
        });
        recorder.start();
        button.classList.add("recording");
        button.textContent = "■";
        button.setAttribute("aria-label", "Остановить и отправить запись");
        voiceLimitTimer = window.setTimeout(function () {
            stopVoiceRecording(false);
        }, 120_000);
    } catch (error) {
        if (voiceStream) voiceStream.getTracks().forEach(track => track.stop());
        voiceStream = null;
        voiceRecorder = null;
        window.alert("Не удалось получить доступ к микрофону: " + error.message);
    }
});

document.getElementById("backButton").addEventListener("click", function () {
    chatApp.classList.remove("chat-open");
});

function setTheme(theme) {
    const selectedTheme = ["light", "dark", "cyberpunk"].includes(theme) ? theme : "light";
    document.body.classList.toggle("dark-theme", selectedTheme === "dark");
    document.body.classList.toggle("cyberpunk-theme", selectedTheme === "cyberpunk");
    document.body.dataset.theme = selectedTheme;
    themeToggle.textContent = selectedTheme === "cyberpunk"
        ? "☣"
        : selectedTheme === "dark" ? "☀️" : "🌙";
    themeToggle.setAttribute("aria-pressed", String(selectedTheme !== "light"));
    themeToggle.setAttribute(
        "aria-label",
        selectedTheme === "light" ? "Включить тёмную тему" : "Включить светлую тему"
    );
    localStorage.setItem("messenger-theme", selectedTheme);
    const themeSelect = document.getElementById("themeSelect");
    if (themeSelect) themeSelect.value = selectedTheme;
}

setTheme(localStorage.getItem("messenger-theme"));
themeToggle.addEventListener("click", function () {
    setTheme(document.body.dataset.theme === "light" ? "dark" : "light");
});
document.getElementById("themeSelect").addEventListener("change", function () {
    setTheme(this.value);
});

document.getElementById("commandOpen").addEventListener("click", function () {
    document.getElementById("commandStatus").textContent = "";
    commandDialog.showModal();
    commandInput.value = "";
    commandInput.focus();
});
document.getElementById("commandClose").addEventListener("click", function () {
    commandDialog.close();
});
commandDialog.addEventListener("click", function (event) {
    if (event.target === commandDialog) commandDialog.close();
});
document.getElementById("commandForm").addEventListener("submit", async function (event) {
    event.preventDefault();
    const input = commandInput.value.trim();
    const status = document.getElementById("commandStatus");
    if (!input.startsWith("/") || input.length > 200) {
        status.textContent = "Команда должна начинаться с /.";
        return;
    }
    const [command, ...args] = input.slice(1).split(/\s+/);
    const argument = args.join(" ");
    switch (command.toLowerCase()) {
        case "help":
            status.textContent =
                "/help · /theme light|dark|cyberpunk · /search имя или Digital ID · "
                + "/settings · /notifications · /pins · /back";
            break;
        case "theme":
            if (!["light", "dark", "cyberpunk"].includes(argument.toLowerCase())) {
                status.textContent = "Используй: /theme light, /theme dark или /theme cyberpunk.";
                return;
            }
            setTheme(argument.toLowerCase());
            status.textContent = "Тема изменена.";
            break;
        case "search":
            if (argument.length < 2) {
                status.textContent = "Напиши не меньше двух символов для поиска.";
                return;
            }
            document.getElementById("userSearch").value = argument;
            commandDialog.close();
            await searchUsers();
            break;
        case "settings":
            commandDialog.close();
            document.getElementById("settingsOpen").click();
            break;
        case "notifications":
            commandDialog.close();
            document.getElementById("settingsOpen").click();
            window.setTimeout(function () {
                document.getElementById("notificationSettings").scrollIntoView({
                    behavior: "smooth",
                    block: "center"
                });
            }, 100);
            break;
        case "pins":
            if (!activeChat) {
                status.textContent = "Сначала открой чат.";
                return;
            }
            await loadPinnedMessages();
            status.textContent = "Закреплённые сообщения обновлены.";
            break;
        case "back":
            chatApp.classList.remove("chat-open");
            commandDialog.close();
            break;
        default:
            status.textContent = "Неизвестная команда. Введи /help.";
    }
});

const savedFontSize = localStorage.getItem("messenger-font-size");
document.documentElement.dataset.fontSize =
    ["small", "medium", "large"].includes(savedFontSize) ? savedFontSize : "medium";
const fontSizeSelect = document.getElementById("fontSizeSelect");
fontSizeSelect.value = document.documentElement.dataset.fontSize;
fontSizeSelect.addEventListener("change", function () {
    document.documentElement.dataset.fontSize = this.value;
    localStorage.setItem("messenger-font-size", this.value);
});

document.getElementById("settingsOpen").addEventListener("click", async function () {
    profileStatus.textContent = "";
    notificationsStatus.textContent = "";
    document.getElementById("passwordStatus").textContent = "";
    try {
        currentUser = await api("/api/me");
        displayNameInput.value = currentUser.display_name || currentUser.username;
        renderAvatar(profileAvatar, currentUser);
        document.getElementById("digitalIdValue").textContent = currentUser.digital_id || "";
        settingsDialog.showModal();
    } catch (error) {
        showAuth(error.message);
    }
});
document.getElementById("settingsClose").addEventListener("click", function () {
    settingsDialog.close();
});
settingsDialog.addEventListener("click", function (event) {
    if (event.target === settingsDialog) settingsDialog.close();
});

document.getElementById("digitalIdCopy").addEventListener("click", async function () {
    const digitalId = document.getElementById("digitalIdValue").textContent;
    if (!digitalId) return;
    try {
        await navigator.clipboard.writeText(digitalId);
        profileStatus.textContent = "Digital ID скопирован.";
    } catch (error) {
        profileStatus.textContent = "Не удалось скопировать. Выдели ID и скопируй вручную.";
    }
});

document.getElementById("saveProfile").addEventListener("click", async function () {
    const displayName = displayNameInput.value.trim();
    if (!displayName || displayName.length > 30) {
        profileStatus.textContent = "Имя должно содержать от 1 до 30 символов.";
        return;
    }
    try {
        const user = await api("/api/profile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ display_name: displayName })
        });
        currentUser = user;
        profileStatus.textContent = "Имя сохранено.";
        if (activeChat) {
            const selectedUser = knownUsers.find(item => item.username === activeChat);
            if (selectedUser) chatTitle.textContent = selectedUser.display_name || selectedUser.username;
        }
        searchUsers();
    } catch (error) {
        profileStatus.textContent = error.message;
    }
});

async function encodeAvatar(file) {
    const sourceUrl = URL.createObjectURL(file);
    try {
        const image = new Image();
        await new Promise((resolve, reject) => {
            image.addEventListener("load", resolve, { once: true });
            image.addEventListener("error", reject, { once: true });
            image.src = sourceUrl;
        });
        let width = image.naturalWidth;
        let height = image.naturalHeight;
        if (!width || !height) throw new Error("Не удалось открыть изображение.");

        const scale = Math.min(1, 512 / Math.max(width, height));
        width = Math.max(1, Math.round(width * scale));
        height = Math.max(1, Math.round(height * scale));
        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d");
        if (!context) throw new Error("Браузер не смог обработать изображение.");

        let blob = null;
        while (width >= 64 && height >= 64) {
            canvas.width = width;
            canvas.height = height;
            context.drawImage(image, 0, 0, width, height);
            for (const quality of [0.82, 0.68, 0.54]) {
                blob = await new Promise(resolve =>
                    canvas.toBlob(resolve, "image/jpeg", quality)
                );
                if (blob && blob.size <= 512 * 1024) break;
            }
            if (blob && blob.size <= 512 * 1024) break;
            width = Math.round(width * 0.8);
            height = Math.round(height * 0.8);
        }
        if (!blob || blob.size > 512 * 1024) {
            throw new Error("Не удалось уменьшить фото до 512 КБ.");
        }

        return await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.addEventListener("load", function () {
                const result = String(reader.result);
                resolve(result.slice(result.indexOf(",") + 1));
            }, { once: true });
            reader.addEventListener("error", reject, { once: true });
            reader.readAsDataURL(blob);
        });
    } finally {
        URL.revokeObjectURL(sourceUrl);
    }
}

avatarInput.addEventListener("change", async function () {
    const file = this.files[0];
    if (!file) return;
    profileStatus.textContent = "Обрабатываю изображение...";
    try {
        const encoded = await encodeAvatar(file);
        await api("/api/avatar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ data: encoded })
        });
        currentUser.has_avatar = true;
        renderAvatar(profileAvatar, currentUser);
        if (activeChatType === "user" && activeChat === currentUser.username) {
            renderAvatar(chatAvatar, currentUser);
        }
        profileStatus.textContent = "Фото профиля обновлено.";
    } catch (error) {
        profileStatus.textContent = error.message || "Не удалось обработать изображение.";
    } finally {
        this.value = "";
    }
});

document.getElementById("avatarRemove").addEventListener("click", async function () {
    if (!currentUser || !currentUser.has_avatar) return;
    try {
        await api("/api/avatar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ remove: true })
        });
        currentUser.has_avatar = false;
        renderAvatar(profileAvatar, currentUser);
        if (activeChatType === "user" && activeChat === currentUser.username) {
            renderAvatar(chatAvatar, currentUser);
        }
        profileStatus.textContent = "Фото профиля удалено.";
    } catch (error) {
        profileStatus.textContent = error.message;
    }
});

function updateNotificationsButton() {
    const enabled = localStorage.getItem("messenger-notifications") === "true";
    notificationsToggle.textContent = enabled ? "Выключить уведомления" : "Включить уведомления";
    if (!("Notification" in window)) {
        notificationsToggle.disabled = true;
        notificationsStatus.textContent = "Этот браузер не поддерживает уведомления.";
    } else if (Notification.permission === "denied") {
        notificationsToggle.disabled = true;
        notificationsStatus.textContent = "Уведомления запрещены в настройках браузера.";
    } else {
        notificationsToggle.disabled = false;
        notificationsStatus.textContent = enabled ? "Уведомления включены." : "";
    }
}

notificationsToggle.addEventListener("click", async function () {
    if (localStorage.getItem("messenger-notifications") === "true") {
        localStorage.setItem("messenger-notifications", "false");
        updateNotificationsButton();
        return;
    }
    if (!("Notification" in window)) {
        updateNotificationsButton();
        return;
    }
    try {
        const permission = Notification.permission === "default"
            ? await Notification.requestPermission()
            : Notification.permission;
        if (permission === "granted") {
            localStorage.setItem("messenger-notifications", "true");
        } else {
            notificationsStatus.textContent = "Браузер не разрешил уведомления.";
        }
    } catch (error) {
        notificationsStatus.textContent = "Не удалось запросить разрешение уведомлений.";
    }
    updateNotificationsButton();
});

function notifyNewMessage(message) {
    if (document.visibilityState === "visible"
        || localStorage.getItem("messenger-notifications") !== "true"
        || !("Notification" in window)
        || Notification.permission !== "granted") {
        return;
    }
    const body = message.deleted
        ? "Сообщение удалено"
        : message.kind === "media"
            ? "Отправлено фото или видео"
            : message.kind === "voice"
                ? "Получено голосовое сообщение"
                : message.kind === "poll"
                    ? "Новый опрос"
                    : message.text;
    const notification = new Notification(message.conversation_name || message.sender_display, {
        body,
        tag: "message-" + message.id
    });
    notification.onclick = async function () {
        window.focus();
        notification.close();
        if (message.conversation_id.startsWith("group:")) {
            try {
                if (!knownGroups.length) await loadGroups();
                const groupId = Number(message.conversation_id.slice(6));
                const group = knownGroups.find(item => item.id === groupId);
                if (group) await openGroup(group);
            } catch (error) {
                window.alert(error.message);
            }
        } else {
            await openChat(message.conversation_id, message.conversation_name);
        }
    };
}

function unreadStorageKey() {
    return currentUser ? "messenger-unread-" + currentUser.id : "";
}

function saveUnreadCounts() {
    if (!currentUser) return;
    localStorage.setItem(unreadStorageKey(), JSON.stringify(unreadCounts));
    updateUnreadBadge();
}

function loadUnreadCounts() {
    unreadCounts = {};
    if (!currentUser) return;
    try {
        const saved = JSON.parse(localStorage.getItem(unreadStorageKey()) || "{}");
        if (saved && typeof saved === "object" && !Array.isArray(saved)) {
            for (const [conversation, count] of Object.entries(saved)) {
                if (Number.isSafeInteger(count) && count > 0) unreadCounts[conversation] = count;
            }
        }
    } catch (error) {
        console.error("Не удалось прочитать счётчики непрочитанных сообщений:", error);
    }
    updateUnreadBadge();
}

function updateUnreadBadge() {
    const count = Object.values(unreadCounts).reduce((total, value) => total + value, 0);
    const badge = document.getElementById("unreadBadge");
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.classList.toggle("hidden", count === 0);
    document.title = count ? "(" + count + ") Мессенджер" : "Мессенджер";
}

function markConversationRead(conversationId) {
    if (!conversationId || !unreadCounts[conversationId]) return;
    delete unreadCounts[conversationId];
    saveUnreadCounts();
}

async function pollNotifications() {
    if (!currentUser) return;
    try {
        const cursorKey = "messenger-notification-cursor-" + currentUser.id;
        let cursor = Number(localStorage.getItem(cursorKey));
        if (!Number.isSafeInteger(cursor) || cursor < 0) {
            const initial = await api("/api/notifications?initial=1");
            cursor = initial.cursor;
            localStorage.setItem(cursorKey, String(cursor));
        }
        const result = await api("/api/notifications?after=" + cursor);
        let unreadChanged = false;
        for (const message of result.messages) {
            if (message.sender === currentUser.username) continue;
            if (activeChat === message.conversation_id
                && document.visibilityState === "visible") {
                if (unreadCounts[message.conversation_id]) {
                    delete unreadCounts[message.conversation_id];
                    unreadChanged = true;
                }
                continue;
            }
            unreadCounts[message.conversation_id] =
                (unreadCounts[message.conversation_id] || 0) + 1;
            unreadChanged = true;
            notifyNewMessage(message);
        }
        if (result.cursor > cursor) {
            localStorage.setItem(cursorKey, String(result.cursor));
        }
        if (unreadChanged) saveUnreadCounts();
    } catch (error) {
        if (error.message === "Нужно войти в аккаунт.") showAuth(error.message);
        else {
            console.error("Не удалось обновить уведомления:", error);
            if (settingsDialog.open) notificationsStatus.textContent = error.message;
        }
    } finally {
        if (currentUser) {
            notificationTimer = window.setTimeout(pollNotifications, 3000);
        }
    }
}

document.getElementById("changePassword").addEventListener("click", async function () {
    const oldPassword = document.getElementById("oldPasswordInput").value;
    const newPassword = document.getElementById("newPasswordInput").value;
    const confirmation = document.getElementById("confirmPasswordInput").value;
    const status = document.getElementById("passwordStatus");
    if (newPassword.length < 8 || newPassword.length > 128) {
        status.textContent = "Новый пароль должен содержать от 8 до 128 символов.";
        return;
    }
    if (newPassword !== confirmation) {
        status.textContent = "Новые пароли не совпадают.";
        return;
    }
    try {
        await api("/api/password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ old_password: oldPassword, new_password: newPassword })
        });
        document.getElementById("oldPasswordInput").value = "";
        document.getElementById("newPasswordInput").value = "";
        document.getElementById("confirmPasswordInput").value = "";
        status.textContent = "Пароль изменён.";
    } catch (error) {
        status.textContent = error.message;
    }
});

document.getElementById("logoutButton").addEventListener("click", async function () {
    try {
        await api("/api/logout", { method: "POST" });
    } finally {
        showAuth("Ты вышел из аккаунта.");
    }
});

api("/api/me")
    .then(showChatApp)
    .catch(function () { showAuth(""); });
