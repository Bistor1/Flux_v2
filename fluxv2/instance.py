"""Single-instance lock. A second launch sends SHOW and exits."""

import os
import socket

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
    except Exception:
        pass
    return os.path.join(flux_state, "fluxv2.sock")


def single_instance_or_signal():
    """Become the primary instance, or wake the one already running.

    Returns the bound socket when this process owns the lock. The caller
    must keep it open. Returns None after sending SHOW to the other instance.
    """
    sock_path = lock_socket_path()
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(1.0)
        client.connect(sock_path)
        client.sendall(SHOW)
        client.close()
        return None
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
        pass

    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except OSError:
        pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(sock_path)
    server.listen(1)
    server.settimeout(None)
    try:
        os.chmod(sock_path, 0o600)
    except OSError:
        pass
    return server


def serve_show(server, on_show):
    """Block until the socket closes. Call on_show for each SHOW message.

    on_show runs on this thread. It must not touch Tk.
    """
    while True:
        try:
            conn, _ = server.accept()
        except Exception:
            return
        try:
            if conn.recv(16) == SHOW:
                on_show()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
