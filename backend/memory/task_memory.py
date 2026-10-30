from collections import deque
from dataclasses import dataclass
import time
from uuid import uuid4
from agent.computer_state import ComputerState
from agent.state_machine import TaskMachine


@dataclass
class WorkflowState:
    workflow_id: str
    intent_digest: str | None = None
    current_step: int = 0
    total_steps: int = 0
    expected_application: str | None = None
    window_handle: int | None = None
    last_verified_result: str | None = None
    last_target_id: str | None = None
    deadline: float | None = None
    cancelled: bool = False


class TaskMemory:
    """Bounded metadata only; no persisted commands, typed text or observations."""
    def __init__(self):
        self.task_id = uuid4().hex
        self.machine = TaskMachine()
        self.workflow = WorkflowState(self.task_id)
        self.computer = ComputerState()
        self.recent_actions = deque(maxlen=30)

    def record(self, tool, execution, verification):
        self.recent_actions.append((tool, execution, verification))

    def clear(self):
        self.computer = ComputerState()
        self.recent_actions.clear()
        self.workflow = WorkflowState(self.task_id)
