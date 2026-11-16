class DomainError(Exception):
    """A rejected business request; no partial business publication."""


class InvalidModel(DomainError):
    pass


class RequestConflict(DomainError):
    pass


class UnknownRevision(DomainError):
    pass


class UnknownVariable(DomainError):
    pass


class TaskConflict(DomainError):
    pass


class UnknownTask(DomainError):
    pass


class NotRun(DomainError):
    pass


class ImpossibleObservation(DomainError):
    pass


class InvalidTask(DomainError):
    pass
