"""Explicit task lifecycle; cancellation always wins."""
from enum import StrEnum
from utils.helpers import SafeError


class TaskStatus(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    PLANNING = "PLANNING"
    OBSERVING = "OBSERVING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING = "WAITING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED}
TRANSITIONS = {
    TaskStatus.IDLE: {TaskStatus.LISTENING, TaskStatus.PLANNING},
    TaskStatus.LISTENING: {TaskStatus.TRANSCRIBING},
    TaskStatus.TRANSCRIBING: {TaskStatus.PLANNING},
    TaskStatus.PLANNING: {TaskStatus.OBSERVING},
    TaskStatus.OBSERVING: {TaskStatus.EXECUTING, TaskStatus.VERIFYING, TaskStatus.RECOVERING},
    TaskStatus.EXECUTING: {TaskStatus.WAITING_CONFIRMATION, TaskStatus.WAITING, TaskStatus.OBSERVING},
    TaskStatus.WAITING_CONFIRMATION: {TaskStatus.EXECUTING},
    TaskStatus.WAITING: {TaskStatus.OBSERVING},
    TaskStatus.VERIFYING: {TaskStatus.OBSERVING, TaskStatus.RECOVERING, TaskStatus.COMPLETED},
    TaskStatus.RECOVERING: {TaskStatus.OBSERVING},
}


class TaskMachine:
    def __init__(self):
        self.status = TaskStatus.IDLE

    def move(self, status):
        status = TaskStatus(status)
        if self.status == status:
            return
        if self.status in TERMINAL or (status not in {TaskStatus.CANCELLED, TaskStatus.BLOCKED, TaskStatus.FAILED}
                                      and status not in TRANSITIONS.get(self.status, set())):
            raise SafeError("Invalid task transition")
        self.status = status
