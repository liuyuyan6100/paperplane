"""Optional cloud authentication. Local mode keeps its loopback-only defaults."""
import os
import hashlib
import secrets
from urllib.parse import urlsplit


class CloudAccess:
    def __init__(self, origin, issuer, audience, email, *, keys=None):
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError("PAPERPLANE_PUBLIC_URL must be an HTTPS origin without a path")
        identity = urlsplit(issuer)
        if (identity.scheme != "https" or not identity.hostname
                or not identity.hostname.endswith(".cloudflareaccess.com")
                or identity.username or identity.password or identity.port
                or identity.path or identity.query or identity.fragment):
            raise ValueError("PAPERPLANE_ACCESS_ISSUER must be your Cloudflare Access HTTPS origin")
        if not audience or not email or "@" not in email:
            raise ValueError("Cloud mode requires an Access audience and owner email")
        import jwt
        self.jwt = jwt
        self.origin, self.host = origin, parsed.netloc
        self.issuer, self.audience, self.email = issuer, audience, email.lower()
        self.keys = keys or jwt.PyJWKClient(issuer + "/cdn-cgi/access/certs", timeout=8,
                                          cache_jwk_set=True, lifespan=300)

    @classmethod
    def from_env(cls):
        origin = os.environ.get("PAPERPLANE_PUBLIC_URL", "").rstrip("/")
        if not origin:
            return None
        return cls(origin, os.environ.get("PAPERPLANE_ACCESS_ISSUER", "").rstrip("/"),
                   os.environ.get("PAPERPLANE_ACCESS_AUDIENCE", ""),
                   os.environ.get("PAPERPLANE_OWNER_EMAIL", ""))

    def verify(self, token):
        if not isinstance(token, str) or not token or len(token) > 16384:
            return False
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = self.jwt.decode(token, key, algorithms=['RS256'], audience=self.audience,
                                     issuer=self.issuer, leeway=5,
                                     options={'require': ['exp', 'iat', 'sub', 'email', 'type']})
            return claims['type'] == 'app' and claims['email'].lower() == self.email
        except Exception:
            return False

    def verify_sync(self, token, device_id='', device_secret=''):
        """A separate Access application scopes this identity to the sync endpoint."""
        audience = os.environ.get('PAPERPLANE_SYNC_AUDIENCE', '')
        client_id = os.environ.get('PAPERPLANE_SYNC_CLIENT_ID', '')
        device_hash = os.environ.get('PAPERPLANE_SYNC_DEVICE_HASH', '')
        if device_hash and client_id and isinstance(device_secret, str) and 32 <= len(device_secret) <= 1024:
            if secrets.compare_digest(client_id, device_id) and secrets.compare_digest(device_hash, hashlib.sha256(device_secret.encode()).hexdigest()):
                return True
        if not audience or not client_id or not isinstance(token, str) or not token or len(token) > 16384:
            return False
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = self.jwt.decode(token, key, algorithms=['RS256'], audience=audience,
                                     issuer=self.issuer, leeway=5,
                                     options={'require': ['exp', 'iat', 'common_name', 'type']})
            return claims['type'] == 'app' and claims['common_name'] == client_id
        except Exception:
            return False
