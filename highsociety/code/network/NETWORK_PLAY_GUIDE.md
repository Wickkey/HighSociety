# HighSociety Network Play Guide

This guide explains how to play HighSociety over a network (WiFi/LAN) using multiple terminals.

## Overview

The network setup consists of:
- **Server** (`network_server.py`): Runs on one machine, hosts the game
- **Client** (`network_client.py`): Runs on each player's machine/terminal, connects to the server

## Quick Start

### Step 1: Start the Server

On the **host machine** (the one that will run the game):

```bash
cd /Users/vignesh/Documents/HighSociety
python3 network_server.py --players 2 --port 8888
```

**Options:**
- `--players N`: Number of players (default: 2, minimum: 2)
- `--port PORT`: Port number (default: 8888)
- `--host HOST`: Host address (default: 0.0.0.0 for all interfaces)

The server will display:
- The IP address to share with players
- The port number
- Wait for all players to connect

**Example:**
```bash
python3 network_server.py --players 4 --port 8888
```

### Step 2: Connect Clients

On **each player's machine/terminal**, run:

```bash
cd /Users/vignesh/Documents/HighSociety
python3 network_client.py --host <SERVER_IP> --port 8888
```

**Replace `<SERVER_IP>` with:**
- The IP address shown by the server
- Or use `localhost` if connecting from the same machine

**Example:**
```bash
# If server IP is 192.168.1.100
python3 network_client.py --host 192.168.1.100 --port 8888
```

### Step 3: Play!

Once all players are connected, the game will start automatically!

## Finding Your Server IP Address

### On macOS/Linux:
```bash
# Method 1: Using ifconfig
ifconfig | grep "inet " | grep -v 127.0.0.1

# Method 2: Using ipconfig (macOS)
ipconfig getifaddr en0

# Method 3: The server will show it automatically
```

### On Windows:
```bash
ipconfig
# Look for "IPv4 Address" under your network adapter
```

## Playing Over WiFi Network

1. **Make sure all devices are on the same WiFi network**

2. **Start the server on one machine:**
   ```bash
   python3 network_server.py --players 4
   ```
   Note the IP address shown (e.g., `192.168.1.100`)

3. **On each player's device, connect:**
   ```bash
   python3 network_client.py --host 192.168.1.100 --port 8888
   ```


## Game Controls

During the game, you can:

- **Bid a single card:** Enter a number (e.g., `10`)
- **Bid multiple cards:** Enter a list (e.g., `[1, 2, 3]`)
- **Pass/Fold:** Enter `pass` or `fold` to withdraw from auction
- **Quit:** Enter `quit` to leave the game

## Troubleshooting

### Quick Diagnostic Tool

First, run the diagnostic tool to identify the issue:

```bash
# On the CLIENT machine, test connection:
python3 test_connection.py <SERVER_IP> 8888

# Example:
python3 test_connection.py 192.168.1.100 8888
# Or for localhost:
python3 test_connection.py localhost 8888
```

This will check:
- Network connectivity
- Firewall status
- Port availability
- IP configuration

### Common Connection Issues

#### 1. "Connection refused" or "Failed to connect"

**Step-by-step fix:**

1. **Verify server is running:**
   ```bash
   # On server machine, make sure you see:
   # "🎮 HighSociety Game Server Started!"
   # "👥 Waiting for X player(s) to connect..."
   ```

2. **Check IP address:**
   - Server shows IP when starting (e.g., `192.168.1.100`)
   - Use EXACTLY that IP in client
   - If on same machine, use `localhost` instead

3. **Verify same network:**
   ```bash
   # On server machine:
   ifconfig | grep "inet "  # macOS/Linux
   ipconfig                  # Windows
   
   # On client machine, check if IPs are in same range
   # e.g., both 192.168.1.x or both 10.0.0.x
   ```

4. **Test with localhost first:**
   ```bash
   # If server and client on SAME machine:
   python3 network_client.py --host localhost --port 8888
   ```

5. **Check firewall:**
   
   **macOS:**
   - System Settings > Network > Firewall > Options
   - Allow Python/terminal through firewall
   - Or temporarily disable firewall to test
   
   **Linux:**
   ```bash
   sudo ufw allow 8888/tcp
   # Or check status:
   sudo ufw status
   ```
   
   **Windows:**
   - Windows Defender Firewall > Allow an app
   - Add Python to allowed apps

6. **Try different port:**
   ```bash
   # Server:
   python3 network_server.py --port 8889
   
   # Client:
   python3 network_client.py --host <IP> --port 8889
   ```

#### 2. "Address already in use"

**Fix:**
```bash
# Check if another server is running:
lsof -i :8888  # macOS/Linux
netstat -ano | findstr :8888  # Windows

# Kill the process or use different port:
python3 network_server.py --port 8889
```

#### 3. "Connection timeout"

**Possible causes:**
- Server not running
- Wrong IP address
- Firewall blocking
- Network connectivity issue

**Fix:**
- Run diagnostic tool: `python3 test_connection.py <IP> <PORT>`
- Verify server is actually listening
- Check firewall settings
- Try pinging the server: `ping <SERVER_IP>`

#### 4. "Name or service not known" (DNS error)

**Fix:**
- Use IP address instead of hostname
- Example: Use `192.168.1.100` instead of `server.local`

### Players can't see messages
- ✅ Check network connectivity
- ✅ Verify firewall isn't blocking connections
- ✅ Make sure all clients are properly connected

### Game hangs or disconnects
- ✅ Check network stability
- ✅ Players have 60 seconds to respond (timeout)
- ✅ Ensure all players stay connected

## Example: 4-Player Game on Same WiFi

**Terminal 1 (Server - Host Machine):**
```bash
python3 network_server.py --players 4
# Shows: Server IP: 192.168.1.100, Port: 8888
```

**Terminal 2 (Player 1 - Same or different machine):**
```bash
python3 network_client.py --host 192.168.1.100 --port 8888
```

**Terminal 3 (Player 2):**
```bash
python3 network_client.py --host 192.168.1.100 --port 8888
```

**Terminal 4 (Player 3):**
```bash
python3 network_client.py --host 192.168.1.100 --port 8888
```

**Terminal 5 (Player 4):**
```bash
python3 network_client.py --host 192.168.1.100 --port 8888
```

Once all 4 clients connect, the game starts automatically!

## Notes

- The server must be started **before** clients connect
- All players must connect before the game starts
- Players can be on different machines or the same machine (using different terminals)
- The game runs on the server; clients just send/receive messages
- If a player disconnects, they'll be marked inactive

## Security Note

This is a simple implementation for local network play. For internet play, consider:
- Using SSH tunnels
- Adding authentication
- Using a more secure protocol (TLS/SSL)

Enjoy playing HighSociety over the network! 🎮

