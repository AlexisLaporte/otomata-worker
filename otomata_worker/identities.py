"""Identity management for platform accounts."""

from datetime import datetime
from typing import Optional

from sqlalchemy import func

from .models import Identity, RateLimit
from .database import get_session
from .secrets import secrets_service


class IdentityManager:
    """Manage platform identities with rate limit awareness."""

    def get_available(self, platform: str, action_type: Optional[str] = None) -> Optional[int]:
        """Get least-used active identity ID that can make requests.

        Args:
            platform: Platform name (linkedin, kaspr)
            action_type: Optional action to check rate limits for

        Returns:
            Identity ID with lowest recent usage, or None if all exhausted
        """
        with get_session() as session:
            # Get active identities ordered by last_used_at (oldest first)
            identities = session.query(Identity).filter(
                Identity.platform == platform,
                Identity.status == 'active'
            ).order_by(
                Identity.last_used_at.asc().nullsfirst()
            ).all()

            if not identities:
                return None

            if not action_type:
                return identities[0].id

            # Check rate limits for each
            from .rate_limiter import DBRateLimiter
            rate_limiter = DBRateLimiter()

            for identity in identities:
                can_request, _ = rate_limiter.can_request(identity.id, action_type)
                if can_request:
                    return identity.id

            return None

    def get_by_name(self, platform: str, name: str) -> Optional[dict]:
        """Get identity by platform and name."""
        with get_session() as session:
            identity = session.query(Identity).filter(
                Identity.platform == platform,
                Identity.name == name
            ).first()
            if identity:
                return self._to_dict(identity)
            return None

    def get_by_id(self, identity_id: int) -> Optional[dict]:
        """Get identity by ID."""
        with get_session() as session:
            identity = session.query(Identity).get(identity_id)
            if identity:
                return self._to_dict(identity)
            return None

    def _to_dict(self, identity: Identity) -> dict:
        """Convert identity to dict."""
        return {
            'id': identity.id,
            'platform': identity.platform,
            'name': identity.name,
            'account_type': identity.account_type,
            'status': identity.status,
            'user_agent': identity.user_agent,
            'last_used_at': identity.last_used_at,
            'blocked_at': identity.blocked_at,
            'blocked_reason': identity.blocked_reason,
            'created_at': identity.created_at,
        }

    def mark_used(self, identity_id: int):
        """Update last_used_at timestamp."""
        with get_session() as session:
            identity = session.query(Identity).get(identity_id)
            if identity:
                identity.last_used_at = datetime.utcnow()

    def mark_blocked(self, identity_id: int, reason: str):
        """Mark identity as blocked."""
        with get_session() as session:
            identity = session.query(Identity).get(identity_id)
            if identity:
                identity.status = 'blocked'
                identity.blocked_at = datetime.utcnow()
                identity.blocked_reason = reason

    def mark_active(self, identity_id: int):
        """Mark identity as active (unblock)."""
        with get_session() as session:
            identity = session.query(Identity).get(identity_id)
            if identity:
                identity.status = 'active'
                identity.blocked_at = None
                identity.blocked_reason = None

    def get_cookie(self, identity_id: int) -> Optional[str]:
        """Get decrypted cookie for identity."""
        with get_session() as session:
            identity = session.query(Identity).get(identity_id)
            if identity and identity.cookie_encrypted:
                return secrets_service.decrypt(identity.cookie_encrypted)
            return None

    def set_cookie(self, identity_id: int, cookie: str):
        """Set encrypted cookie for identity."""
        encrypted = secrets_service.encrypt(cookie)
        with get_session() as session:
            identity = session.query(Identity).get(identity_id)
            if identity:
                identity.cookie_encrypted = encrypted

    def create(
        self,
        platform: str,
        name: str,
        cookie: Optional[str] = None,
        user_agent: Optional[str] = None,
        account_type: str = 'free',
        status: str = 'active'
    ) -> int:
        """Create a new identity. Returns identity ID."""
        with get_session() as session:
            identity = Identity(
                platform=platform,
                name=name,
                account_type=account_type,
                status=status,
                user_agent=user_agent
            )
            if cookie:
                identity.cookie_encrypted = secrets_service.encrypt(cookie)

            session.add(identity)
            session.flush()
            return identity.id

    def list_all(self, platform: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
        """List identities with stats."""
        with get_session() as session:
            query = session.query(Identity)

            if platform:
                query = query.filter(Identity.platform == platform)
            if status:
                query = query.filter(Identity.status == status)

            identities = query.order_by(Identity.platform, Identity.name).all()

            return [
                {
                    'id': i.id,
                    'platform': i.platform,
                    'name': i.name,
                    'account_type': i.account_type,
                    'status': i.status,
                    'last_used_at': i.last_used_at.isoformat() if i.last_used_at else None,
                    'blocked_at': i.blocked_at.isoformat() if i.blocked_at else None,
                    'blocked_reason': i.blocked_reason,
                }
                for i in identities
            ]

    def delete(self, identity_id: int) -> bool:
        """Delete an identity."""
        with get_session() as session:
            identity = session.query(Identity).get(identity_id)
            if identity:
                session.delete(identity)
                session.commit()
                return True
            return False
