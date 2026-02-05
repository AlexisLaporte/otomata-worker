"""Secrets management service with Fernet encryption."""

import os
from datetime import datetime
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from .models import Secret, SecretScope
from .database import get_session


class SecretsService:
    """Service for encrypted secrets management."""

    def __init__(self):
        self._fernet: Optional[Fernet] = None

    @property
    def fernet(self) -> Fernet:
        """Lazy-load Fernet cipher from SECRETS_MASTER_KEY."""
        if self._fernet is None:
            master_key = os.environ.get('SECRETS_MASTER_KEY')
            if not master_key:
                raise ValueError(
                    "SECRETS_MASTER_KEY not set. "
                    "Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
            self._fernet = Fernet(master_key.encode())
        return self._fernet

    def encrypt(self, value: str) -> str:
        """Encrypt a value."""
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, encrypted_value: str) -> str:
        """Decrypt a value."""
        try:
            return self.fernet.decrypt(encrypted_value.encode()).decode()
        except InvalidToken:
            raise ValueError("Invalid encryption key or corrupted data")

    def get(self, key: str, user_id: Optional[int] = None) -> Optional[str]:
        """Get and decrypt a secret.

        Priority: user-scoped (if user_id provided) > platform-scoped.
        """
        with get_session() as session:
            # Try user-scoped first if user_id provided
            if user_id:
                secret = session.query(Secret).filter(
                    Secret.key == key,
                    Secret.scope == SecretScope.USER,
                    Secret.user_id == user_id
                ).first()
                if secret:
                    if secret.expires_at and secret.expires_at < datetime.utcnow():
                        return None
                    return self.decrypt(secret.encrypted_value)

            # Fall back to platform-scoped
            secret = session.query(Secret).filter(
                Secret.key == key,
                Secret.scope == SecretScope.PLATFORM
            ).first()
            if secret:
                if secret.expires_at and secret.expires_at < datetime.utcnow():
                    return None
                return self.decrypt(secret.encrypted_value)

        return None

    def set(
        self,
        key: str,
        value: str,
        scope: SecretScope = SecretScope.PLATFORM,
        user_id: Optional[int] = None,
        description: Optional[str] = None,
        expires_at: Optional[datetime] = None
    ) -> Secret:
        """Encrypt and store a secret. Updates if exists, creates otherwise."""
        encrypted = self.encrypt(value)

        with get_session() as session:
            query = session.query(Secret).filter(
                Secret.key == key,
                Secret.scope == scope
            )
            if scope == SecretScope.USER:
                query = query.filter(Secret.user_id == user_id)
            else:
                query = query.filter(Secret.user_id.is_(None))

            secret = query.first()

            if secret:
                secret.encrypted_value = encrypted
                secret.description = description
                secret.expires_at = expires_at
                secret.updated_at = datetime.utcnow()
            else:
                secret = Secret(
                    key=key,
                    scope=scope,
                    user_id=user_id if scope == SecretScope.USER else None,
                    encrypted_value=encrypted,
                    description=description,
                    expires_at=expires_at
                )
                session.add(secret)

            session.commit()
            session.refresh(secret)
            return secret

    def delete(self, key: str, scope: SecretScope = SecretScope.PLATFORM, user_id: Optional[int] = None) -> bool:
        """Delete a secret. Returns True if deleted."""
        with get_session() as session:
            query = session.query(Secret).filter(
                Secret.key == key,
                Secret.scope == scope
            )
            if scope == SecretScope.USER:
                query = query.filter(Secret.user_id == user_id)
            else:
                query = query.filter(Secret.user_id.is_(None))

            secret = query.first()
            if secret:
                session.delete(secret)
                session.commit()
                return True
        return False

    def list_keys(self, scope: Optional[SecretScope] = None, user_id: Optional[int] = None) -> list[dict]:
        """List secrets metadata (without values)."""
        with get_session() as session:
            query = session.query(Secret)

            if scope:
                query = query.filter(Secret.scope == scope)
            if user_id:
                query = query.filter(
                    (Secret.user_id == user_id) | (Secret.scope == SecretScope.PLATFORM)
                )

            secrets = query.order_by(Secret.key).all()
            return [
                {
                    'id': s.id,
                    'key': s.key,
                    'scope': s.scope.value,
                    'user_id': s.user_id,
                    'description': s.description,
                    'expires_at': s.expires_at.isoformat() if s.expires_at else None,
                    'created_at': s.created_at.isoformat() if s.created_at else None,
                    'updated_at': s.updated_at.isoformat() if s.updated_at else None,
                }
                for s in secrets
            ]

    def get_for_task(self, keys: list[str], user_id: Optional[int] = None) -> dict[str, str]:
        """Get multiple secrets for task execution.

        Returns a dict of key -> decrypted value for injection into env.
        """
        result = {}
        for key in keys:
            value = self.get(key, user_id)
            if value:
                result[key] = value
        return result


# Singleton instance
secrets_service = SecretsService()
