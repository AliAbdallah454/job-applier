"""One table per user: sync state, applications, alerts, alert jobs, read messages and opened jobs.

Mail bodies are never stored. A sort key prefix tells the kinds of item apart.
"""

import hashlib
import uuid
from datetime import UTC, datetime

from boto3.dynamodb.conditions import Key

MAIL_JOB_PREFIX = "linkedin-"


def timestamp():
    return datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class MailStore:
    def __init__(self, table):
        self.table = table

    def _items(self, user_id, prefix):
        arguments = {
            "KeyConditionExpression": Key("user_id").eq(user_id)
            & Key("item_id").begins_with(prefix)
        }
        items = []
        while True:
            page = self.table.query(**arguments)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            arguments["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def _get(self, user_id, item_id):
        response = self.table.get_item(
            Key={"user_id": user_id, "item_id": item_id}, ConsistentRead=True
        )
        return response.get("Item")

    def _put_new(self, item):
        try:
            self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(item_id)")
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def sync_state(self, user_id):
        return self._get(user_id, "sync") or {}

    def save_sync_state(self, user_id, **values):
        names = {f"#v{index}": name for index, name in enumerate(values)}
        attributes = {f":v{index}": value for index, value in enumerate(values.values())}
        self.table.update_item(
            Key={"user_id": user_id, "item_id": "sync"},
            UpdateExpression="SET " + ", ".join(f"#v{i} = :v{i}" for i in range(len(values))),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=attributes,
        )

    def seen(self, user_id, message_key):
        return self._get(user_id, f"message#{message_key}") is not None

    def mark_seen(self, user_id, message_key, kind):
        self._put_new({"user_id": user_id, "item_id": f"message#{message_key}", "kind": kind})

    def applications(self, user_id):
        return self._items(user_id, "application#")

    def application(self, user_id, application_id):
        return self._get(user_id, f"application#{application_id}")

    def save_application(self, item):
        self.table.put_item(Item=item)

    def delete_application(self, user_id, application_id):
        self.table.delete_item(Key={"user_id": user_id, "item_id": f"application#{application_id}"})

    def new_application(self, user_id, company, role):
        application_id = uuid.uuid4().hex
        return {
            "user_id": user_id,
            "item_id": f"application#{application_id}",
            "application_id": application_id,
            "company": company,
            "role": role,
            "events": [],
        }

    def interests(self, user_id):
        return self._items(user_id, "interest#")

    def interest(self, user_id, job_id):
        return self._get(user_id, f"interest#{job_id}")

    def save_interest(self, user_id, job, opened_at):
        """Note an opened job post; opening one again keeps its link to an application."""
        saved = self.interest(user_id, job["job_id"]) or {}
        self.table.put_item(
            Item={
                **saved,
                **job,
                "user_id": user_id,
                "item_id": f"interest#{job['job_id']}",
                "opened_at": opened_at,
            }
        )

    def link_interest(self, user_id, job_id, application_id):
        self.table.update_item(
            Key={"user_id": user_id, "item_id": f"interest#{job_id}"},
            UpdateExpression="SET application_id = :id",
            ExpressionAttributeValues={":id": application_id},
        )

    def delete_interest(self, user_id, job_id):
        self.table.delete_item(Key={"user_id": user_id, "item_id": f"interest#{job_id}"})

    def alerts(self, user_id):
        return self._items(user_id, "alert#")

    def save_alert(self, user_id, message_key, subject, received_at, jobs):
        self._put_new(
            {
                "user_id": user_id,
                "item_id": f"alert#{received_at}#{message_key[:16]}",
                "alert_id": message_key[:16],
                "subject": subject,
                "received_at": received_at,
                "jobs": jobs,
            }
        )
        for job in jobs:
            self._put_new({"user_id": user_id, "item_id": f"job#{job['linkedin_id']}", **job})

    def jobs(self, user_id):
        return {item["linkedin_id"]: item for item in self._items(user_id, "job#")}

    def job(self, user_id, linkedin_id):
        return self._get(user_id, f"job#{linkedin_id}")

    def save_job_details(self, user_id, linkedin_id, details):
        """Fill in what an email left out; only known jobs can be updated."""
        job = self.job(user_id, linkedin_id)
        if job is None:
            return None
        known = {name: value for name, value in details.items() if value}
        saved = {**job, **known}
        self.table.put_item(Item=saved)
        return saved


def apply_status(application, status, subject, received_at, *, message_key=None):
    """Add an event; the newest one sets the status, and a repeated message changes nothing."""
    if message_key and any(item.get("message") == message_key for item in application["events"]):
        return application
    event = {"status": status, "subject": subject, "received_at": received_at}
    if message_key:
        event["message"] = message_key
    events = sorted([*application["events"], event], key=lambda item: item["received_at"])
    return {
        **application,
        "events": events,
        "status": events[-1]["status"],
        "updated_at": events[-1]["received_at"],
    }
