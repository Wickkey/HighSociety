#!/usr/bin/env python3
"""
Connection Diagnostic Tool
Helps diagnose network connection issues for HighSociety game.
"""

import socket
import sys
import subprocess
import platform

def get_local_ip():
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def test_port_listening(host, port):
    """Test if a port is listening."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        print(f"   Error testing port: {e}")
        return False

def test_connection(host, port):
    """Test connection to server."""
    print(f"\n🔍 Testing connection to {host}:{port}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result == 0:
            print(f"   ✅ Connection successful!")
            return True
        else:
            print(f"   ❌ Connection failed (Error code: {result})")
            return False
    except socket.gaierror:
        print(f"   ❌ DNS resolution failed - hostname '{host}' not found")
        return False
    except socket.timeout:
        print(f"   ❌ Connection timeout - server not responding")
        return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False

def check_firewall_macos():
    """Check firewall status on macOS."""
    print("\n🔥 Checking macOS Firewall...")
    try:
        result = subprocess.run(['/usr/libexec/ApplicationFirewall/socketfilterfw', '--getglobalstate'], 
                              capture_output=True, text=True, timeout=5)
        if 'enabled' in result.stdout.lower():
            print("   ⚠️  Firewall is ENABLED")
            print("   💡 You may need to allow Python through firewall:")
            print("      System Settings > Network > Firewall > Options")
            return True
        else:
            print("   ✅ Firewall is disabled or not blocking")
            return False
    except Exception:
        print("   ⚠️  Could not check firewall status")
        return False

def check_firewall_linux():
    """Check firewall status on Linux."""
    print("\n🔥 Checking Linux Firewall...")
    try:
        # Check if ufw is active
        result = subprocess.run(['ufw', 'status'], capture_output=True, text=True, timeout=5)
        if 'active' in result.stdout.lower():
            print("   ⚠️  UFW firewall is ACTIVE")
            print("   💡 You may need to allow the port:")
            print(f"      sudo ufw allow {port}/tcp")
            return True
        else:
            print("   ✅ UFW firewall is inactive")
            return False
    except FileNotFoundError:
        print("   ℹ️  UFW not installed (check other firewalls manually)")
        return False
    except Exception:
        print("   ⚠️  Could not check firewall status")
        return False

def main():
    print("="*60)
    print("🔧 HighSociety Connection Diagnostic Tool")
    print("="*60)
    
    # Get server info
    if len(sys.argv) < 3:
        print("\nUsage: python3 test_connection.py <SERVER_IP> <PORT>")
        print("Example: python3 test_connection.py 192.168.1.100 8888")
        print("\nOr test localhost:")
        print("Example: python3 test_connection.py localhost 8888")
        sys.exit(1)
    
    host = sys.argv[1]
    port = int(sys.argv[2])
    
    print(f"\n📋 Configuration:")
    print(f"   Server: {host}")
    print(f"   Port: {port}")
    
    # Test 1: Check if server is reachable
    print(f"\n{'='*60}")
    print("TEST 1: Network Reachability")
    print(f"{'='*60}")
    
    if host == "localhost" or host == "127.0.0.1":
        print("   ℹ️  Testing localhost connection")
    else:
        print(f"   ℹ️  Testing connection to {host}")
    
    connection_ok = test_connection(host, port)
    
    # Test 2: Check local IP
    print(f"\n{'='*60}")
    print("TEST 2: Local Network Info")
    print(f"{'='*60}")
    local_ip = get_local_ip()
    print(f"   Your local IP: {local_ip}")
    
    if host != "localhost" and host != "127.0.0.1" and host != local_ip:
        print(f"   ⚠️  Server IP ({host}) differs from your IP ({local_ip})")
        print(f"   💡 Make sure both devices are on the same network")
    
    # Test 3: Check firewall
    print(f"\n{'='*60}")
    print("TEST 3: Firewall Check")
    print(f"{'='*60}")
    
    system = platform.system()
    if system == "Darwin":  # macOS
        check_firewall_macos()
    elif system == "Linux":
        check_firewall_linux()
    else:
        print(f"   ℹ️  Platform: {system} - check firewall manually")
    
    # Test 4: Port availability
    print(f"\n{'='*60}")
    print("TEST 4: Port Status")
    print(f"{'='*60}")
    
    if host == "localhost" or host == "127.0.0.1":
        is_listening = test_port_listening("127.0.0.1", port)
        if is_listening:
            print(f"   ✅ Port {port} is listening on localhost")
        else:
            print(f"   ❌ Port {port} is NOT listening")
            print(f"   💡 Make sure the server is running!")
    else:
        print(f"   ℹ️  Cannot test remote port status from here")
    
    # Summary
    print(f"\n{'='*60}")
    print("📊 DIAGNOSIS SUMMARY")
    print(f"{'='*60}")
    
    if connection_ok:
        print("✅ Connection test PASSED!")
        print("   Your network setup looks good.")
    else:
        print("❌ Connection test FAILED")
        print("\n🔧 Troubleshooting Steps:")
        print("   1. ✅ Verify server is running:")
        print("      python3 network_server.py --players 2")
        print("\n   2. ✅ Check IP address is correct")
        print("      Server should show its IP when starting")
        print("\n   3. ✅ Ensure same network:")
        print("      Both devices must be on same WiFi/LAN")
        print("\n   4. ✅ Check firewall settings:")
        if system == "Darwin":
            print("      macOS: System Settings > Network > Firewall")
        elif system == "Linux":
            print(f"      Linux: sudo ufw allow {port}/tcp")
        print("\n   5. ✅ Try different port:")
        print("      Server: --port 8889")
        print("      Client: --port 8889")
        print("\n   6. ✅ Test with localhost first:")
        print("      If same machine: use 'localhost' as host")
    
    print(f"\n{'='*60}\n")

if __name__ == '__main__':
    main()

