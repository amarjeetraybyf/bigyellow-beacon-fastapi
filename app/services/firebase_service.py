"""
BigYellow Beacon – Firebase Cloud Server Integration
Integrates Firebase Admin SDK & Cloud Firestore with offline fallback resilience.
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any, List

import firebase_admin
from firebase_admin import credentials, firestore, auth

from app.config import (
    FIREBASE_SERVICE_ACCOUNT_KEY,
    FIREBASE_PROJECT_ID,
    FIREBASE_DATABASE_URL,
    FIREBASE_OFFLINE_FALLBACK,
    BASE_DIR,
)

_firebase_app: Optional[firebase_admin.App] = None
_firestore_db: Optional[Any] = None
_is_connected: bool = False


def init_firebase() -> bool:
    global _firebase_app, _firestore_db, _is_connected

    if _is_connected:
        return True

    try:
        cred = None
        # Check service account key path
        sa_path = FIREBASE_SERVICE_ACCOUNT_KEY
        if sa_path:
            resolved = Path(sa_path)
            if not resolved.is_absolute():
                resolved = BASE_DIR / sa_path
            if resolved.exists():
                cred = credentials.Certificate(str(resolved))

        options: Dict[str, Any] = {}
        if FIREBASE_PROJECT_ID:
            options["projectId"] = FIREBASE_PROJECT_ID
        if FIREBASE_DATABASE_URL:
            options["databaseURL"] = FIREBASE_DATABASE_URL

        if not firebase_admin._apps:
            if cred:
                _firebase_app = firebase_admin.initialize_app(cred, options)
            elif os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                _firebase_app = firebase_admin.initialize_app(options=options)
            elif FIREBASE_PROJECT_ID:
                _firebase_app = firebase_admin.initialize_app(options=options)
            else:
                # No Firebase credentials provided; will use offline fallback
                _is_connected = False
                return False
        else:
            _firebase_app = firebase_admin.get_app()

        _firestore_db = firestore.client()
        _is_connected = True
        print("[Firebase] Connected to Cloud Firestore successfully.")
        return True
    except Exception as e:
        print(f"[Firebase] Initialization failed or offline fallback active: {e}")
        _is_connected = False
        return False


def is_firebase_connected() -> bool:
    return _is_connected


def get_firestore_client():
    if not _is_connected:
        init_firebase()
    return _firestore_db


def verify_firebase_token(id_token: str) -> Optional[Dict[str, Any]]:
    """
    Verify Firebase Auth ID Token.
    Returns decoded token dictionary if valid, or None.
    """
    if not _is_connected and not init_firebase():
        return None

    try:
        decoded = auth.verify_id_token(id_token)
        return decoded
    except Exception:
        return None


def sync_document_to_firestore(collection: str, doc_id: str, data: Dict[str, Any]) -> bool:
    """Sync a document to Firebase Firestore if connected."""
    if not _is_connected and not init_firebase():
        return False
    try:
        client = get_firestore_client()
        if client:
            client.collection(collection).document(doc_id).set(data, merge=True)
            return True
    except Exception as e:
        print(f"[Firebase] Sync error for {collection}/{doc_id}: {e}")
    return False


def fetch_collection_from_firestore(collection: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch documents from Firebase Firestore."""
    if not _is_connected and not init_firebase():
        return None
    try:
        client = get_firestore_client()
        if client:
            docs = client.collection(collection).stream()
            return [d.to_dict() for d in docs]
    except Exception as e:
        print(f"[Firebase] Fetch error for {collection}: {e}")
    return None
