"""Configure VoxPilot's intentional MTA mode before importing COM/UIA libraries."""
import sys
import warnings


def configure_com():
    sys.coinit_flags = 0
    # pywinauto announces any externally selected mode, even its preferred MTA.
    # Keep actual COM failures and its STA fallback warning visible.
    warnings.filterwarnings(
        "ignore",
        message=r"^Apply externally defined coinit_flags: 0$",
        category=UserWarning,
        module=r"^pywinauto$",
    )
