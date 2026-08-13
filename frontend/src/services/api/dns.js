// DNS Zones & Records

export async function getDNSZones() {
    return this.request('/dns/');
}

export async function getDNSZone(zoneId) {
    return this.request(`/dns/${zoneId}`);
}

export async function createDNSZone(data) {
    return this.request('/dns/', { method: 'POST', body: data });
}

// Domains-page portfolio: every domain across connected DNS providers, merged with
// adopted zones (read-only; viewing is open to any authenticated user).
export async function getDnsPortfolio() {
    return this.request('/dns/portfolio');
}

// Idempotently adopt a provider domain into a managed zone so it can be edited /
// reached in the Cloudflare ops surface. Returns the (existing-or-new) zone.
export async function adoptDnsZone(domain, dnsProviderConfigId) {
    return this.request('/dns/adopt', {
        method: 'POST',
        body: { domain, dns_provider_config_id: dnsProviderConfigId },
    });
}

// Live records for a provider zone addressed by connection + provider zone id —
// lets the Domains drawer show a domain's real DNS without adopting it first.
export async function getProviderRecords(configId, providerZoneId) {
    const params = new URLSearchParams({ config_id: configId, zone: providerZoneId });
    return this.request(`/dns/provider-records?${params.toString()}`);
}

// Create or update one record in a live provider zone. Pass provider_record_id to
// edit in place. A record ServerKit did not create comes back 409 with
// requires_confirmation, so the caller can re-send with allow_foreign rather than
// silently overwriting someone's hand-written record.
export async function writeProviderRecord(configId, providerZoneId, record, options = {}) {
    return this.request('/dns/provider-records', {
        method: 'PUT',
        body: {
            config_id: configId,
            zone: providerZoneId,
            ...record,
            ...(options.allowForeign ? { allow_foreign: true } : {}),
        },
    });
}

// Delete one record from a live provider zone. Also 409s with
// requires_confirmation for a record ServerKit did not create.
export async function deleteProviderRecord(configId, providerZoneId, providerRecordId, options = {}) {
    return this.request('/dns/provider-records', {
        method: 'DELETE',
        body: {
            config_id: configId,
            zone: providerZoneId,
            provider_record_id: providerRecordId,
            ...(options.allowForeign ? { allow_foreign: true } : {}),
        },
    });
}

// Registration expiry / registrar via RDAP (WHOIS successor) — lazy fallback when
// no connected provider has the data. One domain per call.
export async function getDomainRegistration(domain) {
    return this.request(`/dns/registration?domain=${encodeURIComponent(domain)}`);
}

export async function deleteDNSZone(zoneId) {
    return this.request(`/dns/${zoneId}`, { method: 'DELETE' });
}

export async function getDNSRecords(zoneId) {
    return this.request(`/dns/${zoneId}/records`);
}

export async function createDNSRecord(zoneId, data) {
    return this.request(`/dns/${zoneId}/records`, { method: 'POST', body: data });
}

export async function updateDNSRecord(recordId, data) {
    return this.request(`/dns/records/${recordId}`, { method: 'PUT', body: data });
}

export async function deleteDNSRecord(recordId) {
    return this.request(`/dns/records/${recordId}`, { method: 'DELETE' });
}

export async function getDNSPresets() {
    return this.request('/dns/presets');
}

export async function applyDNSPreset(zoneId, preset, variables = {}) {
    return this.request(`/dns/${zoneId}/apply-preset`, {
        method: 'POST',
        body: { preset, variables },
    });
}

export async function checkDNSPropagation(domain, type = 'A') {
    return this.request(`/dns/propagation/${domain}?type=${type}`);
}

export async function exportDNSZone(zoneId) {
    return this.request(`/dns/${zoneId}/export`);
}

// DNS unification — provider change log & live zone mirror (Cloudflare)
export async function getDnsChanges({ configId, zone, result, limit } = {}) {
    const params = new URLSearchParams();
    if (configId != null) params.append('config_id', configId);
    if (zone) params.append('zone', zone);
    if (result) params.append('result', result);
    if (limit != null) params.append('limit', limit);
    const qs = params.toString();
    return this.request(`/dns/changes${qs ? `?${qs}` : ''}`);
}

export async function getZoneMirror(zoneId) {
    return this.request(`/dns/${zoneId}/mirror`);
}

// Every DNS record ServerKit owns across all provider zones, in one place.
export async function getManagedDnsRecords() {
    return this.request('/dns/managed');
}

export async function importDNSZone(zoneId, zoneFile) {
    return this.request(`/dns/${zoneId}/import`, {
        method: 'POST',
        body: { zone_file: zoneFile },
    });
}

// Dynamic DNS
export async function getDdnsHosts() {
    return this.request('/ddns/hosts');
}

export async function createDdnsHost(data) {
    return this.request('/ddns/hosts', { method: 'POST', body: data });
}

export async function deleteDdnsHost(hostId) {
    return this.request(`/ddns/hosts/${hostId}`, { method: 'DELETE' });
}

export async function regenerateDdnsToken(hostId) {
    return this.request(`/ddns/hosts/${hostId}/regenerate-token`, { method: 'POST' });
}
