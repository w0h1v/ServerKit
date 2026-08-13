"""One adapter per application platform, behind a three-method contract.

Each adapter answers the same three questions so the API and the UI never learn a
platform's vocabulary:

    verify()                 -> {'account': <label>}   validate the credential NOW
    list_projects()          -> [NORMALISED PROJECT]
    list_resources(project)  -> [NORMALISED RESOURCE]  deployments / services / DBs

Everything is read-only. These platforms host things ServerKit did not create, and
the same reasoning that blocks destroy on an adopted cloud server applies harder
here: a "delete project" button next to someone's production Supabase database is
not a feature, it is a hazard. Writes stay out until they are designed on purpose.

Why `verify()` exists at all: storing a credential that has never been exercised
is how a connection ends up looking configured while being broken. The cloud
provider path had exactly that bug — a bad Vultr key saved cleanly and only failed
later, at provision time. Every adapter here proves the token works before we keep
it.

NORMALISED PROJECT: external_id, name, status, url, region, framework, updated_at
NORMALISED RESOURCE: external_id, name, kind, status, url, updated_at
"""
import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 25


class PlatformError(Exception):
    """A platform rejected us, or answered in a way we cannot use."""


def _iso(value):
    """Normalise the three timestamp flavours these APIs return, or None.

    Vercel sends epoch milliseconds, Railway and Supabase send ISO strings. A
    caller should never have to know which.
    """
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        # Vercel's createdAt/updatedAt are ms since epoch.
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def _raise_for(resp, platform):
    """Turn an HTTP failure into a message an operator can act on.

    A bare 401 body is usually empty, and "request failed (401)" tells nobody
    whether the token is wrong, expired, or missing a scope.
    """
    if resp.status_code in (401, 403):
        raise PlatformError(
            f'{platform} rejected the token ({resp.status_code}) — check it is valid '
            'and has access to the account you expect')
    if resp.status_code == 429:
        raise PlatformError(f'{platform} rate-limited this request (429) — try again shortly')
    if resp.status_code >= 400:
        detail = ''
        try:
            body = resp.json()
            detail = body.get('message') or body.get('error') or ''
            if isinstance(detail, dict):
                detail = detail.get('message') or ''
        except Exception:
            detail = (resp.text or '')[:160]
        raise PlatformError(f'{platform} returned {resp.status_code}{": " + detail if detail else ""}')


class VercelAdapter:
    """Vercel REST API. Bearer token; an optional teamId scopes to a team."""

    platform = 'vercel'
    label = 'Vercel'
    BASE = 'https://api.vercel.com'
    token_hint = 'Account token from vercel.com/account/tokens'
    scope_hint = 'Team ID (optional — leave blank for your personal scope)'

    def __init__(self, token, scope_id=None):
        self.token = token
        self.scope_id = scope_id

    def _get(self, path, **params):
        if self.scope_id:
            params['teamId'] = self.scope_id
        resp = requests.get(f'{self.BASE}{path}',
                            headers={'Authorization': f'Bearer {self.token}'},
                            params=params or None, timeout=TIMEOUT)
        _raise_for(resp, self.label)
        return resp.json() or {}

    def verify(self):
        user = (self._get('/v2/user') or {}).get('user') or {}
        label = user.get('username') or user.get('email') or user.get('name')
        if self.scope_id:
            label = f'{label} · team {self.scope_id}' if label else f'team {self.scope_id}'
        return {'account': label or 'Vercel account'}

    def list_projects(self):
        out = []
        # Cursor pagination: `pagination.next` is a timestamp to pass as `until`.
        until = None
        for _page in range(20):
            body = self._get('/v9/projects', limit=100, **({'until': until} if until else {}))
            for p in body.get('projects') or []:
                latest = (p.get('targets') or {}).get('production') or {}
                out.append({
                    'external_id': p.get('id'),
                    'name': p.get('name'),
                    # Vercel has no project-level status; the production deployment's
                    # readyState is the closest honest answer, and absent if never deployed.
                    'status': (latest.get('readyState') or '').lower() or 'no deployment',
                    'url': f"https://{latest.get('url')}" if latest.get('url') else None,
                    'region': None,
                    'framework': p.get('framework'),
                    'updated_at': _iso(p.get('updatedAt')),
                })
            until = ((body.get('pagination') or {}).get('next'))
            if not until:
                break
        return out

    def list_resources(self, project):
        body = self._get('/v6/deployments', projectId=project, limit=20)
        return [{
            'external_id': d.get('uid') or d.get('id'),
            'name': d.get('name') or d.get('url') or 'deployment',
            'kind': 'deployment',
            'status': (d.get('readyState') or d.get('state') or '').lower() or None,
            'url': f"https://{d.get('url')}" if d.get('url') else None,
            'updated_at': _iso(d.get('created') or d.get('createdAt')),
        } for d in (body.get('deployments') or [])]


class SupabaseAdapter:
    """Supabase Management API. Bearer personal access token."""

    platform = 'supabase'
    label = 'Supabase'
    BASE = 'https://api.supabase.com'
    token_hint = 'Personal access token from supabase.com/dashboard/account/tokens'
    scope_hint = None

    def __init__(self, token, scope_id=None):
        self.token = token
        self.scope_id = scope_id

    def _get(self, path):
        resp = requests.get(f'{self.BASE}{path}',
                            headers={'Authorization': f'Bearer {self.token}'},
                            timeout=TIMEOUT)
        _raise_for(resp, self.label)
        return resp.json()

    def verify(self):
        orgs = self._get('/v1/organizations') or []
        names = [o.get('name') for o in orgs if isinstance(o, dict) and o.get('name')]
        return {'account': ', '.join(names[:2]) or 'Supabase account'}

    def list_projects(self):
        projects = self._get('/v1/projects') or []
        return [{
            'external_id': p.get('id') or p.get('ref'),
            'name': p.get('name'),
            'status': (p.get('status') or '').lower() or None,
            # The REST endpoint of a project is derived from its ref, and it is the
            # one thing an operator actually wants to copy out of this list.
            'url': f"https://{p.get('ref')}.supabase.co" if p.get('ref') else None,
            'region': p.get('region'),
            'framework': f"postgres {(p.get('database') or {}).get('version', '')}".strip()
                         if isinstance(p.get('database'), dict) else 'postgres',
            'updated_at': _iso(p.get('created_at')),
        } for p in projects if isinstance(p, dict)]

    def list_resources(self, project):
        # A project's own database is the resource worth surfacing; Supabase has no
        # deployment list to speak of.
        p = self._get(f'/v1/projects/{project}') or {}
        db = p.get('database') or {}
        if not db:
            return []
        return [{
            'external_id': f"{project}:db",
            'name': db.get('host') or 'database',
            'kind': 'database',
            'status': (p.get('status') or '').lower() or None,
            'url': None,
            'updated_at': _iso(p.get('created_at')),
        }]


class RailwayAdapter:
    """Railway public API — GraphQL, single endpoint, Bearer account token."""

    platform = 'railway'
    label = 'Railway'
    BASE = 'https://backboard.railway.com/graphql/v2'
    token_hint = 'Account or workspace token from Railway → Account → Tokens'
    scope_hint = None

    def __init__(self, token, scope_id=None):
        self.token = token
        self.scope_id = scope_id

    def _query(self, query, **variables):
        resp = requests.post(
            self.BASE,
            headers={'Authorization': f'Bearer {self.token}',
                     'Content-Type': 'application/json'},
            json={'query': query, 'variables': variables or {}}, timeout=TIMEOUT)
        _raise_for(resp, self.label)
        body = resp.json() or {}
        # GraphQL reports failure in a 200 body, so an HTTP check alone would call
        # an auth error a success and hand back empty data.
        if body.get('errors'):
            first = (body['errors'] or [{}])[0]
            raise PlatformError(f"{self.label}: {first.get('message') or 'query failed'}")
        return body.get('data') or {}

    def verify(self):
        data = self._query('query { me { id name email } }')
        me = data.get('me') or {}
        return {'account': me.get('name') or me.get('email') or 'Railway account'}

    def list_projects(self):
        data = self._query("""
            query {
              me {
                projects {
                  edges { node { id name description updatedAt
                    environments { edges { node { id name } } } } }
                }
              }
            }
        """)
        edges = (((data.get('me') or {}).get('projects') or {}).get('edges')) or []
        out = []
        for edge in edges:
            node = (edge or {}).get('node') or {}
            envs = (((node.get('environments') or {}).get('edges')) or [])
            out.append({
                'external_id': node.get('id'),
                'name': node.get('name'),
                # Railway has no project-level status field; environment count is
                # the useful fact, and inventing a health value would be a lie.
                'status': f'{len(envs)} environment' + ('' if len(envs) == 1 else 's'),
                'url': f"https://railway.com/project/{node.get('id')}" if node.get('id') else None,
                'region': None,
                'framework': node.get('description') or None,
                'updated_at': _iso(node.get('updatedAt')),
            })
        return out

    def list_resources(self, project):
        data = self._query("""
            query($id: String!) {
              project(id: $id) {
                services { edges { node { id name updatedAt } } }
              }
            }
        """, id=project)
        edges = (((data.get('project') or {}).get('services') or {}).get('edges')) or []
        return [{
            'external_id': (e.get('node') or {}).get('id'),
            'name': (e.get('node') or {}).get('name') or 'service',
            'kind': 'service',
            'status': None,
            'url': None,
            'updated_at': _iso((e.get('node') or {}).get('updatedAt')),
        } for e in edges]


ADAPTERS = {a.platform: a for a in (VercelAdapter, SupabaseAdapter, RailwayAdapter)}


def get_adapter(platform, token, scope_id=None):
    cls = ADAPTERS.get(platform)
    if not cls:
        raise PlatformError(f'Unsupported platform: {platform}')
    return cls(token, scope_id)


def catalog():
    """What the UI needs to render the connect form, without hardcoding it there."""
    return [{
        'platform': cls.platform,
        'label': cls.label,
        'token_hint': cls.token_hint,
        'scope_hint': cls.scope_hint,
    } for cls in ADAPTERS.values()]
