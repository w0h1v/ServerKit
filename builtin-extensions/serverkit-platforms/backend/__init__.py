"""ServerKit Platforms extension backend package.

Connections to application platforms (Railway / Vercel / Supabase) plus a live,
read-only inventory of what runs on them. Mounted at ``/api/v1/platforms`` via the
manifest's ``url_prefix``. The PlatformConnection model lives in core (same
arrangement as CloudProvider) so the schema is managed by core migrations; this
package owns the adapters, the service and the HTTP surface.
"""
from .platforms import platforms_bp

__all__ = ['platforms_bp']
