# Quick Troubleshooting Guide

## 🔍 Step 1: Run Diagnostic Tool

**On the CLIENT machine:**
```bash
python3 test_connection.py <SERVER_IP> 8888
```

Replace `<SERVER_IP>` with the IP shown by the server.

This will tell you exactly what's wrong!

---

## ✅ Quick Checklist

### Server Side (Host Machine)

- [ ] Server is running: `python3 network_server.py --players 2`
- [ ] You see: "🎮 HighSociety Game Server Started!"
- [ ] Server shows an IP address (e.g., `192.168.1.100`)
- [ ] Server is waiting: "👥 Waiting for 2 player(s) to connect..."
- [ ] No error messages in server terminal

### Client Side (Player Machine)

- [ ] Using correct IP address (exactly as shown by server)
- [ ] Using correct port (default: 8888)
- [ ] Command: `python3 network_client.py --host <IP> --port 8888`
- [ ] On same WiFi/LAN network as server

### Network

- [ ] Both devices on same network
- [ ] Can ping server: `ping <SERVER_IP>` (if ping works, network is OK)
- [ ] Firewall not blocking (see below)

### Firewall Fixes

**macOS:**
```bash
# Check firewall:
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate

# Allow Python through firewall:
# System Settings > Network > Firewall > Options > Add Python
```

**Linux:**
```bash
# Allow port:
sudo ufw allow 8888/tcp

# Check status:
sudo ufw status
```

**Windows:**
- Windows Defender Firewall > Allow an app
- Add Python to allowed apps

---

## 🧪 Test Locally First

If connection fails, test on the **same machine** first:

**Terminal 1 (Server):**
```bash
python3 network_server.py --players 2
```

**Terminal 2 (Client):**
```bash
python3 network_client.py --host localhost --port 8888
```

If this works, the issue is network/firewall related.

---

## 🔧 Common Fixes

### Fix 1: Wrong IP Address
```bash
# Server shows: "Server IP: 192.168.1.100"
# Client must use: --host 192.168.1.100
# NOT: --host 192.168.1.101 (wrong!)
```

### Fix 2: Different Networks
```bash
# Check both devices:
ifconfig | grep "inet "  # macOS/Linux
ipconfig                  # Windows

# IPs should be in same range:
# ✅ Both 192.168.1.x
# ✅ Both 10.0.0.x
# ❌ One 192.168.1.x and one 10.0.0.x (different networks!)
```

### Fix 3: Firewall Blocking
```bash
# Temporarily disable firewall to test
# If it works, firewall is the issue
# Then re-enable and allow Python/port
```

### Fix 4: Port Already in Use
```bash
# Check what's using port 8888:
lsof -i :8888  # macOS/Linux
netstat -ano | findstr :8888  # Windows

# Use different port:
python3 network_server.py --port 8889
python3 network_client.py --host <IP> --port 8889
```

### Fix 5: Server Not Started
```bash
# Make sure server is running BEFORE client tries to connect
# Server must be waiting: "👥 Waiting for X player(s)..."
```

---

## 📞 Still Not Working?

1. **Run diagnostic:** `python3 test_connection.py <IP> <PORT>`
2. **Check error message** - it usually tells you what's wrong
3. **Try localhost** - if same machine, use `localhost` instead of IP
4. **Check firewall logs** - may show blocked connections
5. **Try different port** - some networks block certain ports

---

## 💡 Pro Tips

- **Same machine?** Always use `localhost` instead of IP
- **Different machines?** Must be on same WiFi/LAN
- **Corporate network?** May block connections - try different network
- **VPN active?** May interfere - try disabling VPN
- **Multiple network adapters?** Make sure using correct IP

---

## 🆘 Emergency Workaround

If nothing works, you can still play locally:

```bash
# Just use the regular CLI version:
python3 main.py
```

This works on a single machine with multiple terminals (each terminal = one player).

