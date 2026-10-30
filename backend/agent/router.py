from utils.performance import timed
import json
from dataclasses import dataclass
from agent.schemas import Plan
from safety.policy import INTERACTIVE, validate_planned_targets
from utils.helpers import SafeError, Cancelled

@dataclass(frozen=True)
class Result:
    tool: str
    status: str
    detail: object = None

class Router:
    def __init__(self, registry, validator, permissions, limiter, stop, confirmations, audit, guard=None, windows=None, status=print):
        self.registry, self.validator, self.permissions = registry, validator, permissions
        self.limiter, self.stop, self.confirmations = limiter, stop, confirmations
        self.audit, self.guard, self.windows, self.status = audit, guard, windows, status

    @timed("security")
    def preflight(self, plan):
        self.stop.check()
        if not isinstance(plan, Plan):
            raise SafeError("Invalid plan type")
        # Revalidate even programmatically constructed plans.
        try:
            checked = Plan.parse(json.dumps({"goal": plan.goal, "actions": [{"tool": a.tool, "arguments": a.arguments} for a in plan.actions]}))
            actions = []
            for action in checked.actions:
                spec = self.registry.spec(action.tool)
                actions.append(self.validator.validate(action, spec))
                if self.permissions.evaluate(spec.risk) not in {"allow", "confirm"}:
                    raise SafeError("Action blocked by security policy")
            validate_planned_targets(actions)
            from agent.task_scope import TaskScope
            getattr(self, 'task_scope', TaskScope()).validate(actions)
            self.limiter.check_plan(actions)
            return tuple(actions)
        except SafeError:
            raise
        except Exception:
            raise SafeError("Security validation failed; plan denied") from None

    @timed("execution")
    def execute(self, plan, from_model=False, lifecycle=None, announce=True):
        results = []
        active_tool, risk = "unrecognized", "HIGH"
        try:
            self.status("[VALIDATING]")
            actions = self.preflight(plan)
            for action in actions:
                self.stop.check()
                active_tool = action.tool
                spec = self.registry.spec(active_tool)
                risk = spec.risk.value
                # Revalidate immediately before execution as well as at preflight.
                action = self.validator.validate(action, spec)
                decision = self.permissions.evaluate(spec.risk)
                if decision not in {"allow", "confirm"}:
                    raise SafeError("Action denied")
                target = None
                if action.tool in INTERACTIVE | {"scroll"}:
                    if self.guard is None:
                        raise SafeError("Input guard unavailable")
                    target = self.guard.snapshot(action)
                closing = None
                if action.tool in {"close_app", "close_window"}:
                    if self.windows is None:
                        raise SafeError("Window identity guard unavailable")
                    closing = self.windows.select(**action.arguments).handle
                from safety.policy import ordinary_input
                if (decision == "confirm" and not ordinary_input(action, target)) or from_model:
                    if lifecycle: lifecycle("WAITING_CONFIRMATION")
                    if self.confirmations.confirm(action, target or closing, self.stop) is not True:
                        raise SafeError("Confirmation denied or expired")
                self.stop.check()
                # Fail closed if policy changed while confirmation was pending.
                if self.permissions.evaluate(spec.risk) != decision:
                    raise SafeError("Policy changed during confirmation")
                if target is not None:
                    self.guard.recheck(action, target)
                    self.guard.arm(action, target)
                if closing is not None and self.windows.select(**action.arguments).handle != closing:
                    raise SafeError("Window changed during confirmation")
                if closing is not None and hasattr(self.registry, "bind_closing"):
                    self.registry.bind_closing(action, closing)
                self.limiter.consume(action)
                self.audit.event(active_tool, risk, "ALLOWED")
                self.stop.check()
                if lifecycle: lifecycle("EXECUTING")
                self.status("[EXECUTING] " + active_tool)
                self.stop.check()
                if action.tool in {'wait_for', 'wait_for_response'}:
                    if lifecycle: lifecycle('WAITING')
                    self.status('[WAITING] Bounded condition polling')
                detail = self.registry.invoke(action)
                if self.guard is not None: self.guard.disarm()
                self.audit.event(active_tool, risk, "SUCCESS")
                results.append(Result(active_tool, "SUCCESS", detail))
                self.stop.check()
            if announce: self.status("[DONE]")
        except Exception as exc:
            status = "CANCELLED" if isinstance(exc, Cancelled) else "BLOCKED" if isinstance(exc, SafeError) else "ERROR"
            # PyAutoGUI failures and internal failures latch the stop token.
            if status in {"ERROR", "CANCELLED"}: self.stop.stop()
            try: self.audit.event(active_tool, risk, status)
            except Exception: self.stop.stop()
            message = str(exc) if isinstance(exc, SafeError) else "Execution failed safely; check installation and target"
            results.append(Result(active_tool, status, message))
            self.status("[" + ("BLOCKED" if status == "CANCELLED" else status) + "] " + message)
        finally:
            if self.guard is not None: self.guard.disarm()
        return results
