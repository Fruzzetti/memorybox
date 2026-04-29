const userInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const resultsArea = document.getElementById('results-area');

// [v1.1.0] Identity & Vault State Management
let activeProfile = null;

/** 
 * [v1.6.4] Deterministic Identity Colors 
 * Generates a consistent, vibrant archival color based on the identity name.
 */
function getProfileColor(name) {
    if (!name) return 'var(--accent-primary)';
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
        hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    const h = Math.abs(hash % 360);
    // HSL: S=65% (Rich), L=45% (Meaty/Legible)
    return `hsl(${h}, 65%, 45%)`;
}
let profileToLogin = null;
let currentPinBuffer = "";
let conversationHistory = [];
let abortController = null;
let emptyVaultUXTriggered = false; // [v1.6.3] Track if we've handled the empty-vault splash

// Auto-resize textarea
if (userInput) {
    userInput.addEventListener('input', () => {
        userInput.style.height = 'auto';
        userInput.style.height = userInput.scrollHeight + 'px';
    });
}

window.addEventListener('DOMContentLoaded', () => {
    restoreTheme();
    checkVaultStatus();
    initDiagnostics();
    startIngestionPolling(); // [v1.2.0] Deep Sensing Init
});

function initDiagnostics() {
    // Brand Icon as the hidden trigger
    const brandIcon = document.getElementById('brandIcon');
    if (brandIcon) {
        brandIcon.onclick = () => toggleDiagnostics();
    }
    
    updateMemoryStats();
}

function toggleDiagnostics() {
    const diagPanel = document.getElementById('diagnosticPanel');
    if (diagPanel) {
        diagPanel.style.display = diagPanel.style.display === 'none' ? 'block' : 'none';
        if (diagPanel.style.display === 'block') updateMemoryStats();
    }
}

async function toggleGovernance() {
    const govPanel = document.getElementById('governancePanel');
    if (!govPanel) return;
    
    const isOpening = govPanel.style.display !== 'flex';
    govPanel.style.display = isOpening ? 'flex' : 'none';
    
    if (isOpening) {
        await fetchGovernanceData();
    }
}

async function fetchGovernanceData() {
    try {
        const res = await fetchWithAuth('api/governance/status');
        const data = await res.json();
        
        if (data.identity) {
            document.getElementById('gov-display-name').innerText = data.identity.name;
            document.getElementById('gov-legal-name').value = data.identity.legal_name || '';
            document.getElementById('gov-mimicry-toggle').checked = data.identity.mimicry;
            document.getElementById('gov-user-role').innerText = data.identity.role;
        }
        
        if (data.session) {
            document.getElementById('gov-session-ip').innerText = data.session.ip;
        }
    } catch (e) {
        console.error("Governance Pulse Failed:", e);
    }
}

async function saveGovernanceIdentity() {
    const btn = document.getElementById('gov-save-btn');
    const originalText = btn.innerText;
    btn.innerText = "PERSISTING...";
    btn.disabled = true;

    try {
        const legal_name = document.getElementById('gov-legal-name').value;
        const mimicry = document.getElementById('gov-mimicry-toggle').checked;
        
        const res = await fetchWithAuth('api/governance/update_identity', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ legal_name, mimicry })
        });
        
        const data = await res.json();
        if (data.status === 'success') {
            btn.innerText = "GOVERNANCE SOLIDIFIED";
            setTimeout(() => {
                btn.innerText = originalText;
                btn.disabled = false;
            }, 2000);
        } else {
            throw new Error(data.message);
        }
    } catch (e) {
        btn.innerText = "FAILED";
        btn.disabled = false;
        console.error("Governance Identity Save Failed:", e);
    }
}

// [v1.6.2] Hardware-Anchored Session Wrapper
async function fetchWithAuth(url, options = {}) {
    const token = localStorage.getItem('memorybox_token');
    const profileRaw = localStorage.getItem('memorybox_profile');
    if (token && profileRaw) {
        const profile = JSON.parse(profileRaw);
        if (!options.headers) options.headers = {};
        options.headers['X-MemoryBox-Token'] = token;
        options.headers['X-MemoryBox-User-ID'] = profile.id;
    }
    return fetch(url, options);
}

function setTheme(themeName, btn = null, persist = true) {
    const themes = ['theme-noir', 'theme-solaris', 'theme-deepsea', 'theme-timeless', 'theme-botanical', 'theme-modern', 'theme-graphical'];
    document.body.classList.remove(...themes);
    document.body.classList.add(`theme-${themeName}`);
    localStorage.setItem('memorybox_theme', themeName);

    // [v1.1.0] Persist to profile if authenticated
    if (persist && activeProfile) {
        fetchWithAuth('api/profiles/update_theme', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: activeProfile.id, theme: themeName })
        }).catch(err => console.error("Theme persistence failed:", err));
    }
    
    // Update Brand Identity
    const brandIcon = document.getElementById('brandIcon');
    if (brandIcon) {
        const v = "?v=4";
        if (themeName === 'timeless') brandIcon.src = 'static/assets/icon_chest.png' + v;
        else if (themeName === 'botanical') brandIcon.src = 'static/assets/icon_cabinet.png' + v;
        else brandIcon.src = 'static/assets/icon_safe.png' + v;
    }
    
    document.querySelectorAll('.legend-item').forEach(item => {
        item.classList.remove('active');
        if (item.getAttribute('onclick') && item.getAttribute('onclick').includes(`'${themeName}'`)) {
            item.classList.add('active');
        }
    });
}

function restoreTheme() {
    const saved = localStorage.getItem('memorybox_theme') || 'modern';
    setTheme(saved);
}

async function checkVaultStatus() {
    try {
        const res = await fetch('api/vault/status');
        const data = await res.json();
        
        const sealedOverlay = document.getElementById('vault-sealed-overlay');
        
        // [v1.2.0] Continuity Heartbeat
        updateWipeBanner(data.wipe_status, data.wipe_meta);

        if (data.sealed) {
            sealedOverlay.style.display = 'flex';
        } else {
            sealedOverlay.style.display = 'none';
            
            // [v1.2.9] Vault Mount Check
            const diagRes = await fetchWithAuth('api/diagnostic/vault');
            
            // [v1.8.8] Integrity Check: If the diagnostic endpoint returns 401, our token is stale.
            if (diagRes.status === 401) {
                console.warn("[VAULT] Session token rejected. Purging stale identity.");
                activeProfile = null; // [v1.8.8] Force clear memory state
                localStorage.removeItem('memorybox_profile');
                localStorage.removeItem('memorybox_token');
                showIdentityHub();
                return;
            }

            const diagData = await diagRes.json();
            const statsInfo = document.getElementById('statsInfo');
            if (diagData.mount_status === 'DETACHED' && statsInfo) {
                statsInfo.innerHTML = `<span style="color: #ff4d4d; font-weight: bold;">⚠️ VAULT STORAGE DETACHED</span>`;
                activeProfile = null;
                showIdentityHub(); 
                return;
            }

            // [v1.5.0] Smart Identity Handshake: Reconcile session with database
            const savedRaw = localStorage.getItem('memorybox_profile');
            let sessionIsValid = false;

            if (savedRaw) {
                const savedProfile = JSON.parse(savedRaw);
                const token = localStorage.getItem('memorybox_token');
                
                if (!token) {
                    sessionIsValid = false;
                } else {
                    try {
                        const profilesRes = await fetchWithAuth('api/profiles');
                        if (profilesRes.status === 401) {
                            sessionIsValid = false;
                        } else {
                            const profiles = await profilesRes.json();
                            sessionIsValid = Array.isArray(profiles) && profiles.some(p => p.id === savedProfile.id);
                        }
                    } catch (e) { 
                        console.warn("Profile verification failed.");
                        sessionIsValid = false; 
                    }
                }
            }

            if (sessionIsValid) {
                // [v1.8.8] Rationale: Only hydrate after all handshakes (diag + profiles) pass.
                activeProfile = JSON.parse(savedRaw);
                updateActiveUserBadge();
                
                const vUI = document.getElementById('vault-ui');
                if (vUI) vUI.style.display = 'flex';
                
                // [v1.6.6] Sovereign Handshake: Pull live stats immediately on unseal
                updateMemoryStats();
                startIngestionPolling();

                if (curationMode === null) {
                    const savedMode = localStorage.getItem('memorybox_curationMode') || 'visual';
                    setCurationMode(savedMode);
                }
                
                // Hide any overlays
                const overlay = document.getElementById('identity-overlay');
                if (overlay) overlay.style.display = 'none';

            } else {
                // Ghost Session or No Session
                activeProfile = null; // Ensure clean state
                const vUI = document.getElementById('vault-ui');
                if (vUI) vUI.style.display = 'none';

                if (data.user_registry_empty) {
                    showOnboarding();
                } else {
                    localStorage.removeItem('memorybox_profile');
                    localStorage.removeItem('memorybox_token');
                    showIdentityHub();
                }
            }
        }
    } catch (e) { 
        console.error("Vault check failed:", e); 
        activeProfile = null;
        const vUI = document.getElementById('vault-ui');
        if (vUI) vUI.style.display = 'none';
        showIdentityHub(); 
    }
}

// --- Deep Sensing Hub [v1.2.0] ---
let sensingPollInterval = null;
let lastSensingState = "IDLE";

function startIngestionPolling() {
    if (sensingPollInterval) return;
    sensingPollInterval = setInterval(pollIngestionStatus, 5000);
}

async function pollIngestionStatus() {
    try {
        const res = await fetchWithAuth('api/ingestion/status');
        const data = await res.json();
        
        const monitor = document.getElementById('deep-sensing-monitor');
        const title = document.getElementById('sensing-title');
        const file = document.getElementById('sensing-file');
        const bar = document.getElementById('sensing-progress-fill');

        if (data.state && data.state !== 'IDLE' && data.state !== 'DONE' && data.state !== 'COMPLETED') {
            monitor.classList.add('active');
            title.innerText = `Deep Sensing: ${data.state}`;
            file.innerText = data.current_file || "Analyzing...";
            const progress = data.progress || 0;
            bar.style.width = `${progress}%`;
            lastSensingState = data.state;

            // Update individual card if visible
            if (data.current_file) {
                const cards = document.querySelectorAll('.ingest-item');
                cards.forEach(card => {
                    const nameLabel = card.querySelector('.file-name-label');
                    if (nameLabel && nameLabel.innerText.includes(data.current_file)) {
                        const cardStatus = card.querySelector('.status') || card.querySelector('.file-name-label').nextElementSibling;
                        if (cardStatus) {
                            cardStatus.innerText = data.state;
                            cardStatus.style.color = "var(--accent-primary)";
                        }
                        const cardBar = card.querySelector('.ingest-bar') || card.querySelector('.ingest-progress .ingest-bar');
                        if (cardBar) {
                            cardBar.parentElement.style.display = 'block';
                            cardBar.style.width = `${progress}%`;
                        }
                    }
                });
            }
        } else {
            if (lastSensingState !== 'IDLE') {
                // [v1.2.6] Success State: Success Banner then Fade
                title.innerText = "Vault Re-Indexed";
                file.innerText = "Deep Resolution Complete.";
                bar.style.width = "100%";
                
                // Refresh local session data
                fetch('api/archive/refresh', { method: 'POST' });
                // [v1.3.0] Revision Lock: Ingestion completion should NOT reset the curation bench.
                // We only refresh the stats, keeping the current batch stable.
                console.log("[SENSING] Pass complete. Refreshing system stats, keeping curation bench stable.");
                
                lastSensingState = "IDLE";
                setTimeout(() => {
                    monitor.classList.remove('active');
                }, 3000);
            } else {
                monitor.classList.remove('active');
            }
        }
    } catch (e) {
        console.warn("Sensing poll failed.");
    }
}

// Global Governance Polling [v1.2.0]
setInterval(checkVaultStatus, 30000); // Check every 30s

async function unsealVault() {
    const key = document.getElementById('appliance-key-input').value;
    const errorEl = document.getElementById('unseal-error');
    if (!key) return;

    try {
        const res = await fetch('api/vault/unseal', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ key })
        });
        const data = await res.json();
        if (data.status === 'success') {
            document.getElementById('vault-sealed-overlay').style.display = 'none';
            // [v1.2.6] Forced Refresh: Bypass polling delay on unseal
            checkVaultStatus(); 
            showIdentityHub();
        } else {
            errorEl.innerText = data.message;
            errorEl.style.display = 'block';
        }
    } catch (e) {
        errorEl.innerText = "Connection failed.";
        errorEl.style.display = 'block';
    }
}

async function showIdentityHub() {
    const overlay = document.getElementById('identity-overlay');
    const portal = document.getElementById('direct-auth-portal');
    const gridWrap = document.getElementById('profiles-grid-container');
    const profilesList = document.getElementById('profiles-list');
    const createForm = document.getElementById('create-profile-container');
    const pinMgmt = document.getElementById('pin-management-container');
    
    overlay.style.display = 'flex';
    if (gridWrap) gridWrap.style.display = 'none';
    if (createForm) createForm.style.display = 'none';
    if (pinMgmt) pinMgmt.style.display = 'none';
    if (portal) portal.style.display = 'none';
    
    // Reset any previous errors
    document.getElementById('login-error').style.display = 'none';

    if (!activeProfile) {
        // [v1.1.0] Strictly Isolated Login Portal
        overlay.classList.add('portal-mode');
        portal.style.display = 'block';
        if (gridWrap) gridWrap.style.display = 'none';
        if (createForm) createForm.style.display = 'none';
        
        // Focus name field for tactical entry
        document.getElementById('portal-name-input').value = '';
        document.getElementById('portal-pin-input').value = '';
        const cancelBtn = document.getElementById('portal-cancel-btn');
        if (cancelBtn) cancelBtn.style.display = 'none';
        
        setTimeout(() => document.getElementById('portal-name-input').focus(), 100);
    } else {
        // Authenticated Management View
        overlay.classList.remove('portal-mode');
        gridWrap.style.display = 'block';
        profilesList.style.display = 'grid';

        profilesList.innerHTML = '<p style="grid-column: 1/-1;">Recalling Archival Identities...</p>';

        try {
            const res = await fetchWithAuth('api/profiles');
            const profiles = await res.json();
            profilesList.innerHTML = '';
            
            profiles.forEach(p => {
                const isActive = activeProfile && activeProfile.id === p.id;
                const canReset = (activeProfile.role === 'SUPERADMIN' && p.role !== 'SUPERADMIN') || (activeProfile.role === 'ADMIN' && p.role === 'USER');

                const displayRole = p.role === 'ADMIN' ? 'ADMINISTRATOR' : (p.role === 'SUPERADMIN' ? 'SUPER-ADMINISTRATOR' : p.role);

                const userColor = getProfileColor(p.name);
                const tile = document.createElement('div');
                tile.className = `profile-tile ${isActive ? 'active' : ''}`;
                tile.style.position = 'relative';
                tile.innerHTML = `
                    <div class="profile-avatar" style="background: ${userColor}; box-shadow: 0 0 15px ${userColor}66;">${p.name[0].toUpperCase()}</div>
                    <div style="font-weight: 600; font-size: 0.9rem;">${p.name.toUpperCase()}</div>
                    <div style="font-size: 0.7rem; opacity: 0.5;">${displayRole}</div>
                    ${isActive ? '<div class="resume-badge">RESUME SESSION</div>' : ''}
                `;

                if (isActive || canReset) {
                    const settingsBtn = document.createElement('div');
                    settingsBtn.innerHTML = '⚙️';
                    settingsBtn.style = "position:absolute; top:5px; right:5px; padding:5px; cursor:pointer; font-size:12px; opacity:0.6;";
                    settingsBtn.title = isActive ? "Change PIN" : "Administrative Reset";
                    settingsBtn.onclick = (e) => {
                        e.stopPropagation();
                        showPinManagement(p, !isActive);
                    };
                    tile.appendChild(settingsBtn);
                }

                tile.onclick = isActive ? hideIdentityHub : () => selectProfileForLogin(p);
                profilesList.appendChild(tile);
            });

            // [v1.2.6] Identity Hub expansion: Admins can add standard Users
            if (activeProfile.role === 'SUPERADMIN' || activeProfile.role === 'ADMIN') {
                const plus = document.createElement('div');
                plus.className = 'profile-tile';
                plus.style.border = '2px dashed var(--accent-primary)';
                plus.style.opacity = '0.6';
                plus.style.display = 'flex';
                plus.style.flexDirection = 'column';
                plus.style.alignItems = 'center';
                plus.style.justifyContent = 'center';
                plus.innerHTML = `
                    <div class="profile-avatar" style="background:transparent; color:var(--accent-primary); font-size:2rem; margin-bottom:10px;">+</div>
                    <div style="font-weight: 600; font-size: 0.8rem; color:var(--accent-primary);">ADD USER</div>
                `;
                plus.onclick = showCreateProfile;
                profilesList.appendChild(plus);
            }
        } catch (e) { 
            profilesList.innerHTML = '<p style="color:red;">Error loading profiles.</p>';
        }
    }
}

function showOnboarding() {
    const overlay = document.getElementById('identity-overlay');
    const portal = document.getElementById('direct-auth-portal');
    const gridWrap = document.getElementById('profiles-grid-container');
    const createForm = document.getElementById('create-profile-container');
    
    overlay.style.display = 'flex';
    overlay.classList.add('portal-mode');
    
    if (gridWrap) gridWrap.style.display = 'none';
    if (portal) portal.style.display = 'none';
    
    if (createForm) {
        createForm.style.display = 'block';
        const title = createForm.querySelector('h3');
        if (title) title.innerText = "CREATE SUPER-ADMINISTRATOR";
        
        // Lock role to SUPERADMIN for the first user
        const roleSel = document.getElementById('new-profile-role');
        if (roleSel) {
            roleSel.value = 'SUPERADMIN';
            roleSel.disabled = true;
            roleSel.style.opacity = '0.3';
        }
        
        document.getElementById('new-profile-name').value = '';
        document.getElementById('new-profile-pin').value = '';
        document.getElementById('new-profile-name').focus();
    }
}

function showCreateProfile() {
    const gridWrap = document.getElementById('profiles-grid-container');
    const createForm = document.getElementById('create-profile-container');
    
    if (gridWrap) gridWrap.style.display = 'none';
    if (createForm) {
        createForm.style.display = 'block';
        const title = createForm.querySelector('h3');
        if (title) title.innerText = "Ingest New Archival Identity";
        
        const roleSel = document.getElementById('new-profile-role');
        if (roleSel) {
            // [v1.5.0] Hierarchical Role Filtering
            const opts = Array.from(roleSel.options);
            
            // 1. SuperAdmin is never a selectable option here (Vault Owner singleton)
            opts.forEach(opt => {
                if (opt.value === 'SUPERADMIN') opt.style.display = 'none';
                else opt.style.display = 'block';
            });

            if (activeProfile.role === 'SUPERADMIN') {
                roleSel.value = 'ADMIN';
                roleSel.disabled = false;
                roleSel.style.opacity = '1';
            } else if (activeProfile.role === 'ADMIN') {
                roleSel.value = 'USER';
                roleSel.disabled = true;
                roleSel.style.opacity = '0.3';
            }
        }
        
        // Reset fields
        document.getElementById('new-profile-name').value = '';
        document.getElementById('new-profile-pin').value = '';
        const err = document.getElementById('create-error');
        if (err) err.style.display = 'none';
        document.getElementById('new-profile-name').focus();
    }
}

async function submitNewProfile() {
    const name = document.getElementById('new-profile-name').value.trim();
    const legal_name = document.getElementById('new-profile-legal-name').value.trim();
    const role = document.getElementById('new-profile-role').value;
    const pin = document.getElementById('new-profile-pin').value.trim();
    const errorEl = document.getElementById('create-error');
    
    errorEl.style.display = 'none';

    try {
        const res = await fetchWithAuth('api/profiles/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, legal_name, role, pin }) // [v1.6.3] Identity derived from token
        });
        
        let data;
        const contentType = res.headers.get("content-type");
        if (contentType && contentType.indexOf("application/json") !== -1) {
            data = await res.json();
        } else {
            const text = await res.text();
            throw new Error(`Server returned non-JSON response: ${text.substring(0, 100)}`);
        }
        
        if (data.status === 'success') {
            showIdentityHub(); // Refresh list & show grid
        } else {
            errorEl.innerText = data.message || "Archive rejection.";
            errorEl.style.display = 'block';
        }
    } catch (e) {
        console.error("Forge Error:", e);
        errorEl.innerText = `Forge failed: ${e.message || "Unknown error"}`;
        errorEl.style.display = 'block';
    }
}

let targetProfileForPin = null;
let isPinReset = false;

function showPinManagement(user, isReset) {
    targetProfileForPin = user;
    isPinReset = isReset;
    
    const gridWrap = document.getElementById('profiles-grid-container');
    const pinMgmt = document.getElementById('pin-management-container');
    
    if (gridWrap) gridWrap.style.display = 'none';
    if (pinMgmt) pinMgmt.style.display = 'block';
    
    document.getElementById('pin-mgmt-title').innerText = isReset ? `Reset PIN: ${user.name}` : `Change PIN: ${user.name}`;
    document.getElementById('current-pin-block').style.display = isReset ? 'none' : 'block';
    
    // Clear fields
    document.getElementById('change-pin-current').value = '';
    document.getElementById('change-pin-new').value = '';
    document.getElementById('change-pin-confirm').value = '';
    const err = document.getElementById('pin-mgmt-error');
    if (err) err.style.display = 'none';
}

async function submitPinChange() {
    const targetId = targetProfileForPin.id;
    const currentPin = document.getElementById('change-pin-current').value.trim();
    const newPin = document.getElementById('change-pin-new').value.trim();
    const confirmPin = document.getElementById('change-pin-confirm').value.trim();
    const errorEl = document.getElementById('pin-mgmt-error');
    
    if (newPin !== confirmPin) {
        errorEl.innerText = "New PIN tokens do not match.";
        errorEl.style.display = 'block';
        return;
    }
    
    if (newPin.length < 6) {
        errorEl.innerText = "New PIN must be 6+ characters.";
        errorEl.style.display = 'block';
        return;
    }

    try {
        const res = await fetchWithAuth('api/profiles/change_pin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                target_id: targetId,
                current_pin: currentPin,
                new_pin: newPin
            }) // [v1.6.3] issuer_id derived from token
        });
        
        const data = await res.json();
        if (data.status === 'success') {
            alert(data.message);
            // v1.2.6: Robust clean-up after successful seal update
            document.getElementById('pin-management-container').style.display = 'none';
            showIdentityHub();
        } else {
            errorEl.innerText = data.message;
            errorEl.style.display = 'block';
        }
    } catch (e) {
        errorEl.innerText = "Master Control Communication Error.";
        errorEl.style.display = 'block';
    }
}

function hideIdentityHub() {
    const overlay = document.getElementById('identity-overlay');
    if (overlay) overlay.style.display = 'none';
}

function confirmLogout() {
    if (confirm(`Extract identity session for ${activeProfile.name.toUpperCase()}?`)) {
        logout();
    }
}

function logout() {
    // [v1.2.9] Hard Reset: Wipe all memory before handover
    activeProfile = null;
    profileToLogin = null;
    conversationHistory = [];
    pendingFiles = [];
    currentPinBuffer = "";
    
    localStorage.removeItem('memorybox_profile');
    localStorage.removeItem('memorybox_token'); // [v1.6.3] Identity Hardening: Clear tokens
    
    // Clear the DOM to prevent 'Ghost Messages'
    const results = document.getElementById('results-area');
    if (results) results.innerHTML = '';
    
    // [v1.2.9] Appliance Protocol: Force reload for 100% clean state
    location.reload();
}

function selectProfileForLogin(profile) {
    profileToLogin = profile;
    
    // UI Transition: Management Grid -> Direct Login Portal
    const portal = document.getElementById('direct-auth-portal');
    const grid = document.getElementById('profiles-grid-container');
    const pinPad = document.getElementById('pin-pad-container');
    const nameInput = document.getElementById('portal-name-input');
    const pinInput = document.getElementById('portal-pin-input');

    if (grid) grid.style.display = 'none';
    if (pinPad) pinPad.style.display = 'none'; // Ensure touch pad is hidden
    if (portal) portal.style.display = 'block';

    const cancelBtn = document.getElementById('portal-cancel-btn');
    if (cancelBtn) cancelBtn.style.display = 'block';

    if (nameInput) nameInput.value = profile.name;
    
    // Clear previous PIN buffer/field
    currentPinBuffer = "";
    if (pinInput) {
        pinInput.value = "";
        setTimeout(() => pinInput.focus(), 100);
    }

    // Set descriptive label for the portal mode
    const desc = document.getElementById('portal-description');
    if (desc) desc.innerText = `Verify identity to switch session to ${profile.name.toUpperCase()}.`;
}

function appendPin(num) {
    if (currentPinBuffer.length >= 20) return;
    currentPinBuffer += num;
    document.getElementById('profile-pin-input').value = currentPinBuffer.replace(/./g, '•');
}

function clearPin() {
    currentPinBuffer = "";
    document.getElementById('profile-pin-input').value = "";
}

// [v1.1.0] Ingestion Privacy State
let currentIngestVisibility = 'SHARED';
let pendingFiles = [];
let ingestionInitialized = false;

function setIngestVisibility(mode) {
    currentIngestVisibility = mode;
    document.getElementById('ingest-visibility-private').classList.toggle('active', mode === 'PRIVATE');
    document.getElementById('ingest-visibility-shared').classList.toggle('active', mode === 'SHARED');
    
    const desc = document.getElementById('visibility-description');
    if (mode === 'PRIVATE') {
        desc.innerHTML = `Uploads will only appear in your searches.`;
    } else {
        desc.innerHTML = `Uploads will appear in searches by any user.`;
    }
}

function clearSessionUI() {
    // [v1.2.9] Mnemonic Sanitization. 
    conversationHistory = [];
    currentPinBuffer = "";
    
    const results = document.getElementById('results-area');
    if (results) results.innerHTML = '';
    
    // Reset ingestion queue UI
    const queue = document.getElementById('ingest-queue');
    if (queue) {
        queue.innerHTML = '';
        queue.style.display = 'none';
        pendingFiles = [];
        updateCommitButton();
    }
}

async function submitLogin() {
    const nameInput = document.getElementById('portal-name-input');
    const pinInput = document.getElementById('portal-pin-input');
    const errorEl = document.getElementById('login-error');
    
    const name = (profileToLogin ? profileToLogin.name : nameInput.value).trim();
    const pin = pinInput.value.trim();
    
    if (!name) {
        errorEl.innerText = "Identity Name required.";
        errorEl.style.display = 'block';
        return;
    }
    if (!pin || pin.length < 6) {
        errorEl.innerText = "PIN must be at least 6 alphanumeric characters.";
        errorEl.style.display = 'block';
        pinInput.focus();
        return;
    }

    try {
        const res = await fetch('api/profiles/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                id: profileToLogin ? profileToLogin.id : null,
                name: profileToLogin ? null : name, 
                pin 
            })
        });
        const data = await res.json();

        if (data.status === 'success') {
            // [v1.6.2] Persist Hardware Token
            localStorage.setItem('memorybox_token', data.token);
            
            // If logging in via direct name, we need to fetch the profile details
            if (!profileToLogin) {
                // Find profile in the freshly authed data or re-list [v1.1.0]
                const allRes = await fetchWithAuth('api/profiles');
                const all = await allRes.json();
                
                if (Array.isArray(all)) {
                    activeProfile = all.find(p => p.name.toLowerCase() === name.toLowerCase());
                } else {
                    console.error("Profile synchronization failure:", all);
                    throw new Error("Profile list unavailable. Please refresh.");
                }
            } else {
                activeProfile = profileToLogin;
            }
            
            if (!activeProfile) throw new Error("Identity not found in vault.");

            localStorage.setItem('memorybox_profile', JSON.stringify(activeProfile));
            
            // [v1.2.9] Session Sanitization before display
            clearSessionUI();

            // [v1.1.0] Apply Account Theme
            if (data.theme) {
                setTheme(data.theme, null, false);
            }
            
            document.getElementById('identity-overlay').style.display = 'none';
            updateActiveUserBadge();
            addBotMessage(`Identity Decrypted. Welcome back, ${activeProfile.name}.`);
            document.getElementById('vault-ui').style.display = 'flex';
            
            // [v1.6.5] Post-Login Sync: Force immediate state reconciliation
            checkVaultStatus(); 
            if (typeof fetchCurationBatch === 'function') {
                fetchCurationBatch(currentRevisionLevel);
            }
        } else {
            errorEl.innerText = data.message || data.detail || "Verification failed: Unknown server error.";
            errorEl.style.display = 'block';
            pinInput.value = ''; // Clear PIN for security
            pinInput.focus();
        }
    } catch (e) {
        console.error("Auth Exception:", e);
        errorEl.innerText = `Verification failed: ${e.message || "Unknown error"}`;
        errorEl.style.display = 'block';
        pinInput.value = '';
    }
}

function updateActiveUserBadge() {
    const badge = document.getElementById('active-user-badge');
    if (activeProfile && badge) {
        // [v1.2.0] Show only the first initial
        const initial = activeProfile.name[0].toUpperCase();
        const userColor = getProfileColor(activeProfile.name);
        badge.innerHTML = `
            <div class="user-avatar-mini" style="background: ${userColor}; box-shadow: 0 0 10px ${userColor}44;">${initial}</div>
            <span style="font-size: 0.75rem; font-weight: 600; color: var(--text-main); margin-right: 2px;">${activeProfile.name.toUpperCase()}</span>
        `;
        badge.style.display = 'flex';
        badge.style.alignItems = 'center';
        badge.style.justifyContent = 'center';
        
        // [v1.2.0] Role-based Identity Actions
        badge.onclick = (e) => {
            e.stopPropagation();
            handleBadgeAction();
        };

        // Fresh diagnostics check for role-based buttons
        initDiagnostics();
    }
}

async function handleBadgeAction() {
    if (!activeProfile) return;
    
    const role = activeProfile.role;
    if (role === 'USER') {
        if (confirm(`Extract session for ${activeProfile.name.toUpperCase()}? (Logout)`)) {
            logout();
        }
    } else {
        // ADMIN / SUPERADMIN Action Menu
        const choice = prompt(
            `IDENTITY ACTIONS: ${activeProfile.name.toUpperCase()} (${role})\n\n` +
            `Type 'LOGOUT' to exit session.\n` +
            `Type 'SEAL' to lock the entire vault appliance.\n` +
            `Type 'CANCEL' to return.`,
            "CANCEL"
        );
        
        if (!choice) return;
        const action = choice.toUpperCase().trim();
        
        if (action === 'LOGOUT') {
            logout();
        } else if (action === 'SEAL') {
            sealVault();
        }
    }
}

async function sealVault() {
    if (!confirm("☣️ SEAL APPLIANCE: This will lock the vault for all users and requires the Master Key to reopen. Proceed?")) return;
    try {
        const res = await fetch('api/vault/seal', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
            localStorage.clear();
            location.reload();
        }
    } catch(e) { alert("Seal operation failed."); }
}

async function updateMemoryStats() {
    const statsContainer = document.getElementById('memoryStats');
    const subtitle = document.getElementById('app-subtitle');
    
    try {
        const resp = await fetchWithAuth('api/personal/stats');
        
        // [v1.8.6] Handle Unauthorized or Sealed states explicitly
        if (resp.status === 401) {
            if (subtitle) subtitle.innerText = "Vault Sealed. Stand by for unseal...";
            return;
        }

        const data = await resp.json();
        if (data.status !== 'Error' && data.writings !== undefined) {
            if (statsContainer) {
                statsContainer.style.display = 'block';
                document.getElementById('stat-writings').innerText = data.writings.toLocaleString();
                document.getElementById('stat-images').innerText = `${data.images_described} / ${data.writings} (Estimated)`;
                if (data.images_described === 0) {
                    document.getElementById('stat-images').style.color = '#ff4444';
                } else {
                    document.getElementById('stat-images').style.color = 'var(--accent-primary)';
                }
            }
            
            // [v1.8.0] Branding Update: Branded tagline and MB Context calculation
            if (subtitle) {
                const total = data.writings + (data.images_described || 0);
                const contextVal = data.context_mb || 0;
                // [v1.8.8] Simplified Archival Telemetry tagline
                subtitle.innerText = `Total Memories: ${total.toLocaleString()} | Archive Context: ${contextVal} MB`;
            }

            // [v1.8.1] UX Update: Disable auto-switch to Ingestion view
            if (data.writings === 0 && (data.images_described || 0) === 0) {
                if (!emptyVaultUXTriggered) {
                    emptyVaultUXTriggered = true;
                    console.log("[UX] Empty Vault detected. (Auto-switch disabled by policy)");
                    setGreeting("Ingest some memories above to personalize our conversations!");
                }
                if (data.writings > 0 && !emptyVaultUXTriggered) {
                    emptyVaultUXTriggered = true; 
                    setGreeting(`I have access to ${data.writings.toLocaleString()} memories. What would you like to see?`);
                }

                // [v1.8.7] Continuity Guard: If vault is online but we are still initializing, update subtitle
                if (subtitle && !subtitle.innerText.includes("connected")) {
                     subtitle.innerText = "Archives Online. Syncing context...";
                }
            }
        } else if (data.status === 'Error' && subtitle) {
             // Use the server's error message if available, else a generic busy state
             subtitle.innerText = `Vault Attention Required: ${data.message || "Archives Busy"}`;
        }
    } catch (e) {
        console.warn("Failed to fetch memory stats:", e);
        if (subtitle && subtitle.innerText.includes("Initializing")) {
            subtitle.innerText = "Establishing secure connection to archives...";
        }
    }
}

// Browser logic scrubbed

// Copy functions scrubbed

async function handleBrokenMedia(el) {
    if (el.dataset.errorHandled) return;
    el.dataset.errorHandled = "true";
    const src = el.getAttribute('src');
    
    // [v1.6.5] Auto-Healing: If it's a protected archive link, punch through with auth.
    if (src && (src.includes('api/personal/') || src.includes('api/vault/'))) {
        try {
            const res = await fetchWithAuth(src);
            if (res.ok) {
                const blob = await res.blob();
                const blobUrl = URL.createObjectURL(blob);
                el.src = blobUrl;
                if (el.tagName === 'VIDEO') {
                    el.load(); // Refresh video source
                    el.controls = true;
                }
                console.log(`[SECURITY] Auto-healed protected media: ${src}`);
            }
        } catch (e) {
            console.warn("Media healing failed:", e);
        }
    }
}

// Backward compatibility for legacy img onerror
function handleBrokenImage(img) { handleBrokenMedia(img); }

function handleLoadedImage(img) {
    if (!img || !img.dataset) return;
    if (img.dataset.alikeHandled) return;
    img.dataset.alikeHandled = "true";
    const src = img.getAttribute('src');
    
    // [v1.8.0] Secure ID-based Discovery
    if (src.includes('api/personal/image/id/')) {
        const id = src.split('api/personal/image/id/')[1];
        const container = document.createElement('div');
        container.style.marginTop = '0.5rem';
        container.innerHTML = `<button class="diag-btn small purple" onclick="triggerFindAlike(${id}, 'visual')">🔍 Find Similar Memories</button>`;
        img.parentElement.insertBefore(container, img.nextSibling);
    } 
    // Compatibility fallthrough for legacy path-based images
    else if (src.includes('api/personal/image/') || src.includes('api/personal/file/')) {
        const path = decodeURIComponent(src.split(/\/api\/personal\/(?:image|file)\//)[1]);
        const container = document.createElement('div');
        container.style.marginTop = '0.5rem';
        container.innerHTML = `<button class="diag-btn small purple" onclick="triggerFindAlike(null, 'visual', '${path}')">🔍 Find Similar Memories</button>`;
        img.parentElement.insertBefore(container, img.nextSibling);
    }
}


async function triggerFindAlike(id, type = 'visual', path = null) {
    const userMsgDiv = document.createElement('div');
    userMsgDiv.className = 'ai-response glass user-card fadeIn';
    const queryStr = id ? `ID: #${id}` : (path ? path : "this memory");
    userMsgDiv.innerHTML = `<h4>You</h4><div class="response-text">Find memories similar to ${queryStr}...</div>`;
    resultsArea.appendChild(userMsgDiv);
    
    const aiRespDiv = document.createElement('div');
    aiRespDiv.className = 'ai-response glass response-card fadeIn';
    aiRespDiv.innerHTML = `<h4>MemoryBox</h4><div class="response-text"><p>Consulting the Mind Map (Time / Location / Context)...</p></div>`;
    resultsArea.appendChild(aiRespDiv);
    window.scrollTo(0, document.body.scrollHeight);
    
    try {
        const endpoint = id ? `api/personal/find_alike?id=${id}&type=${type}` : `api/personal/find_alike?path=${encodeURIComponent(path)}`;
        const resp = await fetchWithAuth(endpoint);
        const data = await resp.json();
        const responseBox = aiRespDiv.querySelector('.response-text');
        
        if (data.status === 'success') {
            let html = "";
            if (data.related && data.related.length > 0) {
                html += `<p style="margin-bottom: 1.5rem;">I found these closely related memories in the archive:</p><div class="archive-deck" style="display:flex; flex-direction:column; gap:2rem;">`;
                for (const item of data.related) {
                    if (item.type === 'visual') {
                        const blobUrl = await secureImageFetch(item.url);
                        const descSnippet = item.description && item.description !== item.mnemonic ? `<br/><span style="opacity:0.8;">${item.description.substring(0, 100)}...</span>` : "";
                        html += `<div class="archive-specimen" style="width:100%;">
                            <img src="${blobUrl}" style="width:100%; height:auto; border-radius:12px; box-shadow: 0 8px 30px rgba(0,0,0,0.5); border: 1px solid rgba(255,255,255,0.05);">
                            <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-top:0.6rem; padding: 0 0.5rem;">
                                <p style="font-size:0.75rem; color:var(--text-dim); line-height:1.5;"><strong>${item.mnemonic || 'Visual Memory'}</strong>${descSnippet}</p>
                                <button class="diag-btn mini" style="margin-top:2px;" onclick="jumpToCurationBench(${item.id}, 'visual')" title="Open in Curation Bench">🔍</button>
                            </div>
                        </div>`;
                    } else {
                        // [v1.8.0] Enhanced Specimen Cards (Textual/Media)
                        const title = item.type === 'media' ? 'Media Memory' : 'Archival Writing';
                        const accent = item.type === 'media' ? 'var(--accent-secondary)' : 'var(--accent-primary)';
                        
                        html += `<div class="archive-specimen textual-card" style="width:100%; padding: 1rem; background: rgba(255,255,255,0.03); border-radius: 12px; border-left: 3px solid ${accent};">
                            <div style="font-size:0.7rem; color:${accent}; text-transform:uppercase; letter-spacing:1px; margin-bottom:0.5rem;">${title}</div>
                            <p style="font-size:0.85rem; color:var(--text-main); margin-bottom:0.8rem; font-family: 'Outfit', sans-serif;">"${(item.content || "").substring(0, 150)}..."</p>
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <p style="font-size:0.75rem; color:var(--text-dim);">${item.description}</p>
                                <button class="diag-btn mini" onclick="jumpToCurationBench(${item.id}, '${item.type}')" title="Open in Curation Bench">🔍</button>
                            </div>
                        </div>`;
                    }
                }
                html += `</div>`;
            }

            
            // [v1.8.10] Synthesis Rendering: Handles 'Slow Burn' (Deferred) status
            if (data.synthesis) {
                let displaySynthesis = data.synthesis;
                if (data.synthesis === "DEFERRED_REFLECTION") {
                    displaySynthesis = `<span style="color:var(--accent-secondary); font-weight:600;">[Mnemonic Digestion In Progress]</span> This connection is profound and requires deeper reflection than the current cycle allows. I am still weaving these threads; please return in a few minutes for the full narrative synthesis.`;
                }
                html += `<p style="margin-top:20px; font-style:italic; border-left:3px solid var(--accent-primary); padding-left:15px; opacity:0.9; line-height:1.6; color:var(--text-main);">${displaySynthesis}</p>`;
            } else if (!data.related || data.related.length === 0) {
                html += `<p>I couldn't find any specific correlating items directly tied to the timestamps, GPS, or entity text strings of that photo.</p>`;
            }
            
            responseBox.innerHTML = html;
        } else {
            responseBox.innerHTML = `<p>Relevance engine error: ${data.message || 'Unknown issue'}</p>`;
        }
    } catch(e) {
        aiRespDiv.querySelector('.response-text').innerHTML = `<p>Relevance engine error.</p>`;
    }
}

function triggerRecalibration(path) { userInput.value = `/reanalyze ${path}`; performSearch(); }
function triggerVisualSearch() {
    const input = document.getElementById('visualSearchInput');
    const query = input.value.trim();
    if (!query) return;
    userInput.value = `/search_visual ${query}`;
    performSearch();
}

function copyAndDirectOpen(path) {
    navigator.clipboard.writeText(path);
    alert('Path copied.');
}

async function performSearch() {
    const query = userInput.value.trim();
    if (!query) return;
    userInput.value = '';
    userInput.style.height = 'auto';
    const userMsgDiv = document.createElement('div');
    userMsgDiv.className = 'ai-response glass user-card fadeIn';
    userMsgDiv.innerHTML = `<h4>You</h4><div class="response-text">${query}</div>`;
    resultsArea.appendChild(userMsgDiv);
    
    const aiRespDiv = document.createElement('div');
    aiRespDiv.className = 'ai-response glass response-card fadeIn';
    const uniqueId = 'stream-' + Date.now();
    aiRespDiv.innerHTML = `<h4>MemoryBox</h4><div id="loading-${uniqueId}" class="loading-container"><div class="loading-spinner"></div><div style="font-size: 0.8rem; color: rgba(255,255,255,0.4); margin-top: 0.5rem; letter-spacing: 1px;">PROCESSING...</div></div><div class="response-text" id="${uniqueId}"></div>`;
    resultsArea.appendChild(aiRespDiv);
    
    const streamingText = document.getElementById(uniqueId);
    const loadingContainer = document.getElementById(`loading-${uniqueId}`);
    window.scrollTo(0, document.body.scrollHeight);

    try {
        abortController = new AbortController();
        const response = await fetchWithAuth('api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                query, 
                personal_mode: true, 
                history: conversationHistory,
                user_id: activeProfile ? activeProfile.id : 0,
                super_admin_mode: (typeof superAdminMode !== 'undefined') ? superAdminMode : false
            }),
            signal: abortController.signal
        });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullAnswer = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value);
            if (chunk.startsWith("__STATUS__:")) {
                const statusLabel = document.querySelector(`#loading-${uniqueId} div[style*="letter-spacing"]`);
                if (statusLabel) statusLabel.innerText = chunk.split("__STATUS__:")[1].trim().toUpperCase();
                continue;
            }
            fullAnswer += chunk;
            if (loadingContainer) loadingContainer.style.display = 'none';
            // [v1.6.5] Global Media Security: Inject auto-healing hooks into all images and videos
            streamingText.innerHTML = marked.parse(fullAnswer)
                .replace(/<img /g, '<img onerror="handleBrokenMedia(this)" onload="handleLoadedImage(this)" ')
                .replace(/<video /g, '<video onerror="handleBrokenMedia(this)" ')
                .replace(/<source /g, '<source onerror="handleBrokenMedia(this)" ');
            window.scrollTo(0, document.body.scrollHeight);
        }
        conversationHistory.push({ role: "user", content: query });
        conversationHistory.push({ role: "assistant", content: fullAnswer });
        
        // Add Archival Action Tray
        const actionTray = document.createElement('div');
        actionTray.className = 'card-actions';
        actionTray.innerHTML = `<button class="archive-btn" onclick="toggleArchival(this, \`${query.replace(/`/g, '\\`').replace(/\$/g, '\\$')}\`, \`${fullAnswer.replace(/`/g, '\\`').replace(/\$/g, '\\$')}\`)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg>
            Archive
        </button>`;
        aiRespDiv.appendChild(actionTray);

    } catch (error) { streamingText.innerText = "Error: " + error.message; }
    finally { if (loadingContainer) loadingContainer.style.display = 'none'; }
}

async function toggleArchival(btn, query, response) {
    if (btn.classList.contains('filed')) return;
    btn.innerHTML = `<div class="loading-spinner" style="width:12px; height:12px; border-width:2px; display:inline-block; vertical-align:middle; margin-right:5px;"></div> Archiving...`;
    
    try {
        const resp = await fetchWithAuth('api/personal/archive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                query, 
                response,
                user_id: activeProfile ? activeProfile.id : 0,
                visibility: 'SHARED' // Archived chats are shared by default in this context
            })
        });
        const data = await resp.json();
        if (data.status === 'success') {
            btn.classList.add('filed');
            btn.innerHTML = `✓ Filed`;
            updateMemoryStats();
        }
    } catch (e) {
        btn.innerHTML = `Error`;
    }
}

async function finalizeSession() {
    const summaryToggle = document.getElementById('archive-summary-toggle');
    const verbatimToggle = document.getElementById('archive-verbatim-toggle');
    const finalizeBtn = document.querySelector('.finalize-btn');
    
    // [v1.5.0] Multi-Archival Protocol
    const doSummary = summaryToggle && summaryToggle.checked;
    const doVerbatim = verbatimToggle && verbatimToggle.checked;

    if (doSummary || doVerbatim) {
        finalizeBtn.innerHTML = "Archiving Session...";
        finalizeBtn.disabled = true;
        
        try {
            // 1. Verbatim Transcript Ingestion
            if (doVerbatim) {
                const vResp = await fetchWithAuth('api/personal/ingest_chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        history: conversationHistory,
                        user_id: activeProfile ? activeProfile.id : 0
                    })
                });
                const vData = await vResp.json();
                if (vData.status === 'success') {
                    const notice = document.createElement('div');
                    notice.className = 'ai-response glass response-card fadeIn';
                    notice.innerHTML = `<div class="response-text" style="color:var(--accent-primary); font-size:0.85rem; font-weight:600;">📁 RAW TRANSCRIPT VAULTED: ${vData.file}</div>`;
                    resultsArea.appendChild(notice);
                }
            }

            // 2. AI Narrative Summary Pass
            if (doSummary) {
                const resp = await fetchWithAuth('api/personal/summarize_session', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        history: conversationHistory,
                        user_id: activeProfile ? activeProfile.id : 0
                    })
                });
                const data = await resp.json();
                if (data.status === 'success') {
                    const summaryDiv = document.createElement('div');
                    summaryDiv.className = 'ai-response glass response-card fadeIn';
                    summaryDiv.style.borderLeft = '4px solid #ffd700';
                    summaryDiv.innerHTML = `<h4>Archive Record: Session Summary</h4><div class="response-text"><i>${data.summary}</i><p style="margin-top:10px; font-size:0.8rem; opacity:0.6;">✨ This session has been summarized and committed to the permanent archive.</p></div>`;
                    resultsArea.appendChild(summaryDiv);
                }
            }
            
            updateMemoryStats();
            startIngestionPolling(); // Start monitoring the background sensing of the new journal
        } catch (e) {
            console.error("Archival failed", e);
        }
    }
    
    // Aesthetic Lock-down
    userInput.disabled = true;
    sendBtn.disabled = true;
    finalizeBtn.innerHTML = "Session Locked";
    finalizeBtn.style.opacity = "0.3";
}

sendBtn.addEventListener('click', performSearch);
userInput.addEventListener('keypress', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); performSearch(); } });
initDiagnostics();

// [v1.2.6] Focused Curation State
let curationBatch = [];
let currentSpecimenIndex = 0;
let currentRevisionLevel = 0;
let curationVisibility = 'SHARED';
let curationHidden = 0;
let workbenchActiveId = null; // [v1.3.2] Lock identifying the memory in the workbench
// [v1.4.0] Curation Mode & Walkthrough State
let curationMode = null; // 'visual' or 'textual'
let consecutiveSkips = 0;
let superAdminMode = true; // [v1.5.0] Default to ON for Admins
let curationShowExcluded = false; // [v1.8.8] Archive Recall Toggle: Included vs Excluded
let totalEligibleCount = 0; // [v1.8.8] Total count of memories at current revision level
const SKIP_THRESHOLD = 4;

let curationCache = {
    visual: { batch: [], index: 0, level: 0 },
    textual: { batch: [], index: 0, level: 0 }
};

function setCurationMode(mode) {
    if (curationMode === mode) return;
    
    // SAVE: Current state into the appropriate bucket (if we have a current mode)
    if (curationMode) {
        curationCache[curationMode] = {
            batch: [...curationBatch],
            index: currentSpecimenIndex,
            level: currentRevisionLevel
        };
    }
    
    curationMode = mode;
    localStorage.setItem('memorybox_curationMode', mode);
    document.getElementById('mode-toggle-visual').classList.toggle('active', mode === 'visual');
    document.getElementById('mode-toggle-textual').classList.toggle('active', mode === 'textual');
    
    // Clear walkthrough if switching
    clearWalkthrough();
    
    // LOAD: Retrieve from the new bucket
    const cached = curationCache[mode];
    curationBatch = cached.batch;
    currentSpecimenIndex = cached.index;
    currentRevisionLevel = cached.level;
    
    if (curationBatch.length === 0) {
        fetchCurationBatch(0);
    } else {
        renderActiveSpecimen();
    }
}

function toggleSuperAdminMode() {
    superAdminMode = !superAdminMode;
    const label = document.getElementById('super-admin-label');
    if (label) {
        label.innerText = superAdminMode ? "SUPER-ADMIN MODE: ON" : "SUPER-ADMIN MODE: OFF";
        const badge = document.getElementById('admin-badge');
        if (badge) {
            badge.style.background = superAdminMode ? "var(--accent-primary)" : "rgba(255,255,255,0.1)";
            badge.style.color = superAdminMode ? "white" : "var(--text-muted)";
        }
    }
    // Refresh recall with new visibility scope
    fetchCurationBatch(currentRevisionLevel);
}

// [v1.8.8] Recall Toggle: Context-Aware (Item Revision vs Global Recall)
function toggleCurationInclusion() {
    if (workbenchActiveId) {
        // [v1.8.8] CASE 1: Workbench is Active. 
        // Act as a shortcut for the individual item's inclusion state.
        // This is part of the revision and requires clicking 'INGEST' to save.
        setArchivalRecall(curationHidden === 0 ? 1 : 0);
        return;
    }

    // [v1.8.8] CASE 2: Global Recall Filter. 
    // Flips between curated (INCLUDED) and discarded (EXCLUDED) memories for the whole bench.
    curationShowExcluded = !curationShowExcluded;
    const btn = document.getElementById('curate-recall-toggle');
    if (btn) {
        btn.innerText = curationShowExcluded ? "Recall: EXCLUDED" : "Recall: INCLUDED";
        btn.classList.toggle('active', curationShowExcluded);
    }
    console.log(`[CURATE] Recall Filter: ${curationShowExcluded ? 'EXCLUDED' : 'INCLUDED'}`);
    updateRecallToggleLabel();
    fetchCurationBatch(currentRevisionLevel);
}

// [v1.8.8] Consistent Recall UI labeling
function updateRecallToggleLabel() {
    const btn = document.getElementById('curate-recall-toggle');
    if (!btn) return;
    
    if (workbenchActiveId) {
        // [v1.8.8] Item Context (REVISION)
        btn.innerText = curationHidden === 1 ? "Recall: EXCLUDED" : "Recall: INCLUDED";
        btn.style.opacity = "1";
    } else {
        // [v1.8.8] Global Context (RECALL FILTER)
        btn.innerText = curationShowExcluded ? "Recall: EXCLUDED" : "Recall: INCLUDED";
        btn.style.opacity = "0.7";
    }
}

let currentSpecimenBlobUrl = null;

// [v1.7.0] The Curation Bridge: Jump from Chat results to the Curation Bench
async function jumpToCurationBench(id, type = 'visual') {
    console.log(`[BRIDGE] Jumping to specimen: ID=${id} Type=${type}`);
    try {
        const resp = await fetchWithAuth(`api/personal/revisit?id=${id}&type=${type}`);
        const data = await resp.json();
        
        if (data.status === 'success' && data.specimen) {
            // [v1.3.0] Continuity: Clear existing batch and replace with target
            curationBatch = [data.specimen];
            currentSpecimenIndex = 0;
            curationMode = data.specimen.type === 'media' ? 'textual' : data.specimen.type;
            
            // Re-render Bench
            renderActiveSpecimen();
            
            // Switch to Bench view and scroll up
            document.getElementById('curation-bench').style.display = 'block';
            window.scrollTo({ top: 0, behavior: 'smooth' });
            
            // Trigger auto-open if workbench is closed
            const refining = document.getElementById('specimen-refining-view');
            if (refining && refining.style.display === 'none') {
                 handleRevise(); // Open workbench directly when jumping from results
            }
        } else {
            alert("Archive specimen lookup failed: " + (data.message || "Unknown error"));
        }
    } catch(e) {
        console.error("[BRIDGE] Jump Failed:", e);
    }
}


async function loadSecureImage(imgElement, url) {
    if (!url) return;
    
    // [v1.2.6] Memory Lifecycle: Clean up previous specimen bytes
    if (currentSpecimenBlobUrl) {
        URL.revokeObjectURL(currentSpecimenBlobUrl);
        currentSpecimenBlobUrl = null;
    }
    
    try {
        const response = await fetchWithAuth(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const blob = await response.blob();
        currentSpecimenBlobUrl = URL.createObjectURL(blob);
        if (imgElement) imgElement.src = currentSpecimenBlobUrl;
    } catch (e) {
        console.error("[CURATE] Secure Media Load Failed:", e);
        if (imgElement) imgElement.src = "static/assets/broken_vault.png"; // Fallback asset
    }
}

/**
 * [v1.8.24] On-Demand Media Recall: Injects the player only when requested.
 */
function injectMediaPlayer(url, isVideo) {
    const container = document.getElementById('media-witness-placeholder');
    if (!container) return;
    
    container.innerHTML = isVideo ? 
        `<video id="active-media-player" controls autoplay src="${url}" style="width:100%; max-height:400px; border-radius:12px; background:#000; box-shadow: 0 10px 30px rgba(0,0,0,0.5);"></video>` : 
        `<audio id="active-media-player" controls autoplay src="${url}" style="width:100%; max-width:480px; filter: invert(1) hue-rotate(180deg); opacity:0.9;"></audio>`;
}

function clearWalkthrough() {
    consecutiveSkips = 0;
    document.getElementById('curate-just-right').classList.remove('walkthrough-highlight');
}

async function fetchCurationBatch(level = 0, retry = 0) {
    currentRevisionLevel = level;
    if (!activeProfile) {
        if (retry < 5) {
            console.log(`[CURATE] Profile not ready, retrying... (${retry+1}/5)`);
            setTimeout(() => fetchCurationBatch(level, retry + 1), 500);
        }
        return;
    }
    
    try {
        console.log(`[CURATE] Fetching ${curationMode} recall level=${level} superAdmin=${superAdminMode} excluded=${curationShowExcluded}`);
        // [v1.8.8] Auth Security: remove session_user_id (server now derives it from the token)
        const resp = await fetchWithAuth(`api/personal/curate/batch?mode=${curationMode}&level=${level}&super_admin_mode=${superAdminMode}&show_excluded=${curationShowExcluded}`);
        const data = await resp.json();
        
        console.log(`[CURATE] API Status: ${data.status}`, data);
        
        // Always reveal command center if authenticated
        document.getElementById('command-center').style.display = 'block';
        initIngestion();

        if (data.status === 'success' && data.batch && data.batch.length > 0) {
            // [v1.3.3] Identity Anchoring: Capture the ID of the memory we are currently looking at.
            const anchorId = curationBatch[currentSpecimenIndex]?.id;
            
            curationBatch = data.batch;
            totalEligibleCount = data.total_eligible || 0;
            
            if (anchorId) {
                // Search the new batch for the anchored memory.
                const newIdx = curationBatch.findIndex(i => i.id === anchorId);
                if (newIdx !== -1) {
                    currentSpecimenIndex = newIdx;
                    console.log(`[CURATE] Identity Anchored: Memory ${anchorId} moved to index ${newIdx}`);
                } else {
                    // Memory was removed from the queue (e.g. curated by another session or moved levels)
                    console.warn(`[CURATE] Anchor Lost: Memory ${anchorId} is no longer in recall.`);
                    currentSpecimenIndex = 0;
                }
            } else {
                currentSpecimenIndex = 0;
            }
            
            document.getElementById('curation-bench').style.display = 'block';
            document.getElementById('empty-curation').style.display = 'none';
            renderActiveSpecimen();
        } else {
            console.warn(`[CURATE] Empty recall or failure. Records: ${data.batch?.length || 0}`);
            curationBatch = [];
            document.getElementById('curation-bench').style.display = 'none';
            document.getElementById('empty-curation').style.display = 'block';
            document.getElementById('curation-queue-pos').innerText = `Empty`;
            
            // If they just finished level 0, prompt for Level 1
            const levelBtn = document.getElementById('level-up-btn');
            if (levelBtn) {
                levelBtn.innerText = level === 0 ? "Scan Level 1 Memories" : "Scan Higher Levels";
                levelBtn.onclick = () => fetchCurationBatch(level + 1);
            }

            // [v1.7.6] Ingestion-First Strategy: If level 0 is empty, prioritize the upload interface
            if (level === 0) {
                console.log("[UX] Curation empty. (Auto-switch disabled by policy)");
            }
        }
    } catch (e) {
        console.warn("Curation batch fetch failed.", e);
    }
}

function renderActiveSpecimen() {
    // [v1.8.8] Persistent Header Governance: These controls must be visible even on an empty bench
    const recallBtn = document.getElementById('curate-recall-toggle');
    if (recallBtn) {
        recallBtn.style.display = 'inline-block';
    }
    
    const adminMode = document.getElementById('admin-badge');
    if (adminMode) {
        adminMode.style.display = activeProfile && (activeProfile.role === 'ADMIN' || activeProfile.role === 'SUPERADMIN') ? 'inline-block' : 'none';
    }
    
    updateRecallToggleLabel();
    if (!curationBatch || curationBatch.length === 0) {
        document.getElementById('curation-bench').style.display = 'none';
        document.getElementById('empty-curation').style.display = 'block';
        document.getElementById('curation-queue-pos').innerText = `Empty`;
        totalEligibleCount = 0;
        return;
    }
    
    document.getElementById('curation-bench').style.display = 'block';
    document.getElementById('empty-curation').style.display = 'none';
    
    // [v1.8.8] Absolute Progress: Recall X / Y
    const currentPos = currentSpecimenIndex + 1;
    document.getElementById('curation-queue-pos').innerText = `Recall ${currentPos} / ${totalEligibleCount}`;
    const item = curationBatch[currentSpecimenIndex];
    
    // [v1.4.0] Adaptive Staging
    const imgView = document.getElementById('active-image');
    const textView = document.getElementById('textual-artifact-viewer');
    
    if (curationMode === 'visual' || item.type === 'visual') {
        imgView.style.display = 'block';
        textView.style.display = 'none';
        
        if (imgView && imgView.dataset) delete imgView.dataset.errorHandled;
        // [v1.2.6] Secure Staging: Proxied through memory-only Blob
        loadSecureImage(imgView, item.url);
        
        document.getElementById('specimen-desc').innerText = item.description || "No visual sense recorded.";
    } else {
        imgView.style.display = 'none';
        textView.style.display = 'flex';
        // [v1.4.1] Valid Asset Fallback: Use icon_safe.png if sensing hasn't generated a thumbnail yet
        const thumbEl = document.getElementById('artifact-thumbnail');
        if (thumbEl) {
            if (thumbEl.dataset) delete thumbEl.dataset.errorHandled;
            // [v1.8.24] Archival Aesthetic: Use premium cabinet icon for text/audio metadata
            thumbEl.src = item.thumbnail || "static/assets/icon_cabinet.png";
        }
        document.getElementById('artifact-text-preview').innerText = item.content || "Empty document artifact.";
        
        // [v1.8.0] Media Witness Panel (Whisper & Video Integration)
        const isMedia = (path) => path && (path.endsWith('.m4a') || path.endsWith('.mp3') || path.endsWith('.wav') || path.endsWith('.ogg') || path.endsWith('.mp4') || path.endsWith('.mov') || path.endsWith('.mkv'));
        
        if (isMedia(item.url || item.path) || item.is_media) {
            const mediaUrl = item.url || (item.id ? `api/personal/media/id/${item.id}` : null);
            const isVideo = item.path && (item.path.endsWith('.mp4') || item.path.endsWith('.mov') || item.path.endsWith('.mkv'));
            
            textView.style.display = 'flex';
            
            // [v1.9.0] Storyboard Generation (Column Stack)
            if (isVideo) {
                const sidebar = textView.querySelector('.artifact-sidebar');
                try {
                    populateVideoStoryboard(mediaUrl, sidebar);
                } catch (sbErr) {
                    console.warn("[CURATE] Storyboard Recall Delayed:", sbErr);
                }
            }

            document.getElementById('artifact-text-preview').innerHTML = `
                <div class="audio-sensing-container fadeIn" style="text-align:center; width:100%; padding: 0; margin-top: -10px;">
                    <div style="font-size:0.7rem; margin-bottom:1rem; color:var(--accent-secondary); font-weight: 600; letter-spacing:1px; opacity: 0.8; text-transform: uppercase;">
                        ${isVideo ? "VIDEO WITNESS" : "AUDIO WITNESS"}
                    </div>
                    
                    <div id="media-witness-placeholder" class="media-recall-placeholder glass" onclick="injectMediaPlayer('${mediaUrl}', ${isVideo})">
                        <div class="recall-icon">
                            <img src="static/assets/icon_cabinet.png" style="width:40px; opacity:0.5; margin-bottom:10px;">
                        </div>
                        <div style="font-size:0.8rem; font-weight:600; color:var(--text-main);">RECALL MEDIA</div>
                        <div style="font-size:0.6rem; opacity:0.5; margin-top:5px;">Sovereign Stream from Vault</div>
                    </div>

                    <div style="margin-top:2rem; text-align:left; border-top:1px solid rgba(255,255,255,0.05); padding-top:1.5rem;">
                        <div style="font-size:0.7rem; color:var(--text-dim); text-transform:uppercase; margin-bottom:0.8rem; letter-spacing:0.5px;">Sensing Artifact: Transcript</div>
                        <p id="transcript-body" style="font-size:0.9rem; line-height:1.7; color:var(--text-main); font-family:'Outfit', sans-serif; white-space:pre-wrap;">${item.content || "Generating Transcript..."}</p>
                    </div>
                </div>
            `;
            
            // Trigger automatic sensing if description is placeholder
            if (!item.content || item.content.includes("Sensing pending")) {
                triggerAudioTranscription(item);
            }
        } else {
            document.getElementById('artifact-text-preview').innerText = item.content || "Empty document artifact.";
        }
        
        // [v1.8.8] Curation State Sync: Display the primary sensed description for all artifacts
        document.getElementById('specimen-desc').innerText = item.description || "No archival sensing summarized.";
    }

    // [v1.8.20] Unified Archival Counter: show progress against the full eligible total
    const displayTotal = Math.max(totalEligibleCount, curationBatch.length);
    document.getElementById('curation-queue-pos').innerText = `Recall ${currentSpecimenIndex + 1} / ${displayTotal}`;
    
    const metadataTag = document.getElementById('specimen-metadata');
    if (metadataTag) {
        document.getElementById('owner-tag-name').innerText = item.owner_name || "Archivist";
        
        // [v1.8.7] Terms of the Appliance: Strictly map PRIVATE/INDIVIDUAL -> INDIVIDUAL, SHARED -> COMMUNAL
        const displayVisibility = (item.visibility === 'PRIVATE' || item.visibility === 'INDIVIDUAL') ? "INDIVIDUAL" : "COMMUNAL";
        document.getElementById('visibility-tag-status').innerText = displayVisibility;
        
        metadataTag.style.display = activeProfile && (activeProfile.role === 'ADMIN' || activeProfile.role === 'SUPERADMIN') ? 'block' : 'none';
    }

    // [v1.8.8] Included/Excluded toggle is now handled at the header level

    // [v1.6.0] Archival Governance (Excluded/Included) logic
    const includeBtn = document.getElementById('recall-include-btn');
    const excludeBtn = document.getElementById('recall-exclude-btn');
    const exclusionTip = document.getElementById('exclusion-tip');
    const activeSpecimen = document.getElementById('active-specimen');
    
    if (includeBtn && excludeBtn && exclusionTip) {
        // [v1.6.1] Logic: Use the draft state if the workbench is open for this item
        const isWorkbenchForThis = workbenchActiveId && String(item.id) === String(workbenchActiveId);
        const effectiveHidden = isWorkbenchForThis ? curationHidden : (item.hidden || 0);
        
        const isExcluded = effectiveHidden === 1;
        includeBtn.classList.toggle('active', !isExcluded);
        excludeBtn.classList.toggle('active', isExcluded);
        exclusionTip.innerText = isExcluded ? "Omitted from Reasoning and Recall." : "Active in Reasoning and Recollection.";
        
        if (activeSpecimen) {
            // [v1.6.1] Surgical Seclusion: Grayscale only hits the specimen container
            activeSpecimen.classList.toggle('excluded', isExcluded);
        }
    }

    const refining = document.getElementById('specimen-refining-view');
    const isWorkbenchOpen = refining && refining.style.display !== 'none';

    if (isWorkbenchOpen && workbenchActiveId === item.id) {
        console.log("[CURATE] Shielding active workbench from background Sensing overwrite.");
        checkWorkbenchDirty();
    } else {
        // Synchronize workbench inputs and draft states for the new specimen
        curationVisibility = item.visibility || 'SHARED';
        curationHidden = item.hidden || 0;
        
        if (refining) {
            document.getElementById('grounding-input').value = item.note || "";
            setCurationVisibility(curationVisibility);
            document.getElementById('regen-preview').style.display = 'none';
            document.getElementById('regen-keep').innerText = "INGEST REVISIONS"; 
            if (isWorkbenchOpen) {
                 workbenchActiveId = item.id;
            }
        }
    }

    // [v1.8.8] Contextual Button Labeling
    updateRecallToggleLabel();
}

async function triggerAudioTranscription(item) {
    const desc = document.getElementById('specimen-desc');
    desc.innerHTML = `<div class="loading-spinner" style="width:12px; height:12px; display:inline-block; margin-right:8px;"></div> TRANSCRIPTING AUDIO (Whisper large-v3)...`;
    
    try {
        const resp = await fetchWithAuth('api/sensing/transcribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: item.path || item.url })
        });
        const data = await resp.json();
        if (data.status === 'success') {
            item.description = data.text;
            desc.innerText = data.text;
            console.log("[SENSING] Transcription captured.");
        } else {
            desc.innerText = `Sensing Error: ${data.message}`;
        }
    } catch (e) {
        desc.innerText = "Failed to connect to AI Sensing engine.";
    }
}

function nextSpecimen() {
    currentSpecimenIndex++;
    if (currentSpecimenIndex >= curationBatch.length) {
        fetchCurationBatch(currentRevisionLevel);
    } else {
        renderActiveSpecimen();
    }
}

async function secureImageFetch(url) {
    try {
        const res = await fetchWithAuth(url);
        if (res.ok) {
            const blob = await res.blob();
            return URL.createObjectURL(blob);
        }
    } catch (e) { console.warn("Secure image load failed:", e); }
    return "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="; // Blank
}

async function handleJustRight() {
    const item = curationBatch[currentSpecimenIndex];
    const btn = document.getElementById('curate-just-right');
    btn.disabled = true;
    try {
        const res = await fetchWithAuth('api/personal/curate/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: item.id, mode: curationMode })
        });
        
        const json = await res.json().catch(() => null);
        if (res.ok && json?.status === 'success') {
            clearWalkthrough(); // [v1.4.0] Successful curation breaks the loop
            curationBatch.splice(currentSpecimenIndex, 1);
            if (curationBatch.length === 0) {
                fetchCurationBatch(currentRevisionLevel);
            } else {
                if (currentSpecimenIndex >= curationBatch.length) currentSpecimenIndex = 0;
                renderActiveSpecimen();
            }
        } else {
            const msg = json?.message || `Err ${res.status}: Archival Node Rejected Curation.`;
            alert(`Sovereignty Error: ${msg}`);
        }
    } catch (e) { 
        console.error("Approval failed:", e);
        const diag = (item) ? `Target ID: ${item.id}` : "Target missing";
        alert(`Connectivity Failure: Your approval could not reach the archival vault.\n\nDiagnostic: ${e.name}: ${e.message}\nContext: ${diag}`);
    }
    btn.disabled = false;
}

function handleSkip() {
    // Moves to back of current local skip list
    const item = curationBatch.splice(currentSpecimenIndex, 1)[0];
    curationBatch.push(item);
    
    // [v1.4.0] Walkthrough Detection
    consecutiveSkips++;
    if (consecutiveSkips >= SKIP_THRESHOLD) {
        const jrBtn = document.getElementById('curate-just-right');
        jrBtn.classList.add('walkthrough-highlight');
        console.log("[WALKTHROUGH] Loop detected. Highlighting Just Right.");
    }

    // [v1.8.22] Force-Advance Logic: If we are skipping but the batch is empty or stuck, re-fetch.
    if (curationBatch.length <= 1) {
        console.log("[CURATE] Force-Advancing archival queue...");
        fetchCurationBatch(currentRevisionLevel);
    } else {
        renderActiveSpecimen();
    }
}

let mnemonicSnapshot = "";

function handleRevise() {
    const item = curationBatch[currentSpecimenIndex];
    if (!item) return;
    
    workbenchActiveId = item.id;
    // Snapshot for dirty checking
    item.hidden_snapshot = item.hidden || 0;
    curationHidden = item.hidden || 0; // Initialize draft from DB state
    const sensingArea = document.getElementById('specimen-desc');
    
    // Principle: Archival Reversibility. Take a snapshot before sharpening.
    mnemonicSnapshot = sensingArea.innerText;
    
    document.getElementById('specimen-refining-view').style.display = 'block';
    toggleCurationButtons(false);
    
    document.getElementById('grounding-input').value = item.note || "";
    document.getElementById('regen-preview').style.display = 'none';
    document.getElementById('proposed-desc').innerText = ""; // [v1.8.10] Prevent state bleed from previous memories
    document.getElementById('regen-keep').innerText = "INGEST REVISIONS";
    
    // [v1.7.5] Dual-Sensing Entry State: Show both paths clearly
    const genBtn = document.getElementById('regen-sharpen');
    const blindBtn = document.getElementById('regen-blind');
    if (genBtn) {
        genBtn.innerText = "RE-GENERATE (VISION)";
        genBtn.style.display = 'inline-block';
        genBtn.disabled = false;
    }
    if (blindBtn) {
        blindBtn.innerText = "RE-GENERATE (BLIND)";
        blindBtn.style.display = 'inline-block';
        blindBtn.disabled = false;
    }

    // [v1.5.0] UI Hardening: Lockdown top navigation during revision
    setTopButtonsState(false);

    // [v1.3.2] Access & Recall Sync
    curationVisibility = item.visibility || 'SHARED';
    setCurationVisibility(curationVisibility);
    // [v1.6.0] Recall state is already handled by renderActiveSpecimen() but we ensure consistency
    checkWorkbenchDirty();
}

function setTopButtonsState(enabled) {
    // Top tabs
    const tabs = ['tab-curation', 'tab-ingestion'];
    tabs.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.disabled = !enabled;
            el.style.opacity = enabled ? "1" : "0.3";
            el.style.pointerEvents = enabled ? "auto" : "none";
        }
    });

    // Brand/Diagnostics toggle
    const brand = document.getElementById('brandIcon');
    if (brand) brand.style.pointerEvents = enabled ? "auto" : "none";

    // Header Actions
    const settingsBtn = document.querySelector('.dash-tab.icon-tab[title="System Settings"]');
    const usersBtn = document.querySelector('.dash-tab.icon-tab[title="User Profile"]');
    [settingsBtn, usersBtn].forEach(btn => {
        if (btn) {
            btn.disabled = !enabled;
            btn.style.opacity = enabled ? "1" : "0.3";
            btn.style.pointerEvents = enabled ? "auto" : "none";
        }
    });
    
    // Theme Legend
    const legend = document.querySelector('.theme-legend');
    if (legend) {
        legend.style.opacity = enabled ? "1" : "0.3";
        legend.style.pointerEvents = enabled ? "auto" : "none";
    }
}

function checkWorkbenchDirty() {
    const item = curationBatch.find(i => String(i.id) === String(workbenchActiveId));
    if (!item) return;

    const currentText = document.getElementById('grounding-input').value;
    const currentVis = curationVisibility;
    const currentHidden = curationHidden;
    const hasProposed = document.getElementById('regen-preview').style.display !== 'none';

    // [v1.6.1] Inclusion: Track changes in inclusion state for the 'Dirty' dirty check
    const isDirty = (currentText !== (item.note || "")) || 
                    (currentVis !== (item.visibility || "SHARED")) ||
                    (currentHidden !== (item.hidden || 0)) ||
                    hasProposed;

    const ingestBtn = document.getElementById('regen-keep');
    if (ingestBtn) {
        ingestBtn.classList.toggle('locked', !isDirty);
        ingestBtn.disabled = !isDirty;
    }
}

function setCurationVisibility(mode) {
    curationVisibility = mode;
    const btnPrivate = document.getElementById('curate-visibility-private');
    const btnShared = document.getElementById('curate-visibility-shared');
    const accessTip = document.getElementById('access-tip');
    
    console.log(`[GOVERNANCE] Setting Access: ${mode}`);
    
    if (btnPrivate && btnShared) {
        const isPrivate = (mode === 'PRIVATE' || mode === 'INDIVIDUAL');
        btnPrivate.classList.toggle('active', isPrivate);
        btnShared.classList.toggle('active', !isPrivate);
    }
    
    if (accessTip) {
        accessTip.innerText = mode === 'PRIVATE' ? 
            "Restricted to your personal identity vault." : 
            "Visible to all authenticated appliance users.";
    }
    
    checkWorkbenchDirty();
}

function toggleCurationButtons(enabled) {
    const ids = ['curate-just-right', 'curate-reminisce', 'curate-skip'];
    ids.forEach(id => {
        const btn = document.getElementById(id);
        if (btn) {
            btn.disabled = !enabled;
            btn.classList.toggle('locked', !enabled);
        }
    });
}

function handleReminisce() {
    const item = curationBatch[currentSpecimenIndex];
    if (item && (item.id || item.path)) {
        // [v1.8.0] Standardized ID-based Remiscence
        const label = item.mnemonic || item.description || "this memory";
        triggerFindAlike(item.id, item.type || curationMode, item.path);
        
        // Bonus: Provide soft feedback that we are searching
        const desc = document.getElementById('specimen-desc');
        const original = desc.innerText;
        desc.innerText = `Searching the vault for memories like '${label}'... (Check the Chat)`;
        setTimeout(() => { desc.innerText = original; }, 3000);
    }
}


async function handleSharpen(isBlind = false) {
    const item = curationBatch[currentSpecimenIndex];
    if (!item || (workbenchActiveId && item.id !== workbenchActiveId)) {
        alert("Integrity Error: The memory you are revising is no longer active.");
        return;
    }
    const context = document.getElementById('grounding-input').value;
    const genBtn = document.getElementById('regen-sharpen');
    const blindBtn = document.getElementById('regen-blind');
    
    // [v1.6.5] Dual-Workflow UI Hardening: Hide both while considering
    const activeBtn = isBlind ? blindBtn : genBtn;
    const inactiveBtn = isBlind ? genBtn : blindBtn;
    
    activeBtn.innerText = isBlind ? "FAST SYNTHESIS..." : "RE-EXAMINING...";
    activeBtn.disabled = true;
    if (inactiveBtn) inactiveBtn.style.display = 'none';
    
    try {
        const res = await fetchWithAuth('api/personal/curate/regenerate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: item.id, context: context, mode: curationMode, blind: isBlind })
        });
        const data = await res.json();
        if (data.status === 'success') {
            document.getElementById('proposed-desc').innerText = data.new_description;
            document.getElementById('regen-preview').style.display = 'block';
            checkWorkbenchDirty();
        } else {
            alert(`Sensing Issue: ${data.message || 'Unknown error'}`);
        }
    } catch (e) { 
        alert("AI Sensing failed to sharpen context."); 
    }
    
    // [v1.7.5] Restoration: Bring back both options after thinking is complete
    genBtn.innerText = "RE-GENERATE (VISION)";
    genBtn.disabled = false;
    genBtn.style.display = 'inline-block';
    
    if (blindBtn) {
        blindBtn.innerText = "RE-GENERATE (BLIND)";
        blindBtn.disabled = false;
        blindBtn.style.display = 'inline-block';
    }
}

async function handleKeep() {
    // [v1.3.3] Identity Locking: Never use the index to find the commit target.
    // Always find the item by the specific workbenchActiveId.
    const item = curationBatch.find(i => i.id === workbenchActiveId);
    
    if (!item) {
        alert("Integrity Error: The memory you are revising is no longer in the active recall. Action aborted.");
        return;
    }

    // Use proposed sensing if available, otherwise original card text.
    const newDesc = document.getElementById('proposed-desc').innerText || document.getElementById('specimen-desc').innerText;
    
    try {
        const res = await fetchWithAuth('api/personal/curate/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                id: item.id, 
                description: newDesc,
                visibility: curationVisibility,
                hidden: curationHidden, // [v1.6.1] Atomic persistence
                mode: curationMode,
                note: document.getElementById('grounding-input').value // [v1.8.10] Ensure grounding note is captured
            })
        });

        const json = await res.json().catch(() => null);
        if (res.ok && json?.status === 'success') {
            workbenchActiveId = null; // Unlock
            setTopButtonsState(true); // Re-enable UI
            document.getElementById('specimen-refining-view').style.display = 'none';
            toggleCurationButtons(true);
            
            // [v1.8.21] Sequential advancement like Just Right
            curationBatch.splice(currentSpecimenIndex, 1);
            if (curationBatch.length === 0) {
                fetchCurationBatch(currentRevisionLevel);
            } else {
                if (currentSpecimenIndex >= curationBatch.length) currentSpecimenIndex = 0;
                renderActiveSpecimen();
            }
        } else {
            const msg = json?.message || `Err ${res.status}: Archival Node Rejected Version Update.`;
            alert(`Archival Rejection: ${msg}`);
        }
    } catch (e) { 
        console.error("[CURATE] Update Link Failure:", e);
        const diag = (item) ? `Target ID: ${item.id}` : "Target missing";
        alert(`Connectivity Failure: Your revision could not be committed to the archival vault.\n\nDiagnostic: ${e.name}: ${e.message}\nContext: ${diag}`);
    }
}

async function setArchivalRecall(state) {
    // [v1.6.1] Logic Update: Staging state only. No atomic API call here.
    curationHidden = state;
    renderActiveSpecimen();
    updateRecallToggleLabel();
    checkWorkbenchDirty();
}

function handleCancel() {
    workbenchActiveId = null; // [v1.3.2] Unlock
    curationHidden = 0; // Reset draft
    setTopButtonsState(true); // Re-enable UI
    document.getElementById('specimen-desc').innerText = mnemonicSnapshot;
    document.getElementById('specimen-refining-view').style.display = 'none';
    toggleCurationButtons(true);
    renderActiveSpecimen(); // Restore visual state (including grayscale/inclusion)
}

async function toggleExclusion() {
    // [DEPRECATED v1.6.0] Replaced by setArchivalRecall
    const current = (curationBatch.find(i => i.id === workbenchActiveId) || curationBatch[currentSpecimenIndex])?.hidden || 0;
    setArchivalRecall(current === 0 ? 1 : 0);
}

function toggleCurationBench() {
    const carousel = document.getElementById('curation-carousel');
    carousel.classList.toggle('collapsed');
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('curate-just-right')?.addEventListener('click', handleJustRight);
    document.getElementById('curate-revise')?.addEventListener('click', handleRevise);
    document.getElementById('curate-reminisce')?.addEventListener('click', handleReminisce);
    document.getElementById('curate-skip')?.addEventListener('click', handleSkip);
    document.getElementById('curate-recall-toggle')?.addEventListener('click', toggleCurationInclusion);
    document.getElementById('regen-sharpen')?.addEventListener('click', () => handleSharpen(false));
    document.getElementById('regen-blind')?.addEventListener('click', () => handleSharpen(true));
    document.getElementById('regen-keep')?.addEventListener('click', handleKeep);
    document.getElementById('regen-cancel')?.addEventListener('click', handleCancel);
    document.getElementById('curation-rollup-toggle')?.addEventListener('click', toggleCurationBench);

    // [v1.3.2] Workflow Interlocks
    document.getElementById('grounding-input')?.addEventListener('input', checkWorkbenchDirty);
});

function setVaultView(mode) {
    const cc = document.getElementById('command-center');
    const tabCuration = document.getElementById('tab-curation');
    const tabIngestion = document.getElementById('tab-ingestion');
    const paneCuration = document.getElementById('curation-carousel');
    const paneIngestion = document.getElementById('ingestion-vault');
    
    if (mode === 'ingestion') {
        cc.classList.add('ingestion-active');
        tabIngestion.classList.add('active');
        tabCuration.classList.remove('active');
        paneIngestion.classList.add('active');
        paneCuration.classList.remove('active');
    } else {
        cc.classList.remove('ingestion-active');
        tabIngestion.classList.remove('active');
        tabCuration.classList.add('active');
        paneIngestion.classList.remove('active');
        paneCuration.classList.add('active');
    }
}

function initIngestion() {
    if (ingestionInitialized) return;
    const dropZone = document.getElementById('drop-zone');
    const fileChooser = document.getElementById('file-chooser');
    if (!dropZone || !fileChooser) return;

    // [v1.8.10] Mobile Ingestion Support: Click to choose
    dropZone.addEventListener('click', () => fileChooser.click());
    
    fileChooser.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFiles(e.target.files);
            // Clear chooser so same file can be selected again if needed
            fileChooser.value = '';
        }
    });

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, e => {
            e.preventDefault();
            e.stopPropagation();
        }, false);
    });

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.add('dragover'), false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'), false);
    });

    dropZone.addEventListener('drop', async e => {
        const items = e.dataTransfer.items;
        if (items) {
            handleItems(items);
        } else {
            handleFiles(e.dataTransfer.files);
        }
    }, false);
    
    ingestionInitialized = true;
}

async function handleItems(items) {
    const queue = document.getElementById('ingest-queue');
    if (queue) queue.style.display = 'block';

    // [v1.6.5] Anti-Evaporation Capture:
    // WebKit entries from DataTransferItems evaporate during await calls.
    // We must collect all entries SYNCHRONOUSLY before the first await.
    const entries = [];
    for (let i = 0; i < items.length; i++) {
        const entry = items[i].webkitGetAsEntry();
        if (entry) entries.push(entry);
    }

    for (const entry of entries) {
        await traverseFileTree(entry);
    }
    updateCommitButton();
}

async function traverseFileTree(entry, path = "") {
    if (entry.isFile) {
        const file = await new Promise((resolve) => entry.file(resolve));
        addFileToStaging(file, path);
    } else if (entry.isDirectory) {
        const dirReader = entry.createReader();
        
        // [v1.2.6] Deep Recursion: readEntries is not guaranteed to return all items in one call.
        const readAllEntries = async () => {
            let allEntries = [];
            let readBatch = async () => {
                const batch = await new Promise((resolve) => dirReader.readEntries(resolve));
                if (batch.length > 0) {
                    allEntries = allEntries.concat(batch);
                    await readBatch();
                }
            };
            await readBatch();
            return allEntries;
        };

        const children = await readAllEntries();
        for (const childEntry of children) {
            await traverseFileTree(childEntry, path + entry.name + "/");
        }
    }
}

function handleFiles(files) {
    const queue = document.getElementById('ingest-queue');
    if (queue) queue.style.display = 'block';

    Array.from(files).forEach(file => {
        addFileToStaging(file, "");
    });
    updateCommitButton();
}

function addFileToStaging(file, relativePath) {
    const queue = document.getElementById('ingest-queue');
    const fileId = 'file-' + Math.random().toString(36).substr(2, 9);
    
    // [v1.2.6] Enhanced Staging Metadata: Capture relative directory for path-preservation
    pendingFiles.push({ 
        id: fileId, 
        file: file, 
        relativePath: relativePath + file.name,
        status: 'staged' 
    });
    
    const item = document.createElement('div');
    item.className = 'ingest-item staged fadeIn';
    item.id = fileId;
    
    // [v1.2.6] UI Path Hint: Display folder hierarchy before the name
    const pathHint = relativePath ? `<span style="opacity:0.4; font-weight:normal; font-size:0.7rem;">${relativePath}</span> ` : "";
    
    item.innerHTML = `
        <div class="ingest-item-header" style="display:flex; align-items:center; gap:12px; width:100%;">
            <button class="ingest-remove-btn" onclick="removeStagedItem('${fileId}')" 
                title="Remove from archive queue"
                style="margin-right: 4px;">×</button>
            <span class="file-name-label" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:bold; font-size:0.85rem; color:var(--text-main); opacity: 0.8; flex-shrink:1;">${pathHint}${file.name}</span>
        </div>
        
        <div class="memory-note-wrap" style="width:100%; margin-top:5px; position:relative;">
            <textarea class="memory-note-input" 
                placeholder="ARCHIVIST GROUNDING: e.g. Uncle Jim at the 1994 BBQ..."
                maxlength="280"
                oninput="updateNoteCounter(this)"
                rows="1"
                style="display:block; width:100%; box-sizing:border-box; min-height:42px; background:rgba(0,0,0,0.5); border:1px solid rgba(255,255,255,0.15); border-radius:6px; padding:11px 80px 11px 14px; color:#fff; font-size:0.85rem; font-family:'Inter', sans-serif; resize:none; overflow:hidden; transition:all 0.2s;"></textarea>
            <div class="note-counter" style="position:absolute; top:21px; right:14px; transform:translateY(-50%); font-size:0.6rem; opacity:0.3; font-weight:bold; pointer-events:none;">0 / 280</div>
        </div>
    `;
    queue.prepend(item);
}

function removeStagedItem(fileId) {
    pendingFiles = pendingFiles.filter(f => f.id !== fileId);
    const el = document.getElementById(fileId);
    if (el) el.remove();
    updateCommitButton();
}

function updateCommitButton() {
    const commitBtn = document.getElementById('commit-ingestion-btn');
    const stagedCount = pendingFiles.filter(f => f.status === 'staged').length;
    if (stagedCount > 0) {
        commitBtn.style.display = 'block';
        commitBtn.innerText = `Ingest into archive (${stagedCount} Items)`;
        commitBtn.disabled = false;
        commitBtn.style.opacity = "1";
    } else {
        commitBtn.style.display = 'none';
    }
}

async function commitDigestion() {
    const commitBtn = document.getElementById('commit-ingestion-btn');
    const queueItems = document.querySelectorAll('.ingest-item.staged');
    const notesMap = {};
    
    // 1. [v1.2.6] UI Preparation
    queueItems.forEach(item => {
        const fileId = item.id;
        const fileData = pendingFiles.find(f => f.id === fileId);
        const fileName = fileData ? fileData.file.name : "Unknown File";
        const noteArea = item.querySelector('.memory-note-input');
        const noteWrap = item.querySelector('.memory-note-wrap');
        
        if (noteArea && noteArea.value.trim()) {
            notesMap[fileName] = noteArea.value.trim();
        }
        
        if (noteWrap) noteWrap.style.opacity = '0.5';
        if (noteArea) noteArea.disabled = true;
    });

    commitBtn.disabled = true;
    commitBtn.style.display = "none";
    
    const progressContainer = document.getElementById('upload-progress-container');
    const progressBar = document.getElementById('upload-progress-bar');
    const progressText = document.getElementById('upload-progress-text');
    progressContainer.style.display = 'block';

    try {
        // 2. [v1.2.6] Phase 1: Structured Upload
        const toUpload = pendingFiles.filter(f => f.status === 'staged');
        const total = toUpload.length;
        
        for (let i = 0; i < total; i++) {
            const item = toUpload[i];
            const formData = new FormData();
            formData.append('file', item.file);
            formData.append('relativePath', item.relativePath);

            await fetchWithAuth('api/ingestion/upload', {
                method: 'POST',
                body: formData,
                signal: AbortSignal.timeout(60000) // [v1.8.11] Prevent hanging on orphaned file handles
            });

            const percent = Math.round(((i + 1) / total) * 100);
            progressBar.style.width = percent + '%';
            progressText.innerText = percent + '%';
        }

        progressText.innerText = 'UPLOADS COMPLETE | VAULTING...';

        // [v1.8.7] UI Deep Clean: Ensure the staging list is unequivocally wiped from both memory and DOM
        pendingFiles = pendingFiles.filter(f => f.status !== 'staged');
        updateCommitButton();
        
        const queue = document.getElementById('ingest-queue');
        if (queue) {
            queue.innerHTML = '';
            queue.style.display = 'none';
            // Also reset the drop zone text if needed
            const dropText = document.querySelector('.drop-zone p');
            if (dropText) dropText.innerText = "Memories Vaulted. Drag more to ingest.";
        }

        // 3. [v1.2.6] Phase 2: Ingester Trigger
        let triggerSuccess = false;
        try {
            const res = await fetchWithAuth('api/ingestion/trigger', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    target: "/home/concierge/memories/Archive/Incoming",
                    notes: notesMap,
                    visibility: currentIngestVisibility,
                    author_id: activeProfile ? activeProfile.id : 1
                }),
                // 15 second timeout for heavy load
                signal: AbortSignal.timeout(15000) 
            });
            const data = await res.json();
            if (data.status === 'success') {
                triggerSuccess = true;
            } else {
                // [v1.8.1] Enhanced Error Reporting
                console.error("[TRIGGER] Server error:", data.message);
                alert("Archive trigger failed: " + (data.message || "Unknown Server Error"));
            }
        } catch (f) {
            console.warn("Trigger timeout or lag, checking heartbeat...", f);
            // [v1.8.1] Safety: Only assume success if it was a timeout, not a 400/500
            if (f.name === 'TimeoutError' || f.message.includes('timeout')) {
                triggerSuccess = true; 
            } else {
                alert("Connection error during trigger. Check server console.");
            }
        }
        
        if (triggerSuccess) {
            addBotMessage("Vaulting Triggered. The appliance is busy sensing your memories.");
            
            // Clear local progress and hand off to global sensing monitor
            progressContainer.style.display = 'none';
            startIngestionPolling();

        } else {
            alert("Archive trigger failed. Please check server logs.");
            commitBtn.disabled = false;
            commitBtn.style.display = "block";
            progressContainer.style.display = 'none';
        }
    } catch (e) {
        console.warn("Global Ingestion error:", e);
        startIngestionPolling();
    }
}

function updateNoteCounter(textarea) {
    const counter = textarea.parentElement.querySelector('.note-counter');
    if (counter) {
        counter.innerText = `${textarea.value.length} / 280`;
        counter.style.opacity = textarea.value.length > 250 ? "1" : "0.4";
        counter.style.color = textarea.value.length >= 280 ? "var(--accent-error)" : "inherit";
    }
}


// --- Continuity Governance (v1.2.0) ---

function updateWipeBanner(status, meta) {
    const banner = document.getElementById('continuity-banner');
    if (!banner) return;

    if (!status || status === 'IDLE') {
        banner.style.display = 'none';
        return;
    }

    banner.style.display = 'flex';
    banner.className = `governance-banner ${status === 'PENDING' ? 'banner-pending' : 'banner-authorized'}`;

    let content = "";
    if (status === 'PENDING') {
        content = `<span>⚠️ ARCHIVE DEPOSITORY SEALED: Destruction is Disabled.</span>`;
    } else {
        content = `<span>🛡️ PRESERVATION MODE ACTIVE: All memories are cryptographically protected.</span>`;
    }
    banner.innerHTML = content;
}

async function initiateWipe() {
    alert("Archival Destruction is DISABLED on this appliance");
    location.reload();
}




async function confirmWipe() {
    if (!activeProfile || !confirm("☣️ CRITICAL: Last chance. This will PERMANENTLY ERASE all memories and identities. Proceed with destruction?")) return;
    try {
        const res = await fetchWithAuth('api/vault/wipe/confirm', {
            method: 'POST',
            body: JSON.stringify({ user_id: activeProfile.id }),
            headers: {'Content-Type': 'application/json'}
        });
        const data = await res.json();
        alert(data.message);
        localStorage.clear();
        location.reload();
    } catch (e) { alert("Confirmation failed."); }
}

// [v1.2.0] Dynamic Button Text Toggle (INPUT/INGEST)
document.addEventListener('DOMContentLoaded', () => {
    const summaryToggle = document.getElementById('archive-summary-toggle');
    const sendBtnText = document.getElementById('send-btn-text');
    if (summaryToggle && sendBtnText) {
        summaryToggle.addEventListener('change', () => {
            sendBtnText.innerText = summaryToggle.checked ? 'INGEST' : 'INPUT';
        });
        // Initial state check
        sendBtnText.innerText = summaryToggle.checked ? 'INGEST' : 'INPUT';
    }
});

/**
 * [v1.9.0] Sovereign Storyboard: Browser-Side Frame Extraction
 * Asynchronously seeks through the archival video blob to capture witness frames.
 */
async function populateVideoStoryboard(url, container) {
    if (!container) return;
    container.innerHTML = '<div style="font-size:0.6rem; opacity:0.5; text-align:center; padding-top:20px;">Recalling Witnesses...</div>';

    try {
        const response = await fetchWithAuth(url);
        if (!response.ok) throw new Error("Archival Rejection");
        const blob = await response.blob();
        const blobUrl = URL.createObjectURL(blob);
        
        const v = document.createElement('video');
        v.src = blobUrl;
        v.muted = true;
        v.playsInline = true;
        
        v.onloadedmetadata = async () => {
            const duration = v.duration;
            // 0.1s, 25%, 50%, 75%, 95%
            const timestamps = [0.1, duration * 0.25, duration * 0.5, duration * 0.75, duration * 0.95];
            container.innerHTML = ''; // Clear "Recalling..."
            
            for (const time of timestamps) {
                const img = document.createElement('img');
                img.style.opacity = '0';
                img.style.transition = 'opacity 0.5s ease';
                img.title = `Witness Frame @ ${Math.round(time)}s`;
                container.appendChild(img);
                
                // Synchronous seek/capture loop
                v.currentTime = time;
                await new Promise(resolve => {
                    const onSeeked = () => {
                        v.removeEventListener('seeked', onSeeked);
                        resolve();
                    };
                    v.addEventListener('seeked', onSeeked);
                });
                
                // Capture to Canvas
                const canvas = document.createElement('canvas');
                // High-performance downsampling for sidebar (approx 120px wide)
                const scale = 120 / v.videoWidth;
                canvas.width = 120;
                canvas.height = v.videoHeight * scale;
                
                const ctx = canvas.getContext('2d');
                ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
                
                img.src = canvas.toDataURL('image/jpeg', 0.6);
                img.style.opacity = '1';
                img.style.borderRadius = '4px';
                img.style.marginBottom = '5px';
            }
            // Cleanup high-memory blob after a delay
            setTimeout(() => URL.revokeObjectURL(blobUrl), 10000);
        };
        
        v.onerror = () => {
            container.innerHTML = '<div style="font-size:0.6rem; color:var(--accent-primary); padding-top:20px;">Witness Corrupted</div>';
            URL.revokeObjectURL(blobUrl);
        };

    } catch (e) {
        console.warn("[STORYBOARD] Handshake failed:", e);
        container.innerHTML = ''; 
    }
}
