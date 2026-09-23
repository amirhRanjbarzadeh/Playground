# 1. the order is preserved
# 2. parents come after children
# 3. each class appears exactly once.
# first AlertService is added. then in parents SmsNotifier comes first so it is added. then its parent
# is Notifier since it is parent of EmailNotifier as well it is for now kept then EmailNotifier is added
# then Notifier and its parent that is object

class Notifier:
    def send(self, msg):
        return f"base: {msg}"


class EmailNotifier(Notifier):
    def send(self, msg):
        return f"email: {msg}"


class SmsNotifier(Notifier):
    def send(self, msg):
        return f"sms: {msg}"


class AlertService(SmsNotifier, EmailNotifier):
    pass

print([c.__name__ for c in AlertService.__mro__]) # [AlertService, SmsNotifierNotifier, EmailNotifier, Notifier, object]
print(AlertService().send("hi")) # sms: hi

try:
    class Broken(Notifier, EmailNotifier):
        pass
except TypeError as exc:
    print(exc)