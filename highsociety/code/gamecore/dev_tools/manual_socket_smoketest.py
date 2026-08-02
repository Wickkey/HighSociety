#!/usr/bin/env python3
"""
Simple test to verify server-client communication works
"""

import socket
import threading
import time

def test_server():
    """Simple test server"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('localhost', 9999))
    server.listen(1)
    print("Test server listening on localhost:9999")
    
    conn, addr = server.accept()
    print(f"Client connected from {addr}")
    
    # Send prompt
    print("Sending: 'Enter your username: '")
    conn.sendall(b"Enter your username: ")
    time.sleep(0.1)
    
    # Receive response
    data = conn.recv(1024)
    print(f"Received: {data}")
    username = data.decode().strip()
    print(f"Username: '{username}'")
    
    conn.close()
    server.close()
    print("Test server closed")

def test_client():
    """Simple test client"""
    time.sleep(1)  # Wait for server to start
    
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(('localhost', 9999))
    print("Connected to server")
    
    # Receive prompt
    data = client.recv(1024)
    print(f"Received from server: {data}")
    prompt = data.decode()
    print(f"Prompt: '{prompt}'")
    
    # Send response
    response = "testuser"
    print(f"Sending: '{response}'")
    client.sendall(response.encode())
    
    client.close()
    print("Test client closed")

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'client':
        test_client()
    else:
        # Run server in thread, client in main
        server_thread = threading.Thread(target=test_server, daemon=True)
        server_thread.start()
        time.sleep(0.5)
        test_client()
        time.sleep(1)

