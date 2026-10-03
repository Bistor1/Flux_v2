"""Single-instance lock. A second launch sends SHOW and exits."""

import fcntl
import os
import socket
import time

SHOW = b"SHOW"


def lock_socket_path():
    """Unix socket in the user's state dir."""
    state = os.environ.get(
        "XDG_STATE_HOME",
        os.path.expanduser("~/.local/state"),
    )
    flux_state = os.path.join(state, "fluxv2")
    try:
        os.makedirs(flux_state, exist_ok=True)
    except OSError:
        pass
    return os.path.join(flux_state, "fluxv2.sock")


class Instance:
    """Owns the flock and the listening socket. Keep it open."""

    def __init__(self, server, lock_fd, sock_path):
        self.server = server
        self._lock_fd = lock_fd
        self.sock_path = sock_path

    def close(self):
        try:
            self.server.close()
        except OSError:
            pass
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None


def single_instance_or_signal():
    """Become the primary instance, or wake the one already running.

    Returns an Instance when this process owns the lock. The caller must
    keep it open. Returns None after sending SHOW to the other instance.

    The lock is an flock, not the socket file. A failed connect must not
    delete a live socket.
    """
    sock_path = lock_socket_path()
    lock_path = sock_path + ".lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        _signal(sock_path)
        return None
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        _signal(sock_path)
        return None
    except OSError:
        os.close(lock_fd)
        _signal(sock_path)
        return None

    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except OSError:
        pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(sock_path)
        server.listen(8)
    except OSError:
        server.close()
        os.close(lock_fd)
        return None
    try:
        os.chmod(sock_path, 0o600)
    except OSError:
        pass
    return Instance(server, lock_fd, sock_path)


def _signal(sock_path):
    """Tell the running instance to show its window. Retry while it starts."""
    for _ in range(40):
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(0.25)
            client.connect(sock_path)
            client.sendall(SHOW)
            client.close()
            return
        except OSError:
            time.sleep(0.05)


def serve_show(server, on_show):
    """Block until the socket closes. Call on_show for each SHOW message.

    on_show runs on this thread. It must not touch Tk.
    """
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        try:
            data = b""
            while len(data) < len(SHOW):
                chunk = conn.recv(len(SHOW) - len(data))
                if not chunk:
                    break
                data += chunk
            if data == SHOW:
                on_show()
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
