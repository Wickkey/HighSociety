from audioop import reverse
import socket
from socket import error as SocketError
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager

_protocol = {
    "<BLANK>": "\n",
    "<HEARTBEAT>":"<HEARTBEAT>"
}

_reverse_protocol = {val:key for key,val in _protocol.items()}

def receive_message(conn: socket.socket, nbytes: int = 1024) -> str:
    """
    Receive a message from the client until a newline is received.
    """
    buffer = ""
    while "\n" not in buffer:
        chunk = conn.recv(nbytes)
        if not chunk:
            LoggingManager.error(f"⚠️ Client disconnected.")
            raise SocketError(f"⚠️ Client disconnected.")
        buffer += chunk.decode()

    buffer = buffer.strip()
    return process_received_messages(buffer)


def send_message(conn: socket.socket, message: str) -> None:
    """
    Send a message to the client. Processes it before sending it.
    """
    message = process_outgoing_messages(message)
    try:
        conn.sendall((message + "\n").encode())
    except SocketError:
        LoggingManager.error(f"⚠️ Connection lost while sending to client.")
        raise SocketError(f"⚠️ Connection lost while sending to client.")

def process_received_messages(message: str) -> str:
    """
    Replace protocol keywords with their mapped values on received messages.

    Messages may contain the keywords: <BLANK>
    Example: a line containing '<BLANK>' becomes '\n'.
    """
    for key, value in _protocol.items():
        message = message.replace(key,value)

    return message

def process_outgoing_messages(message: str) -> str:
    """
    Replace protocol keywords with their mapped values before sending a message.

    Example: a line containing '\n' becomes '<BLANK>'.
    """
    for key, value in _reverse_protocol.items():
        message = message.replace(key, value)

    return message