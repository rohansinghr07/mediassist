"""MongoDB connection and collections.

Collections:
  patients      one per Telegram user (_id = chat_id)
  sessions      chat state per user (_id = chat_id)
  departments   _id = key, name_en, name_hi, order
  doctors       _id = short id, name, department, experience_years, cal_event_type_id, active
  appointments  _id = APT-0001 ... (copy of each Cal.com booking)
  tickets       _id = ISS-0001 / CMP-0001
  alerts        _id = ALT-0001 (emergency messages)
  counters      running numbers for the IDs above
"""
from datetime import datetime, timezone

from pymongo import ReturnDocument

from config import settings

client = None
database = None
patients = sessions = departments = doctors = None
appointments = tickets = alerts = counters = None


async def init(client_override=None):
    global client, database, patients, sessions, departments, doctors
    global appointments, tickets, alerts, counters
    if client_override is not None:
        client = client_override
    else:
        from pymongo import AsyncMongoClient
        client = AsyncMongoClient(settings.MONGODB_URI, tz_aware=True)
    database = client[settings.MONGODB_DB]
    patients = database["patients"]
    sessions = database["sessions"]
    departments = database["departments"]
    doctors = database["doctors"]
    appointments = database["appointments"]
    tickets = database["tickets"]
    alerts = database["alerts"]
    counters = database["counters"]
    try:
        await appointments.create_index([("chat_id", 1), ("start", 1)])
        await appointments.create_index("cal_uid")
        await appointments.create_index([("status", 1), ("start", 1)])
        await tickets.create_index([("status", 1), ("created_at", -1)])
        await doctors.create_index("department")
    except Exception:  # index creation is optional
        pass


async def next_id(prefix: str) -> str:
    doc = await counters.find_one_and_update(
        {"_id": prefix},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"{prefix}-{doc['seq']:04d}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt):
    """Mongo may return naive UTC datetimes - make them timezone-aware."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
