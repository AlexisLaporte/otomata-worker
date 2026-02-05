"""DB-backed rate limiter for distributed workers."""

from datetime import datetime, date, timedelta
from typing import Tuple, Optional

from .models import RateLimit
from .database import get_session


# Default rate limits by action type
DEFAULT_LIMITS = {
    # LinkedIn
    'profile_visit': {'hourly': 30, 'daily': 150},
    'search': {'hourly': 20, 'daily': 100},
    'connection_request': {'hourly': 10, 'daily': 50},
    'message': {'hourly': 15, 'daily': 75},
    # Kaspr
    'kaspr_lookup': {'hourly': 50, 'daily': 500},
    # Default
    'default': {'hourly': 60, 'daily': 300},
}


class DBRateLimiter:
    """Database-backed rate limiter with hourly and daily limits."""

    def __init__(self, limits: Optional[dict] = None):
        """Initialize with custom limits or use defaults."""
        self.limits = limits or DEFAULT_LIMITS

    def _get_limits(self, action_type: str) -> dict:
        """Get limits for action type."""
        return self.limits.get(action_type, self.limits['default'])

    def _get_or_create_record(self, session, identity_id: int, action_type: str) -> RateLimit:
        """Get or create rate limit record for today."""
        today = date.today()

        record = session.query(RateLimit).filter(
            RateLimit.identity_id == identity_id,
            RateLimit.action_type == action_type,
            RateLimit.date == today
        ).first()

        if not record:
            record = RateLimit(
                identity_id=identity_id,
                action_type=action_type,
                date=today,
                hourly_timestamps=[],
                daily_count=0
            )
            session.add(record)
            session.flush()

        return record

    def _prune_hourly_timestamps(self, timestamps: list) -> list:
        """Remove timestamps older than 1 hour."""
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        return [ts for ts in timestamps if datetime.fromisoformat(ts) > one_hour_ago]

    def can_request(self, identity_id: int, action_type: str) -> Tuple[bool, int]:
        """Check if a request can be made.

        Args:
            identity_id: Identity ID
            action_type: Type of action (profile_visit, search, etc.)

        Returns:
            Tuple of (can_request, wait_seconds)
            - can_request: True if request can be made now
            - wait_seconds: Seconds to wait if can_request is False
        """
        limits = self._get_limits(action_type)
        hourly_limit = limits['hourly']
        daily_limit = limits['daily']

        with get_session() as session:
            record = self._get_or_create_record(session, identity_id, action_type)

            # Clean up old hourly timestamps
            hourly_timestamps = self._prune_hourly_timestamps(record.hourly_timestamps or [])

            # Check daily limit
            if record.daily_count >= daily_limit:
                # Calculate seconds until midnight
                now = datetime.utcnow()
                tomorrow = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
                wait_seconds = int((tomorrow - now).total_seconds())
                return False, wait_seconds

            # Check hourly limit
            if len(hourly_timestamps) >= hourly_limit:
                # Calculate when oldest timestamp will expire
                oldest = datetime.fromisoformat(hourly_timestamps[0])
                wait_until = oldest + timedelta(hours=1)
                wait_seconds = max(0, int((wait_until - datetime.utcnow()).total_seconds()))
                return False, wait_seconds

            return True, 0

    def record_request(self, identity_id: int, action_type: str):
        """Record a request was made."""
        with get_session() as session:
            record = self._get_or_create_record(session, identity_id, action_type)

            now = datetime.utcnow()

            # Update hourly timestamps
            hourly_timestamps = self._prune_hourly_timestamps(record.hourly_timestamps or [])
            hourly_timestamps.append(now.isoformat())
            record.hourly_timestamps = hourly_timestamps

            # Update daily count
            record.daily_count = (record.daily_count or 0) + 1
            record.last_request_at = now

    def get_stats(self, identity_id: int, action_type: Optional[str] = None) -> dict:
        """Get rate limit stats for identity.

        Args:
            identity_id: Identity ID
            action_type: Optional specific action, or all actions

        Returns:
            Dict with usage stats
        """
        with get_session() as session:
            query = session.query(RateLimit).filter(
                RateLimit.identity_id == identity_id,
                RateLimit.date == date.today()
            )

            if action_type:
                query = query.filter(RateLimit.action_type == action_type)

            records = query.all()

            stats = {}
            for record in records:
                limits = self._get_limits(record.action_type)
                hourly_timestamps = self._prune_hourly_timestamps(record.hourly_timestamps or [])

                stats[record.action_type] = {
                    'hourly_used': len(hourly_timestamps),
                    'hourly_limit': limits['hourly'],
                    'daily_used': record.daily_count,
                    'daily_limit': limits['daily'],
                    'last_request': record.last_request_at.isoformat() if record.last_request_at else None,
                }

            return stats

    def reset_daily(self, identity_id: int, action_type: Optional[str] = None):
        """Reset daily counters (for testing or manual reset)."""
        with get_session() as session:
            query = session.query(RateLimit).filter(
                RateLimit.identity_id == identity_id
            )
            if action_type:
                query = query.filter(RateLimit.action_type == action_type)

            query.delete()
