"""Small flock adapter for Unix and Windows byte-range locks."""
import os

if os.name != "nt":
    from fcntl import flock, LOCK_EX, LOCK_NB, LOCK_UN
else:
    import msvcrt
    LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4

    def flock(file, flags):
        descriptor = file.fileno()
        file.seek(0)
        if flags & LOCK_UN:
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
        if os.fstat(descriptor).st_size == 0:
            file.write("\0")
            file.flush()
            file.seek(0)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK if flags & LOCK_NB else msvcrt.LK_LOCK, 1)
        except OSError as error:
            raise BlockingIOError(str(error)) from error
