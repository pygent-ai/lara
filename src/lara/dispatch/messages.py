from __future__ import annotations

from html import escape

from pygent import UserMessage, freeze_json_object

from .models import DispatchItem, DispatchTask

AUTOMATION_MESSAGE_KIND = "lara.automation.trigger"


def automation_trigger_message(
    task: DispatchTask, item: DispatchItem
) -> UserMessage:
    content = "\n".join(
        (
            (
                '<automation-trigger schema-version="1" '
                f'automation-id="{escape(task.task_id, quote=True)}" '
                f'run-id="{escape(item.item_id, quote=True)}">'
            ),
            f"  <task-name>{escape(task.name, quote=False)}</task-name>",
            f"  <scheduled-for>{escape(item.created_at, quote=False)}</scheduled-for>",
            f"  <timezone>{escape(task.timezone_name, quote=False)}</timezone>",
            f"  <mode>{escape(task.destination, quote=False)}</mode>",
            "  <unattended>true</unattended>",
            "  <instructions>",
            f"{escape(task.prompt, quote=False)}",
            "  </instructions>",
            "</automation-trigger>",
        )
    )
    return UserMessage(
        content=content,
        kind=AUTOMATION_MESSAGE_KIND,
        data=freeze_json_object(
            {
                "origin": "automation",
                "automation_id": task.task_id,
                "automation_run_id": item.item_id,
                "raw_content": task.prompt,
            }
        ),
    )


__all__ = ["AUTOMATION_MESSAGE_KIND", "automation_trigger_message"]
