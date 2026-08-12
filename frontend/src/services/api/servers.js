// Server management, remote Docker, fleet management, terminals, cloud provisioning

// Servers (Multi-Server Management) endpoints
export async function getServers(options = {}) {
    // allWorkspaces neutralizes the ambient X-Workspace-Id header so a
    // cross-workspace view (resource management) sees every server.
    const config = options.allWorkspaces ? { headers: { 'X-Workspace-Id': '' } } : {};
    return this.request('/servers', config);
}

// Reassign a server to a workspace (#33). Pass null to move it to Default.
export async function setServerWorkspace(serverId, workspaceId) {
    return this.request(`/servers/${serverId}/workspace`, {
        method: 'PUT',
        body: { workspace_id: workspaceId },
    });
}

export async function getServer(id) {
    return this.request(`/servers/${id}`);
}

export async function createServer(data) {
    return this.request('/servers', {
        method: 'POST',
        body: data
    });
}

export async function updateServer(id, data) {
    return this.request(`/servers/${id}`, {
        method: 'PUT',
        body: data
    });
}

export async function deleteServer(id) {
    return this.request(`/servers/${id}`, {
        method: 'DELETE'
    });
}

export async function getServerStatus(id) {
    return this.request(`/servers/${id}/status`);
}

export async function getServerMetrics(id) {
    return this.request(`/servers/${id}/metrics`);
}

export async function pingServer(id) {
    return this.request(`/servers/${id}/ping`, {
        method: 'POST'
    });
}

// Server Onboarding (lifecycle state machine)
export async function startServerOnboarding(id) {
    return this.request(`/servers/${id}/onboarding/start`, {
        method: 'POST'
    });
}

export async function retryServerOnboarding(id) {
    return this.request(`/servers/${id}/onboarding/retry`, {
        method: 'POST'
    });
}

export async function getServerOnboardingStatus(id) {
    return this.request(`/servers/${id}/onboarding/status`);
}

// Server Registration
//
// Optionally pass { expires_in } to control token lifetime in seconds.
// -1 means "never expires"; missing means the panel default (7 days).
// Response includes connection_string + registration_token + registration_expires.
export async function generateRegistrationToken(serverId, body = {}) {
    return this.request(`/servers/${serverId}/regenerate-token`, {
        method: 'POST',
        body
    });
}

export async function registerServer(registrationData) {
    return this.request('/servers/register', {
        method: 'POST',
        body: registrationData
    });
}

// Server Groups
export async function getServerGroups() {
    return this.request('/servers/groups');
}

export async function createServerGroup(data) {
    return this.request('/servers/groups', {
        method: 'POST',
        body: data
    });
}

export async function updateServerGroup(id, data) {
    return this.request(`/servers/groups/${id}`, {
        method: 'PUT',
        body: data
    });
}

export async function deleteServerGroup(id) {
    return this.request(`/servers/groups/${id}`, {
        method: 'DELETE'
    });
}

// Remote Docker Operations (via agent)
export async function getRemoteContainers(serverId, all = false) {
    return this.request(`/servers/${serverId}/docker/containers?all=${all}`);
}

export async function getRemoteContainer(serverId, containerId) {
    return this.request(`/servers/${serverId}/docker/containers/${containerId}`);
}

export async function startRemoteContainer(serverId, containerId) {
    return this.request(`/servers/${serverId}/docker/containers/${containerId}/start`, {
        method: 'POST'
    });
}

export async function stopRemoteContainer(serverId, containerId) {
    return this.request(`/servers/${serverId}/docker/containers/${containerId}/stop`, {
        method: 'POST'
    });
}

export async function restartRemoteContainer(serverId, containerId) {
    return this.request(`/servers/${serverId}/docker/containers/${containerId}/restart`, {
        method: 'POST'
    });
}

export async function removeRemoteContainer(serverId, containerId, force = false) {
    return this.request(`/servers/${serverId}/docker/containers/${containerId}?force=${force}`, {
        method: 'DELETE'
    });
}

export async function getRemoteContainerStats(serverId, containerId) {
    return this.request(`/servers/${serverId}/docker/containers/${containerId}/stats`);
}

export async function getRemoteContainerLogs(serverId, containerId, tail = 100, since = null) {
    const params = new URLSearchParams({ tail });
    if (since) params.append('since', since);
    return this.request(`/servers/${serverId}/docker/containers/${containerId}/logs?${params}`);
}

export async function getRemoteImages(serverId) {
    return this.request(`/servers/${serverId}/docker/images`);
}

export async function pullRemoteImage(serverId, image) {
    return this.request(`/servers/${serverId}/docker/images/pull`, {
        method: 'POST',
        body: { image }
    });
}

export async function removeRemoteImage(serverId, imageId, force = false) {
    return this.request(`/servers/${serverId}/docker/images/${imageId}?force=${force}`, {
        method: 'DELETE'
    });
}

export async function getRemoteVolumes(serverId) {
    return this.request(`/servers/${serverId}/docker/volumes`);
}

export async function removeRemoteVolume(serverId, volumeName, force = false) {
    return this.request(`/servers/${serverId}/docker/volumes/${encodeURIComponent(volumeName)}?force=${force}`, {
        method: 'DELETE'
    });
}

export async function getRemoteNetworks(serverId) {
    return this.request(`/servers/${serverId}/docker/networks`);
}

export async function removeRemoteNetwork(serverId, networkId) {
    return this.request(`/servers/${serverId}/docker/networks/${encodeURIComponent(networkId)}`, {
        method: 'DELETE'
    });
}

export async function getRemoteSystemMetrics(serverId) {
    return this.request(`/servers/${serverId}/system/metrics`);
}

export async function getRemoteSystemInfo(serverId) {
    return this.request(`/servers/${serverId}/system/info`);
}

// Remote Cron Operations (via agent)
export async function getRemoteCronStatus(serverId) {
    return this.request(`/servers/${serverId}/cron/status`);
}

export async function getRemoteCronJobs(serverId) {
    return this.request(`/servers/${serverId}/cron/jobs`);
}

export async function addRemoteCronJob(serverId, body) {
    return this.request(`/servers/${serverId}/cron/jobs`, {
        method: 'POST',
        body
    });
}

// Edit an existing remote cron entry. Agent ids are content-derived, so a
// schedule/command change returns a fresh Entry (new id) the caller adopts.
// Gated on the agent's cron.update capability (agent >= 1.2.0).
export async function updateRemoteCronJob(serverId, jobId, body) {
    return this.request(`/servers/${serverId}/cron/jobs/${encodeURIComponent(jobId)}`, {
        method: 'PUT',
        body
    });
}

export async function removeRemoteCronJob(serverId, jobId) {
    return this.request(`/servers/${serverId}/cron/jobs/${encodeURIComponent(jobId)}`, {
        method: 'DELETE'
    });
}

export async function toggleRemoteCronJob(serverId, jobId, enabled) {
    return this.request(`/servers/${serverId}/cron/jobs/${encodeURIComponent(jobId)}/toggle`, {
        method: 'POST',
        body: { enabled }
    });
}

// Remote Capabilities

// Asks the agent to re-run its capability probe and pushes the
// refreshed payload back. Used after the user installs a runtime,
// configures sudoers, or adds a new package manager so feature tabs
// light up without restarting the agent service.
export async function refreshRemoteCapabilities(serverId) {
    return this.request(`/servers/${serverId}/refresh-capabilities`, {
        method: 'POST'
    });
}

// Remote Packages (via agent)
//
// install / upgrade are streaming: the panel returns
// { jobId, channel } and the frontend subscribes to the matching
// Socket.IO room (server_<id>_<channel>) for live install output.

export async function getRemotePackages(serverId) {
    return this.request(`/servers/${serverId}/packages`);
}

export async function searchRemotePackages(serverId, query, limit = 100) {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    return this.request(`/servers/${serverId}/packages/search?${params.toString()}`);
}

export async function getRemotePackageInfo(serverId, name) {
    return this.request(`/servers/${serverId}/packages/info/${encodeURIComponent(name)}`);
}

export async function updateRemotePackageCache(serverId) {
    return this.request(`/servers/${serverId}/packages/update-cache`, {
        method: 'POST'
    });
}

export async function installRemotePackages(serverId, names) {
    return this.request(`/servers/${serverId}/packages/install`, {
        method: 'POST',
        body: { names: Array.isArray(names) ? names : [names] }
    });
}

export async function removeRemotePackage(serverId, name) {
    return this.request(`/servers/${serverId}/packages/remove`, {
        method: 'POST',
        body: { name }
    });
}

export async function upgradeRemotePackages(serverId, { names = null, all = false } = {}) {
    const body = {};
    if (all) body.all = true;
    if (names && names.length) body.names = names;
    return this.request(`/servers/${serverId}/packages/upgrade`, {
        method: 'POST',
        body
    });
}

// Remote Services (systemd)

export async function getRemoteServices(serverId, { state = null, type = 'service' } = {}) {
    const params = new URLSearchParams({ type });
    if (state) params.set('state', state);
    return this.request(`/servers/${serverId}/services?${params.toString()}`);
}

export async function getRemoteServiceStatus(serverId, unit) {
    return this.request(`/servers/${serverId}/services/${encodeURIComponent(unit)}`);
}

export async function controlRemoteService(serverId, unit, action) {
    return this.request(`/servers/${serverId}/services/${encodeURIComponent(unit)}/${action}`, {
        method: 'POST'
    });
}

export async function getRemoteServiceLogs(serverId, unit, lines = 200) {
    const params = new URLSearchParams({ lines: String(lines) });
    return this.request(`/servers/${serverId}/services/${encodeURIComponent(unit)}/logs?${params.toString()}`);
}

export async function reloadRemoteSystemdDaemon(serverId) {
    return this.request(`/servers/${serverId}/services/daemon-reload`, {
        method: 'POST'
    });
}

// Remote Runtimes (pyenv / pyenv-win)
//
// pyenvBootstrap and installPythonVersion are streaming: returned
// payload contains { jobId, channel } and the frontend subscribes via
// the same Socket.IO room pattern as installRemotePackages.

export async function getRemoteRuntimes(serverId) {
    return this.request(`/servers/${serverId}/runtimes`);
}

export async function bootstrapRemotePyenv(serverId) {
    return this.request(`/servers/${serverId}/runtimes/pyenv/bootstrap`, {
        method: 'POST'
    });
}

export async function getRemotePythonVersions(serverId) {
    return this.request(`/servers/${serverId}/runtimes/python`);
}

export async function getRemotePythonAvailable(serverId) {
    return this.request(`/servers/${serverId}/runtimes/python/available`);
}

export async function getRemotePythonCurrent(serverId) {
    return this.request(`/servers/${serverId}/runtimes/python/current`);
}

export async function installRemotePythonVersion(serverId, version) {
    return this.request(`/servers/${serverId}/runtimes/python/install`, {
        method: 'POST',
        body: { version }
    });
}

export async function uninstallRemotePythonVersion(serverId, version) {
    return this.request(`/servers/${serverId}/runtimes/python/uninstall`, {
        method: 'POST',
        body: { version }
    });
}

export async function setRemotePythonGlobal(serverId, version) {
    return this.request(`/servers/${serverId}/runtimes/python/global`, {
        method: 'POST',
        body: { version }
    });
}

export async function setRemotePythonLocal(serverId, version, dir) {
    return this.request(`/servers/${serverId}/runtimes/python/local`, {
        method: 'POST',
        body: { version, dir }
    });
}

// Remote Files (via agent)
export async function getRemoteAllowedPaths(serverId) {
    return this.request(`/servers/${serverId}/files/allowed-paths`);
}

export async function browseRemoteFiles(serverId, path) {
    return this.request(`/servers/${serverId}/files/browse?path=${encodeURIComponent(path)}`);
}

export async function readRemoteFile(serverId, path) {
    return this.request(`/servers/${serverId}/files/read?path=${encodeURIComponent(path)}`);
}

export async function writeRemoteFile(serverId, path, content) {
    return this.request(`/servers/${serverId}/files/write`, {
        method: 'POST',
        body: { path, content }
    });
}

// Remote Cloudflared (Tunnels)
export async function getRemoteCloudflaredStatus(serverId) {
    return this.request(`/servers/${serverId}/cloudflared/status`);
}

// Triggers `cloudflared tunnel login` on the agent. Returns
// {job_id, channel}; subscribe via JobProgressModal to receive the
// auth URL, then watch for the done event when cert.pem lands.
export async function startRemoteCloudflaredLogin(serverId) {
    return this.request(`/servers/${serverId}/cloudflared/login`, {
        method: 'POST'
    });
}

export async function getRemoteCloudflaredTunnels(serverId) {
    return this.request(`/servers/${serverId}/cloudflared/tunnels`);
}

export async function createRemoteCloudflaredTunnel(serverId, name) {
    return this.request(`/servers/${serverId}/cloudflared/tunnels`, {
        method: 'POST',
        body: { name }
    });
}

export async function routeRemoteCloudflaredTunnel(serverId, tunnelRef, hostname) {
    return this.request(`/servers/${serverId}/cloudflared/tunnels/${encodeURIComponent(tunnelRef)}/route`, {
        method: 'POST',
        body: { hostname }
    });
}

export async function deleteRemoteCloudflaredTunnel(serverId, tunnelRef) {
    return this.request(`/servers/${serverId}/cloudflared/tunnels/${encodeURIComponent(tunnelRef)}`, {
        method: 'DELETE'
    });
}

// Get available servers for Docker operations
export async function getAvailableServers() {
    return this.request('/servers/available');
}

// Remote Docker Compose Operations
export async function getRemoteComposeProjects(serverId) {
    return this.request(`/servers/${serverId}/docker/compose/projects`);
}

export async function getRemoteComposePs(serverId, projectPath) {
    return this.request(`/servers/${serverId}/docker/compose/ps`, {
        method: 'POST',
        body: { project_path: projectPath }
    });
}

export async function remoteComposeUp(serverId, projectPath, options = {}) {
    return this.request(`/servers/${serverId}/docker/compose/up`, {
        method: 'POST',
        body: {
            project_path: projectPath,
            detach: options.detach !== false,
            build: options.build || false
        }
    });
}

export async function remoteComposeDown(serverId, projectPath, options = {}) {
    return this.request(`/servers/${serverId}/docker/compose/down`, {
        method: 'POST',
        body: {
            project_path: projectPath,
            volumes: options.volumes || false,
            remove_orphans: options.removeOrphans !== false
        }
    });
}

export async function remoteComposeLogs(serverId, projectPath, service = null, tail = 100) {
    return this.request(`/servers/${serverId}/docker/compose/logs`, {
        method: 'POST',
        body: {
            project_path: projectPath,
            service: service,
            tail: tail
        }
    });
}

export async function remoteComposeRestart(serverId, projectPath, service = null) {
    return this.request(`/servers/${serverId}/docker/compose/restart`, {
        method: 'POST',
        body: {
            project_path: projectPath,
            service: service
        }
    });
}

export async function remoteComposePull(serverId, projectPath, service = null) {
    return this.request(`/servers/${serverId}/docker/compose/pull`, {
        method: 'POST',
        body: {
            project_path: projectPath,
            service: service
        }
    });
}

// Server Historical Metrics
export async function getServerMetricsHistory(serverId, period = '24h') {
    return this.request(`/servers/${serverId}/metrics/history?period=${period}`);
}

export async function getServerMetricsAggregated(serverId, period = '24h', aggregation = 'hourly') {
    return this.request(`/servers/${serverId}/metrics/aggregated?period=${period}&aggregation=${aggregation}`);
}

export async function compareServerMetrics(serverIds, metric = 'cpu', period = '24h') {
    const ids = Array.isArray(serverIds) ? serverIds.join(',') : serverIds;
    return this.request(`/servers/metrics/compare?ids=${ids}&metric=${metric}&period=${period}`);
}

export async function getMetricsRetentionStats() {
    return this.request('/servers/metrics/retention');
}

export async function triggerMetricsCleanup() {
    return this.request('/servers/metrics/cleanup', { method: 'POST' });
}

// Remote Terminal
export async function createTerminalSession(serverId, cols = 80, rows = 24) {
    return this.request(`/servers/${serverId}/terminal`, {
        method: 'POST',
        body: { cols, rows }
    });
}

export async function sendTerminalInput(sessionId, data) {
    return this.request(`/servers/terminal/${sessionId}/input`, {
        method: 'POST',
        body: { data }
    });
}

export async function resizeTerminal(sessionId, cols, rows) {
    return this.request(`/servers/terminal/${sessionId}/resize`, {
        method: 'POST',
        body: { cols, rows }
    });
}

export async function closeTerminalSession(sessionId) {
    return this.request(`/servers/terminal/${sessionId}`, {
        method: 'DELETE'
    });
}

export async function listTerminalSessions() {
    return this.request('/servers/terminal/sessions');
}

// Security Features (per-server)
export async function getAllowedIPs(serverId) {
    return this.request(`/servers/${serverId}/allowed-ips`);
}

export async function updateAllowedIPs(serverId, allowedIPs) {
    return this.request(`/servers/${serverId}/allowed-ips`, {
        method: 'PUT',
        body: { allowed_ips: allowedIPs }
    });
}

export async function getConnectionInfo(serverId) {
    return this.request(`/servers/${serverId}/connection-info`);
}

export async function rotateAPIKey(serverId) {
    return this.request(`/servers/${serverId}/rotate-api-key`, {
        method: 'POST'
    });
}

export async function getServerSecurityAlerts(serverId, options = {}) {
    const params = new URLSearchParams();
    if (options.status) params.append('status', options.status);
    if (options.severity) params.append('severity', options.severity);
    if (options.limit) params.append('limit', options.limit);
    const query = params.toString() ? `?${params.toString()}` : '';
    return this.request(`/servers/${serverId}/security/alerts${query}`);
}

export async function getAllSecurityAlerts(options = {}) {
    const params = new URLSearchParams();
    if (options.status) params.append('status', options.status);
    if (options.severity) params.append('severity', options.severity);
    if (options.type) params.append('type', options.type);
    if (options.limit) params.append('limit', options.limit);
    const query = params.toString() ? `?${params.toString()}` : '';
    return this.request(`/servers/security/alerts${query}`);
}

export async function getSecurityAlertCounts(serverId = null) {
    const query = serverId ? `?server_id=${serverId}` : '';
    return this.request(`/servers/security/alerts/counts${query}`);
}

export async function acknowledgeAlert(alertId) {
    return this.request(`/servers/security/alerts/${alertId}/acknowledge`, {
        method: 'POST'
    });
}

export async function resolveAlert(alertId) {
    return this.request(`/servers/security/alerts/${alertId}/resolve`, {
        method: 'POST'
    });
}

// Agent Downloads
export async function getAgentVersion() {
    return this.request('/servers/agent/version');
}

export async function getAgentDownloadUrl(os, arch) {
    const baseUrl = this.baseUrl.replace('/api/v1', '');
    return `${baseUrl}/api/v1/servers/agent/download/${os}/${arch}`;
}

// Agent Fleet Management endpoints
export async function getFleetHealth() {
    return this.request('/servers/fleet/health');
}

export async function getAgentVersions() {
    return this.request('/servers/fleet/versions');
}

export async function addAgentVersion(data) {
    return this.request('/servers/fleet/versions', {
        method: 'POST',
        body: data
    });
}

export async function upgradeFleet(serverIds, versionId) {
    return this.request('/servers/fleet/upgrade', {
        method: 'POST',
        body: { server_ids: serverIds, version_id: versionId }
    });
}

export async function startRollout(data) {
    return this.request('/servers/fleet/rollout', {
        method: 'POST',
        body: data
    });
}

export async function getRollouts(status) {
    const query = status ? `?status=${status}` : '';
    return this.request(`/servers/fleet/rollouts${query}`);
}

export async function getRollout(rolloutId) {
    return this.request(`/servers/fleet/rollouts/${rolloutId}`);
}

export async function cancelRollout(rolloutId) {
    return this.request(`/servers/fleet/rollouts/${rolloutId}/cancel`, {
        method: 'POST'
    });
}

export async function startDiscovery(duration = 10) {
    return this.request(`/servers/fleet/discovery?duration=${duration}`, {
        method: 'POST'
    });
}

export async function getDiscoveredAgents() {
    return this.request('/servers/fleet/discovery');
}

export async function approveRegistration(serverId) {
    return this.request(`/servers/fleet/approve/${serverId}`, {
        method: 'POST'
    });
}

export async function rejectRegistration(serverId) {
    return this.request(`/servers/fleet/reject/${serverId}`, {
        method: 'POST'
    });
}

export async function getQueuedCommands(serverId) {
    const query = serverId ? `?server_id=${serverId}` : '';
    return this.request(`/servers/fleet/commands/queued${query}`);
}

export async function retryCommand(commandId) {
    return this.request(`/servers/fleet/commands/${commandId}/retry`, {
        method: 'POST'
    });
}

export async function getServerDiagnostics(serverId) {
    return this.request(`/servers/fleet/diagnostics/${serverId}`);
}

// Fleet Monitor (Cross-Server Monitoring) endpoints
export async function getFleetHeatmap(groupId) {
    const query = groupId ? `?group_id=${groupId}` : '';
    return this.request(`/fleet-monitor/heatmap${query}`);
}

export async function getFleetComparison(serverIds, metric, period) {
    const ids = serverIds.join(',');
    return this.request(`/fleet-monitor/comparison?ids=${ids}&metric=${metric}&period=${period}`);
}

export async function getFleetAlerts(params = {}) {
    const searchParams = new URLSearchParams();
    if (params.status) searchParams.append('status', params.status);
    if (params.severity) searchParams.append('severity', params.severity);
    if (params.server_id) searchParams.append('server_id', params.server_id);
    if (params.limit) searchParams.append('limit', params.limit);
    const query = searchParams.toString();
    return this.request(`/fleet-monitor/alerts${query ? '?' + query : ''}`);
}

export async function acknowledgeFleetAlert(alertId) {
    return this.request(`/fleet-monitor/alerts/${alertId}/acknowledge`, { method: 'POST' });
}

export async function resolveFleetAlert(alertId) {
    return this.request(`/fleet-monitor/alerts/${alertId}/resolve`, { method: 'POST' });
}

export async function getFleetThresholds(serverId) {
    const query = serverId ? `?server_id=${serverId}` : '';
    return this.request(`/fleet-monitor/thresholds${query}`);
}

export async function createFleetThreshold(data) {
    return this.request('/fleet-monitor/thresholds', { method: 'POST', body: data });
}

export async function deleteFleetThreshold(thresholdId) {
    return this.request(`/fleet-monitor/thresholds/${thresholdId}`, { method: 'DELETE' });
}

export async function getFleetAnomalies(serverId) {
    const query = serverId ? `?server_id=${serverId}` : '';
    return this.request(`/fleet-monitor/anomalies${query}`);
}

export async function getCapacityForecast(serverId, metric = 'disk') {
    return this.request(`/fleet-monitor/forecast/${serverId}?metric=${metric}`);
}

export async function searchFleet(query, type = 'any') {
    return this.request(`/fleet-monitor/search?q=${encodeURIComponent(query)}&type=${type}`);
}

export async function exportFleetCsv(serverIds, metric, period) {
    const ids = serverIds.join(',');
    const url = `${this.baseUrl}/fleet-monitor/export/csv?ids=${ids}&metric=${metric}&period=${period}`;
    const token = this.getToken();
    const response = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {}
    });
    return response.blob();
}

// Agent Plugins endpoints
export async function getAgentPlugins(status) {
    const query = status ? `?status=${status}` : '';
    return this.request(`/agent-plugins/${query}`);
}

export async function getAgentPlugin(id) {
    return this.request(`/agent-plugins/${id}`);
}

export async function createAgentPlugin(data) {
    return this.request('/agent-plugins/', { method: 'POST', body: data });
}

export async function updateAgentPlugin(id, data) {
    return this.request(`/agent-plugins/${id}`, { method: 'PUT', body: data });
}

export async function deleteAgentPlugin(id) {
    return this.request(`/agent-plugins/${id}`, { method: 'DELETE' });
}

export async function installAgentPlugin(pluginId, serverId, config) {
    return this.request(`/agent-plugins/${pluginId}/install`, {
        method: 'POST', body: { server_id: serverId, config }
    });
}

export async function bulkInstallAgentPlugin(pluginId, serverIds, config) {
    return this.request(`/agent-plugins/${pluginId}/bulk-install`, {
        method: 'POST', body: { server_ids: serverIds, config }
    });
}

export async function getPluginInstallations(pluginId) {
    return this.request(`/agent-plugins/${pluginId}/installations`);
}

export async function getServerPlugins(serverId) {
    return this.request(`/agent-plugins/server/${serverId}`);
}

export async function enablePluginInstall(installId) {
    return this.request(`/agent-plugins/installs/${installId}/enable`, { method: 'POST' });
}

export async function disablePluginInstall(installId) {
    return this.request(`/agent-plugins/installs/${installId}/disable`, { method: 'POST' });
}

export async function uninstallPlugin(installId) {
    return this.request(`/agent-plugins/installs/${installId}`, { method: 'DELETE' });
}

export async function updatePluginInstallConfig(installId, config) {
    return this.request(`/agent-plugins/installs/${installId}/config`, {
        method: 'PUT', body: { config }
    });
}

export async function getPluginSpec() {
    return this.request('/agent-plugins/spec');
}

// Server Templates endpoints
export async function getServerTemplates(category) {
    const query = category ? `?category=${category}` : '';
    return this.request(`/server-templates/${query}`);
}

export async function getServerTemplate(id) {
    return this.request(`/server-templates/${id}`);
}

export async function getServerTemplateLibrary() {
    return this.request('/server-templates/library');
}

export async function createServerTemplateFromLibrary(key) {
    return this.request(`/server-templates/library/${key}`, { method: 'POST' });
}

export async function createServerTemplate(data) {
    return this.request('/server-templates/', { method: 'POST', body: data });
}

export async function updateServerTemplate(id, data) {
    return this.request(`/server-templates/${id}`, { method: 'PUT', body: data });
}

export async function deleteServerTemplate(id) {
    return this.request(`/server-templates/${id}`, { method: 'DELETE' });
}

export async function assignServerTemplate(templateId, serverId) {
    return this.request(`/server-templates/${templateId}/assign`, {
        method: 'POST', body: { server_id: serverId }
    });
}

export async function bulkAssignTemplate(templateId, serverIds) {
    return this.request(`/server-templates/${templateId}/bulk-assign`, {
        method: 'POST', body: { server_ids: serverIds }
    });
}

export async function getTemplateAssignments(templateId) {
    return this.request(`/server-templates/${templateId}/assignments`);
}

export async function unassignTemplate(assignmentId) {
    return this.request(`/server-templates/assignments/${assignmentId}`, { method: 'DELETE' });
}

export async function checkTemplateDrift(assignmentId) {
    return this.request(`/server-templates/assignments/${assignmentId}/check`, { method: 'POST' });
}

export async function remediateTemplateDrift(assignmentId) {
    return this.request(`/server-templates/assignments/${assignmentId}/remediate`, { method: 'POST' });
}

export async function getTemplateCompliance() {
    return this.request('/server-templates/compliance');
}

export async function getServerTemplateAssignments(serverId) {
    return this.request(`/server-templates/server/${serverId}`);
}

// Workspaces endpoints
export async function getWorkspaces(params = {}) {
    const query = new URLSearchParams();
    if (params.all) query.append('all', 'true');
    if (params.include_archived) query.append('include_archived', 'true');
    const qs = query.toString();
    return this.request(`/workspaces/${qs ? '?' + qs : ''}`);
}

export async function getWorkspace(id) {
    return this.request(`/workspaces/${id}`);
}

export async function createWorkspace(data) {
    return this.request('/workspaces/', { method: 'POST', body: data });
}

export async function updateWorkspace(id, data) {
    return this.request(`/workspaces/${id}`, { method: 'PUT', body: data });
}

export async function archiveWorkspace(id) {
    return this.request(`/workspaces/${id}/archive`, { method: 'POST' });
}

export async function restoreWorkspace(id) {
    return this.request(`/workspaces/${id}/restore`, { method: 'POST' });
}

export async function deleteWorkspace(id) {
    return this.request(`/workspaces/${id}`, { method: 'DELETE' });
}

export async function getWorkspaceMembers(workspaceId) {
    return this.request(`/workspaces/${workspaceId}/members`);
}

export async function addWorkspaceMember(workspaceId, userId, role) {
    return this.request(`/workspaces/${workspaceId}/members`, {
        method: 'POST', body: { user_id: userId, role: role || 'member' }
    });
}

export async function updateWorkspaceMemberRole(memberId, role) {
    return this.request(`/workspaces/members/${memberId}/role`, {
        method: 'PUT', body: { role }
    });
}

export async function removeWorkspaceMember(memberId) {
    return this.request(`/workspaces/members/${memberId}`, { method: 'DELETE' });
}

export async function getWorkspaceApiKeys(workspaceId) {
    return this.request(`/workspaces/${workspaceId}/api-keys`);
}

export async function createWorkspaceApiKey(workspaceId, name, scopes) {
    return this.request(`/workspaces/${workspaceId}/api-keys`, {
        method: 'POST', body: { name, scopes }
    });
}

export async function revokeWorkspaceApiKey(keyId) {
    return this.request(`/workspaces/api-keys/${keyId}/revoke`, { method: 'POST' });
}

// Cloud Provisioning endpoints
export async function getCloudProviders() {
    return this.request('/cloud/providers');
}

export async function createCloudProvider(data) {
    return this.request('/cloud/providers', { method: 'POST', body: data });
}

export async function deleteCloudProvider(id) {
    return this.request(`/cloud/providers/${id}`, { method: 'DELETE' });
}

export async function getCloudProviderOptions(type) {
    return this.request(`/cloud/providers/${type}/options`);
}

// Read-only preview of what an import WOULD adopt. Writes nothing, so it is safe
// to call before asking the operator to confirm.
export async function discoverCloudProvider(id) {
    return this.request(`/cloud/providers/${id}/discover`);
}

// Adopt the provider's untracked servers and refresh the tracked ones. Destroys
// nothing — a server missing remotely is flagged, never deleted.
export async function syncCloudProvider(id) {
    return this.request(`/cloud/providers/${id}/sync`, { method: 'POST' });
}

export async function getCloudServers(providerId) {
    const query = providerId ? `?provider_id=${providerId}` : '';
    return this.request(`/cloud/servers${query}`);
}

export async function getCloudServer(id) {
    return this.request(`/cloud/servers/${id}`);
}

export async function createCloudServer(data) {
    return this.request('/cloud/servers', { method: 'POST', body: data });
}

export async function destroyCloudServer(id) {
    return this.request(`/cloud/servers/${id}`, { method: 'DELETE' });
}

export async function resizeCloudServer(id, size) {
    return this.request(`/cloud/servers/${id}/resize`, { method: 'POST', body: { size } });
}

export async function getCloudSnapshots(serverId) {
    return this.request(`/cloud/servers/${serverId}/snapshots`);
}

export async function createCloudSnapshot(serverId, name) {
    return this.request(`/cloud/servers/${serverId}/snapshots`, { method: 'POST', body: { name } });
}

export async function deleteCloudSnapshot(id) {
    return this.request(`/cloud/snapshots/${id}`, { method: 'DELETE' });
}

export async function getCloudCosts() {
    return this.request('/cloud/costs');
}

// Linked Panel — this panel linked to a MASTER ServerKit as a worker
// (embedded agent mode: the master manages this server without a separate
// Go agent install).
export async function getLinkedPanel() {
    return this.request('/linked-panel');
}

export async function linkPanel(data) {
    // data: { master_url, registration_token, name? }
    return this.request('/linked-panel', { method: 'POST', body: data });
}

export async function unlinkPanel() {
    return this.request('/linked-panel', { method: 'DELETE' });
}
