class Screen:
    def __init__(self, store):
        self.store = store

    def take_screenshot(self):
        result = self.store.capture()
        # No vision consumer in V1: dispose immediately after reporting dimensions.
        self.store.clear()
        return result
