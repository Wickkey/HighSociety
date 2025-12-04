# HighSociety Architecture - How Network Play Works

## Class Hierarchy

```
BasePlayer (abstract base class)
├── CLIPlayer (for local CLI play)
│   └── Uses: input() and print()
│
└── NetworkPlayer (for network play)
    └── Uses: socket connection (conn.sendall/recv)
```

Both `CLIPlayer` and `NetworkPlayer` inherit from `BasePlayer`, so they have the same interface:
- `get_bid()` - Get bid from player
- `send_message()` - Send message to player
- `place_bid()` - Place a bid
- `withdraw_bid()` - Withdraw from auction
- `add_status_card()` - Add won card
- etc.

## How It All Connects

### 1. Local CLI Play (main.py)

```
main.py
  │
  ├─> Creates CLIPlayer instances
  │   └─> CLIPlayer(name, username)
  │       └─> Inherits from BasePlayer
  │
  └─> PlayGame(players=[CLIPlayer, CLIPlayer, ...], mode='cli')
      │
      ├─> Creates CLIHost (for displaying messages)
      │
      └─> game.play_game()
          │
          └─> Uses player.get_bid() → calls input() in CLIPlayer
          └─> Uses player.send_message() → calls print() in CLIPlayer
```

**Flow:**
1. `main.py` creates `CLIPlayer` objects
2. Passes them to `PlayGame(players, mode='cli')`
3. `PlayGame` uses `CLIHost` to display messages
4. During game, calls `player.get_bid()` → `CLIPlayer.get_bid()` → `input()`
5. Calls `player.send_message()` → `CLIPlayer.send_message()` → `print()`

### 2. Network Play (network_server.py)

```
network_server.py
  │
  ├─> Creates socket server
  │   └─> Waits for clients to connect
  │
  ├─> For each client connection:
  │   ├─> Accepts socket connection
  │   ├─> Gets username/name from client
  │   └─> Creates NetworkPlayer(name, username, socket_conn)
  │       └─> Inherits from BasePlayer
  │
  └─> PlayGame(players=[NetworkPlayer, NetworkPlayer, ...], mode='network')
      │
      ├─> Creates NetworkHost (for broadcasting messages)
      │
      └─> game.play_game()
          │
          └─> Uses player.get_bid() → NetworkPlayer.get_bid()
          │   └─> conn.sendall("Enter your bid: ")
          │   └─> conn.recv(1024) → gets input from client
          │
          └─> Uses player.send_message() → NetworkPlayer.send_message()
              └─> conn.sendall(message) → sends to client
```

**Flow:**
1. `network_server.py` creates socket server
2. Accepts connections from `network_client.py`
3. Creates `NetworkPlayer` objects (one per connection)
4. Passes them to `PlayGame(players, mode='network')`
5. `PlayGame` uses `NetworkHost` to broadcast messages
6. During game, calls `player.get_bid()` → `NetworkPlayer.get_bid()` → sends prompt via socket, receives response
7. Calls `player.send_message()` → `NetworkPlayer.send_message()` → sends via socket

### 3. Network Client (network_client.py)

```
network_client.py
  │
  ├─> Connects to server socket
  │
  └─> Main loop:
      ├─> Receives messages from server (socket.recv)
      │   └─> Displays to user (print)
      │
      └─> Gets user input (input())
          └─> Sends to server (socket.sendall)
```

**Flow:**
1. Client connects to server
2. Receives prompts from server (e.g., "Enter your bid: ")
3. Displays to user
4. Gets user input
5. Sends input back to server

## Key Point: Polymorphism

The magic is that **`PlayGame` doesn't care** if players are `CLIPlayer` or `NetworkPlayer`!

Both implement the same interface from `BasePlayer`:
- `get_bid()` - returns bid (int, list, or "pass")
- `send_message()` - sends message to player
- `place_bid()` - places a bid
- etc.

So `PlayGame` can use either type interchangeably:

```python
# This works with CLIPlayer:
game = PlayGame(players=[CLIPlayer(...), CLIPlayer(...)], mode='cli')

# This works with NetworkPlayer:
game = PlayGame(players=[NetworkPlayer(...), NetworkPlayer(...)], mode='network')

# The game logic is IDENTICAL - only the player type changes!
```

## Complete Network Flow Example

**Server Side:**
```python
# network_server.py
conn, addr = server_socket.accept()  # Client connects
player = NetworkPlayer(name, username, conn)  # Create network player
players.append(player)

game = PlayGame(players=players, mode='network')  # Same PlayGame class!
game.play_game()  # Game runs, uses player.get_bid() and player.send_message()
```

**During Game (Server):**
```python
# In PlayGame.normal_card_auction()
player.send_message("Auctioning: Prestige Card")  # → NetworkPlayer.send_message()
# → conn.sendall("Auctioning: Prestige Card\n")  # Sends to client

bid = player.get_bid()  # → NetworkPlayer.get_bid()
# → conn.sendall("Enter your bid: ")  # Sends prompt
# → data = conn.recv(1024)  # Receives "10" from client
# → returns [10]  # Returns bid to game
```

**Client Side:**
```python
# network_client.py receives "Auctioning: Prestige Card\n"
# Displays: "Auctioning: Prestige Card"

# Receives "Enter your bid: "
# Displays: "Enter your bid: "
# User types: "10"
# Sends: "10" to server
```

## Summary

- **`main.py`** = Creates `CLIPlayer` → `PlayGame` → Local play
- **`network_server.py`** = Creates `NetworkPlayer` → `PlayGame` → Network play
- **`network_client.py`** = Connects to server, sends/receives messages
- **`PlayGame`** = Same game logic, works with both player types
- **Polymorphism** = `CLIPlayer` and `NetworkPlayer` both inherit `BasePlayer`, so they're interchangeable

The game logic in `PlayGame` is **completely independent** of whether players are local or network-based!

