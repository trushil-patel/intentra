class Mouse:
    def __init__(self, stop, guard):
        self.stop = stop
        self.guard = guard

    def click(self):
        import pyautogui
        self.stop.check()
        self.guard.verify_input()
        pyautogui.click()
        return {"status": "input_sent"}

    def double_click(self):
        import pyautogui
        self.stop.check()
        self.guard.verify_input()
        pyautogui.doubleClick(interval=0.15)
        return {"status": "input_sent"}

    def right_click(self):
        import pyautogui
        self.stop.check()
        self.guard.verify_input()
        pyautogui.rightClick()
        return {"status": "input_sent"}

    def scroll(self, direction, amount):
        from tools.scroll_target import scroll
        return scroll(self.guard.windows, self.guard, self.stop, direction, amount)
