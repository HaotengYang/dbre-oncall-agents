"""What every fault provides."""


class Fault:
    """One way of breaking the lab.

    fault_id is "<class>.<variant>". root_cause is the label a correct
    diagnosis must give. planned_changes describes, in plain words, what
    start() does; it is recorded before start() runs, so even an injector
    killed mid-start leaves ground truth behind.

    start() breaks the lab. stop() undoes everything start() did that does not
    end by itself when the injector's connections close. stop() must be safe
    to call after a start() that failed part-way.
    """

    fault_id = ""
    root_cause = ""
    summary = ""
    planned_changes = ()

    def start(self, lab_access):
        raise NotImplementedError

    def stop(self, lab_access):
        raise NotImplementedError
