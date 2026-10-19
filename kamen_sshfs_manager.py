"""
Kamen SSHFS Manager - mount remote folders over SSH as Windows drive letters.
Python packages: pillow, pystray, paramiko (UI itself is built-in tkinter).
Requires: WinFsp + SSHFS-Win (https://github.com/winfsp/sshfs-win/releases)

The code lives in the kamen/ package; this file only starts the app.
"""
import os
import sys

from kamen.ssh import ASKPASS_ENV

if __name__ == "__main__":
    # Launched by ssh as SSH_ASKPASS (see kamen/ssh.py): answer and exit before
    # any UI starts. Must stay before the single-instance check, since the
    # helper is this same exe running while the app is open.
    if os.environ.get(ASKPASS_ENV) and len(sys.argv) == 2:
        from kamen.ui import _askpass_main
        sys.exit(_askpass_main(sys.argv[1]))
    from kamen.winutil import claim_single_instance
    if not claim_single_instance():
        sys.exit(0)
    from kamen.ui import App
    App().mainloop()
    # A probe thread stuck on a dead drive must not keep the process alive
    import logging
    logging.shutdown()
    os._exit(0)
