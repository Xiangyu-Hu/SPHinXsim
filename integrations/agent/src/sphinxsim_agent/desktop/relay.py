"""Fixed duplex relay for the Worker's authenticated Docker transport."""
import socket
import sys
import threading

with socket.create_connection(("127.0.0.1", 6901), timeout=10) as connection:
    connection.settimeout(None)

    def upload():
        try:
            while chunk := sys.stdin.buffer.read1(65536):
                connection.sendall(chunk)
        finally:
            connection.shutdown(socket.SHUT_WR)

    threading.Thread(target=upload, daemon=True).start()
    while chunk := connection.recv(65536):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
