from highsociety.code.gamecore.player.player import BasePlayer

import socket

class NetworkPlayer(BasePlayer):
    """Remote player using sockets."""
    def __init__(self, name, username, conn):
        super().__init__(name, username)
        self.conn = conn
        self.conn.settimeout(60)  # timeout after 60 seconds of no response

    def print_player_info(self):
        self.send_message(f"{self.username}'s status cards: {self.status_cards}")
        self.send_message(f"Current Bid: {self.current_money_card_bids}")
        self.send_message(f"Remaining Money: {self.money_cards}")

    def send_message(self, msg: str):
        try:
            self.conn.sendall(msg.encode())
        except (BrokenPipeError, ConnectionResetError):
            print(f"⚠️ Connection lost while sending to {self.username}")

    def get_bid(self):
        """Request a bid from the client and validate it."""
        while True:
            self.send_message("Enter your bid: ")
            try:
                data = self.conn.recv(1024)
                if not data:
                    print(f"⚠️ {self.username} disconnected.")
                    return None

                bid = data.decode().strip()

                # Handle special commands
                if bid.lower() in ["pass", "fold", "quit"]:
                    return bid.lower()

                # Parse numeric input or list input
                if bid.startswith("[") and bid.endswith("]"):
                    try:
                        nums = [int(x.strip()) for x in bid.strip("[]").split(",") if x.strip()]
                        return nums
                    except ValueError:
                        self.send_message("⚠️ Invalid list format. Try again.\n")
                        continue
                else:
                    try:
                        return int(bid)
                    except ValueError:
                        self.send_message("⚠️ Invalid input. Enter a number or list.\n")
                        continue

            except socket.timeout:
                print(f"⏰ {self.username} timed out.")
                return None

            except (ConnectionResetError, BrokenPipeError):
                print(f"⚠️ {self.username} disconnected unexpectedly.")
                return None

    def close(self):
        try:
            self.conn.close()
        except:
            pass

