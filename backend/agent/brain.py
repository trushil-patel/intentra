from utils.performance import timed
from agent.context import UserCommand, Source
from agent.planner import deterministic, deterministic_v2, normalized, STOP_WORDS, PROMPT_REQUIRED_GOAL
from agent.schemas import Plan
from safety.validator import safe_text
from utils.helpers import SafeError

class Brain:
    def __init__(self, provider, router, stop, executor=None):
        self.provider, self.router, self.stop = provider, router, stop
        self.executor = executor

    @timed("command")
    def handle(self, command):
        if not isinstance(command, UserCommand) or command.source is not Source.USER:
            raise SafeError("Observed content cannot authorize actions")
        if type(command.text) is not str or not 1 <= len(command.text) <= 2000:
            raise SafeError("Invalid user command")
        if normalized(command.text) in STOP_WORDS:
            self.stop.stop()
            return []
        self.stop.check()
        generation = self.stop.generation
        if not safe_text(command.text):
            raise SafeError("Sensitive or dangerous request blocked")
        try:
            plan = (deterministic_v2 if self.executor else deterministic)(command.text)
            from agent.command_classifier import classify, CommandKind
            kind = classify(command.text, plan)
            if kind == CommandKind.REPEAT:
                raise SafeError("I didn't understand that command. Please repeat.")
            from_model = kind == CommandKind.COMPLEX
            context = self.executor.prepare(plan=plan) if self.executor else {}
            if self.executor:
                from agent.task_scope import TaskScope
                self.router.task_scope = TaskScope.from_user(command.text)
            if from_model:
                raw = self.provider.generate_plan(command.text, context)
                self.stop.check_generation(generation)
                plan = Plan.parse(raw)
                if self.executor:
                    from agent.task_scope import validate_model_scope
                    validate_model_scope(command.text, plan, context)
        except Exception:
            if hasattr(self.stop, 'end_task'): self.stop.end_task()
            from agent.task_scope import TaskScope
            self.router.task_scope = TaskScope()
            if hasattr(self.router.registry, 'clear_task'): self.router.registry.clear_task()
            if self.executor:
                from agent.state_machine import TaskStatus, TERMINAL
                machine = self.executor.memory.machine
                if machine.status not in TERMINAL:
                    machine.move(TaskStatus.CANCELLED if self.stop.stopped else TaskStatus.BLOCKED)
            raise
        if self.executor:
            return self.executor.execute(plan, from_model=from_model,
                                         needs_prompt=not from_model and plan.goal == PROMPT_REQUIRED_GOAL)
        return self.router.execute(plan, from_model=from_model)
