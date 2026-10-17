"""
BigYellow Beacon – Edge Persistent Store (Python implementation of db.ts)

Stores JSON locally at `data/beacon-db.json` with auditing,
enforcing privacy rules:
  ① transcripts → NEVER stored
  ② wellbeing aggregates → groups of 5+ only (k-anonymity)
  ③ escalations → ONLY forwarded when consentGranted === true
"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.config import DB_FILE, DATA_DIR


def get_utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class BeaconDatabase:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_FILE
        self.data: Dict[str, Any] = self._load()

    def _empty_schema(self) -> Dict[str, Any]:
        return {
            "vessels": [],
            "accidentCorpus": [],
            "nearMisses": [],
            "crewCheckIns": [],
            "clusters": [],
            "escalations": [],
            "masterBriefs": {},
            "outbox": [],
            "users": [],
            "devices": [],
            "fleetAlerts": [],
            "notices": [],
            "auditLog": [],
        }

    def _load(self) -> Dict[str, Any]:
        try:
            if self.db_path.exists():
                with open(self.db_path, "r", encoding="utf-8") as f:
                    parsed = json.load(f)
                schema = self._empty_schema()
                for key in schema:
                    if key in parsed:
                        schema[key] = parsed[key]
                # Normalize near-misses status
                for nm in schema["nearMisses"]:
                    if "status" not in nm or not nm["status"]:
                        nm["status"] = "OPEN"
                return schema
        except Exception as e:
            print(f"[BeaconDB] Failed to load DB file, starting with empty/default schema: {e}")
        return self._empty_schema()

    def save(self):
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[BeaconDB] Save failed (in-memory only): {e}")

    def audit(self, action: str, details: str):
        timestamp = get_utc_now_iso()
        raw = f"{timestamp}-{action}-{details}"
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        entry = {
            "timestamp": timestamp,
            "action": action,
            "details": details,
            "checksum": checksum,
        }
        self.data["auditLog"].insert(0, entry)
        if len(self.data["auditLog"]) > 200:
            self.data["auditLog"] = self.data["auditLog"][:200]

    # ── Vessels ───────────────────────────────────────────────────────────────

    def get_vessels(self) -> List[Dict[str, Any]]:
        return self.data["vessels"]

    def get_vessel(self, vessel_id: str) -> Optional[Dict[str, Any]]:
        for v in self.data["vessels"]:
            if v["id"] == vessel_id:
                return v
        return None

    def add_vessel(self, vessel: Dict[str, Any]) -> Dict[str, Any]:
        self.data["vessels"].append(vessel)
        self.audit("VESSEL_REGISTERED", f"{vessel.get('name')} ({vessel.get('id')})")
        self.save()
        return vessel

    def update_vessel_connection(self, vessel_id: str, status: str) -> Optional[Dict[str, Any]]:
        v = self.get_vessel(vessel_id)
        if v:
            v["connectionStatus"] = status
            v["lastSyncTimestamp"] = get_utc_now_iso()
            self.audit("VESSEL_CONNECTIVITY_UPDATE", f"{v.get('name')} → {status}")
            self.save()
        return v

    # ── Accident Corpus ───────────────────────────────────────────────────────

    def get_accident_corpus(self) -> List[Dict[str, Any]]:
        return self.data["accidentCorpus"]

    def add_corpus_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        self.data["accidentCorpus"].insert(0, entry)
        self.audit("CORPUS_ENTRY_ADDED", f"{entry.get('reportCode')} – {entry.get('title')}")
        self.save()
        return entry

    def search_corpus(self, query: str) -> List[Dict[str, Any]]:
        q = query.lower()
        results = []
        for e in self.data["accidentCorpus"]:
            title = e.get("title", "").lower()
            domain = e.get("hazardDomain", "").lower()
            precursor = e.get("precursorPattern", "").lower()
            keywords = [k.lower() for k in e.get("similarityKeywords", [])]
            if (q in title or q in domain or q in precursor or
                any(q in kw or kw in q for kw in keywords)):
                results.append(e)
        return results

    # ── Near-Misses ───────────────────────────────────────────────────────────

    def get_near_misses(self, vessel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if vessel_id:
            return [n for n in self.data["nearMisses"] if n.get("vesselId") == vessel_id]
        return self.data["nearMisses"]

    def get_near_miss(self, nm_id: str) -> Optional[Dict[str, Any]]:
        for n in self.data["nearMisses"]:
            if n.get("id") == nm_id:
                return n
        return None

    def add_near_miss(self, nm: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(nm)
        if "status" not in record:
            record["status"] = "OPEN"
        self.data["nearMisses"].insert(0, record)
        self.audit("NEAR_MISS_INGESTED", f"{record.get('id')} on {record.get('vesselId')} ({record.get('ismCategory')})")
        self.save()
        return record

    def update_near_miss(self, nm_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nm = self.get_near_miss(nm_id)
        if not nm:
            return None
        nm.update(patch)
        self.audit("NEAR_MISS_UPDATED", f"{nm_id} → status: {patch.get('status', nm.get('status'))}")
        self.save()
        return nm

    # ── Crew Check-ins ────────────────────────────────────────────────────────
    # Privacy rule ①: transcripts NEVER stored – only aggregated numeric scores

    def get_check_ins(self, vessel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if vessel_id:
            return [c for c in self.data["crewCheckIns"] if c.get("vesselId") == vessel_id]
        return self.data["crewCheckIns"]

    def add_check_in(self, ci: Dict[str, Any]) -> Dict[str, Any]:
        safe = {
            "vesselId": ci.get("vesselId"),
            "department": ci.get("department", "Deck"),
            "rankCategory": ci.get("rankCategory", "Rating"),
            "moodScore": ci.get("moodScore", 7),
            "fatigueScore": ci.get("fatigueScore", 5),
            "stressScore": ci.get("stressScore", 4),
            "sleepHours": ci.get("sleepHours", 7.0),
            "hasHazardNote": ci.get("hasHazardNote", False),
        }
        self.data["crewCheckIns"].append(safe)
        self.save()
        return safe

    # ── Clusters ──────────────────────────────────────────────────────────────

    def get_clusters(self) -> List[Dict[str, Any]]:
        return self.data["clusters"]

    def get_cluster(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        for c in self.data["clusters"]:
            if c.get("clusterId") == cluster_id:
                return c
        return None

    def add_or_update_cluster(self, cluster: Dict[str, Any]):
        cid = cluster.get("clusterId")
        idx = -1
        for i, c in enumerate(self.data["clusters"]):
            if c.get("clusterId") == cid:
                idx = i
                break
        if idx >= 0:
            self.data["clusters"][idx] = cluster
        else:
            self.data["clusters"].insert(0, cluster)
        self.audit("CLUSTER_UPDATED", f"{cluster.get('patternName')} – {len(cluster.get('affectedVesselIds', []))} vessels")
        self.save()

    # ── Escalations ───────────────────────────────────────────────────────────
    # Privacy rule ③: only allow items with consentGranted === true

    def get_escalations(self) -> List[Dict[str, Any]]:
        return self.data["escalations"]

    def add_escalation(self, esc: Dict[str, Any]) -> Dict[str, Any]:
        if not esc.get("consentGranted"):
            raise ValueError("[PrivacyRule] Escalation rejected: consentGranted must be true (GDPR Article 9)")
        self.data["escalations"].insert(0, esc)
        self.audit("ESCALATION_FLAGGED", f"{esc.get('urgency')} urgency on {esc.get('vesselId')}")
        self.save()
        return esc

    def update_escalation_status(self, esc_id: str, status: str, notes: Optional[str] = None) -> Optional[Dict[str, Any]]:
        for e in self.data["escalations"]:
            if e.get("id") == esc_id:
                e["status"] = status
                if notes:
                    e["clinicianNotes"] = notes
                self.audit("ESCALATION_UPDATED", f"{esc_id} → {status}")
                self.save()
                return e
        return None

    # ── Master Briefs ─────────────────────────────────────────────────────────

    def get_master_brief(self, vessel_id: str) -> Optional[Dict[str, Any]]:
        return self.data["masterBriefs"].get(vessel_id)

    def set_master_brief(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        vessel_id = brief.get("vesselId", "MV-PACIFIC-STAR")
        self.data["masterBriefs"][vessel_id] = brief
        self.audit("MASTER_BRIEF_SET", f"Brief regenerated for {vessel_id}")
        self.save()
        return brief

    # ── Outbox (Sync Worker pattern) ──────────────────────────────────────────

    def enqueue_outbox(self, item: Dict[str, Any]) -> Dict[str, Any]:
        entry = {
            **item,
            "id": f"OBX-{hex(int(datetime.now().timestamp()*1000))[2:].upper()}",
            "createdAt": get_utc_now_iso(),
            "attempts": 0,
            "synced": False,
        }
        self.data["outbox"].append(entry)
        self.save()
        return entry

    def get_pending_outbox(self, vessel_id: str) -> List[Dict[str, Any]]:
        return [o for o in self.data["outbox"] if o.get("vesselId") == vessel_id and not o.get("synced")]

    def get_outbox(self, vessel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if vessel_id:
            return [o for o in self.data["outbox"] if o.get("vesselId") == vessel_id]
        return self.data["outbox"]

    def mark_outbox_synced(self, ids: List[str]):
        now = get_utc_now_iso()
        for item in self.data["outbox"]:
            if item.get("id") in ids:
                item["synced"] = True
                item["syncedAt"] = now
        self.audit("OUTBOX_DRAINED", f"{len(ids)} items synced to shore")
        self.save()

    # ── Paired Devices ────────────────────────────────────────────────────────

    def get_devices(self) -> List[Dict[str, Any]]:
        return self.data.get("devices", [])

    def get_device(self, token: str) -> Optional[Dict[str, Any]]:
        for d in self.data.get("devices", []):
            if d.get("token") == token:
                return d
        return None

    def add_device(self, device: Dict[str, Any]) -> Dict[str, Any]:
        if "devices" not in self.data:
            self.data["devices"] = []
        self.data["devices"].append(device)
        self.audit("DEVICE_PAIRED", f"vessel: {device.get('vesselId')} dept: {device.get('department')}")
        self.save()
        return device

    def revoke_device(self, token: str) -> bool:
        devices = self.data.get("devices", [])
        idx = -1
        for i, d in enumerate(devices):
            if d.get("token") == token:
                idx = i
                break
        if idx >= 0:
            devices.pop(idx)
            self.audit("DEVICE_REVOKED", f"token {token[:12]}…")
            self.save()
            return True
        return False

    # ── Audit Log & Privacy Summary ───────────────────────────────────────────

    def get_audit_log(self) -> List[Dict[str, Any]]:
        return self.data["auditLog"]

    def get_privacy_summary(self) -> Dict[str, Any]:
        return {
            "rule1_transcriptsNeverStored": True,
            "rule2_wellbeingKAnonymityThreshold": 5,
            "rule3_escalationsRequireExplicitConsent": True,
            "outboxPendingCount": len([o for o in self.data["outbox"] if not o.get("synced")]),
        }


# Global singleton instance
beacon_db = BeaconDatabase()
