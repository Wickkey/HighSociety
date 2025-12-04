# How Network Play Works - Simple Explanation

## The Connection Chain

```
┌─────────────────────────────────────────────────────────────┐
│                    network_server.py                         │
│  (This is like the "host" of the game)                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Creates socket server                                    │
│  2. Waits for clients to connect                            │
│  3. For each client:                                         │
│     - Accepts connection                                     │
│     - Gets username/name                                     │
│     - Creates NetworkPlayer(name, username, socket)        │
│                                                              │
│  4. Once all players connected:                             │
│     game = PlayGame(players=[NetworkPlayer, ...],           │
│                     mode='network')                          │
│     game.play_game()  ← SAME PlayGame class as main.py!     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ Uses
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              PlayGame (gameplay.py)                          │
│  (The actual game logic - SAME for CLI and Network!)        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  During auction:                                              │
│    for player in players:                                    │
│      player.send_message("Auctioning: Card X")              │
│      bid = player.get_bid()                                  │
│      player.place_bid(bid)                                   │
│                                                              │
│  It doesn't know/care if player is:                          │
│    - CLIPlayer (local) or NetworkPlayer (network)          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ Calls methods on
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              NetworkPlayer (networkplayer.py)                │
│  (Inherits from BasePlayer - same interface as CLIPlayer)   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  send_message(msg):                                          │
│    → conn.sendall(msg.encode())  ← Sends to client          │
│                                                              │
│  get_bid():                                                  │
│    → conn.sendall("Enter your bid: ")  ← Sends prompt       │
│    → data = conn.recv(1024)  ← Receives input               │
│    → return bid  ← Returns to PlayGame                      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ Socket connection
                          │ (TCP/IP)
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              network_client.py                               │
│  (Runs on each player's machine)                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Connects to server socket                                │
│  2. Receives messages:                                      │
│     data = sock.recv(4096)                                   │
│     print(data)  ← Shows to player                          │
│                                                              │
│  3. Gets user input:                                         │
│     user_input = input()  ← Player types                    │
│     sock.sendall(user_input)  ← Sends to server              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Key Insight: Polymorphism

**Both `CLIPlayer` and `NetworkPlayer` inherit from `BasePlayer`:**

```python
class BasePlayer:
    def get_bid(self):  # Abstract method
        raise NotImplementedError
    
    def send_message(self, msg):  # Abstract method
        raise NotImplementedError

class CLIPlayer(BasePlayer):
    def get_bid(self):
        return input("Enter bid: ")  # Uses local input()
    
    def send_message(self, msg):
        print(msg)  # Uses local print()

class NetworkPlayer(BasePlayer):
    def get_bid(self):
        self.conn.sendall("Enter bid: ")  # Sends via socket
        return self.conn.recv(1024)  # Receives via socket
    
    def send_message(self, msg):
        self.conn.sendall(msg)  # Sends via socket
```

**So `PlayGame` can use either:**

```python
# Local play:
players = [CLIPlayer("Alice", "alice"), CLIPlayer("Bob", "bob")]
game = PlayGame(players, mode='cli')
game.play_game()

# Network play:
players = [NetworkPlayer("Alice", "alice", conn1), 
           NetworkPlayer("Bob", "bob", conn2)]
game = PlayGame(players, mode='network')
game.play_game()

# The game.play_game() code is IDENTICAL!
# It just calls player.get_bid() and player.send_message()
# The player object handles whether it's local or network!
```

## Complete Flow Example

**Scenario:** Player needs to bid

1. **PlayGame** (gameplay.py):
   ```python
   bid = player.get_bid()  # Calls NetworkPlayer.get_bid()
   ```

2. **NetworkPlayer** (networkplayer.py):
   ```python
   def get_bid(self):
       self.conn.sendall("Enter your bid: ")  # Sends to client
       data = self.conn.recv(1024)  # Waits for response
       return data.decode()  # Returns "10" or "[1,2,3]"
   ```

3. **network_client.py** (on player's machine):
   ```python
   # Receives "Enter your bid: "
   print("Enter your bid: ")  # Shows to player
   user_input = input()  # Player types "10"
   sock.sendall("10")  # Sends back to server
   ```

4. **NetworkPlayer** receives "10", returns it to **PlayGame**

5. **PlayGame** continues with the bid value

## Why This Works

- **Same Interface**: Both player types implement the same methods
- **Polymorphism**: `PlayGame` treats all players the same way
- **Separation of Concerns**: Game logic is separate from I/O method
- **Reusability**: Same `PlayGame` class works for both modes

The game doesn't need to know HOW the player gets input - it just calls `get_bid()` and the player object handles it (whether it's `input()` for CLI or `socket.recv()` for network)!

