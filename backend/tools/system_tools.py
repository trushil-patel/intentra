from datetime import datetime

def get_current_time():
    return {"time": datetime.now().astimezone().isoformat(timespec="seconds")}
