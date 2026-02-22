from __future__ import annotations

import json
import logging

from app.core.config import settings

logger = logging.getLogger("tamgam.cloud_tasks")


def is_enabled() -> bool:
    return (
        settings.cloud_tasks_enabled
        and bool(settings.cloud_tasks_target_url)
        and bool(settings.cloud_tasks_queue)
    )


def enqueue_transcript_processing(transcript_id: str) -> bool:
    """
    Enqueue transcript processing on Cloud Tasks.
    Returns True on enqueue success, False on fallback.
    """
    if not is_enabled():
        return False

    project_id = settings.cloud_tasks_project_id or settings.gcp_project_id
    if not project_id:
        logger.warning("Cloud Tasks disabled: missing project id")
        return False

    try:
        from google.cloud import tasks_v2

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(
            project_id,
            settings.cloud_tasks_location,
            settings.cloud_tasks_queue,
        )

        target_url = settings.cloud_tasks_target_url.rstrip("/")
        task_payload = {"transcript_id": transcript_id}
        headers = {"Content-Type": "application/json"}
        if settings.cloud_tasks_auth_secret:
            headers["X-Task-Secret"] = settings.cloud_tasks_auth_secret

        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{target_url}/api/v1/transcripts/internal/process",
                "headers": headers,
                "body": json.dumps(task_payload).encode("utf-8"),
            }
        }
        client.create_task(request={"parent": parent, "task": task})
        return True
    except Exception as exc:
        logger.exception("Cloud Tasks enqueue failed: %s", exc)
        return False
