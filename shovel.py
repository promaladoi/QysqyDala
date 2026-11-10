"""Track one mouse sweep across a snow-covered ledge."""


class SnowShovel:
    def __init__(self):
        self.enabled = False
        self.cancel_drag()

    @property
    def dragging(self):
        return self.pointer is not None

    def begin_drag(self, snow, x, y, *, visible=None):
        if not self.enabled or self.dragging:
            return False
        owner = snow.bank_owner_at(x, y, visible=visible)
        if owner is None:
            return False
        self.owner = owner
        self.pointer = (x, y)
        return True

    def move_drag(self, snow, x, y, *, visible=None):
        if not self.dragging or self.pointer == (x, y):
            return
        old_x, old_y = self.pointer
        self.pointer = (x, y)
        if x != old_x:
            self.direction = 1 if x > old_x else -1
            snow.shovel(old_x, old_y, x, y, hwnd=self.owner, visible=visible)

    def preview(self, snow, visible):
        if not self.dragging:
            return None
        x, y = self.pointer
        support = snow.support_at(x, y, 52, hwnd=self.owner, visible=visible, within_snow=True)
        if support is None:
            return None
        return x, min(y + 6, support[3]), self.direction

    def cancel_drag(self):
        self.pointer = None
        self.owner = None
        self.direction = 1
