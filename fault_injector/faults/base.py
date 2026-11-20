"""What every fault provides."""


class Fault:
    """One way of breaking the lab.

    fault_id is "<class>.<variant>". root_cause is the label a correct
    diagnosis must give. start() breaks the lab and returns a list of plain
    descriptions of what it changed; stop() undoes everything start() did that
    does not end by itself when the injector's connections close.
    """

    fault_id = ""
    root_cause = ""
    summary = ""

    def start(self, lab_access):
        raise NotImplementedError

    def stop(self, lab_access):
        raise NotImplementedError
